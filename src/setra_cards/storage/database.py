"""Conexion a SQLite + resolucion de rutas de instalacion.

Todos los datos viven en %LOCALAPPDATA%\\SetraCARDS\\data\\ en Windows
(via platformdirs para cross-platform correcto). La primera ejecucion
crea el directorio y las tablas vacias.
"""
from __future__ import annotations

import sys
from pathlib import Path

from platformdirs import user_data_dir
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from setra_cards.storage.models import Base

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
    Base.metadata.create_all(_engine)
    _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _SessionFactory


def get_session() -> Session:
    if _SessionFactory is None:
        init_db()
    assert _SessionFactory is not None
    return _SessionFactory()
