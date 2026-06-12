"""Tests de migraciones de DB: backfill one-shot de room_no_id, indice unico
de rooms, guard de schema drift y arranque protegido (init_db)."""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from setra_cards.storage import database
from setra_cards.storage.database import (
    _BACKFILL_FLAG_KEY,
    _get_setting,
    _has_unique_rooms_index,
    _run_migrations,
)
from setra_cards.storage.models import Base

LEGACY_ROOMS_NO_ROOM_NO_ID = """
    CREATE TABLE rooms (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        sequential_id INTEGER UNIQUE,
        display_number VARCHAR(20) UNIQUE,
        building INTEGER NOT NULL DEFAULT 1,
        floor INTEGER NOT NULL DEFAULT 1,
        state VARCHAR(30) NOT NULL DEFAULT 'limpia',
        notes VARCHAR(500)
    )
"""

LEGACY_ROOMS_WITH_ROOM_NO_ID = """
    CREATE TABLE rooms (
        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
        sequential_id INTEGER UNIQUE,
        display_number VARCHAR(20) UNIQUE,
        building INTEGER NOT NULL DEFAULT 1,
        floor INTEGER NOT NULL DEFAULT 1,
        room_no_id INTEGER NOT NULL DEFAULT 1,
        state VARCHAR(30) NOT NULL DEFAULT 'limpia',
        notes VARCHAR(500)
    )
"""


def _legacy_engine(rooms_ddl: str):
    """Engine con tabla rooms legacy ya creada; create_all agrega el resto."""
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as conn:
        conn.execute(text(rooms_ddl))
        conn.commit()
    Base.metadata.create_all(engine)
    return engine


def _insert_legacy_room(conn, seq: int, display: str, building: int, floor: int,
                        room_no_id: int | None = None) -> None:
    if room_no_id is None:
        conn.execute(text(
            "INSERT INTO rooms (sequential_id, display_number, building, floor, state) "
            "VALUES (:s, :d, :b, :f, 'limpia')"
        ), {"s": seq, "d": display, "b": building, "f": floor})
    else:
        conn.execute(text(
            "INSERT INTO rooms (sequential_id, display_number, building, floor, room_no_id, state) "
            "VALUES (:s, :d, :b, :f, :n, 'limpia')"
        ), {"s": seq, "d": display, "b": building, "f": floor, "n": room_no_id})
    conn.commit()


def _room_no_ids(engine) -> dict[str, int]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT display_number, room_no_id FROM rooms ORDER BY id")
        ).fetchall()
    return {r.display_number: r.room_no_id for r in rows}


# --- Backfill one-shot ---


def test_backfill_runs_when_column_just_added():
    engine = _legacy_engine(LEGACY_ROOMS_NO_ROOM_NO_ID)
    with engine.connect() as conn:
        _insert_legacy_room(conn, 1, "101", 1, 1)
        _insert_legacy_room(conn, 2, "102", 1, 1)
        _insert_legacy_room(conn, 3, "201", 1, 2)
        _insert_legacy_room(conn, 4, "202", 1, 2)

    _run_migrations(engine)

    assert _room_no_ids(engine) == {"101": 1, "102": 2, "201": 1, "202": 2}
    with engine.connect() as conn:
        assert _get_setting(conn, _BACKFILL_FLAG_KEY) == "1"


def test_backfill_never_reruns_on_next_startup():
    engine = _legacy_engine(LEGACY_ROOMS_NO_ROOM_NO_ID)
    with engine.connect() as conn:
        _insert_legacy_room(conn, 1, "101", 1, 1)
        _insert_legacy_room(conn, 2, "102", 1, 1)
    _run_migrations(engine)

    # Simulate a legitimate manual reassignment after the first migration.
    with engine.connect() as conn:
        conn.execute(text("UPDATE rooms SET room_no_id = 7 WHERE display_number = '102'"))
        conn.commit()

    _run_migrations(engine)  # second startup
    _run_migrations(engine)  # third startup

    assert _room_no_ids(engine) == {"101": 1, "102": 7}


