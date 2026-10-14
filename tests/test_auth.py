"""Tests unitarios para auth service."""
import os

import pytest

from setra_cards.services.auth import (
    PBKDF2_ITER,
    RATE_MAX_FAILS_GLOBAL,
    _hash_pin,
    authenticate,
    ensure_seed_admin,
    hash_new_pin,
    role_has_access,
    update_pin,
    verify_pin,
)
from setra_cards.storage.models import Base, LoginAttempt, Operator
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


# --- ensure_seed_admin: garantiza super_manager ACTIVO ---

def test_seed_admin_created_on_empty_db(session):
    op = ensure_seed_admin(session)
    assert op.name == "Admin"
    assert op.role == "super_manager"
    assert op.active is True
    assert op.must_change_pin is True
    # Random PIN policy: the well-known default must NOT work
    assert verify_pin(op, "1234") is False


def test_fresh_seed_pin_is_handed_to_the_login_screen_once(session):
    from setra_cards.services.auth import pop_seed_pin

    pop_seed_pin()  # clear anything left by other tests
    op = ensure_seed_admin(session)
    pin = pop_seed_pin()
    assert pin is not None
    assert verify_pin(op, pin) is True
    assert pop_seed_pin() is None  # shown once only


def test_no_seed_pin_when_a_super_manager_already_exists(session):
    from setra_cards.services.auth import pop_seed_pin

    ensure_seed_admin(session)
    pop_seed_pin()
    ensure_seed_admin(session)  # second launch: admin exists, nothing generated
    assert pop_seed_pin() is None


def test_seed_admin_returns_existing_active_super(session):
    h, salt = hash_new_pin("5678")
    boss = Operator(name="Tomas", pin_hash=h, pin_salt=salt, role="super_manager",
                    active=True, must_change_pin=False)
    session.add(boss)
    session.commit()
    op = ensure_seed_admin(session)
    assert op.id == boss.id
    assert op.pin_hash == h  # untouched
    assert session.query(Operator).filter(Operator.name == "Admin").first() is None


def test_seed_admin_reactivates_when_only_super_is_inactive(session):
    """Bug original: un Admin desactivado contaba como 'ya hay admin' y dejaba la app sin acceso."""
    h, salt = hash_new_pin("5678")
    dead = Operator(name="Admin", pin_hash=h, pin_salt=salt, role="super_manager",
                    active=False, must_change_pin=False)
    session.add(dead)
    session.commit()
    op = ensure_seed_admin(session)
    assert op.id == dead.id
    assert op.active is True
    assert op.must_change_pin is True
    assert op.pin_hash != h  # fresh random PIN


def test_seed_admin_ignores_inactive_super_with_other_name(session):
    h, salt = hash_new_pin("5678")
    dead = Operator(name="ExJefe", pin_hash=h, pin_salt=salt, role="super_manager",
                    active=False, must_change_pin=False)
    session.add(dead)
    session.commit()
    op = ensure_seed_admin(session)
    assert op.name == "Admin"
    assert op.active is True
    assert op.role == "super_manager"


# --- PBKDF2 600k + re-hash transparente ---

def test_pbkdf2_iterations_raised():
    assert PBKDF2_ITER == 600_000


def test_legacy_hash_verifies_and_rehashes_on_login(session):
    """Hash a 200k itera sigue funcionando y se actualiza a 600k al autenticar."""
    salt = os.urandom(16)
    legacy_hash = _hash_pin("4321", salt, iterations=200_000)
    op = Operator(name="legacy_op", pin_hash=legacy_hash, pin_salt=salt.hex(),
                  role="frontdesk", active=True, must_change_pin=False)
    session.add(op)
    session.commit()

    result = authenticate(session, "legacy_op", "4321")
    assert result.ok is True

    session.refresh(op)
    assert op.pin_hash != legacy_hash
    new_salt = bytes.fromhex(op.pin_salt)
    assert op.pin_hash == _hash_pin("4321", new_salt, iterations=PBKDF2_ITER)
    # And the upgraded hash keeps verifying
    assert verify_pin(op, "4321") is True


# --- Bucket global de intentos fallidos ---

def test_global_fail_bucket_blocks_even_fresh_names(session):
    h, salt = hash_new_pin("7777")
    op = Operator(name="victim", pin_hash=h, pin_salt=salt, role="frontdesk",
                  active=True, must_change_pin=False)
    session.add(op)
    for i in range(RATE_MAX_FAILS_GLOBAL):
        session.add(LoginAttempt(operator_name=f"ghost_{i}"))
    session.commit()

    # Correct PIN, but the global bucket is exhausted by other names
    result = authenticate(session, "victim", "7777")
    assert result.ok is False
    assert "Demasiados intentos" in result.reason
