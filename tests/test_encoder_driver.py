"""EncoderDriver tests with a fake serial port.

Covers frame validation (<LEN><STATUS> consistency + expected lengths),
timeout drain/fast-fail, and the staged read_block (no 18-byte blocking
read stalling on 2-byte error frames).
"""
import time

import pytest
import serial

from setra_cards.encoder.driver import (
    ACK_FRAME_LEN,
    EncoderDriver,
)


class FakeSerial:
    """Minimal in-memory stand-in for serial.Serial.

    Each write() pops the next scripted response into the RX buffer.
    read(n) is non-blocking: returns whatever is available, up to n
    (i.e. behaves like a real port whose timeout already elapsed).
    """

    def __init__(self, responses: list[bytes] | None = None):
        self.is_open = True
        self._rx = bytearray()
        self._responses = list(responses or [])
        self.written: list[bytes] = []
        self.read_requests: list[int] = []
        self.reset_count = 0

    @property
    def in_waiting(self) -> int:
        return len(self._rx)

    def write(self, data: bytes) -> int:
        self.written.append(bytes(data))
        if self._responses:
            self._rx.extend(self._responses.pop(0))
        return len(data)

    def flush(self) -> None:
        pass

    def read(self, n: int = 1) -> bytes:
        self.read_requests.append(n)
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def reset_input_buffer(self) -> None:
        self.reset_count += 1
        self._rx.clear()

    def close(self) -> None:
        self.is_open = False


def _driver_with(fake: FakeSerial) -> EncoderDriver:
    drv = EncoderDriver("COM_FAKE")
    drv._ser = fake  # inject fake port, skip real open()
    return drv


# --- Frame validation (_ok / _write_ok) ---

def test_ok_accepts_valid_ack():
    assert EncoderDriver._ok(b"\x01\x00") is True


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_ok_rejects_inconsistent_len_byte():
    # LEN says 5 bytes follow but only 1 does — truncated/stale frame
    assert EncoderDriver._ok(b"\x05\x00") is False


def test_ok_rejects_frame_shorter_than_two_bytes():
    assert EncoderDriver._ok(b"") is False
    assert EncoderDriver._ok(b"\x00") is False


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_ok_rejects_wrong_expected_len():
    # Internally consistent 6-byte frame, but we expected a 2-byte ACK:
    # this is exactly a stale anticollision reply masquerading as an ACK.
    stale = b"\x05\x00\xaa\xbb\xcc\xdd"
    assert EncoderDriver._ok(stale) is True  # frame itself is valid
    assert EncoderDriver._ok(stale, expected_len=ACK_FRAME_LEN) is False


def test_write_ok_accepts_both_documented_acks():
    assert EncoderDriver._write_ok(b"\x01\x00", expected_len=ACK_FRAME_LEN) is True
    assert EncoderDriver._write_ok(b"\x01\x01", expected_len=ACK_FRAME_LEN) is True


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_write_ok_rejects_error_status_and_bad_framing():
    assert EncoderDriver._write_ok(b"\x01\x02", expected_len=ACK_FRAME_LEN) is False
    assert EncoderDriver._write_ok(b"\x02\x00\x00", expected_len=ACK_FRAME_LEN) is False
    assert EncoderDriver._write_ok(b"\x09\x00", expected_len=ACK_FRAME_LEN) is False


# --- beep through the fake port ---

def test_beep_accepts_real_ack():
    fake = FakeSerial(responses=[b"\x01\x00"])
    assert _driver_with(fake).beep() is True


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_beep_rejects_stale_read_frame_as_ack():
    # A late 18-byte read reply starts with 0x11 0x00: old code saw
    # resp[1] == 0x00 and reported a false ACK. Frame length must reject it.
    stale_read_frame = b"\x11\x00" + bytes(16)
    fake = FakeSerial(responses=[stale_read_frame])
    assert _driver_with(fake).beep() is False


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_beep_rejects_garbage_frame():
    fake = FakeSerial(responses=[b"\x00\x00"])  # LEN=0 inconsistent with 2 bytes
    assert _driver_with(fake).beep() is False


# --- timeout: fast-fail + drain ---

