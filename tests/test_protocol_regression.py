"""Regression tests for Locstar protocol invariants.

Protects: global master/laundry tail (block45 bytes 12-15 = 00 00 00 00),
date encoding range (2000-2099), XOR checksum, blank-card detection and
post-write verification logic.
"""
from datetime import datetime

import pytest

from setra_cards.cards.builder import (
    CardData,
    build_guest_card,
    build_laundry_card,
    build_master_card,
    make_date_bytes,
)
from setra_cards.cards.crypto import DEFAULT_SIGNATURE, xor_checksum
from setra_cards.cards.reader import DecodedCard, _decode_date, _is_blank_sector
from setra_cards.cards.writer import GLOBAL_TAIL, _verify_decoded
from setra_cards.encoder.protocol import (
    CARD_TYPE_GUEST,
    CARD_TYPE_LAUNDRY,
    CARD_TYPE_MASTER,
)

NOW = datetime(2026, 6, 11, 14, 30)
EXPIRE = datetime(2026, 12, 31, 12, 0)
SECTOR = 11  # Locstar sector: blocks 44/45/46 + trailer 47
UID = b"\x01\x02\x03\x04"


# --- Global master/laundry tail (the v2.0.9 protocol fix) ---

def test_master_card_tail_is_global_zeros():
    card = build_master_card(now=NOW, expire=EXPIRE, sig=DEFAULT_SIGNATURE)
    assert card.block45[0] == CARD_TYPE_MASTER
    assert card.block45[12:16] == b"\x00\x00\x00\x00"


def test_laundry_card_tail_is_global_zeros():
    card = build_laundry_card(now=NOW, expire=EXPIRE, sig=DEFAULT_SIGNATURE)
    assert card.block45[0] == CARD_TYPE_LAUNDRY
    assert card.block45[12:16] == b"\x00\x00\x00\x00"


def test_master_laundry_builders_reject_building_floor_params():
    """Regression: building/floor were silently-ignored params (removed)."""
    with pytest.raises(TypeError):
        build_master_card(now=NOW, expire=EXPIRE, sig=DEFAULT_SIGNATURE, building=2)
    with pytest.raises(TypeError):
        build_laundry_card(now=NOW, expire=EXPIRE, sig=DEFAULT_SIGNATURE, floor=3)


def test_carddata_has_no_build_block44_method():
    """Regression: dead method with default-DES_KEY footgun was removed."""
    assert not hasattr(CardData, "build_block44")


# --- Date encoding ---

def test_make_date_bytes_encodes_2026():
    assert make_date_bytes(NOW) == bytes([26, 6, 11, 14, 30])


def test_make_date_bytes_rejects_year_2100():
    with pytest.raises(ValueError):
        make_date_bytes(datetime(2100, 1, 1, 0, 0))


def test_make_date_bytes_rejects_year_1999():
    with pytest.raises(ValueError):
        make_date_bytes(datetime(1999, 12, 31, 23, 59))


# --- XOR checksum ---

def test_xor_checksum_known_vector():
    # XOR(0..15) = 0; XOR(0..14) = 15; combined = 15
    assert xor_checksum(bytes(range(16)), bytes(range(15))) == 15


def test_xor_checksum_all_zeros_is_zero():
    assert xor_checksum(bytes(16), bytes(15)) == 0


def test_built_master_card_block46_checksum_is_self_consistent():
    card = build_master_card(now=NOW, expire=EXPIRE, sig=DEFAULT_SIGNATURE)
    assert card.block46[15] == xor_checksum(card.block45, card.block46[:15])


# --- Round-trip: built master card decodes to the protocol fields ---

def test_master_card_block45_round_trip_decode():
    card = build_master_card(now=NOW, expire=EXPIRE, sig=DEFAULT_SIGNATURE)
    b45 = card.block45
    assert b45[0] == CARD_TYPE_MASTER
    assert b45[1] == 0x00  # no room/seq for global master
    assert _decode_date(b45, 2) == NOW
    assert _decode_date(b45, 7) == EXPIRE
    # building/floor/room_no_id positions must all be zero (global tail)
    assert (b45[12], b45[13], b45[14], b45[15]) == (0, 0, 0, 0)


# --- Blank-card detection (reader) ---

