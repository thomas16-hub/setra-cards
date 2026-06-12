"""Auditoria — escribe al log de acciones (tamper-evident hash chain).

The ActionLog table is append-only by intent, but a SQLite file is editable
by anyone who can open it. To make tampering *evident* (we cannot make it
impossible client-side), every row carries a SHA-256 hash chain:

    entry_hash = sha256(prev_hash || action || operator || detail || ts_iso)

where prev_hash is the entry_hash of the immediately preceding row (by id /
insertion order), or "" for the very first hashed row. Editing, deleting or
reordering any row breaks the recomputed chain at that point, which
``verify_chain`` reports. Legacy rows written before this feature carry NULL
hashes and are skipped until the first hashed row (the chain is verifiable
from there forward).
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from setra_cards.storage.models import ActionLog

# Field separator that cannot appear inside a serialized field below (NUL is
# rejected by SQLite TEXT columns, so no detail/operator string can contain
# it). This prevents ambiguity like ("a", "bc") hashing the same as ("ab",
# "c") — a classic length-extension / concatenation collision.
_SEP = "\x00"

# Canonical timestamp serialization: fixed ISO 8601 with microseconds so the
# hash is reproducible regardless of how the row is later loaded. datetime is
# stored without timezone (naive, local) throughout the schema.
_TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"


def _serialize_timestamp(ts: datetime) -> str:
    """Canonical, reproducible ISO representation of a row timestamp."""
    return ts.strftime(_TS_FORMAT)


def compute_entry_hash(
    prev_hash: str,
    action: str,
    operator: str,
    detail: str | None,
    timestamp: datetime,
) -> str:
    """Pure hash of a single log entry given its predecessor's hash.

    None ``operator``/``detail`` are normalized to "" so legacy/partial rows
    hash deterministically. Fields are NUL-separated to avoid concatenation
    ambiguity.
    """
    parts = (
        prev_hash or "",
        action or "",
        operator or "",
        detail or "",
        _serialize_timestamp(timestamp),
    )
    payload = _SEP.join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _last_entry_hash(session: Session) -> str:
    """entry_hash of the most recently inserted row, or "" if none/legacy.

    Ordered by id (insertion order) — NOT timestamp, which can collide between
    two actions logged in the same microsecond.
    """
    last = session.execute(
        select(ActionLog).order_by(ActionLog.id.desc()).limit(1)
    ).scalars().first()
    if last is None or last.entry_hash is None:
        return ""
    return last.entry_hash


def log(
    session: Session,
    action: str,
    operator: str,
    detail: str | None = None,
) -> ActionLog:
    """Append an audit row, linking it into the tamper-evident hash chain.

    Signature and return value are unchanged (returns the persisted row).
    The timestamp is materialized here so the value that is hashed is exactly
    the value that is stored (relying on the column default would hash a
    different/absent value).
    """
    prev_hash = _last_entry_hash(session)
    timestamp = datetime.now()
    entry_hash = compute_entry_hash(prev_hash, action, operator, detail, timestamp)
    entry = ActionLog(
        action=action,
        operator=operator,
        detail=detail,
        timestamp=timestamp,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
    )
    session.add(entry)
    session.commit()
    return entry


def verify_chain(session: Session) -> tuple[bool, int | None]:
    """Recompute the hash chain and detect any edited/deleted/reordered row.

    Returns ``(ok, first_broken_id)``. ``ok`` is True when every hashed row
    links correctly; ``first_broken_id`` is the id of the first row whose
    stored hash does not match the recomputation (or None when ok).

    Legacy rows with NULL ``entry_hash`` written before this feature are
    skipped until the first hashed row, so they never false-positive. The
    expected ``prev_hash`` for the first hashed row is "".
    """
    rows = session.execute(
        select(ActionLog).order_by(ActionLog.id.asc())
    ).scalars().all()

    expected_prev = ""
    started = False
    for row in rows:
        if row.entry_hash is None:
            if started:
                # A hashed row followed by a NULL-hash row means the hashed
                # row was deleted/cleared in the middle of the chain.
                return False, row.id
            # Legacy prefix: skip until the first hashed row.
            continue

        started = True
        recomputed = compute_entry_hash(
            row.prev_hash or "",
            row.action,
            row.operator,
            row.detail,
            row.timestamp,
        )
        # Two independent checks:
        #  - row.entry_hash matches its own contents (content not edited), and
        #  - row.prev_hash matches the previous row's entry_hash (link intact:
        #    detects deletion/reordering of an earlier row).
        if recomputed != row.entry_hash or (row.prev_hash or "") != expected_prev:
            return False, row.id
        expected_prev = row.entry_hash

    return True, None


def recent(session: Session, limit: int = 200) -> list[ActionLog]:
    return session.execute(
        select(ActionLog).order_by(ActionLog.timestamp.desc()).limit(limit)
    ).scalars().all()
