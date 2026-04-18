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

# Jerarquía de roles — igual que locstar-app original
ROLES = ("super_manager", "manager", "frontdesk")
ROLE_LABELS = {
    "super_manager": "Super Manager",
    "manager": "Manager",
    "frontdesk": "Front Desk",
}
_ROLE_RANK: dict[str, int] = {"super_manager": 0, "manager": 1, "frontdesk": 2}


def role_has_access(operator_role: str | None, minimum_role: str) -> bool:
    """True si operator_role tiene igual o mayor jerarquía que minimum_role."""
    return _ROLE_RANK.get(operator_role or "", 99) <= _ROLE_RANK.get(minimum_role, 99)


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
    """Garantiza la existencia de al menos un super_manager.

    Busca un operador con nombre 'Admin' o con rol 'super_manager'. Si no
    encuentra ninguno, crea el Admin seed con PIN 1234 y must_change_pin=True.
    Si ya existe un super_manager con otro nombre, lo devuelve tal cual — no
    crea un Admin oculto adicional.
    """
    admin = session.query(Operator).filter(Operator.name == "Admin").first()
    if admin:
        return admin
    super_op = session.query(Operator).filter(Operator.role == "super_manager").first()
    if super_op:
        return super_op
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
