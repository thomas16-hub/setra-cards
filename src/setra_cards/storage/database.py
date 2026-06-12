"""SQLite connection + install path resolution.

All data lives in %LOCALAPPDATA%\\SETRA\\SetraCARDS\\setra-cards.db on Windows
(via platformdirs for correct cross-platform behavior). The first run creates
the directory and the empty tables.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from platformdirs import user_data_dir
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from setra_cards.storage.models import Base

logger = logging.getLogger(__name__)

APP_NAME = "SetraCARDS"
APP_AUTHOR = "SETRA"

# Settings-table flag that marks the one-shot room_no_id backfill.
# Values: absent = never evaluated, "0" = pending (started, not finished),
# "1" = done (never run again).
_BACKFILL_FLAG_KEY = "room_no_id_backfill_done"

_ROOMS_UNIQUE_INDEX = "uq_rooms_building_floor_room_no_id"
_ROOMS_UNIQUE_COLUMNS = ("building", "floor", "room_no_id")


def app_data_dir() -> Path:
    """Persistent data folder. Windows: %LOCALAPPDATA%\\SETRA\\SetraCARDS."""
    p = Path(user_data_dir(APP_NAME, APP_AUTHOR, roaming=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return app_data_dir() / "setra-cards.db"


def install_root() -> Path:
    """Folder where the .exe lives (for bundled assets)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[3]


_engine = None
_SessionFactory: sessionmaker[Session] | None = None


def init_db() -> sessionmaker[Session]:
    """Create engine + tables on first call, return the session factory.

    Any failure (corrupt DB, broken migration, schema drift) is logged with
    the full traceback and DB path, then re-raised so the caller can surface
    a clear startup error instead of dying silently.
    """
    global _engine, _SessionFactory
    if _SessionFactory is not None:
        return _SessionFactory

    path = db_path()
    engine = create_engine(
        f"sqlite:///{path}",
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        # foreign_keys: enforce FK integrity (off by default in SQLite).
        # journal_mode=WAL: safer concurrent access (watchdog thread +
        # handlers) and live-copy backups.
        # busy_timeout: wait up to 5s on a locked DB instead of failing.
        dbapi_conn.execute("PRAGMA foreign_keys=ON")
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA busy_timeout=5000")

    try:
        _log_quick_check(engine, path)
        Base.metadata.create_all(engine)
        _run_migrations(engine)
    except Exception as exc:
        logger.exception("Database initialization failed (db=%s)", path)
        engine.dispose()
        # User-facing message (may reach the operator's screen): Spanish.
        raise RuntimeError(
            f"No se pudo inicializar la base de datos ({path}). "
            f"Detalle tecnico: {exc}"
        ) from exc

    _engine = engine
    _SessionFactory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return _SessionFactory


def _log_quick_check(engine, path: Path) -> None:
    """Run SQLite integrity quick_check and log the result.

    A failed check is logged loudly but does not abort startup by itself:
    quick_check can report localized corruption while the DB is still
    readable; create_all/migrations will fail on their own if it is not.
    """
    with engine.connect() as conn:
        row = conn.execute(text("PRAGMA quick_check")).fetchone()
    result = row[0] if row else "no result"
    if result == "ok":
        logger.debug("SQLite quick_check ok (db=%s)", path)
    else:
        logger.error("SQLite quick_check FAILED (db=%s): %s", path, result)


def _run_migrations(engine) -> None:
    """Apply additive migrations to existing DBs (ADD COLUMN IF NOT EXISTS),
    one-shot backfills, the rooms unique index, and a schema drift guard."""
    migrations = [
        ("operators", "must_change_pin", "INTEGER NOT NULL DEFAULT 1"),
        ("operators", "role", "TEXT NOT NULL DEFAULT 'frontdesk'"),
        ("card_log", "card_type_byte", "INTEGER NOT NULL DEFAULT 0"),
        ("card_log", "room_id", "INTEGER REFERENCES rooms(id)"),
        ("card_log", "guest_id", "INTEGER REFERENCES guests(id)"),
        ("rooms", "room_no_id", "INTEGER NOT NULL DEFAULT 1"),
        # Tamper-evident audit hash chain. Nullable: existing rows keep NULL
        # hashes; the chain is verifiable from the first hashed row forward.
        ("action_log", "prev_hash", "VARCHAR(64)"),
        ("action_log", "entry_hash", "VARCHAR(64)"),
    ]
    with engine.connect() as conn:
        # Snapshot BEFORE the ALTERs: the backfill must only ever run when
        # this startup is the one that added rooms.room_no_id.
        room_no_id_existed = _column_exists(conn, "rooms", "room_no_id")

        for table, column, col_def in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))
                conn.commit()
            except Exception as exc:
                conn.rollback()
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue
                logger.warning(
                    "Migration ALTER TABLE %s ADD COLUMN %s failed: %s",
                    table, column, exc,
                )

        _run_room_no_id_backfill_once(conn, column_just_added=not room_no_id_existed)
        _ensure_rooms_unique_index(conn)
        _verify_schema(conn)


def _column_exists(conn, table: str, column: str) -> bool:
    rows = conn.execute(text(f"PRAGMA table_info('{table}')")).fetchall()
    return any(row[1] == column for row in rows)


def _get_setting(conn, key: str) -> str | None:
    row = conn.execute(
        text("SELECT value FROM settings WHERE key = :k"), {"k": key}
    ).fetchone()
    return row[0] if row else None


