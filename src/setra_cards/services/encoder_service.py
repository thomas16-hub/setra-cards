"""Servicio de encoder — auto-detect, conectar, watchdog de reconexion.

Flujo:
1. `auto_connect()` escanea todos los puertos COM y hace handshake (beep)
   con cada uno. El primero que responda es el encoder.
2. Si falla, el usuario puede forzar un puerto especifico con
   `connect_port(port)`.
3. Un watchdog en thread chequea cada 30s y reconecta si se cae.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from serial.tools import list_ports

from setra_cards.core.app_state import AppState
from setra_cards.encoder.driver import EncoderDriver, detect_encoder_port

logger = logging.getLogger(__name__)

# Descripciones / fabricantes que tipicamente corresponden a chips USB-serial
# de encoders Locstar. Sirve como filtro "recomendado" en la UI.
ENCODER_HINTS = (
    "ch340", "ch341", "ch9102", "usb-serial",
    "cp210", "silicon labs",
    "ft232", "ftdi",
    "prolific",
)


@dataclass(frozen=True)
class ComPortInfo:
    device: str
    description: str
    manufacturer: str
    looks_like_encoder: bool


def list_com_ports() -> list[ComPortInfo]:
    """Devuelve todos los puertos COM del sistema con metadata."""
    out: list[ComPortInfo] = []
    for p in list_ports.comports():
        desc = (p.description or "").strip()
        mfr = (p.manufacturer or "").strip()
        blob = f"{desc} {mfr}".lower()
        hint = any(h in blob for h in ENCODER_HINTS)
        out.append(ComPortInfo(
            device=p.device,
            description=desc,
            manufacturer=mfr,
            looks_like_encoder=hint,
        ))
    # Puertos compatibles primero
    out.sort(key=lambda p: (not p.looks_like_encoder, p.device))
    return out


def connect_port(state: AppState, port: str) -> tuple[bool, str]:
    """Intenta conectar el encoder en `port`. Cierra el anterior si habia."""
    if state.encoder is not None:
        try:
            state.encoder.close()
        except Exception:
            pass
        state.clear_encoder()

    try:
        enc = EncoderDriver(port)
        enc.open()
        enc.beep()
    except Exception as exc:
        logger.warning("No se pudo conectar encoder en %s: %s", port, exc)
        return False, f"No se pudo abrir {port}: {exc}"

    state.set_encoder(enc, port)
    logger.info("Encoder conectado en %s", port)
    return True, f"Encoder conectado en {port}"


def auto_connect(state: AppState) -> tuple[bool, str]:
    """Escanea todos los puertos y usa el primero que responde al handshake."""
    port = detect_encoder_port()
    if not port:
        return False, "No se detecto ningun encoder conectado"
    return connect_port(state, port)


def disconnect(state: AppState) -> None:
    if state.encoder is not None:
        try:
            state.encoder.close()
        except Exception:
            pass
    state.clear_encoder()


_watchdog_thread: threading.Thread | None = None
_watchdog_stop = threading.Event()


def start_watchdog(state: AppState, interval: float = 30.0) -> None:
    """Chequea cada `interval` segundos y reconecta si el encoder cayo."""
    global _watchdog_thread
    if _watchdog_thread is not None and _watchdog_thread.is_alive():
        return

    _watchdog_stop.clear()

    def _loop() -> None:
        time.sleep(5)  # delay inicial para no pelearse con primera conexion
        while not _watchdog_stop.is_set():
            try:
                enc = state.encoder
                if enc is None or not enc.is_connected:
                    if state.encoder_port:
                        logger.info("Watchdog: reintentando %s", state.encoder_port)
                        connect_port(state, state.encoder_port)
                    else:
                        logger.info("Watchdog: auto-detectando")
                        auto_connect(state)
            except Exception as exc:
                logger.debug("Watchdog excepcion: %s", exc)
            _watchdog_stop.wait(interval)

    _watchdog_thread = threading.Thread(target=_loop, daemon=True, name="encoder-watchdog")
    _watchdog_thread.start()


def stop_watchdog() -> None:
    _watchdog_stop.set()
