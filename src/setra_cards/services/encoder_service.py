"""Encoder service — auto-detect, connect, reconnect watchdog.

Flow:
1. `auto_connect()` scans COM ports and handshakes (beep) with each one.
   The first port that ACKs is the encoder.
2. If that fails, the user can force a specific port with `connect_port(port)`.
3. A watchdog thread checks every 30s and reconnects if the encoder dropped.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from serial.tools import list_ports

from setra_cards.core.app_state import AppState
from setra_cards.encoder.driver import EncoderDriver, detect_encoder_port

logger = logging.getLogger(__name__)

# Descriptions / manufacturers that typically correspond to USB-serial chips
# used by Locstar encoders. Also serves as the "recommended" filter in the UI.
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
    """Return every COM port on the system with metadata."""
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
    # Compatible-looking ports first
    out.sort(key=lambda p: (not p.looks_like_encoder, p.device))
    return out


def connect_port(state: AppState, port: str) -> tuple[bool, str]:
    """Try to connect the encoder on `port`. Closes the previous one if any."""
    if state.encoder is not None:
        try:
            state.encoder.close()
        except Exception:
            pass
        state.clear_encoder()

    try:
        enc = EncoderDriver(port)
        enc.open()
    except Exception as exc:
        logger.warning("Could not open encoder port %s: %s", port, exc)
        return False, f"No se pudo abrir {port}: {exc}"

    if not enc.beep():
        # Port opened but the device did not ACK the handshake — whatever is
        # on this COM port, it is not a Locstar encoder. Never store it.
        logger.warning("Port %s opened but did not ACK beep handshake", port)
        try:
            enc.close()
        except Exception:
            pass
        return False, f"{port} no responde como encoder Locstar"

    state.set_encoder(enc, port)
    # A fresh connection should be kept alive again by the watchdog.
    _watchdog_paused.clear()
    logger.info("Encoder connected on %s", port)
    return True, f"Encoder conectado en {port}"


def _hint_ports() -> list[str]:
    """COM ports whose USB descriptor matches a known encoder chip (ENCODER_HINTS)."""
    return [p.device for p in list_com_ports() if p.looks_like_encoder]


def auto_connect(state: AppState, hint_only: bool = False) -> tuple[bool, str]:
    """Scan COM ports and use the first one that ACKs the handshake.

    hint_only=True restricts the scan to ports whose USB-serial chip matches
    ENCODER_HINTS (CP210x, CH340, FTDI...). Used by the watchdog so it never
    sends bytes to arbitrary ports; the full scan stays reserved for explicit
    user action (hint_only=False, default).
    """
    ports: list[str] | None = None
    if hint_only:
        ports = _hint_ports()
        if not ports:
            return False, "No se detecto ningun encoder conectado"
    port = detect_encoder_port(ports=ports)
    if not port:
        return False, "No se detecto ningun encoder conectado"
    return connect_port(state, port)


def disconnect(state: AppState, pause_watchdog: bool = True) -> None:
    """Close the encoder and release the COM port.

    pause_watchdog=True (default) pauses the watchdog retries so the freshly
    released port is not re-grabbed within the next 30s. The watchdog resumes
    via `resume_watchdog()`, `start_watchdog()` or a successful manual connect.
    """
    if pause_watchdog:
        _watchdog_paused.set()
    if state.encoder is not None:
        try:
            state.encoder.close()
        except Exception:
            pass
    state.clear_encoder()


_watchdog_lock = threading.Lock()
_watchdog_thread: threading.Thread | None = None
_watchdog_stop = threading.Event()
_watchdog_paused = threading.Event()


def _watchdog_tick(state: AppState, last_port: str | None) -> str | None:
    """One reconnect check. Returns the port to remember for the next tick.

    Preserves the last-known port even when a reconnect attempt fails
    (connect_port clears state.encoder_port on failure), so the watchdog keeps
    retrying the same port instead of falling back to a blind scan.
    """
    try:
        enc = state.encoder
        if enc is not None and enc.is_connected:
            return state.encoder_port or last_port
        port = state.encoder_port or last_port
        if port:
            logger.info("Watchdog: retrying %s", port)
            connect_port(state, port)
        else:
            logger.info("Watchdog: auto-detecting (hint ports only)")
            auto_connect(state, hint_only=True)
        return state.encoder_port or port
    except Exception as exc:
        logger.debug("Watchdog exception: %s", exc)
        return last_port


def start_watchdog(state: AppState, interval: float = 30.0) -> None:
    """Check every `interval` seconds and reconnect if the encoder dropped."""
    global _watchdog_thread, _watchdog_stop
    with _watchdog_lock:
        thread = _watchdog_thread
        if thread is not None and thread.is_alive() and not _watchdog_stop.is_set():
            # Already running — just resume in case it was paused.
            _watchdog_paused.clear()
            return

        stop_event = threading.Event()
        _watchdog_stop = stop_event
        _watchdog_paused.clear()

        def _loop() -> None:
            # Initial delay so we don't race the first manual connection
            # (interruptible: stop_watchdog wakes it immediately).
            stop_event.wait(5)
            last_port: str | None = state.encoder_port
            while not stop_event.is_set():
                if not _watchdog_paused.is_set():
                    last_port = _watchdog_tick(state, last_port)
                stop_event.wait(interval)

        _watchdog_thread = threading.Thread(target=_loop, daemon=True, name="encoder-watchdog")
        _watchdog_thread.start()


def stop_watchdog(join_timeout: float = 1.0) -> None:
    """Stop the watchdog and wait (up to `join_timeout`) for the thread to die.

    After this, `start_watchdog()` can spawn a fresh watchdog — each thread
    owns its private stop Event, so an old thread shutting down can neither
    block nor kill the new one.
    """
    global _watchdog_thread
    with _watchdog_lock:
        _watchdog_stop.set()
        thread = _watchdog_thread
        _watchdog_thread = None
    if thread is not None and thread.is_alive():
        thread.join(timeout=join_timeout)


def pause_watchdog() -> None:
    """Pause reconnect retries without killing the thread."""
    _watchdog_paused.set()


def resume_watchdog() -> None:
    """Resume the watchdog reconnect retries."""
    _watchdog_paused.clear()