def _set_setting(conn, key: str, value: str) -> None:
    conn.execute(
        text(
            "INSERT INTO settings (key, value, updated_at) "
            "VALUES (:k, :v, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated_at = excluded.updated_at"
        ),
        {"k": key, "v": value},
    )
    conn.commit()


def _run_room_no_id_backfill_once(conn, *, column_just_added: bool) -> None:
    """One-shot room_no_id backfill, gated by a settings-table flag.

    The backfill renumbers rooms 1, 2, 3... within each (building, floor)
    by id order. That is only correct when every room still has the ALTER
    default (i.e. the column was created THIS startup). Running it on later
    startups would silently renumber legitimately-assigned rooms and break
    already-issued guest cards — so once the flag is '1' we NEVER touch
    room_no_id again.
    """
    flag = _get_setting(conn, _BACKFILL_FLAG_KEY)
    if flag == "1":
        return

    if flag is None and not column_just_added:
        # Column pre-existed (fresh install via create_all, or an older
        # version already migrated it): values are authoritative, never
        # backfill. Persist the flag so this decision is final.
        _set_setting(conn, _BACKFILL_FLAG_KEY, "1")
        return

    # flag == "0" (a previous attempt crashed mid-way; the backfill is
    # deterministic by id order, so re-running converges to the same state)
    # or flag is None with the column just added.
    _set_setting(conn, _BACKFILL_FLAG_KEY, "0")
    _backfill_room_no_id(conn)
    _set_setting(conn, _BACKFILL_FLAG_KEY, "1")


def _backfill_room_no_id(conn) -> None:
    """Assign room_no_id = 1, 2, 3... within each (building, floor) by id.

    Logs every rewritten room (old -> new) so an unexpected renumbering is
    visible in the logs instead of silent.
    """
    rows = conn.execute(
        text(
            "SELECT id, display_number, building, floor, room_no_id "
            "FROM rooms ORDER BY building, floor, id"
        )
    ).fetchall()

    counters: dict[tuple[int, int], int] = {}
    changes: list[tuple[int, str, int, int, int, int]] = []
    for row in rows:
        key = (row.building, row.floor)
        new_no = counters.get(key, 0) + 1
        counters[key] = new_no
        if new_no != row.room_no_id:
            changes.append(
                (row.id, row.display_number, row.building, row.floor,
                 row.room_no_id, new_no)
            )

    for room_id, display, building, floor, old_no, new_no in changes:
        conn.execute(
            text("UPDATE rooms SET room_no_id = :n WHERE id = :i"),
            {"n": new_no, "i": room_id},
        )
        logger.warning(
            "room_no_id backfill: room id=%s display=%s "
            "(building=%s, floor=%s) %s -> %s",
            room_id, display, building, floor, old_no, new_no,
        )
    conn.commit()
    if changes:
        logger.warning("room_no_id backfill rewrote %s room(s)", len(changes))


def _ensure_rooms_unique_index(conn) -> None:
    """Enforce UNIQUE(building, floor, room_no_id) on existing DBs.

    This is the exact triple the lock validates: a collision means two rooms
    would open with the same guest card. If collisions already exist we log
    them loudly and skip the index (crashing here would brick the app);
    otherwise the unique index is created.
    """
    if _has_unique_rooms_index(conn):
        return

    collisions = conn.execute(
        text(
            "SELECT building, floor, room_no_id, COUNT(*) AS n FROM rooms "
            "GROUP BY building, floor, room_no_id HAVING n > 1"
        )
    ).fetchall()
    if collisions:
        for c in collisions:
            logger.error(
                "rooms COLLISION: %s rooms share (building=%s, floor=%s, "
                "room_no_id=%s) — guest cards for these rooms open EACH "
                "OTHER's locks. Unique index NOT created; fix the duplicate "
                "room_no_id values.",
                c.n, c.building, c.floor, c.room_no_id,
            )
        return

    conn.execute(
        text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_ROOMS_UNIQUE_INDEX} "
            f"ON rooms ({', '.join(_ROOMS_UNIQUE_COLUMNS)})"
        )
    )
    conn.commit()


def _has_unique_rooms_index(conn) -> bool:
    """True if any UNIQUE index already covers exactly (building, floor,
    room_no_id) — e.g. the table-level constraint on fresh DBs."""
    indexes = conn.execute(text("PRAGMA index_list('rooms')")).fetchall()
    for idx in indexes:
        # index_list row: (seq, name, unique, origin, partial)
        if not idx[2]:
            continue
        cols = tuple(
            info[2]
            for info in conn.execute(
                text(f"PRAGMA index_info('{idx[1]}')")
            ).fetchall()
        )
        if cols == _ROOMS_UNIQUE_COLUMNS:
            return True
    return False


def _verify_schema(conn) -> None:
    """Schema drift guard: every column the ORM expects must exist.

    Catches a forgotten future migration at startup with a clear error
    instead of a cryptic OperationalError later at query time.
    """
    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        rows = conn.execute(text(f"PRAGMA table_info('{table.name}')")).fetchall()
        existing = {row[1] for row in rows}
        missing.extend(
            f"{table.name}.{col.name}"
            for col in table.columns
            if col.name not in existing
        )
    if missing:
        raise RuntimeError(
            "Database schema is missing expected columns: "
            + ", ".join(missing)
            + ". A migration entry is likely missing in _run_migrations()."
        )


def get_session() -> Session:
    if _SessionFactory is None:
        init_db()
    assert _SessionFactory is not None
    return _SessionFactory()
