"""Orquesta la emision de tarjetas: builder -> writer -> audit en DB.

Escrito desde cero — integrado con Guest, single-hotel.
Delega la crypto/serial a `cards.builder` y `cards.writer` (validados en prod).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from setra_cards.cards.builder import (
    build_auth_card,
    build_clock_card,
    build_guest_card,
    build_master_card,
    build_setting_card,
)
from setra_cards.cards.reader import read_card
from setra_cards.cards.writer import blank_card, write_card
from setra_cards.core.app_state import HotelConfig
from setra_cards.encoder.driver import EncoderDriver
from setra_cards.services.action_log import log as log_action
from setra_cards.storage.models import CardLog, Guest, Room

logger = logging.getLogger(__name__)

CARD_TYPE_GUEST = 0x00
CARD_TYPE_MASTER = 0x10
CARD_TYPE_AUTH = 0xC0
CARD_TYPE_CLOCK = 0x20
CARD_TYPE_SETTING = 0x40


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
        )
    except Exception as exc:
        logger.exception("Error construyendo guest card")
        return CardResult(False, "Guest", error=f"Error interno: {exc}")

    result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key)
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


def create_master_card(
    *, encoder: EncoderDriver, hotel: HotelConfig, session: Session,
    valid_until: datetime, operator: str,
) -> CardResult:
    now = datetime.now()
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
            now=now,
            sig=hotel.signature,
        )
    except Exception as exc:
        return CardResult(False, "Setting", error=f"Error interno: {exc}")
    result = write_card(encoder, hotel.sector, hotel.signature, card_data, hotel.des_key)
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
    decoded = read_card(encoder, hotel.sector, hotel.signature)
    if not decoded:
        return False, "No se pudo leer la tarjeta — coloquela en el encoder", {}
    info = {
        "card_type_byte": decoded.card_type,
        "uid": decoded.uid.hex(":"),
        "room": getattr(decoded, "room", None),
        "building": getattr(decoded, "building", None),
        "floor": getattr(decoded, "floor", None),
        "valid_from": getattr(decoded, "valid_from", None),
        "valid_until": getattr(decoded, "valid_until", None),
    }
    return True, "Tarjeta leida", info


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
