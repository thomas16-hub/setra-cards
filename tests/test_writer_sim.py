"""End-to-end write/blank tests against a simulated MIFARE card + encoder.

The simulator speaks the real encoder framing (4-byte Select reply, 01 01
on refusal, as captured in locstar-locks/*.log) and enforces MIFARE access
bits: under the system bits 78 77 88 data blocks are writable only with
key B, under transport bits FF 07 80 only with key A.
"""
from datetime import datetime, timedelta

import pytest

import setra_cards.cards.writer as writer
from setra_cards.cards.builder import build_guest_card
from setra_cards.cards.crypto import DEFAULT_SIGNATURE as SIG
from setra_cards.cards.crypto import make_block44, make_trailer
from setra_cards.encoder.driver import EncoderDriver
from setra_cards.encoder.protocol import DEFAULT_TRAILER, KEY_TYPE_A

SECTOR = 11
AB_SYSTEM = bytes([0x78, 0x77, 0x88])
AB_TRANSPORT = bytes([0xFF, 0x07, 0x80])
NOW = datetime(2026, 10, 14, 15, 0)


class SimCard:
    def __init__(self, uid: bytes, trailer: bytes = DEFAULT_TRAILER, data: dict | None = None):
        self.uid = uid
        self.blocks = {SECTOR * 4 + i: (data or {}).get(i, bytes(16)) for i in range(3)}
        self.blocks[SECTOR * 4 + 3] = trailer

    def block45(self) -> bytes:
        return self.blocks[SECTOR * 4 + 1]


