"""CRUD + busqueda de huespedes."""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from setra_cards.storage.models import Guest


def list_guests(session: Session, limit: int = 500) -> list[Guest]:
    return session.execute(
        select(Guest).order_by(Guest.updated_at.desc()).limit(limit)
    ).scalars().all()


def search_guests(session: Session, query: str, limit: int = 50) -> list[Guest]:
    q = query.strip()
    if not q:
        return list_guests(session, limit=limit)
    like = f"%{q}%"
    return session.execute(
        select(Guest)
        .where(or_(
            Guest.name.ilike(like),
            Guest.document.ilike(like),
            Guest.phone.ilike(like),
        ))
        .order_by(Guest.updated_at.desc())
        .limit(limit)
    ).scalars().all()


def get_guest(session: Session, guest_id: int) -> Guest | None:
    return session.get(Guest, guest_id)


def find_by_document(session: Session, document: str) -> Guest | None:
    doc = (document or "").strip()
    if not doc:
        return None
    return session.execute(
        select(Guest).where(Guest.document == doc)
    ).scalar_one_or_none()


def create_guest(
    session: Session,
    name: str,
    document: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    notes: str | None = None,
) -> Guest:
    name_clean = (name or "").strip()
    if not name_clean:
        raise ValueError("Nombre del huesped es obligatorio")
    g = Guest(
        name=name_clean,
        document=(document or "").strip() or None,
        phone=(phone or "").strip() or None,
        email=(email or "").strip() or None,
        notes=(notes or "").strip() or None,
    )
    session.add(g)
    session.commit()
    session.refresh(g)
    return g


def update_guest(
    session: Session,
    guest_id: int,
    *,
    name: str | None = None,
    document: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    notes: str | None = None,
) -> Guest:
    g = session.get(Guest, guest_id)
    if not g:
        raise ValueError("Huesped no existe")
    if name is not None:
        g.name = name.strip() or g.name
    if document is not None:
        g.document = document.strip() or None
    if phone is not None:
        g.phone = phone.strip() or None
    if email is not None:
        g.email = email.strip() or None
    if notes is not None:
        g.notes = notes.strip() or None
    session.commit()
    return g


def delete_guest(session: Session, guest_id: int) -> None:
    g = session.get(Guest, guest_id)
    if g:
        session.delete(g)
        session.commit()


def upsert_guest(
    session: Session,
    name: str,
    document: str | None = None,
    phone: str | None = None,
) -> Guest:
    """Busca por documento. Si existe, actualiza nombre/telefono; si no, crea."""
    if document:
        existing = find_by_document(session, document)
        if existing:
            return update_guest(
                session, existing.id, name=name, phone=phone,
            )
    return create_guest(session, name=name, document=document, phone=phone)
