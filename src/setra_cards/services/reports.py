"""Servicio de reportes — estadísticas y logs filtrados de emisiones."""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from setra_cards.storage.models import CardLog


@dataclass
class ReportSummary:
    total: int = 0
    guest: int = 0
    master: int = 0
    auth: int = 0
    clock: int = 0
    setting: int = 0
    laundry: int = 0
    blank: int = 0
    other: int = 0
    failed: int = 0
    unique_rooms: int = 0


def _end_of_day(dt: datetime) -> datetime:
    """Extiende dt al último segundo del día para filtros inclusivos."""
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)


def get_report_summary(
    session: Session,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    card_type: str | None = None,
    room_display: str | None = None,
    operator: str | None = None,
    success_only: bool = False,
) -> ReportSummary:
    q = session.query(CardLog)
    if date_from:
        q = q.filter(CardLog.issued_at >= date_from)
    if date_to:
        q = q.filter(CardLog.issued_at <= _end_of_day(date_to))
    if card_type and card_type != "Todos":
        q = q.filter(CardLog.card_type == card_type)
    if room_display and room_display != "Todas":
        q = q.filter(CardLog.room_display == room_display)
    if operator and operator != "Todos":
        q = q.filter(CardLog.operator == operator)
    if success_only:
        q = q.filter(CardLog.success.is_(True))

    rows = q.all()
    s = ReportSummary()
    s.total = len(rows)
    rooms: set[str] = set()
    for r in rows:
        ct = (r.card_type or "").lower()
        if ct == "guest":
            s.guest += 1
        elif ct == "master":
            s.master += 1
        elif ct == "auth":
            s.auth += 1
        elif ct == "clock":
            s.clock += 1
        elif ct == "setting":
            s.setting += 1
        elif ct == "laundry":
            s.laundry += 1
        elif ct == "blank":
            s.blank += 1
        else:
            s.other += 1
        if not r.success:
            s.failed += 1
        if r.room_display:
            rooms.add(r.room_display)
    s.unique_rooms = len(rooms)
    return s


def get_filtered_logs(
    session: Session,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    card_type: str | None = None,
    room_display: str | None = None,
    operator: str | None = None,
    success_only: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[CardLog], int]:
    q = session.query(CardLog)
    if date_from:
        q = q.filter(CardLog.issued_at >= date_from)
    if date_to:
        q = q.filter(CardLog.issued_at <= _end_of_day(date_to))
    if card_type and card_type != "Todos":
        q = q.filter(CardLog.card_type == card_type)
    if room_display and room_display != "Todas":
        q = q.filter(CardLog.room_display == room_display)
    if operator and operator != "Todos":
        q = q.filter(CardLog.operator == operator)
    if success_only:
        q = q.filter(CardLog.success.is_(True))
    total = q.count()
    logs = q.order_by(CardLog.issued_at.desc()).offset(offset).limit(limit).all()
    return logs, total


def export_csv(logs: list[CardLog]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "ID", "Tipo", "Habitacion", "Huesped_ID", "UID",
        "Emitida", "Valida_desde", "Expira", "Operador", "Exito", "Error",
    ])
    for r in logs:
        writer.writerow([
            r.id,
            r.card_type,
            r.room_display or "",
            r.guest_id or "",
            r.uid_hex or "",
            r.issued_at.strftime("%Y-%m-%d %H:%M:%S") if r.issued_at else "",
            r.valid_from.strftime("%Y-%m-%d %H:%M:%S") if r.valid_from else "",
            r.expires_at.strftime("%Y-%m-%d %H:%M:%S") if r.expires_at else "",
            r.operator or "",
            "SI" if r.success else "NO",
            r.error_message or "",
        ])
    return buf.getvalue()


def get_distinct_rooms(session: Session) -> list[str]:
    rows = (
        session.query(CardLog.room_display)
        .filter(CardLog.room_display.isnot(None))
        .distinct()
        .order_by(CardLog.room_display)
        .all()
    )
    return [r[0] for r in rows]


def get_distinct_operators(session: Session) -> list[str]:
    rows = (
        session.query(CardLog.operator)
        .filter(CardLog.operator.isnot(None))
        .distinct()
        .order_by(CardLog.operator)
        .all()
    )
    return [r[0] for r in rows]