class SimSerial:
    """Fake pyserial port backed by one (swappable) SimCard."""

    def __init__(self, card: SimCard | None, on_command: dict | None = None,
                 refuse_blocks: set[int] | None = None):
        self.is_open = True
        self.card = card
        self.on_command = on_command or {}
        # Blocks whose writes the card refuses (01 01) whatever the key.
        self.refuse_blocks = refuse_blocks or set()
        self.n = 0
        self._rx = bytearray()
        self.loaded: dict = {}
        self.auth = None
        self.selected = False

    @property
    def in_waiting(self) -> int:
        return len(self._rx)

    def flush(self) -> None:
        pass

    def reset_input_buffer(self) -> None:
        self._rx.clear()

    def close(self) -> None:
        self.is_open = False

    def read(self, n: int = 1) -> bytes:
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def _reply(self, data: bytes) -> None:
        self._rx.extend(data)

    def _may_write(self, block: int, key_type: int) -> bool:
        bits = self.card.blocks[(block // 4) * 4 + 3][6:9]
        if bits == AB_TRANSPORT:
            return key_type == KEY_TYPE_A
        if bits == AB_SYSTEM:
            return key_type != KEY_TYPE_A
        return False

    def write(self, data: bytes) -> int:
        self.n += 1
        if self.n in self.on_command:
            self.on_command[self.n](self)
        cmd, card = data[1], self.card
        if data[0] == 0x09 and cmd == 0x06:  # load key
            self.loaded[(data[2], data[3])] = bytes(data[4:10])
            self._reply(b"\x01\x00")
        elif cmd == 0x06:  # beep
            self._reply(b"\x01\x00")
        elif cmd == 0x0B:  # halt
            self.selected, self.auth = False, None
            self._reply(b"\x01\x00")
        elif cmd == 0x02:  # request
            self.selected, self.auth = False, None
            self._reply(b"\x03\x00\x04\x00" if card else b"\x01\x01")
        elif cmd == 0x03:  # anticollision
            self._reply(b"\x05\x00" + card.uid if card else b"\x01\x01")
        elif cmd == 0x04:  # select
            self.selected = card is not None
            self._reply(b"\x03\x00\x08\x1f" if card else b"\x01\x01")
        elif cmd == 0x05:  # auth
            key_type, sector = data[2], data[3]
            key = self.loaded.get((key_type, sector))
            ok = False
            if card and self.selected and key is not None:
                trailer = card.blocks[sector * 4 + 3]
                if key_type == KEY_TYPE_A:
                    ok = trailer[:6] == key
                else:  # key B only usable when the access bits make it non-readable
                    ok = trailer[6:9] == AB_SYSTEM and trailer[10:16] == key
            self.auth = (sector, key_type) if ok else None
            self.selected = self.selected and ok
            self._reply(b"\x01\x00" if ok else b"\x01\x01")
        elif cmd == 0x08:  # read
            block = data[2]
            if card and self.auth and self.auth[0] == block // 4:
                self._reply(b"\x11\x00" + card.blocks[block])
            else:
                self._reply(b"\x01\x01")
        elif data[0] == 0x12 and cmd == 0x09:  # write
            block = data[2]
            if (card and self.auth and self.auth[0] == block // 4
                    and block not in self.refuse_blocks and self._may_write(block, self.auth[1])):
                card.blocks[block] = bytes(data[3:19])
                self._reply(b"\x01\x00")
            else:
                self.selected, self.auth = False, None
                self._reply(b"\x01\x01")  # the refusal the old code counted as success
        return len(data)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _s: None)


def _driver(sim: SimSerial) -> EncoderDriver:
    d = EncoderDriver("SIM")
    d._ser = sim
    return d


def _guest(room_no_id: int):
    return build_guest_card(room=room_no_id, now=NOW, checkout=NOW + timedelta(days=2), sig=SIG,
                            building=1, floor=1, room_no_id=room_no_id)


def _issued_card(uid: bytes, card, trailer: bytes | None = None) -> SimCard:
    return SimCard(uid, trailer or make_trailer(SIG),
                   data={0: make_block44(uid), 1: card.block45, 2: card.block46})


def test_blank_card_is_written_and_verified():
    card = SimCard(b"\xaa\xbb\xcc\xdd")
    new = _guest(5)
    result = writer.write_card(_driver(SimSerial(card)), SECTOR, SIG, new)
    assert result.success, result.error
    assert card.block45() == new.block45


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_reissued_card_really_gets_the_new_room():
    uid = b"\x11\x22\x33\x44"
    card = _issued_card(uid, _guest(5))
    new = _guest(7)
    result = writer.write_card(_driver(SimSerial(card)), SECTOR, SIG, new)
    assert result.success, result.error
    assert card.block45() == new.block45


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_refused_write_is_never_reported_as_success():
    # Key B on the card is not ours: only Sig-A authenticates, every write is
    # refused with 01 01 and the card keeps room 5.
    uid = b"\x11\x22\x33\x44"
    foreign_trailer = SIG.key_a + bytes([0x78, 0x77, 0x88, 0x69]) + b"\x01\x02\x03\x04\x05\x06"
    old = _guest(5)
    card = _issued_card(uid, old, trailer=foreign_trailer)
    result = writer.write_card(_driver(SimSerial(card)), SECTOR, SIG, _guest(7))
    assert result.success is False
    assert card.block45() == old.block45


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_card_removed_mid_write_reports_removal_and_erases_nothing():
    first = SimCard(b"\xa1\xa1\xa1\xa1")
    bystander_data = _guest(9)
    bystander = _issued_card(b"\xb2\xb2\xb2\xb2", bystander_data)

    def remove(sim):
        sim.card = None

    def drop_other_card(sim):
        sim.card = bystander

    sim = SimSerial(first, on_command={20: remove, 40: drop_other_card})
    result = writer.write_card(_driver(sim), SECTOR, SIG, _guest(7))
    assert result.success is False
    assert result.error in (writer.CARD_REMOVED_ERROR, writer.CARD_SWAPPED_ERROR)
    assert bystander.block45() == bystander_data.block45
    assert bystander.blocks[SECTOR * 4 + 3][6:9] == AB_SYSTEM


def test_write_refuses_a_different_card_than_expected():
    card = SimCard(b"\xaa\xbb\xcc\xdd")
    result = writer.write_card(_driver(SimSerial(card)), SECTOR, SIG, _guest(5),
                               expected_uid=b"\x00\x00\x00\x01")
    assert result.success is False
    assert result.error == writer.CARD_SWAPPED_ERROR
    assert card.block45() == bytes(16)


def test_blank_refuses_a_different_card_than_expected():
    data = _guest(5)
    card = _issued_card(b"\xaa\xbb\xcc\xdd", data)
    result = writer.blank_card(_driver(SimSerial(card)), SECTOR, expected_uid=b"\x00\x00\x00\x01")
    assert result.success is False
    assert card.block45() == data.block45


def test_blank_stops_when_card_is_swapped_midway():
    data_a, data_b = _guest(5), _guest(6)
    card_a = _issued_card(b"\xa1\xa1\xa1\xa1", data_a)
    card_b = _issued_card(b"\xb2\xb2\xb2\xb2", data_b)

    def swap(sim):
        sim.card = card_b

    sim = SimSerial(card_a, on_command={12: swap})
    result = writer.blank_card(_driver(sim), SECTOR)
    assert result.success is False
    assert card_b.block45() == data_b.block45


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_refused_trailer_write_is_not_reported_as_success():
    # Data blocks take the write but the trailer keeps KeyA FF: the data reads
    # back fine, yet the lock (system KeyA) could never open the card.
    card = SimCard(b"\xaa\xbb\xcc\xdd")
    sim = SimSerial(card, refuse_blocks={SECTOR * 4 + 3})
    result = writer.write_card(_driver(sim), SECTOR, SIG, _guest(5))
    assert result.success is False


@pytest.mark.xfail(strict=True, reason="v2.1.1: strict encoder behaviour reverted to v2.0.10 pending real-encoder trace (v2.1.0 hotel regression 2026-10-14)")
def test_refused_blank_is_not_reported_as_success():
    data = _guest(5)
    card = _issued_card(b"\xaa\xbb\xcc\xdd", data)
    sim = SimSerial(card, refuse_blocks={SECTOR * 4 + 1, SECTOR * 4 + 2})
    result = writer.blank_card(_driver(sim), SECTOR)
    assert result.success is False
    assert card.block45() == data.block45


def test_one_missed_detection_does_not_abort_the_write():
    card = SimCard(b"\xaa\xbb\xcc\xdd")
    hidden = {}

    def hide(sim):
        hidden["card"] = sim.card
        sim.card = None

    def show(sim):
        sim.card = hidden["card"]

    # Command 20 is the Request that re-checks the card before block 45; the
    # card answers nothing to it, then is back for the retry (command 21).
    sim = SimSerial(card, on_command={20: hide, 21: show})
    new = _guest(5)
    result = writer.write_card(_driver(sim), SECTOR, SIG, new)
    assert result.success, result.error
    assert card.block45() == new.block45


def test_unplugged_encoder_is_reported_as_disconnected_not_as_missing_card():
    sim = SimSerial(SimCard(b"\xaa\xbb\xcc\xdd"))
    driver = _driver(sim)
    driver._ser = None  # what driver._send does when the USB port is lost
    result = writer.write_card(driver, SECTOR, SIG, _guest(5))
    assert result.success is False
    assert result.error == writer.ENCODER_DISCONNECTED_ERROR


def test_blank_then_rewrite_on_issued_card():
    data = _guest(5)
    card = _issued_card(b"\xaa\xbb\xcc\xdd", data)
    result = writer.blank_card(_driver(SimSerial(card)), SECTOR)
    assert result.success, result.error
    assert card.block45() == bytes(16)
    assert card.blocks[SECTOR * 4 + 3] == DEFAULT_TRAILER


class SlowEncoderSerial(SimSerial):
    """Real-encoder timing behind the v2.1.0 hotel regression (2026-10-14).

    Replies arrive later than the 80 ms poll window, so in_waiting is still 0
    when polling ends. v2.0.10 then blocked on read() until the port timeout
    and got the reply; v2.1.0 gave up with an empty reply, so every detection
    failed and each write was aborted as "card removed".
    """

    @property
    def in_waiting(self) -> int:
        return 0


def test_slow_encoder_replies_do_not_abort_the_write():
    card = SimCard(b"\xaa\xbb\xcc\xdd")
    new = _guest(5)
    result = writer.write_card(_driver(SlowEncoderSerial(card)), SECTOR, SIG, new)
    assert result.success, result.error
    assert card.block45() == new.block45


def test_slow_encoder_replies_do_not_abort_the_blank():
    uid = b"\x11\x22\x33\x44"
    card = _issued_card(uid, _guest(5))
    result = writer.blank_card(_driver(SlowEncoderSerial(card)), SECTOR, expected_uid=uid)
    assert result.success, result.error
    assert card.block45() == bytes(16)


class DropAfterHaltSerial(SimSerial):
    """Real encoder (hotel trace 2026-10-14): the first command after a Halt
    gets no reply at all, whatever it is."""

    def __init__(self, card):
        super().__init__(card)
        self._drop_next = False

    def write(self, data: bytes) -> int:
        if self._drop_next:
            self._drop_next = False
            self.n += 1
            return len(data)
        result = super().write(data)
        if data[1] == 0x0B:
            self._drop_next = True
        return result


def test_read_back_survives_the_command_dropped_after_halt():
    card = SimCard(b"\xaa\xbb\xcc\xdd")
    new = _guest(5)
    driver = _driver(DropAfterHaltSerial(card))
    assert writer.write_card(driver, SECTOR, SIG, new).success
    assert card.block45() == new.block45
    error = writer._verify_after_write(driver, SECTOR, SIG, writer.DES_KEY, new, b"\xaa\xbb\xcc\xdd")
    assert error is None


def test_blank_confirm_survives_the_command_dropped_after_halt():
    uid = b"\x11\x22\x33\x44"
    card = _issued_card(uid, _guest(5))
    driver = _driver(DropAfterHaltSerial(card))
    assert writer.blank_card(driver, SECTOR, expected_uid=uid).success
    assert writer._sector_reads_blank(driver, SECTOR)
