"""Tests de validación de habitaciones."""
import pytest

from setra_cards.storage.models import Base
from setra_cards.services.rooms import create_room, update_room
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
    with pytest.raises(ValueError, match="vacio"):
        update_room(session, r.id, display_number="   ")


def test_update_room_building_out_of_range(session):
    r = create_room(session, "105", building=1, floor=1)
    with pytest.raises(ValueError, match="Edificio"):
        update_room(session, r.id, building=300)
