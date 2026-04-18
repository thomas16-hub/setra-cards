"""Tests para filtros de reportes."""
from datetime import datetime, timedelta
import pytest

from setra_cards.storage.models import Base, CardLog
from setra_cards.services.reports import get_filtered_logs, get_report_summary, _end_of_day
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with Session() as s:
        yield s


def _make_log(session, card_type="Guest", issued_at=None, success=True, room="101"):
    entry = CardLog(
        card_type=card_type,
        card_type_byte=0x00,
        uid_hex="AA:BB:CC:DD",
        issued_at=issued_at or datetime.now(),
        success=success,
        room_display=room,
        operator="test",
    )
    session.add(entry)
    session.commit()
    return entry


def test_end_of_day():
    dt = datetime(2025, 1, 15, 10, 30)
    eod = _end_of_day(dt)
    assert eod.hour == 23
    assert eod.minute == 59
    assert eod.second == 59


def test_date_to_inclusive(session):
    today = datetime.now().replace(hour=23, minute=59)
    _make_log(session, issued_at=today)
    date_to = datetime.now().replace(hour=0, minute=0, second=0)
    logs, total = get_filtered_logs(session, date_to=date_to)
    assert total == 1


def test_filter_by_card_type(session):
    _make_log(session, card_type="Guest")
    _make_log(session, card_type="Master")
    logs, total = get_filtered_logs(session, card_type="Guest")
    assert total == 1
    assert logs[0].card_type == "Guest"


def test_summary_counts(session):
    _make_log(session, card_type="Guest")
    _make_log(session, card_type="Master")
    _make_log(session, card_type="Guest", success=False)
    summary = get_report_summary(session)
    assert summary.total == 3
    assert summary.guest == 2
    assert summary.master == 1
    assert summary.failed == 1


def test_summary_respects_card_type_filter(session):
    _make_log(session, card_type="Guest")
    _make_log(session, card_type="Master")
    summary = get_report_summary(session, card_type="Guest")
    assert summary.total == 1
    assert summary.guest == 1
    assert summary.master == 0
