"""Locstar encoder serial driver.

Thread-safe wrapper around pyserial for communicating with the Locstar MF-NK encoder.
Uses a threading lock to ensure only one serial operation at a time (safe for web server).
"""

import logging
import threading
import time
from contextlib import contextmanager
from typing import Generator

import serial

from setra_cards.encoder.protocol import (
    CMD_ANTICOLLISION,
    CMD_BEEP,
    CMD_HALT,
    CMD_REQUEST_ALL,
    CMD_SELECT,
    KEY_TYPE_A,
    CardDetection,
)

logger = logging.getLogger(__name__)

# Expected total RX frame lengths (<LEN> byte included) for known commands.
ACK_FRAME_LEN = 2            # <01> <status>
ANTICOLLISION_FRAME_LEN = 6  # <05> <status> <uid:4>
SELECT_FRAME_LEN = 3         # <02> <status> <sak>
READ_FRAME_LEN = 18          # <11> <status> <data:16>


class EncoderError(Exception):
    """Raised when encoder communication fails."""


def scan_encoder_ports(
    baud: int = 9600, timeout: float = 0.8, ports: list[str] | None = None
) -> list[str]:
    """Scan COM ports and return those that respond to a BEEP command.

    Sends CMD_BEEP (01 06) and checks for ACK (01 00).
    Probes all ports in parallel so total time is ~1 timeout instead of N×timeout.
    Returns list of matching port names in original port order.

    ports: optional explicit list of port names to probe. When None, every
    COM port on the system is probed (full scan — explicit user action only).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from serial.tools.list_ports import comports as list_comports

    from setra_cards.encoder.protocol import CMD_BEEP

    if ports is None:
        ports = [p.device for p in list_comports()]
    logger.info("Scanning %d COM ports for Locstar encoder: %s", len(ports), ports)

    if not ports:
        return []

    def _probe(port: str) -> str | None:
        try:
            with serial.Serial(port, baud, timeout=timeout) as s:
                s.reset_input_buffer()
                time.sleep(0.2)
                s.write(CMD_BEEP)
                s.flush()
                time.sleep(0.15)
                resp = s.read(s.in_waiting or 8)
                if len(resp) >= 2 and resp[0] == 0x01 and resp[1] == 0x00:
                    logger.info("Encoder detected on %s (resp: %s)", port, resp.hex(" "))
                    return port
                logger.debug("Port %s did not respond as encoder (resp: %s)", port, resp.hex(" ") if resp else "empty")
        except (serial.SerialException, OSError) as e:
            logger.debug("Port %s not available: %s", port, e)
        return None

    found: list[str] = []
    with ThreadPoolExecutor(max_workers=min(len(ports), 8)) as exe:
        futures = {exe.submit(_probe, p): p for p in ports}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                found.append(result)

    # Restore original port order
    return sorted(found, key=lambda p: ports.index(p))


def detect_encoder_port(
    baud: int = 9600, timeout: float = 0.8, ports: list[str] | None = None
) -> str | None:
    """Return the first COM port with a Locstar encoder, or None.

    ports: optional explicit list of port names to probe (None = all ports).
    """
    found = scan_encoder_ports(baud, timeout, ports=ports)
    return found[0] if found else None


class EncoderDriver:
    """Thread-safe serial driver for Locstar MF-NK encoder."""

    def __init__(self, port: str, baud: int = 9600, timeout: float = 0.8):
        self._port = port
        self._baud = baud
        self._timeout = timeout
        self._lock = threading.Lock()
        self._ser: serial.Serial | None = None

    def open(self) -> None:
        """Open serial port."""
        with self._lock:
            if self._ser and self._ser.is_open:
                return
            self._ser = serial.Serial(self._port, self._baud, timeout=self._timeout)
            self._ser.reset_input_buffer()
            time.sleep(0.3)
            logger.info("Encoder opened on %s @ %d", self._port, self._baud)

    def close(self) -> None:
        """Close serial port."""
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.close()
                self._ser = None
                logger.info("Encoder closed")

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    @property
    def is_connected(self) -> bool:
        """Check if the serial port is still open and responsive (thread-safe).

        Takes the driver lock so the probe never races a serial operation,
        and snapshots the handle so a concurrent close cannot None it mid-check.
        """
        with self._lock:
            ser = self._ser
            try:
                if ser is None or not ser.is_open:
                    return False
                ser.in_waiting  # noqa: B018 — probes port status
                return True
            except (serial.SerialException, OSError, AttributeError):
                return False

    def _send(self, data: bytes, wait: float = 0.08, read_size: int = 0) -> bytes:
        """Send bytes and read response. Must be called with lock held.

        Polls for response with short sleeps instead of fixed wait.
        Typical response arrives in 20-60ms.
        Raises EncoderError if port is closed or USB is unplugged mid-operation.

        read_size: if > 0, use blocking serial.read(read_size) instead of in_waiting.
        Use for commands with known fixed-length responses (e.g. read_block = 18 bytes).
        """
        if not self._ser or not self._ser.is_open:
            raise EncoderError("Serial port not open")
        try:
            self._ser.reset_input_buffer()
            self._ser.write(data)
            self._ser.flush()
            if read_size:
                # Blocking read: waits until exactly read_size bytes arrive (or timeout)
                resp = self._ser.read(read_size)
                if len(resp) < read_size:
                    # Short read (timeout): drain once so a late frame is not
                    # misread as the reply to the next command.
                    self._drain_input()
                return resp
            # Poll for response: check every 10ms, up to max wait
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if self._ser.in_waiting > 0:
                    # Settle long enough for multi-byte frames at 9600 baud:
                    # 18 bytes = 18.75ms, so 30ms guarantees full frame arrives.
                    time.sleep(0.030)
                    break
                time.sleep(0.01)
            available = self._ser.in_waiting
            if not available:
                # Timeout: fast-fail instead of a blocking read(256), and drain
                # once so a late frame cannot ACK the next command.
                self._drain_input()
                return b""
            return self._ser.read(available)
        except (serial.SerialException, OSError) as e:
            # USB unplugged mid-operation: close the port so is_connected returns False
            logger.warning("Serial port lost during operation: %s — closing port", e)
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
            raise EncoderError(f"Serial port lost: {e}") from e

    def _send_checked(self, name: str, data: bytes, wait: float = 0.08) -> bytes:
        """Send and log. Must be called with lock held."""
        resp = self._send(data, wait)
        tx_hex = data.hex(" ")
        rx_hex = resp.hex(" ") if resp else "(none)"
        logger.debug("  %s TX: %s RX: %s", name, tx_hex, rx_hex)
        return resp

    @staticmethod
    def _frame_valid(resp: bytes, expected_len: int | None = None) -> bool:
        """Validate RX framing: <LEN><STATUS>[DATA], LEN == bytes after it.

        A stale/late frame (e.g. a leftover 18-byte read reply) would fail this
        check instead of being misread as the ACK for the current command.
        expected_len: total frame length when the reply size is known.
        """
        if len(resp) < 2:
            return False
        if resp[0] != len(resp) - 1:
            return False
        if expected_len is not None and len(resp) != expected_len:
            return False
        return True

    @staticmethod
    def _ok(resp: bytes, expected_len: int | None = None) -> bool:
        return EncoderDriver._frame_valid(resp, expected_len) and resp[1] == 0x00

    @staticmethod
    def _write_ok(resp: bytes, expected_len: int | None = None) -> bool:
        """Write can return 01 00 or 01 01, both success (documented acks)."""
        return EncoderDriver._frame_valid(resp, expected_len) and resp[1] in (0x00, 0x01)

    # --- Public API (thread-safe) ---

    @contextmanager
    def transaction(self, blocking: bool = True, timeout: float = -1.0) -> Generator[bool, None, None]:
        """Hold the SERVICE-LEVEL emission lock for a whole multi-command operation.

        This is DIFFERENT from ``self._lock`` (the per-command lock taken inside
        each ``write_block`` / ``read_block`` / ``auth_sector`` call). ``_lock``
        serializes a single serial frame; ``transaction()`` serializes a whole
        card operation (~20 commands) so two operations can never interleave
        commands on the encoder.

        Yields ``True`` when the lock was acquired, ``False`` when a
        non-blocking acquire failed (another operation is already in flight).

        LOCK ORDERING — the emission lock is the OUTER lock, ``self._lock`` is
        the INNER lock. NEVER acquire the emission lock while holding
        ``self._lock``. This contextmanager only takes the OUTER lock; the inner
        per-command lock is taken later, inside each public driver method.

        Delegates to ``encoder_service._emission_lock`` (imported lazily to
        avoid the encoder_service -> driver import cycle) so there is a single
        service-level lock shared by card_service, connect/disconnect and the
        reconnect watchdog.
        """
        from setra_cards.services.encoder_service import emission_transaction

        with emission_transaction(blocking=blocking, timeout=timeout) as acquired:
            yield acquired

    def beep(self) -> bool:
        try:
            with self._lock:
                return self._ok(self._send_checked("Beep", CMD_BEEP), expected_len=ACK_FRAME_LEN)
        except (serial.SerialException, OSError, EncoderError) as e:
            logger.warning("Serial error in beep: %s", e)
            return False

    def detect_card(self) -> CardDetection | None:
        """Full detection: Request → Anticollision → Select."""
        try:
            with self._lock:
                r = self._send(CMD_REQUEST_ALL)
                if not self._ok(r) or len(r) < 4:
                    return None
                atqa = r[2:4]

                r = self._send(CMD_ANTICOLLISION)
                if not self._ok(r, expected_len=ANTICOLLISION_FRAME_LEN):
                    return None
                uid = r[2:6]

                r = self._send(CMD_SELECT)
                if not self._ok(r, expected_len=SELECT_FRAME_LEN):
                    return None
                sak = r[2]

                return CardDetection(atqa=atqa, uid=uid, sak=sak)
        except (serial.SerialException, OSError, EncoderError) as e:
            logger.warning("Serial error in detect_card: %s", e)
            try:
                if self._ser and self._ser.is_open:
                    self._ser.reset_input_buffer()
            except Exception:
                pass
            return None

    def _drain_input(self) -> None:
        """Best-effort discard of pending RX bytes (stale/late frames)."""
        try:
            if self._ser and self._ser.is_open:
                self._ser.reset_input_buffer()
        except Exception:
            pass

    def _reset_on_error(self) -> None:
        """Best-effort buffer flush after serial error."""
        self._drain_input()

    def load_key(self, key_type: int, sector: int, key: bytes) -> bool:
        try:
            with self._lock:
                cmd = bytes([0x09, 0x06, key_type, sector]) + key
                return self._ok(self._send(cmd), expected_len=ACK_FRAME_LEN)
        except (serial.SerialException, OSError, EncoderError) as e:
            logger.warning("Serial error in load_key: %s", e)
            self._reset_on_error()
            return False

    def auth_sector(self, sector: int, key_type: int = KEY_TYPE_A) -> bool:
        try:
            with self._lock:
                first_block = sector * 4
                cmd = bytes([0x04, 0x05, key_type, sector, first_block])
                return self._ok(self._send(cmd), expected_len=ACK_FRAME_LEN)
        except (serial.SerialException, OSError, EncoderError) as e:
            logger.warning("Serial error in auth_sector: %s", e)
            self._reset_on_error()
            return False

    def read_block(self, block: int) -> bytes | None:
        try:
            with self._lock:
                # Staged read: an error reply is only 2 bytes (<LEN><STATUS>),
                # so a blocking read(18) would stall a full serial timeout on
                # every failed read. Read LEN+STATUS first, then the remaining
                # 16 data bytes only on success (STATUS == 0x00 and LEN == 0x11).
                head = self._send(bytes([0x02, 0x08, block]), read_size=2)
                if len(head) < 2:
                    return None
                if head[1] != 0x00 or head[0] != READ_FRAME_LEN - 1:
                    # Error status or malformed length — discard trailing bytes.
                    logger.debug("read_block(%d) error frame: %s", block, head.hex(" "))
                    self._drain_input()
                    return None
                body = self._ser.read(READ_FRAME_LEN - 2) if self._ser else b""
                frame = head + body
                if self._ok(frame, expected_len=READ_FRAME_LEN) and len(frame) >= READ_FRAME_LEN:
                    return frame[2:READ_FRAME_LEN]
                logger.debug("read_block(%d) short/invalid frame: %s", block, frame.hex(" "))
                self._drain_input()
                return None
        except (serial.SerialException, OSError, EncoderError) as e:
            logger.warning("Serial error in read_block: %s", e)
            self._reset_on_error()
            return None

    def write_block(self, block: int, data: bytes) -> bool:
        if len(data) != 16:
            raise ValueError(f"Block data must be 16 bytes, got {len(data)}")
        try:
            with self._lock:
                cmd = bytes([0x12, 0x09, block]) + data
                return self._write_ok(self._send(cmd), expected_len=ACK_FRAME_LEN)
        except (serial.SerialException, OSError, EncoderError) as e:
            logger.warning("Serial error in write_block: %s", e)
            self._reset_on_error()
            return False

    def halt(self) -> bool:
        try:
            with self._lock:
                return self._ok(self._send(CMD_HALT), expected_len=ACK_FRAME_LEN)
        except (serial.SerialException, OSError, EncoderError) as e:
            logger.warning("Serial error in halt: %s", e)
            self._reset_on_error()
            return False


@contextmanager
def open_encoder(port: str, baud: int = 9600) -> Generator[EncoderDriver, None, None]:
    """Context manager for encoder lifecycle."""
    driver = EncoderDriver(port, baud)
    driver.open()
    try:
        yield driver
    finally:
        driver.close()
