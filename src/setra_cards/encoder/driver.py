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


class EncoderError(Exception):
    """Raised when encoder communication fails."""


def scan_encoder_ports(baud: int = 9600, timeout: float = 0.8) -> list[str]:
    """Scan all available COM ports and return those that respond to a BEEP command.

    Sends CMD_BEEP (01 06) and checks for ACK (01 00).
    Probes all ports in parallel so total time is ~1 timeout instead of N×timeout.
    Returns list of matching port names in original port order.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from serial.tools.list_ports import comports as list_comports

    from setra_cards.encoder.protocol import CMD_BEEP

    ports = [p.device for p in list_comports()]
    logger.info("Escaneando %d puertos COM para encoder Locstar: %s", len(ports), ports)

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
                    logger.info("Encoder detectado en %s (resp: %s)", port, resp.hex(" "))
                    return port
                logger.debug("Puerto %s no respondio como encoder (resp: %s)", port, resp.hex(" ") if resp else "vacio")
        except (serial.SerialException, OSError) as e:
            logger.debug("Puerto %s no disponible: %s", port, e)
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


def detect_encoder_port(baud: int = 9600, timeout: float = 0.8) -> str | None:
    """Return the first COM port with a Locstar encoder, or None."""
    found = scan_encoder_ports(baud, timeout)
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
            logger.info("Encoder abierto en %s @ %d", self._port, self._baud)

    def close(self) -> None:
        """Close serial port."""
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.close()
                self._ser = None
                logger.info("Encoder cerrado")

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    @property
    def is_connected(self) -> bool:
        """Check if the serial port is still open and responsive."""
        try:
            if self._ser is None or not self._ser.is_open:
                return False
            self._ser.in_waiting  # noqa: B018 — probes port status
            return True
        except (serial.SerialException, OSError):
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
                return self._ser.read(read_size)
            # Poll for response: check every 10ms, up to max wait
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if self._ser.in_waiting > 0:
                    # Settle long enough for multi-byte frames at 9600 baud:
                    # 18 bytes = 18.75ms, so 30ms guarantees full frame arrives.
                    time.sleep(0.030)
                    break
                time.sleep(0.01)
            return self._ser.read(self._ser.in_waiting or 256)
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
    def _ok(resp: bytes) -> bool:
        return len(resp) >= 2 and resp[1] == 0x00

    @staticmethod
    def _write_ok(resp: bytes) -> bool:
        """Write can return 01 00 or 01 01, both success."""
        return len(resp) >= 2 and resp[1] in (0x00, 0x01)

    # --- Public API (thread-safe) ---

    def beep(self) -> bool:
        try:
            with self._lock:
                return self._ok(self._send_checked("Beep", CMD_BEEP))
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
                if not self._ok(r) or len(r) < 6:
                    return None
                uid = r[2:6]

                r = self._send(CMD_SELECT)
                if not self._ok(r) or len(r) < 3:
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

    def _reset_on_error(self) -> None:
        """Best-effort buffer flush after serial error."""
        try:
            if self._ser and self._ser.is_open:
                self._ser.reset_input_buffer()
        except Exception:
            pass

    def load_key(self, key_type: int, sector: int, key: bytes) -> bool:
        try:
            with self._lock:
                cmd = bytes([0x09, 0x06, key_type, sector]) + key
                return self._ok(self._send(cmd))
        except (serial.SerialException, OSError, EncoderError) as e:
            logger.warning("Serial error in load_key: %s", e)
            self._reset_on_error()
            return False

    def auth_sector(self, sector: int, key_type: int = KEY_TYPE_A) -> bool:
        try:
            with self._lock:
                first_block = sector * 4
                cmd = bytes([0x04, 0x05, key_type, sector, first_block])
                return self._ok(self._send(cmd))
        except (serial.SerialException, OSError, EncoderError) as e:
            logger.warning("Serial error in auth_sector: %s", e)
            self._reset_on_error()
            return False

    def read_block(self, block: int) -> bytes | None:
        try:
            with self._lock:
                # read_size=18: blocking read ensures full 18-byte frame arrives
                # (18 bytes at 9600 baud = 18.75ms; fast poll would only capture partial frame)
                r = self._send(bytes([0x02, 0x08, block]), read_size=18)
                if self._ok(r) and len(r) >= 18:
                    return r[2:18]
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
                return self._write_ok(self._send(cmd))
        except (serial.SerialException, OSError, EncoderError) as e:
            logger.warning("Serial error in write_block: %s", e)
            self._reset_on_error()
            return False

    def halt(self) -> bool:
        try:
            with self._lock:
                return self._ok(self._send(CMD_HALT))
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
