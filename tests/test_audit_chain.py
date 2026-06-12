"""Tests del hash chain tamper-evident del ActionLog.

Cubre: construccion de una cadena valida al loguear N acciones, verify_chain
ok=True en una cadena sana, deteccion de fila editada / borrada / reordenada
(ok=False con el first_broken_id correcto), y que filas legacy con hash NULL
antes de la primera fila hasheada no produzcan falsos positivos.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from setra_cards.services.action_log import compute_entry_hash, log, verify_chain
from setra_cards.storage.models import ActionLog, Base


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        yield s


def _all_rows(session: Session) -> list[ActionLog]:
    return session.execute(
        select(ActionLog).order_by(ActionLog.id.asc())
    ).scalars().all()


# --- Construccion de cadena valida ---


def test_logging_actions_builds_valid_chain(session):
    log(session, "create_master", "thomas", "room=101")
    log(session, "create_guest", "recepcion", "room=102")
    log(session, "blank_card", "thomas", None)

    rows = _all_rows(session)
    assert len(rows) == 3

    # First row links to "" and every row's prev_hash equals the previous
    # row's entry_hash.
    assert rows[0].prev_hash == ""
    assert rows[1].prev_hash == rows[0].entry_hash
    assert rows[2].prev_hash == rows[1].entry_hash

    # Each entry_hash matches its own recomputed content.
    for row in rows:
        assert row.entry_hash == compute_entry_hash(
            row.prev_hash, row.action, row.operator, row.detail, row.timestamp
        )

    ok, broken = verify_chain(session)
    assert ok is True
    assert broken is None


def test_verify_chain_empty_db_is_ok(session):
    ok, broken = verify_chain(session)
    assert ok is True
    assert broken is None


def test_none_detail_hashes_as_empty_string(session):
    entry = log(session, "noop", "thomas", None)
    # None detail must hash identically to "".
    expected = compute_entry_hash("", "noop", "thomas", "", entry.timestamp)
    assert entry.entry_hash == expected


# --- Deteccion de manipulacion ---


def test_editing_detail_breaks_chain_with_right_id(session):
    log(session, "a", "op", "d1")
    target = log(session, "create_master", "op", "room=101")
    log(session, "c", "op", "d3")

    # Tamper directly in the DB (simulate someone editing the .db file).
    session.execute(
        text("UPDATE action_log SET detail = :d WHERE id = :i"),
        {"d": "room=999", "i": target.id},
    )
    session.commit()
    session.expire_all()

    ok, broken = verify_chain(session)
    assert ok is False
    # The recomputed hash of the edited row no longer matches its stored hash.
    assert broken == target.id


def test_editing_operator_breaks_chain(session):
    log(session, "a", "op", "d1")
    target = log(session, "create_master", "thomas", "room=101")

    session.execute(
        text("UPDATE action_log SET operator = :o WHERE id = :i"),
        {"o": "someone_else", "i": target.id},
    )
    session.commit()
    session.expire_all()

    ok, broken = verify_chain(session)
    assert ok is False
    assert broken == target.id


def test_deleting_middle_row_breaks_chain(session):
    log(session, "a", "op", "d1")
    middle = log(session, "b", "op", "d2")
    third = log(session, "c", "op", "d3")

    session.execute(
        text("DELETE FROM action_log WHERE id = :i"), {"i": middle.id}
    )
    session.commit()
    session.expire_all()

    ok, broken = verify_chain(session)
    assert ok is False
    # The third row's prev_hash still points at the deleted row's hash, which
    # no longer matches the now-preceding (first) row's entry_hash.
    assert broken == third.id


def test_deleting_last_row_does_not_false_positive(session):
    log(session, "a", "op", "d1")
    log(session, "b", "op", "d2")
    last = log(session, "c", "op", "d3")

    session.execute(
        text("DELETE FROM action_log WHERE id = :i"), {"i": last.id}
    )
    session.commit()
    session.expire_all()

    # Truncating the tail leaves a still-consistent prefix; this is detectable
    # only with an external anchor, so the chain itself stays verifiable.
    ok, broken = verify_chain(session)
    assert ok is True
    assert broken is None


def test_reordering_rows_breaks_chain(session):
    r1 = log(session, "a", "op", "d1")
    r2 = log(session, "b", "op", "d2")

    # Swap the two rows' content (id stays, payload+hashes swap) -> the
    # prev_hash linkage no longer matches insertion order.
    session.execute(
        text(
            "UPDATE action_log SET action=:a, operator=:o, detail=:d, "
            "timestamp=:t, prev_hash=:p, entry_hash=:e WHERE id=:i"
        ),
        {
            "a": r2.action, "o": r2.operator, "d": r2.detail,
            "t": r2.timestamp, "p": r2.prev_hash, "e": r2.entry_hash,
            "i": r1.id,
        },
    )
    session.commit()
    session.expire_all()

    ok, broken = verify_chain(session)
    assert ok is False
    assert broken == r1.id


# --- Filas legacy con hash NULL ---


def _insert_legacy_row(session: Session, action: str, operator: str,
                       detail: str | None, ts: datetime) -> int:
    """Insert a pre-feature row (NULL prev_hash/entry_hash)."""
    result = session.execute(
        text(
            "INSERT INTO action_log (action, operator, detail, timestamp) "
            "VALUES (:a, :o, :d, :t)"
        ),
        {"a": action, "o": operator, "d": detail, "t": ts},
    )
    session.commit()
    return result.lastrowid


def test_legacy_null_hash_rows_before_first_hash_do_not_false_positive(session):
    # Legacy prefix written before the hash-chain feature existed.
    _insert_legacy_row(session, "old_a", "op", "x", datetime(2025, 1, 1, 8, 0, 0))
    _insert_legacy_row(session, "old_b", "op", None, datetime(2025, 1, 2, 9, 0, 0))

    # New rows start the chain; the first hashed row must link to "".
    first_hashed = log(session, "create_master", "thomas", "room=101")
    log(session, "create_guest", "recepcion", "room=102")

    assert first_hashed.prev_hash == ""

    ok, broken = verify_chain(session)
    assert ok is True
    assert broken is None


def test_legacy_prefix_then_tamper_in_hashed_region_is_detected(session):
    _insert_legacy_row(session, "old", "op", "x", datetime(2025, 1, 1, 8, 0, 0))
    log(session, "a", "op", "d1")
    target = log(session, "create_master", "thomas", "room=101")

    session.execute(
        text("UPDATE action_log SET detail = :d WHERE id = :i"),
        {"d": "tampered", "i": target.id},
    )
    session.commit()
    session.expire_all()

    ok, broken = verify_chain(session)
    assert ok is False
    assert broken == target.id


def test_clearing_a_hashed_row_in_the_middle_is_detected(session):
    log(session, "a", "op", "d1")
    middle = log(session, "b", "op", "d2")
    log(session, "c", "op", "d3")

    # Wipe the hash columns of a middle row (NULL after a hashed row).
    session.execute(
        text("UPDATE action_log SET prev_hash=NULL, entry_hash=NULL WHERE id=:i"),
        {"i": middle.id},
    )
    session.commit()
    session.expire_all()

    ok, broken = verify_chain(session)
    assert ok is False
    assert broken == middle.id
