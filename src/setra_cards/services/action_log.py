"""Auditoria — escribe al log de acciones."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from setra_cards.storage.models import ActionLog


def log(
    session: Session,
    action: str,
    operator: str,
    detail: str | None = None,
) -> ActionLog:
    entry = ActionLog(action=action, operator=operator, detail=detail)
    session.add(entry)
    session.commit()
    return entry


def recent(session: Session, limit: int = 200) -> list[ActionLog]:
    return session.execute(
        select(ActionLog).order_by(ActionLog.timestamp.desc()).limit(limit)
    ).scalars().all()