def test_backfill_skipped_when_column_pre_existed():
    engine = _legacy_engine(LEGACY_ROOMS_WITH_ROOM_NO_ID)
    with engine.connect() as conn:
        # Values intentionally NOT in id order: must remain untouched.
        _insert_legacy_room(conn, 1, "101", 1, 1, room_no_id=5)
        _insert_legacy_room(conn, 2, "102", 1, 1, room_no_id=2)

    _run_migrations(engine)

    assert _room_no_ids(engine) == {"101": 5, "102": 2}
    with engine.connect() as conn:
        assert _get_setting(conn, _BACKFILL_FLAG_KEY) == "1"


def test_backfill_logs_rewrites(caplog):
    engine = _legacy_engine(LEGACY_ROOMS_NO_ROOM_NO_ID)
    with engine.connect() as conn:
        _insert_legacy_room(conn, 1, "101", 1, 1)
        _insert_legacy_room(conn, 2, "102", 1, 1)

    with caplog.at_level(logging.WARNING, logger="setra_cards.storage.database"):
        _run_migrations(engine)

    assert any("room_no_id backfill" in rec.message for rec in caplog.records)


# --- Indice unico (building, floor, room_no_id) ---


def test_unique_index_created_and_enforced_on_migrated_db():
    engine = _legacy_engine(LEGACY_ROOMS_NO_ROOM_NO_ID)
    with engine.connect() as conn:
        _insert_legacy_room(conn, 1, "101", 1, 1)
    _run_migrations(engine)

    with engine.connect() as conn:
        assert _has_unique_rooms_index(conn)
        with pytest.raises(IntegrityError):
            _insert_legacy_room(conn, 2, "101-bis", 1, 1, room_no_id=1)


def test_unique_constraint_on_fresh_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    _run_migrations(engine)

    with engine.connect() as conn:
        assert _has_unique_rooms_index(conn)
        _insert_legacy_room(conn, 1, "101", 1, 1, room_no_id=1)
        with pytest.raises(IntegrityError):
            _insert_legacy_room(conn, 2, "102", 1, 1, room_no_id=1)


def test_collisions_skip_index_and_log_loudly(caplog):
    engine = _legacy_engine(LEGACY_ROOMS_WITH_ROOM_NO_ID)
    with engine.connect() as conn:
        _insert_legacy_room(conn, 1, "101", 1, 1, room_no_id=1)
        _insert_legacy_room(conn, 2, "102", 1, 1, room_no_id=1)  # collision

    with caplog.at_level(logging.ERROR, logger="setra_cards.storage.database"):
        _run_migrations(engine)  # must NOT raise

    with engine.connect() as conn:
        assert not _has_unique_rooms_index(conn)
    assert any("COLLISION" in rec.message for rec in caplog.records)


# --- Schema drift guard ---


def test_schema_drift_guard_raises_on_missing_column():
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.connect() as conn:
        # Legacy guests table missing the 'email' column (not covered by
        # any ALTER migration) -> must fail fast naming table.column.
        conn.execute(text("""
            CREATE TABLE guests (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(200) NOT NULL,
                document VARCHAR(50),
                phone VARCHAR(40),
                notes VARCHAR(500),
                created_at DATETIME,
                updated_at DATETIME
            )
        """))
        conn.commit()
    Base.metadata.create_all(engine)

    with pytest.raises(RuntimeError, match="guests.email"):
        _run_migrations(engine)


# --- init_db protegido + pragmas por conexion ---


@pytest.fixture
def _reset_db_globals(monkeypatch):
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionFactory", None)
    yield
    if database._engine is not None:
        database._engine.dispose()
    database._engine = None
    database._SessionFactory = None


def test_init_db_sets_wal_and_busy_timeout(tmp_path, monkeypatch, _reset_db_globals):
    monkeypatch.setattr(database, "db_path", lambda: tmp_path / "test.db")

    factory = database.init_db()
    with factory() as session:
        assert session.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert session.execute(text("PRAGMA busy_timeout")).scalar() == 5000
        assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_init_db_raises_clear_error_on_corrupt_db(tmp_path, monkeypatch,
                                                  _reset_db_globals, caplog):
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"this is not a sqlite database " * 40)
    monkeypatch.setattr(database, "db_path", lambda: bad)

    with caplog.at_level(logging.ERROR, logger="setra_cards.storage.database"):
        with pytest.raises(RuntimeError, match="No se pudo inicializar la base de datos"):
            database.init_db()

    assert str(bad) in "".join(rec.getMessage() for rec in caplog.records)
    assert database._SessionFactory is None
