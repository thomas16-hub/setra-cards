"""Tests de validación de habitaciones."""
import pytest

from setra_cards.storage.models import ActionLog, Base
from setra_cards.services.rooms import ROOM_NO_ID_MAX, create_room, update_room
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Session() as s:
        yield s


def test_create_room_valid(session):
    r = create_room(session, "101", building=1, floor=1)
    assert r.display_number == "101"


def test_create_room_building_out_of_range(session):
    with pytest.raises(ValueError, match="Edificio"):
        create_room(session, "102", building=256, floor=1)


def test_create_room_floor_out_of_range(session):
    with pytest.raises(ValueError, match="Piso"):
        create_room(session, "103", building=1, floor=-1)


def test_update_room_empty_display(session):
    r = create_room(session, "104", building=1, floor=1)
    with pytest.raises(ValueError, match="vacío"):
        update_room(session, r.id, display_number="   ")


def test_update_room_building_out_of_range(session):
    r = create_room(session, "105", building=1, floor=1)
    with pytest.raises(ValueError, match="Edificio"):
        update_room(session, r.id, building=300)


def test_move_floor_excludes_room_itself_from_series(session):
    """Mover una habitacion de piso no debe inflar la serie con su propio id."""
    create_room(session, "201", building=1, floor=1)  # room_no_id=1
    r = create_room(session, "202", building=1, floor=1)  # room_no_id=2
    moved = update_room(session, r.id, floor=2)
    # Floor 2 is empty: the moved room must get id 1, not 3 (its own old id
    # leaked into max() via autoflush before the fix)
    assert moved.room_no_id == 1


def test_move_floor_continues_existing_series(session):
    create_room(session, "301", building=1, floor=3)  # floor 3: id 1
    create_room(session, "302", building=1, floor=3)  # floor 3: id 2
    r = create_room(session, "401", building=1, floor=4)  # floor 4: id 1
    moved = update_room(session, r.id, floor=3)
    assert moved.room_no_id == 3


def test_room_no_id_capped_at_255(session):
    r = create_room(session, "501", building=1, floor=5)
    r.room_no_id = ROOM_NO_ID_MAX
    session.commit()
    with pytest.raises(ValueError, match="255"):
        create_room(session, "502", building=1, floor=5)


def test_move_floor_logs_action(session):
    create_room(session, "601", building=1, floor=6)
    r = create_room(session, "602", building=1, floor=6)
    old_triple = (r.building, r.floor, r.room_no_id)
    update_room(session, r.id, floor=7, operator="tester")
    entry = (
        session.query(ActionLog)
        .filter(ActionLog.action == "room_lock_id_changed")
        .order_by(ActionLog.id.desc())
        .first()
    )
    assert entry is not None
    assert entry.operator == "tester"
    assert str(old_triple) in entry.detail
    assert "(1, 7, 1)" in entry.detail


def test_update_without_move_does_not_log(session):
    r = create_room(session, "701", building=1, floor=7)
    update_room(session, r.id, state="sucia")
    count = session.query(ActionLog).filter(ActionLog.action == "room_lock_id_changed").count()
    assert count == 0