@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_send_timeout_fast_fails_and_drains():
    fake = FakeSerial(responses=[])  # device never answers
    drv = _driver_with(fake)
    t0 = time.monotonic()
    assert drv.beep() is False
    elapsed = time.monotonic() - t0
    # Poll window is 0.08s; the old fallback read(256) added a full serial
    # timeout on top. Fast-fail must stay well under that.
    assert elapsed < 0.5
    # One reset before TX + one drain after the timeout
    assert fake.reset_count >= 2


def test_write_block_requires_16_bytes():
    drv = _driver_with(FakeSerial())
    with pytest.raises(ValueError):
        drv.write_block(20, b"\x00" * 4)


def test_write_block_accepts_ack_01():
    fake = FakeSerial(responses=[b"\x01\x01"])
    assert _driver_with(fake).write_block(20, bytes(16)) is True


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_write_block_rejects_stale_long_frame():
    fake = FakeSerial(responses=[b"\x11\x00" + bytes(16)])
    assert _driver_with(fake).write_block(20, bytes(16)) is False


# --- detect_card frame validation ---

def test_detect_card_happy_path():
    fake = FakeSerial(responses=[
        b"\x03\x00\x04\x00",          # REQUEST_ALL: ATQA 04 00 (1K)
        b"\x05\x00\xaa\xbb\xcc\xdd",  # ANTICOLLISION: uid
        b"\x03\x00\x08\x1f",          # SELECT as captured from the real encoder
    ])
    det = _driver_with(fake).detect_card()
    assert det is not None
    assert det.uid == b"\xaa\xbb\xcc\xdd"
    assert det.sak == 0x08
    assert det.atqa == b"\x04\x00"


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_detect_card_rejects_three_byte_select():
    # The old test fixture shape; the real encoder never sends it.
    fake = FakeSerial(responses=[
        b"\x03\x00\x04\x00",
        b"\x05\x00\xaa\xbb\xcc\xdd",
        b"\x02\x00\x08",
    ])
    assert _driver_with(fake).detect_card() is None


def test_detect_card_rejects_truncated_anticollision():
    fake = FakeSerial(responses=[
        b"\x03\x00\x04\x00",
        b"\x05\x00\xaa\xbb\xcc",  # LEN says 5 follow, only 3 do
        b"\x03\x00\x08\x1f",
    ])
    assert _driver_with(fake).detect_card() is None


# --- read_block staged read ---

def test_read_block_success_returns_16_data_bytes():
    payload = bytes(range(16))
    fake = FakeSerial(responses=[b"\x11\x00" + payload])
    assert _driver_with(fake).read_block(44) == payload


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_read_block_error_frame_fast_fails_without_blocking_read18():
    fake = FakeSerial(responses=[b"\x01\x14"])  # 2-byte error frame
    assert _driver_with(fake).read_block(44) is None
    # Staged read: only LEN+STATUS (2 bytes) requested — never a blocking read(18)
    assert max(fake.read_requests) == 2


def test_read_block_rejects_status_ok_with_wrong_len():
    # STATUS == 0 but LEN != 0x11: malformed/stale frame, must not be data
    fake = FakeSerial(responses=[b"\x05\x00\xaa\xbb\xcc\xdd"])
    assert _driver_with(fake).read_block(44) is None


def test_read_block_short_body_returns_none():
    fake = FakeSerial(responses=[b"\x11\x00" + bytes(8)])  # body truncated
    assert _driver_with(fake).read_block(44) is None


# --- is_connected thread-safe probe ---

def test_is_connected_false_when_no_port():
    drv = EncoderDriver("COM_FAKE")
    assert drv.is_connected is False


def test_is_connected_true_with_open_fake():
    assert _driver_with(FakeSerial()).is_connected is True


def test_is_connected_false_after_close():
    fake = FakeSerial()
    fake.close()
    assert _driver_with(fake).is_connected is False


def test_is_connected_handles_serial_exception():
    class _Raising:
        is_open = True

        @property
        def in_waiting(self):
            raise serial.SerialException("port gone")

    drv = EncoderDriver("COM_FAKE")
    drv._ser = _Raising()
    assert drv.is_connected is False


def test_is_connected_handles_attribute_error():
    class _Broken:
        is_open = True  # no in_waiting attribute at all

    drv = EncoderDriver("COM_FAKE")
    drv._ser = _Broken()
    assert drv.is_connected is False