def test_is_blank_sector_true_for_all_zero_blocks():
    assert _is_blank_sector(bytes(16), bytes(16)) is True


def test_is_blank_sector_false_when_any_block_nonzero():
    assert _is_blank_sector(b"\x40" + bytes(15), bytes(16)) is False
    assert _is_blank_sector(bytes(16), bytes(15) + b"\x01") is False


def _make_decoded(
    card_type: int = CARD_TYPE_MASTER,
    uid: bytes = UID,
    tail: bytes = b"\x00\x00\x00\x00",
    checksum_valid: bool = True,
    uid_match: bool = True,
    include_block45: bool = True,
) -> DecodedCard:
    block45 = bytes([card_type, 0x00]) + bytes(10) + tail
    raw_blocks = {SECTOR * 4 + 1: block45} if include_block45 else {}
    return DecodedCard(
        uid=uid,
        card_type=card_type,
        card_type_name="test",
        room=0,
        date1=None,
        date2=None,
        building=tail[0],
        floor=tail[1],
        room_no_id=tail[2],
        signature=b"\x95\xb8",
        checksum_valid=checksum_valid,
        uid_match=uid_match,
        raw_blocks=raw_blocks,
    )


def test_decoded_card_is_blank_defaults_false():
    assert _make_decoded().is_blank is False


# --- Post-write verification (_verify_decoded) ---

def test_verify_decoded_accepts_valid_master_card():
    decoded = _make_decoded()
    assert _verify_decoded(decoded, UID, CARD_TYPE_MASTER, SECTOR) is None


def test_verify_decoded_rejects_none():
    error = _verify_decoded(None, UID, CARD_TYPE_MASTER, SECTOR)
    assert error == "Verificación falló: la tarjeta no se grabó correctamente"


def test_verify_decoded_rejects_different_uid():
    decoded = _make_decoded(uid=b"\xaa\xbb\xcc\xdd")
    assert _verify_decoded(decoded, UID, CARD_TYPE_MASTER, SECTOR) is not None


def test_verify_decoded_rejects_uid_mismatch_flag():
    decoded = _make_decoded(uid_match=False)
    assert _verify_decoded(decoded, UID, CARD_TYPE_MASTER, SECTOR) is not None


def test_verify_decoded_rejects_invalid_checksum():
    decoded = _make_decoded(checksum_valid=False)
    assert _verify_decoded(decoded, UID, CARD_TYPE_MASTER, SECTOR) is not None


def test_verify_decoded_rejects_card_type_mismatch():
    decoded = _make_decoded(card_type=CARD_TYPE_GUEST)
    assert _verify_decoded(decoded, UID, CARD_TYPE_MASTER, SECTOR) is not None


def test_verify_decoded_rejects_master_with_nonzero_tail():
    decoded = _make_decoded(tail=b"\x01\x01\x05\x00")
    error = _verify_decoded(decoded, UID, CARD_TYPE_MASTER, SECTOR)
    assert error == "Verificación falló: tail maestra/limpieza incorrecto"


def test_verify_decoded_rejects_laundry_with_missing_block45():
    decoded = _make_decoded(card_type=CARD_TYPE_LAUNDRY, include_block45=False)
    error = _verify_decoded(decoded, UID, CARD_TYPE_LAUNDRY, SECTOR)
    assert error == "Verificación falló: tail maestra/limpieza incorrecto"


def test_verify_decoded_allows_guest_card_with_room_tail():
    """Guest cards legitimately carry building/floor/room_no_id in the tail."""
    decoded = _make_decoded(card_type=CARD_TYPE_GUEST, tail=b"\x01\x02\x07\x00")
    assert _verify_decoded(decoded, UID, CARD_TYPE_GUEST, SECTOR) is None


def test_global_tail_constant_matches_protocol():
    assert GLOBAL_TAIL == b"\x00\x00\x00\x00"


def test_guest_card_tail_carries_room_identity():
    """The lock validates byte[14]=room_no_id on guest cards — keep it wired."""
    card = build_guest_card(
        now=NOW, checkout=EXPIRE, sig=DEFAULT_SIGNATURE,
        building=1, floor=3, room_no_id=12,
    )
    assert card.block45[12] == 1
    assert card.block45[13] == 3
    assert card.block45[14] == 12
    assert card.block45[15] == 0x00
