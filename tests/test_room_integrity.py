"""Room changes must never leave a guest card opening the wrong door."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from setra_cards.services.card_service import invalidate_active_guest_cards
from setra_cards.services.rooms import create_room, delete_room, update_room
from setra_cards.storage.models import Base, CardLog, Room


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):  # same rule as the app DB
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Session() as s:
        yield s


def _guest_card(session, room: Room, expires_in: timedelta) -> CardLog:
    row = CardLog(card_type="Guest", card_type_byte=0x00, room_id=room.id,
                  room_display=room.display_number, uid_hex="AA:BB:CC:DD",
                  expires_at=datetime.now() + expires_in, operator="t", success=True)
    session.add(row)
    session.commit()
    return row


def test_delete_refused_while_a_guest_card_is_valid(session):
    room = create_room(session, "305", floor=3)
    _guest_card(session, room, timedelta(days=1))
    with pytest.raises(ValueError, match="vigente"):
        delete_room(session, room.id)
    assert session.get(Room, room.id) is not None


def test_delete_with_expired_history_keeps_the_history(session):
    room = create_room(session, "305", floor=3)
    old = _guest_card(session, room, timedelta(days=-1))
    delete_room(session, room.id)  # used to raise IntegrityError (FK)
    assert session.get(Room, room.id) is None
    session.refresh(old)
    assert old.room_id is None
    assert old.room_display == "305"


def test_floor_change_refused_while_a_guest_card_is_valid(session):
    room = create_room(session, "305", floor=3)
    _guest_card(session, room, timedelta(days=1))
    with pytest.raises(ValueError, match="vigente"):
        update_room(session, room.id, floor=4)
    session.refresh(room)
    assert room.floor == 3


def test_rename_keeps_the_guest_card_attached(session):
    room = create_room(session, "305", floor=3)
    card = _guest_card(session, room, timedelta(days=1))
    update_room(session, room.id, display_number="305A")  # renaming is allowed
    # Checkout of the renamed room still expires the card (keyed by room id).
    assert invalidate_active_guest_cards(session, room.id) == 1
    session.commit()
    session.refresh(card)
    assert card.expires_at <= datetime.now()


def test_delete_guard_also_sees_legacy_rows_without_room_id(session):
    room = create_room(session, "305", floor=3)
    session.add(CardLog(card_type="Guest", card_type_byte=0x00, room_id=None, room_display="305",
                        expires_at=datetime.now() + timedelta(days=1), operator="t", success=True))
    session.commit()
    with pytest.raises(ValueError, match="vigente"):
        delete_room(session, room.id)


def test_checkout_also_expires_legacy_rows_without_room_id(session):
    room = create_room(session, "305", floor=3)
    legacy = CardLog(card_type="Guest", card_type_byte=0x00, room_id=None, room_display="305",
                     expires_at=datetime.now() + timedelta(days=1), operator="t", success=True)
    session.add(legacy)
    session.commit()
    assert invalidate_active_guest_cards(session, room.id, legacy_display="305") == 1
