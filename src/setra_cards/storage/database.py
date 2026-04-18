"""Conexion a SQLite + resolucion de rutas de instalacion.

Todos los datos viven en %LOCALAPPDATA%\\SetraCARDS\\data\\ en Windows
(via platformdirs para cross-platform correcto). La primera ejecucion
crea el directorio y las tablas vacias.
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


def app_data_dir() -> Path:
    """Carpeta persistente de datos. Windows: %LOCALAPPDATA%\\SetraCARDS."""
    p = Path(user_data_dir(APP_NAME, APP_AUTHOR, roaming=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return app_data_dir() / "setra-cards.db"


def install_root() -> Path:
    """Carpeta donde vive el .exe (para assets empaquetados)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[3]


_engine = None
_SessionFactory: sessionmaker[Session] | None = None


def init_db() -> sessionmaker[Session]:
    """Crea engine+tablas la primera vez, devuelve la session factory."""
    global _engine, _SessionFactory
    if _SessionFactory is not None:
        return _SessionFactory

    path = db_path()
    _engine = create_engine(
        f"sqlite:///{path}",
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(_engine)
    _run_migrations(_engine)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _SessionFactory


def _run_migrations(engine) -> None:
    """Aplica columnas nuevas a DBs existentes (ADD COLUMN IF NOT EXISTS)."""
    migrations = [
        ("operators", "must_change_pin", "INTEGER NOT NULL DEFAULT 1"),
        ("operators", "role", "TEXT NOT NULL DEFAULT 'frontdesk'"),
        ("card_log", "card_type_byte", "INTEGER NOT NULL DEFAULT 0"),
        ("card_log", "room_id", "INTEGER REFERENCES rooms(id)"),
        ("card_log", "guest_id", "INTEGER REFERENCES guests(id)"),
    ]
    with engine.connect() as conn:
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


def get_session() -> Session:
    if _SessionFactory is None:
        init_db()
    assert _SessionFactory is not None
    return _SessionFactory()
