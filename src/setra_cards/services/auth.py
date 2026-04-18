"""Autenticacion — hash PIN (PBKDF2), verificar, rate-limit."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from setra_cards.storage.models import LoginAttempt, Operator

PIN_MIN_LEN = 4
PIN_MAX_LEN = 20
PBKDF2_ITER = 200_000
RATE_WINDOW_MIN = 5
RATE_MAX_FAILS = 5


@dataclass(frozen=True)
class AuthResult:
    ok: bool
    operator: Operator | None = None
    reason: str = ""


def _hash_pin(pin: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, PBKDF2_ITER).hex()


def hash_new_pin(pin: str) -> tuple[str, str]:
    """Devuelve (hash_hex, salt_hex) para almacenar."""
    if not PIN_MIN_LEN <= len(pin) <= PIN_MAX_LEN:
        raise ValueError(f"PIN debe tener entre {PIN_MIN_LEN} y {PIN_MAX_LEN} caracteres")
    salt = os.urandom(16)
    return _hash_pin(pin, salt), salt.hex()


def verify_pin(operator: Operator, pin: str) -> bool:
    try:
        salt = bytes.fromhex(operator.pin_salt)
    except ValueError:
        return False
    computed = _hash_pin(pin, salt)
    return hmac.compare_digest(computed, operator.pin_hash)


def ensure_seed_admin(session: Session) -> Operator:
    """Crea operador Admin con PIN 1234 si no hay ninguno."""
    any_op = session.query(Operator).first()
    if any_op:
        return any_op
    h, salt = hash_new_pin("1234")
    op = Operator(
        name="Admin",
        pin_hash=h,
        pin_salt=salt,
        role="super_manager",
        active=True,
        must_change_pin=True,
    )
    session.add(op)
    session.commit()
    session.refresh(op)
    return op


def update_pin(session: Session, operator_id: int, new_pin: str) -> None:
    op = session.get(Operator, operator_id)
    if not op:
        raise ValueError("Operador no existe")
    h, salt = hash_new_pin(new_pin)
    op.pin_hash = h
    op.pin_salt = salt
    op.must_change_pin = False
    session.commit()


def _recent_fails(session: Session, operator_name: str) -> int:
    cutoff = datetime.now() - timedelta(minutes=RATE_WINDOW_MIN)
    return (
        session.query(LoginAttempt)
        .filter(
            LoginAttempt.operator_name == operator_name,
            LoginAttempt.attempted_at >= cutoff,
        )
        .count()
    )


def _record_fail(session: Session, operator_name: str) -> None:
    session.add(LoginAttempt(operator_name=operator_name))
    session.commit()


def _clear_fails(session: Session, operator_name: str) -> None:
    session.execute(delete(LoginAttempt).where(LoginAttempt.operator_name == operator_name))
    session.commit()


def authenticate(session: Session, name: str, pin: str) -> AuthResult:
    name_clean = name.strip()
    if not name_clean or not pin:
        return AuthResult(False, reason="Usuario y PIN requeridos")

    if _recent_fails(session, name_clean) >= RATE_MAX_FAILS:
        return AuthResult(False, reason="Demasiados intentos fallidos. Espere 5 minutos.")

    op = session.query(Operator).filter(Operator.name == name_clean, Operator.active.is_(True)).first()
    if not op or not verify_pin(op, pin):
        _record_fail(session, name_clean)
        return AuthResult(False, reason="Usuario o PIN incorrecto")

    _clear_fails(session, name_clean)
    return AuthResult(True, operator=op)


def generate_random_pin(length: int = 6) -> str:
    """PIN aleatorio para restablecer — 6 digitos por defecto."""
    return "".join(secrets.choice("0123456789") for _ in range(length))
