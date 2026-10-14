"""Tests for card_service: read diagnosis, blank protection, DB-failure honesty.

The encoder hardware layer (read_card / write_card / blank_card) is
monkeypatched at the card_service module level, so no encoder is needed.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import setra_cards.services.card_service as cs
from setra_cards.cards.crypto import DEFAULT_SIGNATURE
from setra_cards.cards.writer import WriteResult
from setra_cards.core.app_state import HotelConfig
from setra_cards.encoder.protocol import (
    CARD_TYPE_GUEST,
    CARD_TYPE_LAUNDRY,
    CARD_TYPE_MASTER,
    CARD_TYPE_NAMES,
)
from setra_cards.storage.models import Base, CardLog, Guest, Room

UID = b"\x04\x01\x02\x03"


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture()
def hotel():
    return HotelConfig(
        name="Hotel Test", short_name="Test", city="Medellin",
        signature=DEFAULT_SIGNATURE, des_key=b"god\x00\x00\x00\x00\x00",
    )


@pytest.fixture()
def encoder():
    # Hardware calls are monkeypatched; a bare object is enough.
    return object()


def make_decoded(
    card_type: int = CARD_TYPE_GUEST,
    room: int = 5,
    date1: datetime | None = None,
    date2: datetime | None = None,
    building: int = 1,
    floor: int = 2,
    room_no_id: int = 3,
    checksum_valid: bool = True,
    uid_match: bool = True,
    is_blank: bool = False,
):
    """Duck-typed DecodedCard stand-in (includes the new is_blank flag)."""
    return SimpleNamespace(
        uid=UID,
        card_type=card_type,
        card_type_name=CARD_TYPE_NAMES.get(card_type, "?"),
        room=room,
        date1=date1,
        date2=date2,
        building=building,
        floor=floor,
        room_no_id=room_no_id,
        signature=b"\x95\xb8",
        checksum_valid=checksum_valid,
        uid_match=uid_match,
        raw_blocks={},
        is_blank=is_blank,
    )


def add_room(session, seq: int, display: str, room_no_id: int) -> Room:
    room = Room(sequential_id=seq, display_number=display,
                building=1, floor=1, room_no_id=room_no_id)
    session.add(room)
    session.commit()
    return room


# --- Fix 1: read_existing_card real dates + integrity fields ---

def test_read_existing_card_exposes_real_dates_and_flags(monkeypatch, encoder, hotel):
    # Relative dates: a fixed checkout date turns this test red once it passes.
    d1 = (datetime.now() - timedelta(days=1)).replace(second=0, microsecond=0)
    d2 = (datetime.now() + timedelta(days=5)).replace(second=0, microsecond=0)
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded(date1=d1, date2=d2))

    ok, msg, info = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert ok is True
    assert msg == "Tarjeta leida"
    assert info["valid_from"] == d1
    assert info["valid_until"] == d2
    assert info["card_type_name"] == "Guest"
    assert info["checksum_valid"] is True
    assert info["uid_match"] is True
    assert info["signature"] == "95 B8"
    assert info["expired"] is False


def test_read_existing_card_marks_expired(monkeypatch, encoder, hotel):
    d2 = datetime.now() - timedelta(days=1)
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded(date2=d2))

    ok, msg, info = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert ok is True
    assert info["expired"] is True
    assert "VENCIDA" in msg


def test_read_existing_card_corrupt_checksum(monkeypatch, encoder, hotel):
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded(checksum_valid=False))

    ok, msg, info = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert ok is True
    assert msg == "Tarjeta CORRUPTA (checksum inválido)"
    assert info["checksum_valid"] is False


def test_read_existing_card_foreign_system(monkeypatch, encoder, hotel):
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded(uid_match=False))

    ok, msg, _ = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert ok is True
    assert msg == "Tarjeta de otro sistema (UID/DES no coincide)"


def test_read_existing_card_blank(monkeypatch, encoder, hotel):
    monkeypatch.setattr(
        cs, "read_card",
        lambda *a, **k: make_decoded(is_blank=True, checksum_valid=True, uid_match=False),
    )

    ok, msg, _ = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert ok is True
    assert msg == "Tarjeta EN BLANCO"


# --- Fix 2: GLOBAL vs room-bound master/laundry diagnosis ---

def test_read_master_global_status(monkeypatch, encoder, hotel):
    monkeypatch.setattr(
        cs, "read_card",
        lambda *a, **k: make_decoded(
            card_type=CARD_TYPE_MASTER, building=0, floor=0, room_no_id=0,
        ),
    )

    _, _, info = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert info["estado"] == "GLOBAL — abre todas las puertas (correcta)"


@pytest.mark.parametrize("card_type", [CARD_TYPE_MASTER, CARD_TYPE_LAUNDRY])
def test_read_master_laundry_room_bound_status(monkeypatch, encoder, hotel, card_type):
    monkeypatch.setattr(
        cs, "read_card",
        lambda *a, **k: make_decoded(
            card_type=card_type, building=1, floor=2, room_no_id=3,
        ),
    )

    _, _, info = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert info["estado"] == "LIGADA a edif 1 piso 2 hab 3 — tarjeta vieja, RE-EMITIR"


def test_read_guest_card_has_no_estado(monkeypatch, encoder, hotel):
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded())

    _, _, info = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert "estado" not in info


# --- Fix 3: blank_existing_card read-first protection ---

def test_blank_refuses_type_mismatch(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded(card_type=CARD_TYPE_MASTER))
    blanked = []
    monkeypatch.setattr(cs, "blank_card", lambda *a, **k: blanked.append(1) or WriteResult(True, UID))

    result = cs.blank_existing_card(
        encoder=encoder, hotel=hotel, session=session, operator="t",
        expect_card_type=CARD_TYPE_GUEST,
    )

    assert result.ok is False
    assert "Maestra" in result.error
    assert "no la esperada" in result.error
    # No "use Forzar" hint: the UI has no force button to offer.
    assert "No se borró" in result.error
    assert blanked == [], "blank_card must not run on refused mismatch"


def test_blank_force_overrides_mismatch_and_logs_wiped_type(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded(card_type=CARD_TYPE_MASTER))
    monkeypatch.setattr(cs, "blank_card", lambda *a, **k: WriteResult(True, UID))

    result = cs.blank_existing_card(
        encoder=encoder, hotel=hotel, session=session, operator="t",
        expect_card_type=CARD_TYPE_GUEST, force=True,
    )

    assert result.ok is True
    row = session.query(CardLog).order_by(CardLog.id.desc()).first()
    assert row.card_type == "Blank"
    assert row.card_type_byte == CARD_TYPE_MASTER, "wiped master must stay traceable"


def test_blank_unreadable_card_with_expect_refuses(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: None)
    monkeypatch.setattr(cs, "blank_card", lambda *a, **k: WriteResult(True, UID))

    result = cs.blank_existing_card(
        encoder=encoder, hotel=hotel, session=session, operator="t",
        expect_card_type=CARD_TYPE_GUEST,
    )

    assert result.ok is False
    assert "No se borró" in result.error


# --- 2026-10-14 scan fixes ---

def test_blank_refuses_when_card_differs_from_confirmed_uid(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded())
    blanked = []
    monkeypatch.setattr(cs, "blank_card", lambda *a, **k: blanked.append(1) or WriteResult(True, UID))

    result = cs.blank_existing_card(
        encoder=encoder, hotel=hotel, session=session, operator="t",
        expect_uid=b"\x09\x09\x09\x09", force=True,
    )

    assert result.ok is False
    assert blanked == []


def test_checkout_blank_refuses_another_rooms_guest_card(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded(building=1, floor=2, room_no_id=3))
    blanked = []
    monkeypatch.setattr(cs, "blank_card", lambda *a, **k: blanked.append(1) or WriteResult(True, UID))

    result = cs.blank_existing_card(
        encoder=encoder, hotel=hotel, session=session, operator="t",
        expect_card_type=CARD_TYPE_GUEST, expect_room=(1, 2, 4),
    )

    assert result.ok is False
    assert "otra habitación" in result.error
    assert blanked == []


def test_blank_passes_the_inspected_uid_to_the_writer(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded())
    seen = {}

    def fake_blank(*a, **k):
        seen.update(k)
        return WriteResult(True, UID)

    monkeypatch.setattr(cs, "blank_card", fake_blank)
    cs.blank_existing_card(encoder=encoder, hotel=hotel, session=session, operator="t")
    assert seen["expected_uid"] == UID


@pytest.mark.parametrize("legacy_byte", [0x40, 0x20])
def test_old_master_and_laundry_cards_are_flagged_for_reissue(monkeypatch, encoder, hotel, legacy_byte):
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded(card_type=legacy_byte))

    ok, msg, info = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert ok is True
    assert "RE-EMITIR" in msg
    assert "antigua" in info["tipo"]
    assert "RE-EMITIR" in info["estado"]


def test_clock_card_is_never_shown_as_expired(monkeypatch, encoder, hotel):
    from setra_cards.encoder.protocol import CARD_TYPE_CLOCK

    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded(
        card_type=CARD_TYPE_CLOCK, date2=datetime(2001, 3, 1, 1, 0)))

    ok, msg, info = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert info["expired"] is False
    assert info["valid_until"] is None
    assert "VENCIDA" not in msg


@pytest.mark.parametrize(("role", "hours", "allowed"), [
    ("frontdesk", 10, True),
    ("frontdesk", 11, False),
    (None, 11, False),          # unknown role gets the front-desk cap
    ("manager", 24, True),
    ("super_manager", 20, True),
])
def test_laundry_duration_is_capped_by_role(monkeypatch, encoder, hotel, session, role, hours, allowed):
    written = []
    monkeypatch.setattr(cs, "write_card", lambda *a, **k: written.append(1) or WriteResult(True, UID))

    result = cs.create_laundry_card(
        encoder=encoder, hotel=hotel, session=session, hours=hours,
        operator="t", operator_role=role,
    )

    assert result.ok is allowed
    assert bool(written) is allowed
    if not allowed:
        assert "máximo 10 horas" in result.error


@pytest.mark.parametrize("role", ["frontdesk", None])
def test_manager_cards_are_refused_in_the_service_for_front_desk(monkeypatch, encoder, hotel, session, role):
    written = []
    monkeypatch.setattr(cs, "write_card", lambda *a, **k: written.append(1) or WriteResult(True, UID))
    room = add_room(session, 1, "101", 1)
    results = [
        cs.create_master_card(encoder=encoder, hotel=hotel, session=session,
                              valid_until=datetime.now() + timedelta(days=1), operator="t", operator_role=role),
        cs.create_auth_card(encoder=encoder, hotel=hotel, session=session, operator="t", operator_role=role),
        cs.create_clock_card(encoder=encoder, hotel=hotel, session=session, operator="t", operator_role=role),
        cs.create_setting_card(encoder=encoder, hotel=hotel, session=session, room=room,
                               operator="t", operator_role=role),
    ]
    assert all(r.ok is False for r in results)
    assert all("gerente" in r.error for r in results)
    assert written == []


def test_card_from_another_system_signature_is_flagged(monkeypatch, encoder, hotel):
    decoded = make_decoded()
    foreign = type(decoded)(**{**vars(decoded), "signature": b"\xd6\x4d"})
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: foreign)

    ok, msg, _info = cs.read_existing_card(encoder=encoder, hotel=hotel)

    assert "otro hotel" in msg


def test_blank_without_expect_keeps_working_and_logs_room(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(
        cs, "read_card",
        lambda *a, **k: make_decoded(building=1, floor=2, room_no_id=3),
    )
    monkeypatch.setattr(cs, "blank_card", lambda *a, **k: WriteResult(True, UID))

    result = cs.blank_existing_card(encoder=encoder, hotel=hotel, session=session, operator="t")

    assert result.ok is True
    row = session.query(CardLog).order_by(CardLog.id.desc()).first()
    assert row.card_type_byte == CARD_TYPE_GUEST
    assert row.room_display == "B1/P2/H3"


# --- Fix 4: laundry duration validation ---

@pytest.mark.parametrize("hours", [0, -3])
def test_laundry_rejects_non_positive_hours(monkeypatch, encoder, hotel, session, hours):
    wrote = []
    monkeypatch.setattr(cs, "write_card", lambda *a, **k: wrote.append(1) or WriteResult(True, UID))

    result = cs.create_laundry_card(
        encoder=encoder, hotel=hotel, session=session, hours=hours, operator="t",
    )

    assert result.ok is False
    assert result.error == "La duración debe ser mayor a 0 horas"
    assert wrote == [], "no write should happen with invalid duration"


# --- Fix 5: truthful result when DB fails after a successful write ---

def _call_guest(session, encoder, hotel, room):
    return cs.create_guest_card(
        encoder=encoder, hotel=hotel, session=session, room=room, guest=None,
        valid_from=datetime.now(), valid_until=datetime.now() + timedelta(days=1),
        operator="t",
    )


def _call_master(session, encoder, hotel, room):
    return cs.create_master_card(operator_role="manager", 
        encoder=encoder, hotel=hotel, session=session,
        valid_until=datetime.now() + timedelta(days=30), operator="t",
    )


def _call_auth(session, encoder, hotel, room):
    return cs.create_auth_card(operator_role="manager", encoder=encoder, hotel=hotel, session=session, operator="t")


def _call_clock(session, encoder, hotel, room):
    return cs.create_clock_card(operator_role="manager", encoder=encoder, hotel=hotel, session=session, operator="t")


def _call_setting(session, encoder, hotel, room):
    return cs.create_setting_card(operator_role="manager", 
        encoder=encoder, hotel=hotel, session=session, room=room, operator="t",
    )


def _call_laundry(session, encoder, hotel, room):
    return cs.create_laundry_card(
        encoder=encoder, hotel=hotel, session=session, hours=8, operator="t",
    )


@pytest.mark.parametrize(
    "caller",
    [_call_guest, _call_master, _call_auth, _call_clock, _call_setting, _call_laundry],
    ids=["guest", "master", "auth", "clock", "setting", "laundry"],
)
def test_db_failure_after_write_returns_truthful_error(
    monkeypatch, encoder, hotel, session, caller,
):
    room = add_room(session, 1, "101", 1)
    monkeypatch.setattr(cs, "write_card", lambda *a, **k: WriteResult(True, UID))

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cs, "log_action", boom)

    result = caller(session, encoder, hotel, room)

    assert result.ok is False
    assert result.error.startswith("Tarjeta grabada pero falló el registro en base de datos:")
    assert "disk full" in result.error


# --- Fix 6: consolidated guest-card invalidation helper ---

def test_invalidate_active_guest_cards_expires_only_active_rows(session):
    room = add_room(session, 1, "101", 1)
    other = add_room(session, 2, "102", 2)
    future = datetime.now() + timedelta(days=2)
    past = datetime.now() - timedelta(days=2)
    session.add_all([
        CardLog(card_type="Guest", card_type_byte=0x00, room_id=room.id,
                room_display="101", expires_at=future, operator="t", success=True),
        CardLog(card_type="Guest", card_type_byte=0x00, room_id=room.id,
                room_display="101", expires_at=past, operator="t", success=True),
        CardLog(card_type="Guest", card_type_byte=0x00, room_id=other.id,
                room_display="102", expires_at=future, operator="t", success=True),
    ])
    session.commit()

    count = cs.invalidate_active_guest_cards(session, room.id)
    session.commit()

    assert count == 1
    still_active = (
        session.query(CardLog)
        .filter(CardLog.room_id == room.id, CardLog.expires_at > datetime.now())
        .count()
    )
    assert still_active == 0
    other_active = (
        session.query(CardLog)
        .filter(CardLog.room_id == other.id, CardLog.expires_at > datetime.now())
        .count()
    )
    assert other_active == 1, "other rooms must be untouched"


# --- Fix 7: valid_from / expires_at present in laundry and auth logs ---

def test_laundry_log_has_valid_from(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(cs, "write_card", lambda *a, **k: WriteResult(True, UID))

    result = cs.create_laundry_card(
        encoder=encoder, hotel=hotel, session=session, hours=8, operator="t",
    )

    assert result.ok is True
    row = session.query(CardLog).order_by(CardLog.id.desc()).first()
    assert row.valid_from is not None
    assert row.expires_at - row.valid_from == timedelta(hours=8)


def test_auth_log_has_valid_from_and_30_day_expiry(monkeypatch, encoder, hotel, session):
    monkeypatch.setattr(cs, "write_card", lambda *a, **k: WriteResult(True, UID))

    result = cs.create_auth_card(operator_role="manager", encoder=encoder, hotel=hotel, session=session, operator="t")

    assert result.ok is True
    row = session.query(CardLog).order_by(CardLog.id.desc()).first()
    assert row.valid_from is not None
    assert row.expires_at - row.valid_from == timedelta(days=30)


# --- Fix 8: change_guest_room rejects corrupt/foreign cards ---

@pytest.mark.parametrize("kwargs", [{"checksum_valid": False}, {"uid_match": False}])
def test_change_room_rejects_corrupt_or_foreign_card(
    monkeypatch, encoder, hotel, session, kwargs,
):
    old_room = add_room(session, 1, "101", 1)
    new_room = add_room(session, 2, "102", 2)
    monkeypatch.setattr(cs, "read_card", lambda *a, **k: make_decoded(**kwargs))

    result = cs.change_guest_room(
        encoder=encoder, hotel=hotel, session=session,
        old_room=old_room, new_room=new_room, operator="t",
    )

    assert result.ok is False
    assert "corrupta o de otro sistema" in result.error


def test_change_room_happy_path_routes_through_log_emission(
    monkeypatch, encoder, hotel, session,
):
    old_room = add_room(session, 1, "101", 1)
    new_room = add_room(session, 2, "102", 2)
    guest = Guest(name="Juan Tester")
    session.add(guest)
    session.commit()
    checkout = datetime.now() + timedelta(days=2)
    session.add(CardLog(
        card_type="Guest", card_type_byte=0x00, guest_id=guest.id,
        room_id=old_room.id, room_display="101", uid_hex=UID.hex(":"),
        expires_at=checkout, operator="t", success=True,
    ))
    session.commit()

    monkeypatch.setattr(
        cs, "read_card",
        lambda *a, **k: make_decoded(building=1, floor=1, room_no_id=1),
    )
    monkeypatch.setattr(cs, "write_card", lambda *a, **k: WriteResult(True, UID))

    result = cs.change_guest_room(
        encoder=encoder, hotel=hotel, session=session,
        old_room=old_room, new_room=new_room, operator="t",
    )

    assert result.ok is True
    # Old room log expired, new log created via _log_emission
    old_active = (
        session.query(CardLog)
        .filter(CardLog.room_id == old_room.id, CardLog.expires_at > datetime.now())
        .count()
    )
    assert old_active == 0
    new_log = (
        session.query(CardLog)
        .filter(CardLog.room_id == new_room.id, CardLog.success.is_(True))
        .one()
    )
    assert new_log.room_display == "102"
    assert new_log.guest_id == guest.id
    assert session.get(Room, old_room.id).state == "sucia"
