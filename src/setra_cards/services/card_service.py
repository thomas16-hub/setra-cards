"""Orquesta la emision de tarjetas: builder -> writer -> audit en DB.

Escrito desde cero — integrado con Guest, single-hotel.
Delega la crypto/serial a `cards.builder` y `cards.writer` (validados en prod).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from setra_cards.cards.builder import (
    build_auth_card,
    build_clock_card,
    build_guest_card,
    build_laundry_card,
    build_master_card,
    build_setting_card,
)
from setra_cards.cards.reader import DecodedCard, read_card
from setra_cards.cards.s70_reader import read_s70_card
from setra_cards.cards.writer import ENCODER_DISCONNECTED_ERROR, blank_card, write_card
from setra_cards.core.app_state import HotelConfig
from setra_cards.encoder.driver import EncoderDriver
from setra_cards.encoder.protocol import (
    CARD_TYPE_AUTH,
    CARD_TYPE_CHECKOUT,
    CARD_TYPE_CLOCK,
    CARD_TYPE_GUEST,
    CARD_TYPE_HOUSEKEEPING,
    CARD_TYPE_LAUNDRY,
    CARD_TYPE_MASTER,
    CARD_TYPE_SETTING,
)
from setra_cards.services.action_log import log as log_action
from setra_cards.services.encoder_service import emission_transaction
from setra_cards.storage.models import CardLog, Guest, Room

logger = logging.getLogger(__name__)

# Operator-facing message when an emit/blank is attempted while another card
# operation already holds the service-level emission lock (fail-fast path).
BUSY_ERROR = "Operación en curso — espere a que termine la grabación actual"

# Operator-facing card type labels (Spanish) keyed by protocol type byte.
CARD_TYPE_LABELS_ES: dict[int, str] = {
    CARD_TYPE_GUEST: "Huésped",
    # Limpieza shares the Master type byte (0x60): a card read back off the
    # encoder cannot tell them apart, only the issue log can.
    CARD_TYPE_MASTER: "Maestra / Limpieza",
    CARD_TYPE_SETTING: "Configuración",
    CARD_TYPE_CLOCK: "Reloj",
    CARD_TYPE_AUTH: "Autorización",
    # Bytes Setra CARDS wrongly wrote before 2026-10-14 for Maestra (0x40)
    # and Limpieza (0x20). Hotels reported those Maestras opened no door.
    CARD_TYPE_HOUSEKEEPING: "Maestra antigua (re-emitir)",
    CARD_TYPE_CHECKOUT: "Limpieza antigua (re-emitir)",
}

LEGACY_GLOBAL_TYPES = (CARD_TYPE_HOUSEKEEPING, CARD_TYPE_CHECKOUT)
LEGACY_GLOBAL_STATUS = "Tarjeta de una versión anterior de Setra CARDS: puede no abrir. RE-EMITIR"

# Limpieza opens every room, so its duration is capped by role.
FRONTDESK_LAUNDRY_MAX_HOURS = 10
MANAGER_LAUNDRY_MAX_HOURS = 24


def _manager_only(operator_role: str | None, card_label: str) -> CardResult | None:
    """Refuse manager-level cards in the service, not only by hiding the tab.

    Returns a failed CardResult for roles below manager, None when allowed.
    """
    from setra_cards.services.auth import role_has_access

    if role_has_access(operator_role, "manager"):
        return None
    return CardResult(False, card_label, error="Solo un gerente puede emitir esta tarjeta")


def laundry_max_hours(operator_role: str | None) -> int:
    from setra_cards.services.auth import role_has_access

    if role_has_access(operator_role, "manager"):
        return MANAGER_LAUNDRY_MAX_HOURS
    return FRONTDESK_LAUNDRY_MAX_HOURS


# CardLog.card_type_byte sentinel for blank operations on unreadable cards.
BLANK_TYPE_BYTE = 0xFF

# Auth cards are valid 30 days from issue (see builder.build_auth_card).
AUTH_CARD_VALIDITY_DAYS = 30


def _card_type_label(card_type: int) -> str:
    """Spanish display label for a protocol card type byte."""
    return CARD_TYPE_LABELS_ES.get(card_type, f"Desconocida (0x{card_type:02X})")


@dataclass(frozen=True)
class CardResult:
    ok: bool
    card_type: str
    uid_hex: str | None = None
    room_display: str | None = None
    guest_id: int | None = None
    valid_from: datetime | None = None
    expires_at: datetime | None = None
    message: str = ""
    error: str = ""


def _log_emission(
    session: Session,
    card_type: str,
    type_byte: int,
    uid_hex: str | None,
    success: bool,
    operator: str,
    room: Room | None = None,
    guest: Guest | None = None,
    valid_from: datetime | None = None,
    expires_at: datetime | None = None,
    error: str | None = None,
    room_display: str | None = None,
) -> CardLog:
    entry = CardLog(
        card_type=card_type,
        card_type_byte=type_byte,
        guest_id=guest.id if guest else None,
        room_id=room.id if room else None,
        room_display=room_display if room_display is not None else (room.display_number if room else None),
        uid_hex=uid_hex,
        valid_from=valid_from,
        expires_at=expires_at,
        operator=operator,
        success=success,
        error_message=error,
    )
    session.add(entry)
    session.commit()
    return entry


def invalidate_active_guest_cards(
    session: Session, room_id: int, *, now: datetime | None = None,
    legacy_display: str | None = None,
) -> int:
    """Expire all still-active Guest CardLog rows for a room.

    Keyed by room_id, so renaming a room never orphans its guest card.
    legacy_display also expires old rows logged without a room_id.
    Does NOT commit — the caller owns the transaction boundary.
    Returns the number of rows expired.
    """
    effective_now = now or datetime.now()
    room_match = CardLog.room_id == room_id
    if legacy_display:
        room_match = or_(room_match, and_(CardLog.room_id.is_(None),
                                          CardLog.room_display == legacy_display))
    stmt = (
        sql_update(CardLog)
        .where(
            room_match,
            CardLog.card_type == "Guest",
            CardLog.success.is_(True),
            CardLog.expires_at > effective_now,
        )
        .values(expires_at=effective_now)
    )
    result = session.execute(stmt)
    return result.rowcount


def _invalidate_previous_guest_cards(session: Session, room: Room) -> int:
    """Expire previous Guest cards for the room and commit immediately."""
    count = invalidate_active_guest_cards(session, room.id)
    session.commit()
    return count


def create_guest_card(
    *,
    encoder: EncoderDriver,
    hotel: HotelConfig,
    session: Session,
    room: Room,
    guest: Guest | None,
    valid_from: datetime,
    valid_until: datetime,
    operator: str,
    invalidate_previous: bool = True,
) -> CardResult:
    if valid_until <= valid_from:
        return CardResult(False, "Guest", error="El checkout debe ser posterior al check-in")

    try:
        card_data = build_guest_card(
            room=int(room.sequential_id),
            now=valid_from,
            checkout=valid_until,
            sig=hotel.signature,
            building=room.building,
            floor=room.floor,
            room_no_id=room.room_no_id,
        )
    except Exception as exc:
        logger.exception("Error construyendo guest card")
        return CardResult(False, "Guest", error=f"Error interno: {exc}")

    # Fail-fast: a write must never interleave with another in-flight operation.
    with emission_transaction(blocking=False) as acquired:
        if not acquired:
            return CardResult(False, "Guest", error=BUSY_ERROR)

        result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key,
                            building=room.building)
        uid_hex = result.uid.hex(":") if result.uid else None

        if not result.success:
            _log_emission(
                session, "Guest", CARD_TYPE_GUEST, uid_hex, False, operator,
                room=room, guest=guest, valid_from=valid_from, expires_at=valid_until,
                error=result.error,
            )
            return CardResult(False, "Guest", uid_hex=uid_hex, error=result.error or "Error al escribir")

        try:
            if invalidate_previous:
                _invalidate_previous_guest_cards(session, room)

            _log_emission(
                session, "Guest", CARD_TYPE_GUEST, uid_hex, True, operator,
                room=room, guest=guest, valid_from=valid_from, expires_at=valid_until,
            )
            log_action(
                session, "card_guest", operator,
                f"Hab. {room.display_number}, {guest.name if guest else '(sin huesped)'} hasta {valid_until:%Y-%m-%d %H:%M}",
            )
        except Exception as exc:
            session.rollback()
            logger.exception("DB audit failed after successful guest card write")
            return CardResult(
                False, "Guest", uid_hex=uid_hex,
                error=f"Tarjeta grabada pero falló el registro en base de datos: {exc}",
            )
        return CardResult(
            True, "Guest", uid_hex=uid_hex, room_display=room.display_number,
            guest_id=guest.id if guest else None,
            valid_from=valid_from, expires_at=valid_until,
            message=f"Tarjeta grabada para hab. {room.display_number}",
        )


def change_guest_room(
    *,
    encoder: EncoderDriver,
    hotel: HotelConfig,
    session: Session,
    old_room: Room,
    new_room: Room,
    operator: str,
) -> CardResult:
    """Cambia a un huesped de habitacion mid-stay.

    Lee la Guest card actual del encoder (debe ser de old_room), la reescribe
    con los datos de new_room preservando checkout y huesped, invalida el
    CardLog anterior, crea uno nuevo, marca old_room como 'sucia' y new_room
    como 'ocupada' en una sola transaccion.

    El usuario debe pasar la tarjeta reescrita por la cerradura de new_room
    para que aprenda la nueva habitacion.
    """
    if old_room.id == new_room.id:
        return CardResult(False, "Guest", error="La habitacion destino es la misma")

    # Whole read+rewrite is ONE encoder operation — fail-fast so it can never
    # interleave its read or its write with another in-flight operation.
    with emission_transaction(blocking=False) as acquired:
        if not acquired:
            return CardResult(False, "Guest", error=BUSY_ERROR)

        # 1) Leer tarjeta actual para validar que es Guest de old_room
        decoded = read_card(encoder, hotel.sector, hotel.signature, des_key=hotel.des_key)
        if not decoded:
            if not getattr(encoder, "is_open", True):
                return CardResult(False, "Guest", error=ENCODER_DISCONNECTED_ERROR)
            return CardResult(False, "Guest", error="No se pudo leer la tarjeta. Coloquela en el encoder.")
        if not decoded.checksum_valid or not decoded.uid_match:
            return CardResult(
                False, "Guest",
                error="Tarjeta corrupta o de otro sistema — no se puede cambiar de habitación",
            )
        if decoded.card_type != CARD_TYPE_GUEST:
            return CardResult(False, "Guest", error="La tarjeta no es Guest — use checkout + nuevo check-in")
        # Validar que la tarjeta corresponde a old_room (por building+floor+room_no_id)
        if (decoded.building != old_room.building or
                decoded.floor != old_room.floor or
                decoded.room_no_id != old_room.room_no_id):
            return CardResult(
                False, "Guest",
                error=f"La tarjeta no pertenece a Hab. {old_room.display_number} (actual: edif {decoded.building} piso {decoded.floor})",
            )

        # 2) Buscar CardLog vigente para recuperar guest_id y checkout
        now = datetime.now()
        active = (
            session.query(CardLog)
            .filter(
                CardLog.room_id == old_room.id,
                CardLog.card_type == "Guest",
                CardLog.success.is_(True),
                CardLog.expires_at > now,
            )
            .order_by(CardLog.issued_at.desc())
            .first()
        )
        if not active:
            return CardResult(
                False, "Guest",
                error=f"No hay Guest card vigente en DB para Hab. {old_room.display_number}",
            )

        valid_until = active.expires_at or (now + timedelta(days=1))
        guest = session.get(Guest, active.guest_id) if active.guest_id else None

        # 3) Reescribir la tarjeta con los datos de new_room
        try:
            card_data = build_guest_card(
                room=int(new_room.sequential_id),
                now=now,
                checkout=valid_until,
                sig=hotel.signature,
                building=new_room.building,
                floor=new_room.floor,
                room_no_id=new_room.room_no_id,
            )
        except Exception as exc:
            logger.exception("Error construyendo guest card en change_guest_room")
            return CardResult(False, "Guest", error=f"Error interno: {exc}")

        # expected_uid: only rewrite the card that was just validated as old_room's.
        result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key,
                            building=new_room.building, expected_uid=decoded.uid)
        uid_hex = result.uid.hex(":") if result.uid else None
        if not result.success:
            _log_emission(
                session, "Guest", CARD_TYPE_GUEST, uid_hex, False, operator,
                room=new_room, guest=guest, valid_from=now, expires_at=valid_until,
                error=f"change_room: {result.error}",
            )
            return CardResult(False, "Guest", uid_hex=uid_hex, error=result.error or "Error al reescribir tarjeta")

        # 4) Atomic transaction: expire old CardLog + room states + new log + audit.
        #    _log_emission commits, so it goes last — its commit closes the whole unit.
        try:
            invalidate_active_guest_cards(session, old_room.id, now=now)
            # States: old → "sucia" (pending cleaning); new_room is left as-is
            # (staff updates its state per their own flow).
            old_r = session.get(Room, old_room.id)
            if old_r is not None:
                old_r.state = "sucia"
            _log_emission(
                session, "Guest", CARD_TYPE_GUEST, uid_hex, True, operator,
                room=new_room, guest=guest, valid_from=now, expires_at=valid_until,
            )
        except Exception as exc:
            session.rollback()
            logger.exception("Error en transaccion change_guest_room")
            return CardResult(False, "Guest", uid_hex=uid_hex,
                              error=f"Tarjeta reescrita pero falló DB: {exc}")

        log_action(
            session, "room_change", operator,
            f"Hab. {old_room.display_number} → {new_room.display_number}"
            + (f", {guest.name}" if guest else ""),
        )

        return CardResult(
            True, "Guest", uid_hex=uid_hex, room_display=new_room.display_number,
            guest_id=guest.id if guest else None,
            valid_from=now, expires_at=valid_until,
            message=(
                f"Tarjeta reescrita: Hab. {old_room.display_number} → {new_room.display_number}. "
                f"Pasala por la cerradura de {new_room.display_number} para sincronizar."
            ),
        )


def create_master_card(
    *, encoder: EncoderDriver, hotel: HotelConfig, session: Session,
    valid_until: datetime, operator: str, operator_role: str | None,
) -> CardResult:
    if refused := _manager_only(operator_role, "Master"):
        return refused
    now = datetime.now()
    if valid_until <= now:
        return CardResult(False, "Master", error="La fecha de vencimiento debe ser futura")
    try:
        card_data = build_master_card(now=now, expire=valid_until, sig=hotel.signature)
    except Exception as exc:
        return CardResult(False, "Master", error=f"Error interno: {exc}")

    with emission_transaction(blocking=False) as acquired:
        if not acquired:
            return CardResult(False, "Master", error=BUSY_ERROR)

        result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key)
        uid_hex = result.uid.hex(":") if result.uid else None
        if not result.success:
            _log_emission(
                session, "Master", CARD_TYPE_MASTER, uid_hex, False, operator,
                valid_from=now, expires_at=valid_until, error=result.error,
            )
            return CardResult(False, "Master", uid_hex=uid_hex, error=result.error or "Error al escribir")

        try:
            _log_emission(
                session, "Master", CARD_TYPE_MASTER, uid_hex, True, operator,
                valid_from=now, expires_at=valid_until,
            )
            log_action(session, "card_master", operator, f"Hasta {valid_until:%Y-%m-%d %H:%M}")
        except Exception as exc:
            session.rollback()
            logger.exception("DB audit failed after successful master card write")
            return CardResult(
                False, "Master", uid_hex=uid_hex,
                error=f"Tarjeta grabada pero falló el registro en base de datos: {exc}",
            )
        return CardResult(True, "Master", uid_hex=uid_hex, valid_from=now, expires_at=valid_until,
                          message="Llave maestra grabada")


def create_auth_card(
    *, encoder: EncoderDriver, hotel: HotelConfig, session: Session, operator: str,
    operator_role: str | None,
) -> CardResult:
    if refused := _manager_only(operator_role, "Auth"):
        return refused
    now = datetime.now()
    try:
        card_data = build_auth_card(now=now, sig=hotel.signature)
    except Exception as exc:
        return CardResult(False, "Auth", error=f"Error interno: {exc}")
    expires_at = now + timedelta(days=AUTH_CARD_VALIDITY_DAYS)
    with emission_transaction(blocking=False) as acquired:
        if not acquired:
            return CardResult(False, "Auth", error=BUSY_ERROR)

        result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key)
        uid_hex = result.uid.hex(":") if result.uid else None
        if not result.success:
            _log_emission(
                session, "Auth", CARD_TYPE_AUTH, uid_hex, False, operator,
                valid_from=now, expires_at=expires_at, error=result.error,
            )
            return CardResult(False, "Auth", uid_hex=uid_hex, error=result.error or "Error al escribir")

        try:
            _log_emission(
                session, "Auth", CARD_TYPE_AUTH, uid_hex, True, operator,
                valid_from=now, expires_at=expires_at,
            )
            log_action(session, "card_auth", operator, "Tarjeta Auth grabada")
        except Exception as exc:
            session.rollback()
            logger.exception("DB audit failed after successful auth card write")
            return CardResult(
                False, "Auth", uid_hex=uid_hex,
                error=f"Tarjeta grabada pero falló el registro en base de datos: {exc}",
            )
        return CardResult(True, "Auth", uid_hex=uid_hex, message="Tarjeta Auth grabada")


def create_clock_card(
    *, encoder: EncoderDriver, hotel: HotelConfig, session: Session, operator: str,
    operator_role: str | None,
) -> CardResult:
    if refused := _manager_only(operator_role, "Clock"):
        return refused
    now = datetime.now()
    try:
        card_data = build_clock_card(now=now, sig=hotel.signature)
    except Exception as exc:
        return CardResult(False, "Clock", error=f"Error interno: {exc}")
    with emission_transaction(blocking=False) as acquired:
        if not acquired:
            return CardResult(False, "Clock", error=BUSY_ERROR)

        result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key)
        uid_hex = result.uid.hex(":") if result.uid else None
        if not result.success:
            _log_emission(session, "Clock", CARD_TYPE_CLOCK, uid_hex, False, operator, error=result.error)
            return CardResult(False, "Clock", uid_hex=uid_hex, error=result.error or "Error al escribir")

        try:
            _log_emission(session, "Clock", CARD_TYPE_CLOCK, uid_hex, True, operator)
            log_action(session, "card_clock", operator, "Tarjeta Clock grabada")
        except Exception as exc:
            session.rollback()
            logger.exception("DB audit failed after successful clock card write")
            return CardResult(
                False, "Clock", uid_hex=uid_hex,
                error=f"Tarjeta grabada pero falló el registro en base de datos: {exc}",
            )
        return CardResult(True, "Clock", uid_hex=uid_hex, message="Tarjeta Clock grabada")


def create_setting_card(
    *, encoder: EncoderDriver, hotel: HotelConfig, session: Session, room: Room, operator: str,
    operator_role: str | None,
) -> CardResult:
    if refused := _manager_only(operator_role, "Setting"):
        return refused
    now = datetime.now()
    try:
        card_data = build_setting_card(
            room=int(room.sequential_id),
            building=room.building,
            floor=room.floor,
            room_no_id=room.room_no_id,
            now=now,
            sig=hotel.signature,
        )
    except Exception as exc:
        return CardResult(False, "Setting", error=f"Error interno: {exc}")
    with emission_transaction(blocking=False) as acquired:
        if not acquired:
            return CardResult(False, "Setting", error=BUSY_ERROR)

        result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key,
                            building=room.building)
        uid_hex = result.uid.hex(":") if result.uid else None
        if not result.success:
            _log_emission(
                session, "Setting", CARD_TYPE_SETTING, uid_hex, False, operator,
                room=room, error=result.error,
            )
            return CardResult(False, "Setting", uid_hex=uid_hex, error=result.error or "Error al escribir")

        try:
            _log_emission(
                session, "Setting", CARD_TYPE_SETTING, uid_hex, True, operator, room=room,
            )
            log_action(session, "card_setting", operator, f"Habitacion {room.display_number}")
        except Exception as exc:
            session.rollback()
            logger.exception("DB audit failed after successful setting card write")
            return CardResult(
                False, "Setting", uid_hex=uid_hex,
                error=f"Tarjeta grabada pero falló el registro en base de datos: {exc}",
            )
        return CardResult(
            True, "Setting", uid_hex=uid_hex, room_display=room.display_number,
            message=f"Setting card para hab. {room.display_number}",
        )


def create_laundry_card(
    *,
    encoder: EncoderDriver,
    hotel: HotelConfig,
    session: Session,
    hours: int = 8,
    operator: str,
    operator_role: str | None = None,
    staff_name: str | None = None,
    assigned_rooms: list[str] | None = None,
) -> CardResult:
    """Tarjeta de limpieza valida por N horas.

    It is a short-lived Master: it opens EVERY room until it expires.
    Decision 2026-10-14 (Thomas): front desk may issue it for at most
    FRONTDESK_LAUNDRY_MAX_HOURS; only a manager may go longer. An unknown
    role gets the front-desk limit (fail safe).
    Las habitaciones asignadas al staff se registran en el audit log para
    trazabilidad aunque físicamente la tarjeta abra todas.
    """
    if hours <= 0:
        return CardResult(False, "Limpieza", error="La duración debe ser mayor a 0 horas")
    if hours > laundry_max_hours(operator_role):
        return CardResult(
            False, "Limpieza",
            error=(
                f"Recepción puede emitir Limpieza por máximo {FRONTDESK_LAUNDRY_MAX_HOURS} horas. "
                "Para más tiempo, pídasela a un gerente"
            ),
        )

    now = datetime.now()
    expire = now + timedelta(hours=hours)
    try:
        card_data = build_laundry_card(
            now=now,
            expire=expire,
            sig=hotel.signature,
        )
    except Exception as exc:
        return CardResult(False, "Limpieza", error=f"Error interno: {exc}")

    if assigned_rooms:
        rooms_str = ", ".join(assigned_rooms[:10]) + ("..." if len(assigned_rooms) > 10 else "")
        assigned_desc = f" [Hab. asignadas: {rooms_str}]"
    else:
        assigned_desc = ""

    with emission_transaction(blocking=False) as acquired:
        if not acquired:
            return CardResult(False, "Limpieza", error=BUSY_ERROR)

        result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key)
        uid_hex = result.uid.hex(":") if result.uid else None

        # error_message is only set on real failures; assigned_desc is audit
        # metadata and goes in the action_log detail, not in error_message.
        if not result.success:
            _log_emission(
                session, "Laundry", CARD_TYPE_LAUNDRY, uid_hex, False, operator,
                valid_from=now, expires_at=expire, error=result.error,
            )
            return CardResult(False, "Limpieza", uid_hex=uid_hex, error=result.error or "Error al escribir")

        try:
            _log_emission(
                session, "Laundry", CARD_TYPE_LAUNDRY, uid_hex, True, operator,
                valid_from=now, expires_at=expire,
            )
            staff_info = f" para {staff_name}" if staff_name else ""
            detail = f"Tarjeta limpieza{staff_info} — {hours}h, hasta {expire.strftime('%H:%M')}{assigned_desc}"
            log_action(session, "card_laundry", operator, detail)
        except Exception as exc:
            session.rollback()
            logger.exception("DB audit failed after successful laundry card write")
            return CardResult(
                False, "Limpieza", uid_hex=uid_hex,
                error=f"Tarjeta grabada pero falló el registro en base de datos: {exc}",
            )

        msg_extra = ""
        if assigned_rooms:
            msg_extra = f" · {len(assigned_rooms)} hab. asignadas a {staff_name or 'limpiador'}"
        return CardResult(
            True, "Limpieza", uid_hex=uid_hex,
            message=f"Tarjeta limpieza emitida — válida {hours}h (hasta {expire.strftime('%d/%m %H:%M')}){msg_extra}",
        )


def _decoded_room_display(decoded: DecodedCard) -> str | None:
    """Compact room descriptor for audit traceability (fits CardLog.room_display)."""
    if decoded.card_type in (CARD_TYPE_GUEST, CARD_TYPE_SETTING):
        return f"B{decoded.building}/P{decoded.floor}/H{decoded.room_no_id}"
    return None


def blank_existing_card(
    *, encoder: EncoderDriver, hotel: HotelConfig, session: Session, operator: str,
    expect_card_type: int | None = None,
    expect_uid: bytes | None = None,
    expect_room: tuple[int, int, int] | None = None,
    force: bool = False,
) -> CardResult:
    """Blank the card currently on the encoder.

    Reads the card first so the wiped content stays traceable in the audit
    log (a wiped Master is no longer anonymous). When ``expect_card_type`` is
    given and the card on the encoder does not match (or cannot be read), the
    blank is refused unless ``force=True`` — this protects global
    Master/Laundry cards from accidental wipes. ``expect_uid`` (the card the
    operator confirmed on screen) is enforced even with ``force``.
    """
    # Read-first + blank is ONE encoder operation — fail-fast so the pre-blank
    # read and the blank write can never be split by another in-flight write.
    with emission_transaction(blocking=False) as acquired:
        if not acquired:
            return CardResult(False, "Blank", error=BUSY_ERROR)

        decoded = None
        try:
            decoded = read_card(encoder, hotel.sector, hotel.signature, des_key=hotel.des_key)
        except Exception:
            logger.exception("Pre-blank read failed; proceeding without decoded card info")

        if expect_uid is not None and (decoded is None or decoded.uid != expect_uid):
            return CardResult(
                False, "Blank",
                error="La tarjeta en el encoder no es la que confirmó. No se borró nada",
            )

        if expect_card_type is not None and not force:
            if decoded is None:
                return CardResult(
                    False, "Blank",
                    error="No se pudo leer la tarjeta para verificar su tipo. No se borró",
                )
            if decoded.card_type != expect_card_type:
                tipo = _card_type_label(decoded.card_type)
                return CardResult(
                    False, "Blank",
                    error=f"La tarjeta en el encoder es {tipo}, no la esperada. No se borró",
                )
            # expect_room = (building, floor, room_no_id): checkout must only
            # wipe the guest card of the room being checked out.
            card_room = (decoded.building, decoded.floor, decoded.room_no_id)
            if expect_room is not None and card_room != expect_room:
                return CardResult(
                    False, "Blank",
                    error=(
                        f"La tarjeta es de otra habitación (edif {card_room[0]} piso {card_room[1]} "
                        f"hab {card_room[2]}). No se borró"
                    ),
                )

        # Traceability: record WHAT is being wiped in the Blank CardLog row.
        if decoded is not None:
            wiped_type_byte = decoded.card_type
            wiped_room_display = _decoded_room_display(decoded)
            is_blank = bool(getattr(decoded, "is_blank", False))
            wiped_label = "en blanco" if is_blank else _card_type_label(decoded.card_type)
        else:
            wiped_type_byte = BLANK_TYPE_BYTE
            wiped_room_display = None
            wiped_label = "ilegible"

        # Bind the wipe to the card that was inspected above: a swap between
        # the read and the blank must not erase a different card.
        result = blank_card(
            encoder, hotel.sector,
            expected_uid=decoded.uid if decoded is not None else None,
        )
        uid_hex = result.uid.hex(":") if result.uid else None
        if not result.success:
            _log_emission(
                session, "Blank", wiped_type_byte, uid_hex, False, operator,
                room_display=wiped_room_display, error=result.error,
            )
            return CardResult(False, "Blank", uid_hex=uid_hex, error=result.error or "Error al borrar")

        try:
            _log_emission(
                session, "Blank", wiped_type_byte, uid_hex, True, operator,
                room_display=wiped_room_display,
            )
            detail = f"UID {uid_hex} — era {wiped_label}"
            if wiped_room_display:
                detail += f" ({wiped_room_display})"
            log_action(session, "card_blank", operator, detail)
        except Exception as exc:
            session.rollback()
            logger.exception("DB audit failed after successful card blank")
            return CardResult(
                False, "Blank", uid_hex=uid_hex,
                error=f"Tarjeta borrada pero falló el registro en base de datos: {exc}",
            )
        return CardResult(True, "Blank", uid_hex=uid_hex, message="Tarjeta borrada")


def _global_card_status(decoded: DecodedCard) -> str:
    """Diagnose a Master/Laundry tail: GLOBAL (correct) vs room-bound (old bug)."""
    tail = (decoded.building, decoded.floor, decoded.room_no_id)
    if tail == (0, 0, 0):
        return "GLOBAL — abre todas las puertas (correcta)"
    return (
        f"LIGADA a edif {decoded.building} piso {decoded.floor} "
        f"hab {decoded.room_no_id} — tarjeta vieja, RE-EMITIR"
    )


def read_existing_card(
    *, encoder: EncoderDriver, hotel: HotelConfig,
) -> tuple[bool, str, dict]:
    """Read and diagnose the card on the encoder. Returns (ok, message, info)."""
    # Read may briefly wait behind an in-flight write (blocking=True) rather than
    # fail-fast: a read must never interleave its commands with a write either.
    with emission_transaction(blocking=True):
        decoded = read_card(encoder, hotel.sector, hotel.signature, des_key=hotel.des_key)
    if not decoded:
        if not getattr(encoder, "is_open", True):
            return False, ENCODER_DISCONNECTED_ERROR, {}
        return False, "No se pudo leer la tarjeta — coloquela en el encoder", {}

    now = datetime.now()
    # A Clock card has no expiry: bytes 7-11 are weekday/flags, not a date.
    has_expiry = decoded.card_type != CARD_TYPE_CLOCK
    valid_until = decoded.date2 if has_expiry else None
    expired = bool(valid_until is not None and valid_until < now)
    # is_blank is a new reader flag; getattr keeps this working until it lands.
    is_blank = bool(getattr(decoded, "is_blank", False))
    foreign_signature = decoded.signature != hotel.signature.signature

    info = {
        "card_type_byte": decoded.card_type,
        "card_type_name": decoded.card_type_name,
        "tipo": _card_type_label(decoded.card_type),
        "uid": decoded.uid.hex(":"),
        "room": decoded.room,
        "building": decoded.building,
        "floor": decoded.floor,
        "room_no_id": decoded.room_no_id,
        "valid_from": decoded.date1,
        "valid_until": valid_until,
        "checksum_valid": decoded.checksum_valid,
        "uid_match": decoded.uid_match,
        "signature": decoded.signature.hex(" ").upper(),
        "expired": expired,
    }

    if not is_blank and decoded.card_type in (CARD_TYPE_MASTER, CARD_TYPE_LAUNDRY):
        info["estado"] = _global_card_status(decoded)
    elif not is_blank and decoded.card_type in LEGACY_GLOBAL_TYPES:
        info["estado"] = LEGACY_GLOBAL_STATUS

    if is_blank:
        message = "Tarjeta EN BLANCO"
    elif not decoded.checksum_valid:
        message = "Tarjeta CORRUPTA (checksum inválido)"
    elif not decoded.uid_match:
        message = "Tarjeta de otro sistema (UID/DES no coincide)"
    elif foreign_signature:
        message = "Tarjeta de otro hotel o sistema (firma distinta): no abre aquí"
    elif decoded.card_type in LEGACY_GLOBAL_TYPES:
        message = "Tarjeta antigua — RE-EMITIR"
    elif expired:
        message = "Tarjeta leida — VENCIDA"
    else:
        message = "Tarjeta leida"
    return True, message, info


def read_s70_data_card(
    *,
    encoder: EncoderDriver,
    hotel: HotelConfig,
    session: Session,
    operator: str,
) -> tuple[bool, str, dict]:
    """Lee una tarjeta S70 Data Card, extrae eventos de apertura y los
    persiste en s70_events. Cruza cada evento contra CardLog por UID para
    identificar que huesped/operador fue el que abrio.

    Retorna (ok, message, info). info incluye 'events' enriquecidos con
    guest_name / operator cuando hay match en CardLog.
    """
    from setra_cards.storage.models import S70EventLog

    # S70 read is a multi-command read — hold the lock so it never interleaves
    # with a write; blocking=True (a read may briefly wait).
    with emission_transaction(blocking=True):
        data = read_s70_card(encoder, hotel.signature)
    if data is None:
        return False, "No se detecto tarjeta en el encoder", {}
    if not data.is_s70:
        return False, f"La tarjeta no es S70 Data Card (marca: '{data.marker}')", {}

    # Persistir eventos nuevos y enriquecer con match contra CardLog
    enriched_events: list[dict] = []
    new_count = 0
    for ev in data.events:
        # Match por UID contra CardLog
        match = None
        if ev.card_uid_hex:
            match = (
                session.query(CardLog)
                .filter(CardLog.uid_hex == ev.card_uid_hex)
                .order_by(CardLog.issued_at.desc())
                .first()
            )

        # Deduplicar: mismo s70_uid + card_uid + event_time = mismo evento
        existing = None
        if ev.timestamp:
            existing = (
                session.query(S70EventLog)
                .filter(
                    S70EventLog.s70_uid_hex == data.uid_hex,
                    S70EventLog.card_uid_hex == ev.card_uid_hex,
                    S70EventLog.event_time == ev.timestamp,
                )
                .first()
            )

        if not existing:
            session.add(S70EventLog(
                s70_uid_hex=data.uid_hex,
                card_number=data.card_number,
                event_type=ev.event_type,
                event_type_name=ev.event_type_name,
                card_uid_hex=ev.card_uid_hex,
                event_time=ev.timestamp,
                extra=ev.extra,
                building=data.building,
                floor=data.floor,
            ))
            new_count += 1

        enriched_events.append({
            "event_type": ev.event_type,
            "event_type_name": ev.event_type_name,
            "card_uid": ev.card_uid_hex,
            "timestamp": ev.timestamp.strftime("%d/%m/%Y %H:%M") if ev.timestamp else "—",
            "extra": ev.extra,
            "guest_name": (
                session.get(Guest, match.guest_id).name
                if match and match.guest_id else None
            ) if match else None,
            "room_display": match.room_display if match else None,
            "operator": match.operator if match else None,
            "card_type_match": match.card_type if match else None,
        })

    session.commit()
    log_action(
        session, "s70_read", operator,
        f"S70 {data.uid_hex}: {len(data.events)} eventos ({new_count} nuevos)",
    )

    info = {
        "s70_uid": data.uid_hex,
        "marker": data.marker,
        "card_number": data.card_number,
        "collection_date": data.collection_date_str,
        "building": data.building,
        "floor": data.floor,
        "zone": data.zone,
        "sectors_read": data.sectors_read,
        "events": enriched_events,
        "new_count": new_count,
    }
    return True, f"{len(data.events)} eventos leidos ({new_count} nuevos)", info


def active_cards_for_room(session: Session, room: Room, now: datetime | None = None) -> list[CardLog]:
    now = now or datetime.now()
    return (
        session.query(CardLog)
        .filter(
            CardLog.room_id == room.id,
            CardLog.card_type == "Guest",
            CardLog.success.is_(True),
            CardLog.expires_at > now,
        )
        .order_by(CardLog.issued_at.desc())
        .all()
    )


def recent_emissions(session: Session, limit: int = 100) -> list[CardLog]:
    return (
        session.query(CardLog)
        .order_by(CardLog.issued_at.desc())
        .limit(limit)
        .all()
    )
