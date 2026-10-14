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
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from serial.tools import list_ports

from setra_cards.core.app_state import AppState
from setra_cards.encoder.driver import EncoderDriver, detect_encoder_port

logger = logging.getLogger(__name__)

# --- Service-level emission lock --------------------------------------------
#
# `EncoderDriver._lock` only serializes INDIVIDUAL serial commands. A full card
# write is ~20 commands; two operations (a double-click that slips past a UI
# busy flag, or the watchdog/connect_port tearing down the port mid-write) can
# interleave commands or close the port between commands, corrupting a card
# that still ACKs every block. That is the silent "writes OK but won't open"
# failure class.
#
# `_emission_lock` serializes whole card OPERATIONS instead. It is the OUTER
# lock; `EncoderDriver._lock` is the INNER lock.
#
# LOCK ORDERING (must never be violated to avoid deadlock):
#     _emission_lock  (outer, whole operation)
#       -> driver._lock  (inner, single command)
# NEVER acquire `_emission_lock` while holding `driver._lock`. The watchdog
# tick must use a NON-BLOCKING try-acquire and bail when busy — it must never
# block on this lock.
#
# RLock (not Lock) because `connect_port` is reentered by the watchdog tick:
# the watchdog acquires the lock, then calls `connect_port`, which acquires it
# again on the SAME thread. A plain Lock would self-deadlock there.
_emission_lock = threading.RLock()


@contextmanager
def emission_transaction(blocking: bool = True, timeout: float = -1.0) -> Iterator[bool]:
    """Hold the service-level emission lock for a whole encoder operation.

    Yields ``True`` if the lock was acquired (caller owns the encoder for the
    duration of the ``with`` block), ``False`` if a non-blocking acquire failed
    because another operation is already in flight.

    blocking=False  -> fail-fast (emit / blank should never wait behind another
                       in-flight write; return the Spanish busy message instead).
    blocking=True   -> wait for the in-flight operation to finish (read may wait
                       briefly; connect/disconnect must be excluded during a
                       write).

    The lock is released even if the body raises, so a failing operation can
    never leave the encoder permanently locked (no deadlock on normal completion
    or on error).
    """
    acquired = _emission_lock.acquire(blocking=blocking, timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired:
            _emission_lock.release()

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
    """Try to connect the encoder on `port`. Closes the previous one if any.

    Holds the emission lock for the whole connect so the port can never be
    opened/swapped while a card operation is mid-write. The acquire is
    reentrant: when the watchdog tick (which already holds the lock) calls this,
    the same-thread RLock re-acquire succeeds instead of deadlocking.
    """
    with emission_transaction(blocking=True):
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
    # Hold the emission lock so the port is never closed between commands of an
    # in-flight write. Reentrant for the same thread (RLock).
    with emission_transaction(blocking=True):
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

# Hint-port rescan cadence while the last-known port keeps failing: first
# failure, then every 6th tick (30 s at the default 5 s interval).
RESCAN_EVERY_FAILED_TICKS = 6
_failed_ticks = {"n": 0}


def _watchdog_tick(state: AppState, last_port: str | None) -> str | None:
    """One reconnect check. Returns the port to remember for the next tick.

    Preserves the last-known port even when a reconnect attempt fails
    (connect_port clears state.encoder_port on failure), so the watchdog keeps
    retrying the same port instead of falling back to a blind scan.

    NON-BLOCKING on the emission lock: if a card operation is mid-write, the
    tick bails immediately (returns last_port) rather than tearing down the port
    underneath the in-flight write. It must NEVER block on the lock — blocking
    here would stall the watchdog thread behind every long emission.
    """
    try:
        # Try-acquire the OUTER emission lock; skip the whole tick if busy.
        with emission_transaction(blocking=False) as acquired:
            if not acquired:
                logger.debug("Watchdog: emission in progress — skipping tick")
                return last_port
            enc = state.encoder
            if enc is not None and enc.is_connected:
                _failed_ticks["n"] = 0
                return state.encoder_port or last_port
            _failed_ticks["n"] += 1
            port = state.encoder_port or last_port
            ok = False
            # Only re-open the known port when Windows lists it again: listing
            # does no I/O, so an unplugged encoder costs nothing, and a replug
            # on the same socket reconnects on the next 5 s tick.
            if port and port in {p.device for p in list_com_ports()}:
                logger.info("Watchdog: retrying %s", port)
                ok, _msg = connect_port(state, port)
            if not ok and _failed_ticks["n"] % RESCAN_EVERY_FAILED_TICKS == 1:
                # A replug into another USB socket comes back as another COM
                # number. The scan opens and beeps every CP210x/CH340/FTDI
                # device (fiscal printers, scales), so it is throttled to the
                # first failure and then every 30 s, with or without a known port.
                logger.info("Watchdog: rescanning hint ports")
                ok, _msg = auto_connect(state, hint_only=True)
            if ok:
                _failed_ticks["n"] = 0
            return state.encoder_port or port
    except Exception as exc:
        logger.debug("Watchdog exception: %s", exc)
        return last_port


def start_watchdog(state: AppState, interval: float = 5.0) -> None:
    """Check every `interval` seconds and reconnect if the encoder dropped.

    5 s: a connected tick is only a non-blocking port probe, and an unplugged
    encoder used to look "connected" in the UI for up to 30 s.
    """
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
