"""Tests unitarios para auth service."""
import pytest

from setra_cards.services.auth import hash_new_pin, verify_pin, update_pin, _ROLE_RANK, role_has_access
from setra_cards.storage.models import Operator, Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Session() as s:
        yield s


def test_hash_and_verify_pin():
    h, salt = hash_new_pin("1234")
    op = Operator(name="tmp", pin_hash=h, pin_salt=salt, role="frontdesk", active=True, must_change_pin=False)
    assert verify_pin(op, "1234") is True
    assert verify_pin(op, "wrong") is False


def test_hash_different_salts():
    h1, s1 = hash_new_pin("1234")
    h2, s2 = hash_new_pin("1234")
    assert s1 != s2
    assert h1 != h2


def test_role_has_access():
    assert role_has_access("super_manager", "manager") is True
    assert role_has_access("manager", "manager") is True
    assert role_has_access("frontdesk", "manager") is False
    assert role_has_access(None, "frontdesk") is False
    assert role_has_access("frontdesk", None) is True


def test_must_change_pin_flag(session):
    h, salt = hash_new_pin("0000")
    op = Operator(name="test_op", pin_hash=h, pin_salt=salt, role="frontdesk",
                  active=True, must_change_pin=True)
    session.add(op)
    session.commit()
    assert op.must_change_pin is True
    update_pin(session, op.id, "9999")
    session.refresh(op)
    assert op.must_change_pin is False
    assert verify_pin(op, "9999") is True
