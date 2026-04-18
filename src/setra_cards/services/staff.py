"""CRUD de personal del hotel."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from setra_cards.storage.models import Staff

VALID_ROLES = {"recepcion", "limpieza", "mantenimiento", "admin"}


def list_staff(session: Session, active_only: bool = False) -> list[Staff]:
    q = select(Staff).order_by(Staff.name)
    if active_only:
        q = q.where(Staff.active.is_(True))
    return session.execute(q).scalars().all()


def get_staff(session: Session, staff_id: int) -> Staff | None:
    return session.get(Staff, staff_id)


def create_staff(
    session: Session,
    name: str,
    role: str,
    document: str | None = None,
    phone: str | None = None,
    assigned_rooms: list[str] | None = None,
) -> Staff:
    name_clean = (name or "").strip()
    if not name_clean:
        raise ValueError("Nombre requerido")
    if role not in VALID_ROLES:
        raise ValueError(f"Rol invalido: {role}")
    existing = session.execute(select(Staff).where(Staff.name == name_clean)).scalar_one_or_none()
    if existing:
        raise ValueError(f"Ya existe '{name_clean}'")
    s = Staff(
        name=name_clean,
        role=role,
        document=(document or "").strip() or None,
        phone=(phone or "").strip() or None,
        assigned_rooms=json.dumps(assigned_rooms) if assigned_rooms else None,
        active=True,
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def update_staff(
    session: Session,
    staff_id: int,
    *,
    name: str | None = None,
    role: str | None = None,
    document: str | None = None,
    phone: str | None = None,
    assigned_rooms: list[str] | None = None,
    active: bool | None = None,
) -> Staff:
    s = session.get(Staff, staff_id)
    if not s:
        raise ValueError("Empleado no existe")
    if name is not None:
        s.name = name.strip() or s.name
    if role is not None:
        if role not in VALID_ROLES:
            raise ValueError(f"Rol invalido: {role}")
        s.role = role
    if document is not None:
        s.document = document.strip() or None
    if phone is not None:
        s.phone = phone.strip() or None
    if assigned_rooms is not None:
        s.assigned_rooms = json.dumps(assigned_rooms) if assigned_rooms else None
    if active is not None:
        s.active = active
    session.commit()
    return s


def delete_staff(session: Session, staff_id: int) -> None:
    s = session.get(Staff, staff_id)
    if s:
        session.delete(s)
        session.commit()


def assigned_room_list(staff: Staff) -> list[str]:
    if not staff.assigned_rooms:
        return []
    try:
        val = json.loads(staff.assigned_rooms)
        return [str(v) for v in val] if isinstance(val, list) else []
    except Exception:
        return []
