"""Card type constants must match what MF_System v5.0.2 actually writes.

Ground truth: 94 cards in the original software's log/IssueLog.mdb
(card_date_hex = block45 + block46), extracted 2026-10-14.
"""
from datetime import datetime

from setra_cards.cards.builder import build_laundry_card, build_master_card
from setra_cards.encoder.protocol import (
    CARD_TYPE_AUTH,
    CARD_TYPE_BUILDING,
    CARD_TYPE_CHECKOUT,
    CARD_TYPE_CLOCK,
    CARD_TYPE_EMERGENCY,
    CARD_TYPE_FLOOR,
    CARD_TYPE_GUEST,
    CARD_TYPE_HOUSEKEEPING,
    CARD_TYPE_LAUNDRY,
    CARD_TYPE_MASTER,
    CARD_TYPE_NAMES,
    CARD_TYPE_SETTING,
)

# block45 of IssueLog.mdb id 676 ("master card", 2008-04-02 10:20 -> 2008-05-02 10:20)
REAL_MASTER_BLOCK45 = bytes.fromhex("60000804020A140805020A1400000000")


def test_card_type_values_follow_index_times_0x10():
    assert CARD_TYPE_GUEST == 0x00
    assert CARD_TYPE_CHECKOUT == 0x20
    assert CARD_TYPE_FLOOR == 0x30
    assert CARD_TYPE_HOUSEKEEPING == 0x40
    assert CARD_TYPE_BUILDING == 0x50
    assert CARD_TYPE_MASTER == 0x60
    assert CARD_TYPE_EMERGENCY == 0x70
    assert CARD_TYPE_SETTING == 0x80
    assert CARD_TYPE_CLOCK == 0xA0
    assert CARD_TYPE_AUTH == 0xC0


def test_laundry_is_a_master_card():
    assert CARD_TYPE_LAUNDRY == CARD_TYPE_MASTER


def test_names_have_one_entry_per_firmware_type():
    assert CARD_TYPE_NAMES[CARD_TYPE_MASTER] == "Master"
    assert len(CARD_TYPE_NAMES) == 10


def test_master_block45_matches_original_software_byte_for_byte():
    issue = datetime(2008, 4, 2, 10, 20)
    expire = datetime(2008, 5, 2, 10, 20)
    from setra_cards.cards.crypto import DEFAULT_SIGNATURE

    card = build_master_card(now=issue, expire=expire, sig=DEFAULT_SIGNATURE)
    assert card.block45 == REAL_MASTER_BLOCK45


def _vendor_sig():
    """Vendor system that issued IssueLog.mdb ids 675-697 (signature D6 4D, prefix A8)."""
    from setra_cards.cards.crypto import SystemSignature

    return SystemSignature(sig_hi=0xD6, sig_lo=0x4D, prefix=0xA8)


def test_guest_card_matches_vendor_card_byte_for_byte():
    from setra_cards.cards.builder import build_guest_card

    # IssueLog id 684: room 8101 (building 8, floor 1, room_no_id 1)
    real = bytes.fromhex("00210804020A1D0804030E000801010000000000000000000000000000D64DAA")
    card = build_guest_card(now=datetime(2008, 4, 2, 10, 29), checkout=datetime(2008, 4, 3, 14, 0),
                            sig=_vendor_sig(), building=8, floor=1, room_no_id=1)
    assert card.block45 + card.block46 == real


def test_clock_card_matches_vendor_card_byte_for_byte():
    from setra_cards.cards.builder import build_clock_card

    # IssueLog id 679: issued Wednesday 2008-04-02 10:25 -> weekday byte 03
    real = bytes.fromhex("A0000804020A1903010100000000000000000000000000000000000100D64D24")
    card = build_clock_card(now=datetime(2008, 4, 2, 10, 25), sig=_vendor_sig())
    assert card.block45 + card.block46 == real


def test_setting_card_matches_vendor_layout():
    from setra_cards.cards.builder import build_setting_card

    # IssueLog id 678: room 8101. The vendor writes expiry == issue time; the
    # app keeps a 2 h window (the lock ignores it), so dates 7-11 differ.
    real_b45 = bytes.fromhex("80080804020A190804020A1908010100")
    card = build_setting_card(now=datetime(2008, 4, 2, 10, 25), sig=_vendor_sig(),
                              building=8, floor=1, room_no_id=1)
    assert card.block45[:7] == real_b45[:7]
    assert card.block45[12:] == real_b45[12:]


def test_laundry_block45_is_master_with_short_expiry():
    from setra_cards.cards.crypto import DEFAULT_SIGNATURE

    issue = datetime(2026, 10, 14, 8, 0)
    expire = datetime(2026, 10, 14, 16, 0)
    card = build_laundry_card(now=issue, expire=expire, sig=DEFAULT_SIGNATURE)
    assert card.block45[0] == 0x60
    assert card.block45[12:16] == b"\x00\x00\x00\x00"
