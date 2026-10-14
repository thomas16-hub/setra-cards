"""Estado global de la app — operador logueado, encoder activo, config del hotel."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from setra_cards.cards.crypto import DEFAULT_SIGNATURE, SystemSignature
from setra_cards.storage.database import app_data_dir, install_root

logger = logging.getLogger(__name__)


@dataclass
class HotelConfig:
    """Config estatica del hotel (firma, DES key) — viene con el instalador."""

    name: str
    short_name: str
    city: str
    signature: SystemSignature
    des_key: bytes
    sector: int = 11
    checkout_time: str = "12:00"
    # True when no readable hotel.json was found and the Demo values are in
    # use. Not blocking: the Demo signature (95 B8) is the one every SETRA
    # hotel uses, so a PC missing hotel.json still writes working cards, but
    # the operator must see that the hotel identity is not configured.
    is_fallback: bool = False


@dataclass
class OperatorSession:
    """Sesion activa — id y nombre del operador logueado."""

    id: int
    name: str
    role: str
    must_change_pin: bool


class AppState:
    """Singleton de estado compartido entre vistas de Flet."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.operator: OperatorSession | None = None
        self.hotel: HotelConfig = _load_hotel_config()
        # Encoder se setea desde services.encoder tras detect
        self.encoder = None  # EncoderDriver | None
        self.encoder_port: str | None = None

    def login(self, session: OperatorSession) -> None:
        with self._lock:
            self.operator = session

    def logout(self) -> None:
        with self._lock:
            self.operator = None

    def set_encoder(self, enc, port: str) -> None:
        with self._lock:
            self.encoder = enc
            self.encoder_port = port

    def clear_encoder(self) -> None:
        with self._lock:
            self.encoder = None
            self.encoder_port = None

    @property
    def is_logged_in(self) -> bool:
        return self.operator is not None


def _demo_config() -> HotelConfig:
    """Default config (dev / not installed) — Demo signature, never a real hotel."""
    return HotelConfig(
        name="Hotel Demo",
        short_name="Demo",
        city="Local",
        signature=DEFAULT_SIGNATURE,
        des_key=b"god\x00\x00\x00\x00\x00",
        is_fallback=True,
    )


def _load_hotel_config() -> HotelConfig:
    """Carga hotel.json del directorio de instalacion o AppData."""
    candidates = [
        install_root() / "hotel.json",
        app_data_dir() / "hotel.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            return _parse_hotel_json(path)
        except Exception as exc:
            # Loud: a corrupt hotel.json means cards would be written with the
            # WRONG hotel identity. Surface it in the log and keep trying.
            logger.error("hotel.json corrupto en %s: %s", path, exc)
    logger.warning(
        "hotel.json NO encontrado (o ilegible) en %s — usando configuración Demo",
        " / ".join(str(p) for p in candidates),
    )
    return _demo_config()


def _parse_hotel_json(path: Path) -> HotelConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    sig = data["signature"]
    signature = SystemSignature(
        sig_hi=int(sig["sig_hi"]),
        sig_lo=int(sig["sig_lo"]),
        prefix=int(sig["prefix"]),
    )
    des_raw = str(data.get("des_key", "god"))
    if des_raw == "god":
        # Exact literal only — anything else must be valid hex. The old
        # startswith("god") silently mapped typos like "god1234" to the
        # default key.
        des_key = b"god\x00\x00\x00\x00\x00"
    else:
        try:
            des_key = bytes.fromhex(des_raw)
        except ValueError as exc:
            raise ValueError(
                f"des_key invalido en {path}: debe ser 'god' o hex de 8 bytes"
            ) from exc
    if len(des_key) != 8:
        raise ValueError(f"des_key must be 8 bytes, got {len(des_key)}")
    return HotelConfig(
        name=data["name"],
        short_name=data.get("short_name", data["name"]),
        city=data.get("city", ""),
        signature=signature,
        des_key=des_key,
        sector=int(data.get("sector", 11)),
        checkout_time=data.get("checkout_time", "12:00"),
    )


# Singleton global — un solo estado por proceso
_APP_STATE: AppState | None = None


def get_state() -> AppState:
    global _APP_STATE
    if _APP_STATE is None:
        _APP_STATE = AppState()
    return _APP_STATE
