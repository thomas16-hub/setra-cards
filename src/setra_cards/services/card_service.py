"""Orquesta la emision de tarjetas: builder -> writer -> audit en DB.

Escrito desde cero — integrado con Guest, single-hotel.
Delega la crypto/serial a `cards.builder` y `cards.writer` (validados en prod).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

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
from setra_cards.cards.reader import read_card
from setra_cards.cards.s70_reader import read_s70_card, S70CardData
from setra_cards.cards.writer import blank_card, write_card
from setra_cards.core.app_state import HotelConfig
from setra_cards.encoder.driver import EncoderDriver
from setra_cards.encoder.protocol import (
    CARD_TYPE_AUTH,
    CARD_TYPE_CLOCK,
    CARD_TYPE_GUEST,
    CARD_TYPE_LAUNDRY,
    CARD_TYPE_MASTER,
    CARD_TYPE_SETTING,
)
from setra_cards.services.action_log import log as log_action
from setra_cards.storage.models import CardLog, Guest, Room

logger = logging.getLogger(__name__)


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
) -> CardLog:
    entry = CardLog(
        card_type=card_type,
        card_type_byte=type_byte,
        guest_id=guest.id if guest else None,
        room_id=room.id if room else None,
        room_display=room.display_number if room else None,
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


def _invalidate_previous_guest_cards(session: Session, room: Room) -> int:
    """Marca tarjetas anteriores como expiradas para la misma habitacion."""
    now = datetime.now()
    stmt = (
        sql_update(CardLog)
        .where(
            CardLog.room_id == room.id,
            CardLog.card_type == "Guest",
            CardLog.success.is_(True),
            CardLog.expires_at > now,
        )
        .values(expires_at=now)
    )
    result = session.execute(stmt)
    session.commit()
    return result.rowcount


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

    # 1) Leer tarjeta actual para validar que es Guest de old_room
    decoded = read_card(encoder, hotel.sector, hotel.signature, des_key=hotel.des_key)
    if not decoded:
        return CardResult(False, "Guest", error="No se pudo leer la tarjeta. Coloquela en el encoder.")
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

    result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key,
                        building=new_room.building)
    uid_hex = result.uid.hex(":") if result.uid else None
    if not result.success:
        _log_emission(
            session, "Guest", CARD_TYPE_GUEST, uid_hex, False, operator,
            room=new_room, guest=guest, valid_from=now, expires_at=valid_until,
            error=f"change_room: {result.error}",
        )
        return CardResult(False, "Guest", uid_hex=uid_hex, error=result.error or "Error al reescribir tarjeta")

    # 4) Transaccion atomica: expirar CardLog viejo + cambiar estados + log nuevo + audit
    from sqlalchemy import update as sql_update
    try:
        session.execute(
            sql_update(CardLog)
            .where(
                CardLog.room_id == old_room.id,
                CardLog.card_type == "Guest",
                CardLog.success.is_(True),
                CardLog.expires_at > now,
            )
            .values(expires_at=now)
        )
        # Estados: old → sucia (pendiente limpieza), new → ocupada... usamos sucia/limpia
        old_r = session.get(Room, old_room.id)
        new_r = session.get(Room, new_room.id)
        if old_r is not None:
            old_r.state = "sucia"
        # new_room queda como esta (el staff cambia el estado segun su flujo)
        # Crear CardLog nuevo
        session.add(CardLog(
            card_type="Guest",
            card_type_byte=CARD_TYPE_GUEST,
            guest_id=guest.id if guest else None,
            room_id=new_room.id,
            room_display=new_room.display_number,
            uid_hex=uid_hex,
            valid_from=now,
            expires_at=valid_until,
            operator=operator,
            success=True,
        ))
        session.commit()
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
    valid_until: datetime, operator: str,
) -> CardResult:
    now = datetime.now()
    if valid_until <= now:
        return CardResult(False, "Master", error="La fecha de vencimiento debe ser futura")
    try:
        card_data = build_master_card(now=now, expire=valid_until, sig=hotel.signature)
    except Exception as exc:
        return CardResult(False, "Master", error=f"Error interno: {exc}")

    result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key)
    uid_hex = result.uid.hex(":") if result.uid else None
    _log_emission(
        session, "Master", CARD_TYPE_MASTER, uid_hex, result.success, operator,
        valid_from=now, expires_at=valid_until, error=result.error,
    )
    if result.success:
        log_action(session, "card_master", operator, f"Hasta {valid_until:%Y-%m-%d %H:%M}")
        return CardResult(True, "Master", uid_hex=uid_hex, valid_from=now, expires_at=valid_until,
                          message="Llave maestra grabada")
    return CardResult(False, "Master", uid_hex=uid_hex, error=result.error or "Error al escribir")


def create_auth_card(
    *, encoder: EncoderDriver, hotel: HotelConfig, session: Session, operator: str,
) -> CardResult:
    now = datetime.now()
    try:
        card_data = build_auth_card(now=now, sig=hotel.signature)
    except Exception as exc:
        return CardResult(False, "Auth", error=f"Error interno: {exc}")
    result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key)
    uid_hex = result.uid.hex(":") if result.uid else None
    _log_emission(session, "Auth", CARD_TYPE_AUTH, uid_hex, result.success, operator, error=result.error)
    if result.success:
        log_action(session, "card_auth", operator, "Tarjeta Auth grabada")
        return CardResult(True, "Auth", uid_hex=uid_hex, message="Tarjeta Auth grabada")
    return CardResult(False, "Auth", uid_hex=uid_hex, error=result.error or "Error al escribir")


def create_clock_card(
    *, encoder: EncoderDriver, hotel: HotelConfig, session: Session, operator: str,
) -> CardResult:
    now = datetime.now()
    try:
        card_data = build_clock_card(now=now, sig=hotel.signature)
    except Exception as exc:
        return CardResult(False, "Clock", error=f"Error interno: {exc}")
    result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key)
    uid_hex = result.uid.hex(":") if result.uid else None
    _log_emission(session, "Clock", CARD_TYPE_CLOCK, uid_hex, result.success, operator, error=result.error)
    if result.success:
        log_action(session, "card_clock", operator, "Tarjeta Clock grabada")
        return CardResult(True, "Clock", uid_hex=uid_hex, message="Tarjeta Clock grabada")
    return CardResult(False, "Clock", uid_hex=uid_hex, error=result.error or "Error al escribir")


def create_setting_card(
    *, encoder: EncoderDriver, hotel: HotelConfig, session: Session, room: Room, operator: str,
) -> CardResult:
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
    result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key,
                        building=room.building)
    uid_hex = result.uid.hex(":") if result.uid else None
    _log_emission(
        session, "Setting", CARD_TYPE_SETTING, uid_hex, result.success, operator,
        room=room, error=result.error,
    )
    if result.success:
        log_action(session, "card_setting", operator, f"Habitacion {room.display_number}")
        return CardResult(
            True, "Setting", uid_hex=uid_hex, room_display=room.display_number,
            message=f"Setting card para hab. {room.display_number}",
        )
    return CardResult(False, "Setting", uid_hex=uid_hex, error=result.error or "Error al escribir")


def create_laundry_card(
    *,
    encoder: EncoderDriver,
    hotel: HotelConfig,
    session: Session,
    hours: int = 8,
    operator: str,
    staff_name: str | None = None,
    assigned_rooms: list[str] | None = None,
) -> CardResult:
    """Tarjeta de limpieza valida por N horas.

    El protocolo Locstar graba room=0x00 (acceso global a todo el hotel).
    No es posible restringir a habitaciones específicas a nivel de firmware.
    Las habitaciones asignadas al staff se registran en el audit log para
    trazabilidad aunque físicamente la tarjeta abra todas.
    """
    from datetime import timedelta
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

    result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key)
    uid_hex = result.uid.hex(":") if result.uid else None

    if assigned_rooms:
        rooms_str = ", ".join(assigned_rooms[:10]) + ("..." if len(assigned_rooms) > 10 else "")
        assigned_desc = f" [Hab. asignadas: {rooms_str}]"
    else:
        assigned_desc = ""

    # error_message solo se llena cuando hay fallo real; el assigned_desc
    # es metadata de auditoría y va en la detail del action_log, no en error_message
    error_msg = result.error if not result.success else None
    _log_emission(
        session, "Laundry", CARD_TYPE_LAUNDRY, uid_hex, result.success, operator,
        expires_at=expire,
        error=error_msg,
    )
    if result.success:
        staff_info = f" para {staff_name}" if staff_name else ""
        detail = f"Tarjeta limpieza{staff_info} — {hours}h, hasta {expire.strftime('%H:%M')}{assigned_desc}"
        log_action(session, "card_laundry", operator, detail)
        msg_extra = ""
        if assigned_rooms:
            msg_extra = f" · {len(assigned_rooms)} hab. asignadas a {staff_name or 'limpiador'}"
        return CardResult(
            True, "Limpieza", uid_hex=uid_hex,
            message=f"Tarjeta limpieza emitida — válida {hours}h (hasta {expire.strftime('%d/%m %H:%M')}){msg_extra}",
        )
    return CardResult(False, "Limpieza", uid_hex=uid_hex, error=result.error or "Error al escribir")


def blank_existing_card(
    *, encoder: EncoderDriver, hotel: HotelConfig, session: Session, operator: str,
) -> CardResult:
    result = blank_card(encoder, hotel.sector)
    uid_hex = result.uid.hex(":") if result.uid else None
    _log_emission(session, "Blank", 0xFF, uid_hex, result.success, operator, error=result.error)
    if result.success:
        log_action(session, "card_blank", operator, f"UID {uid_hex}")
        return CardResult(True, "Blank", uid_hex=uid_hex, message="Tarjeta borrada")
    return CardResult(False, "Blank", uid_hex=uid_hex, error=result.error or "Error al borrar")


def read_existing_card(
    *, encoder: EncoderDriver, hotel: HotelConfig,
) -> tuple[bool, str, dict]:
    decoded = read_card(encoder, hotel.sector, hotel.signature, des_key=hotel.des_key)
    if not decoded:
        return False, "No se pudo leer la tarjeta — coloquela en el encoder", {}
    info = {
        "card_type_byte": decoded.card_type,
        "uid": decoded.uid.hex(":"),
        "room": getattr(decoded, "room", None),
        "building": getattr(decoded, "building", None),
        "floor": getattr(decoded, "floor", None),
        "room_no_id": getattr(decoded, "room_no_id", None),
        "valid_from": getattr(decoded, "valid_from", None),
        "valid_until": getattr(decoded, "valid_until", None),
    }
    return True, "Tarjeta leida", info


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
