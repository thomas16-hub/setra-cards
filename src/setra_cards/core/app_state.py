"""Estado global de la app — operador logueado, encoder activo, config del hotel."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from setra_cards.cards.crypto import DEFAULT_SIGNATURE, SystemSignature
from setra_cards.storage.database import app_data_dir, install_root


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


def _load_hotel_config() -> HotelConfig:
    """Carga hotel.json del directorio de instalacion o AppData."""
    candidates = [
        install_root() / "hotel.json",
        app_data_dir() / "hotel.json",
    ]
    for path in candidates:
        if path.exists():
            return _parse_hotel_json(path)
    # Fallback: config por defecto (dev / sin instalar)
    return HotelConfig(
        name="Hotel Demo",
        short_name="Demo",
        city="Local",
        signature=DEFAULT_SIGNATURE,
        des_key=b"god\x00\x00\x00\x00\x00",
    )


def _parse_hotel_json(path: Path) -> HotelConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    sig = data["signature"]
    signature = SystemSignature(
        sig_hi=int(sig["sig_hi"]),
        sig_lo=int(sig["sig_lo"]),
        prefix=int(sig["prefix"]),
    )
    des_raw = data.get("des_key", "god")
    if des_raw == "god" or des_raw.startswith("god"):
        des_key = b"god\x00\x00\x00\x00\x00"
    else:
        des_key = bytes.fromhex(des_raw)
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
