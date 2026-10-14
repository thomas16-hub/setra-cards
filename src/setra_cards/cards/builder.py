"""Card data builders for the 4 Locstar card types.

Each builder returns a CardData frozen dataclass with block44, block45, block46, trailer.
Block 44 depends on the card UID (built separately via crypto.make_block44).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from setra_cards.cards.crypto import (
    SystemSignature,
    make_auth_trailer,
    make_trailer,
    xor_checksum,
)
from setra_cards.encoder.protocol import (
    CARD_TYPE_AUTH,
    CARD_TYPE_CLOCK,
    CARD_TYPE_GUEST,
    CARD_TYPE_LAUNDRY,
    CARD_TYPE_MASTER,
    CARD_TYPE_SETTING,
)


@dataclass(frozen=True)
class CardData:
    """Complete card data for sector write (4 blocks of 16 bytes each).

    Block 44 is built in the writer via crypto.make_block44 with the
    hotel's explicit DES key (no default-key shortcut here).
    """

    card_type: int
    block45: bytes
    block46: bytes
    trailer: bytes


def make_date_bytes(dt: datetime) -> bytes:
    """Encode datetime as [YY, MM, DD, HH, MM] (5 bytes, decimal values).

    The protocol stores the year as a single byte offset from 2000,
    so only years 2000-2099 are representable.
    """
    if dt.year < 2000 or dt.year > 2099:
        raise ValueError("El año debe estar entre 2000 y 2099")
    return bytes([dt.year - 2000, dt.month, dt.day, dt.hour, dt.minute])


# Block45 byte[1] on Guest and Setting cards = the room's sequential id, as
# v2.0.10 wrote it. The vendor writes 0x21 (guest) / building (setting), but
# the hotel locks were set up with v2.0.10 cards: v2.1.1 used the vendor
# values and guest cards stopped opening (Torre Primavera, 2026-10-14).


def _finalize_block46(block45: bytes, block46_prefix: bytes) -> bytes:
    """Append XOR checksum to block46 prefix (15 bytes -> 16 bytes)."""
    return block46_prefix + bytes([xor_checksum(block45, block46_prefix)])


def build_auth_card(now: datetime, sig: SystemSignature) -> CardData:
    """Build Authorization card (0xC0).

    - Valid 30 days from issue
    - Block 46 has extended signature: [00, 15 95 B8, 15 95 B8, 00*6, 95 B8, checksum]
    - Trailer uses KeyA=FF (lock doesn't know signature yet)
    """
    expire = now + timedelta(days=30)
    block45 = bytes([CARD_TYPE_AUTH, 0x00]) + make_date_bytes(now) + make_date_bytes(expire) + bytes(4)

    sig3 = sig.auth_sig_block  # 15 95 B8
    block46_prefix = bytes([0x00]) + sig3 + sig3 + bytes(6) + sig.signature
    block46 = _finalize_block46(block45, block46_prefix)

    return CardData(
        card_type=CARD_TYPE_AUTH,
        block45=block45,
        block46=block46,
        trailer=make_auth_trailer(),
    )


def build_clock_card(now: datetime, sig: SystemSignature) -> CardData:
    """Build Clock card (0xA0).

    - Sets lock time to 'now'
    - Block 45: [A0, 00, date(5), weekday, 01, 01, 00, 00*5]
      weekday = ISO day (Mon=1..Sun=7), as the vendor writes it: 01 on a
      Monday, 03 on a Wednesday in IssueLog.mdb. It used to be fixed at 01.
    - Block 46: [00*11, 01, 00, 95 B8, checksum]
    """
    block45 = (
        bytes([CARD_TYPE_CLOCK, 0x00])
        + make_date_bytes(now)
        + bytes([now.isoweekday(), 0x01, 0x01, 0x00])
        + bytes(5)
    )

    block46_prefix = bytes(11) + bytes([0x01, 0x00]) + sig.signature
    block46 = _finalize_block46(block45, block46_prefix)

    return CardData(
        card_type=CARD_TYPE_CLOCK,
        block45=block45,
        block46=block46,
        trailer=make_trailer(sig),
    )


def build_setting_card(
    room: int,
    now: datetime,
    sig: SystemSignature,
    building: int = 1,
    floor: int = 1,
    room_no_id: int = 1,
) -> CardData:
    """Build Setting card (0x80).

    - Assigns room to lock (byte[14] = room_no_id, la cerradura valida por esto)
    - Valid ~2 hours from issue
    - Block 45 (16 bytes): [80, room, date(5), expire(5), building, floor, room_no_id, 00]
    - Block 46: [00*13, 95 B8, checksum]
    """
    expire = now + timedelta(hours=2)
    block45 = (
        bytes([CARD_TYPE_SETTING, room])
        + make_date_bytes(now)
        + make_date_bytes(expire)
        + bytes([building, floor, room_no_id, 0x00])
    )

    block46_prefix = bytes(13) + sig.signature
    block46 = _finalize_block46(block45, block46_prefix)

    return CardData(
        card_type=CARD_TYPE_SETTING,
        block45=block45,
        block46=block46,
        trailer=make_trailer(sig),
    )


def build_guest_card(
    room: int,
    now: datetime,
    checkout: datetime,
    sig: SystemSignature,
    building: int = 1,
    floor: int = 1,
    room_no_id: int = 1,
) -> CardData:
    """Build Guest card (0x00).

    - Opens the assigned room (byte[14] = room_no_id, la cerradura valida por esto)
    - Valid from now until checkout
    - Block 45 (16 bytes): [00, room, issue(5), checkout(5), building, floor, room_no_id, 00]
    - Block 46: [00*13, 95 B8, checksum]
    """
    block45 = (
        bytes([CARD_TYPE_GUEST, room])
        + make_date_bytes(now)
        + make_date_bytes(checkout)
        + bytes([building, floor, room_no_id, 0x00])
    )

    block46_prefix = bytes(13) + sig.signature
    block46 = _finalize_block46(block45, block46_prefix)

    return CardData(
        card_type=CARD_TYPE_GUEST,
        block45=block45,
        block46=block46,
        trailer=make_trailer(sig),
    )


def build_master_card(
    now: datetime,
    expire: datetime,
    sig: SystemSignature,
) -> CardData:
    """Build Master card (0x60): opens every lock in the system.

    Matches cards emitted by MF_System v5.0.2 (IssueLog.mdb):
    [60, 00, issue(5), expire(5), 00 00 00 00]. The global tail was right
    since v2.0.9; the type byte was wrong (0x40 = House Keeping).
    """
    block45 = (
        bytes([CARD_TYPE_MASTER, 0x00])
        + make_date_bytes(now)
        + make_date_bytes(expire)
        + bytes([0x00, 0x00, 0x00, 0x00])
    )

    block46_prefix = bytes(13) + sig.signature
    block46 = _finalize_block46(block45, block46_prefix)

    return CardData(
        card_type=CARD_TYPE_MASTER,
        block45=block45,
        block46=block46,
        trailer=make_trailer(sig),
    )


def build_laundry_card(
    now: datetime,
    expire: datetime,
    sig: SystemSignature,
) -> CardData:
    """Build Limpieza card: a short-lived Master (0x60) that opens every room.

    Decision 2026-10-14 (Thomas): cleaning staff open all rooms, the card
    just expires in hours. Previously emitted 0x20, which the firmware reads
    as a Check Out card, so it never opened anything.
    """
    block45 = (
        bytes([CARD_TYPE_LAUNDRY, 0x00])
        + make_date_bytes(now)
        + make_date_bytes(expire)
        + bytes([0x00, 0x00, 0x00, 0x00])
    )

    block46_prefix = bytes(13) + sig.signature
    block46 = _finalize_block46(block45, block46_prefix)

    return CardData(
        card_type=CARD_TYPE_LAUNDRY,
        block45=block45,
        block46=block46,
        trailer=make_trailer(sig),
    )
