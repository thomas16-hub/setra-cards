"""Tests for S70 data-card reader: event-type labels and signature check.

Covers:
- Event type labels: only 0x00 is verified; every other byte must show the
  raw hex and be marked "(sin verificar)" (no invented semantics).
- Sector 1 system-signature comparison vs the hotel SystemSignature key_a:
  warn (never hard-fail) on mismatch.
- Spanish verdict distinguishes "sector 0 unreadable" from "marker absent".
"""

import logging

import pytest

from setra_cards.cards.crypto import DEFAULT_SIGNATURE
from setra_cards.cards.s70_reader import (
    EVENT_TYPE_NAMES,
    _decode_event,
    event_type_label,
    read_s70_card,
)
from setra_cards.encoder.protocol import KEY_TYPE_A, CardDetection

UID = b"\x12\x34\x56\x78"


class FakeEncoder:
    """In-memory encoder stub: block map + per-sector auth failures."""

    def __init__(self, blocks: dict[int, bytes], auth_fail_sectors=frozenset()):
        self._blocks = dict(blocks)
        self._auth_fail_sectors = frozenset(auth_fail_sectors)

    def detect_card(self):
        return CardDetection(atqa=b"\x00\x04", uid=UID, sak=0x08)

    def load_key(self, key_type, sector, key):
        return True

    def auth_sector(self, sector, key_type=KEY_TYPE_A):
        return sector not in self._auth_fail_sectors

    def read_block(self, block):
        return self._blocks.get(block)


def _make_blocks(
    marker: bytes = b"FDS70V01",
    sig_bytes: bytes | None = None,
    event_type: int = 0x00,
) -> dict[int, bytes]:
    """Build a minimal valid S70 card image (sectors 0-2)."""
    sig_data = DEFAULT_SIGNATURE.key_a if sig_bytes is None else sig_bytes
    assert len(marker) == 8
    assert len(sig_data) == 6
    return {
        # Sector 0: identity
        0: UID + b"\x00\x00\x00\x00" + marker,
        1: bytes([1, 0, 0, 0, 0, 26, 6, 11, 1, 2, 0, 0]) + b"\xff" * 4,
        # Sector 1: system signature (KeyA repeated) + event count
        4: sig_data + b"\x01" + b"\xff" * 9,
        # Sector 2: one event
        8: bytes([event_type]) + b"\xaa\xbb\xcc\xdd"
           + bytes([26, 6, 11, 14, 30, 0]) + b"\xff" * 5,
    }


# --- Event type labels ------------------------------------------------------

def test_event_type_names_only_contains_verified_types():
    # Regression guard: 0x00 is the only event type verified against real
    # lock audit trails — re-adding invented names must fail this test.
    assert set(EVENT_TYPE_NAMES) == {0x00}


def test_verified_label_includes_raw_hex():
    assert event_type_label(0x00) == "Apertura con tarjeta (0x00)"


@pytest.mark.parametrize(
    "event_type",
    [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x10, 0x20, 0x40, 0x80, 0xC0],
)
def test_unverified_label_shows_raw_hex_and_sin_verificar(event_type):
    label = event_type_label(event_type)
    assert label == f"Tipo 0x{event_type:02X} (sin verificar)"
    assert f"0x{event_type:02X}" in label
    assert "(sin verificar)" in label


def test_decode_event_unverified_type_uses_unverified_label():
    block = bytes([0x40]) + b"\xaa\xbb\xcc\xdd" + bytes([26, 6, 11, 14, 30, 0]) + b"\xff" * 5
    event = _decode_event(block)
    assert event is not None
    assert event.event_type == 0x40
    assert event.event_type_name == "Tipo 0x40 (sin verificar)"


def test_decode_event_verified_type_includes_hex():
    block = bytes([0x00]) + b"\xaa\xbb\xcc\xdd" + bytes([26, 6, 11, 14, 30, 0]) + b"\xff" * 5
    event = _decode_event(block)
    assert event is not None
    assert event.event_type_name == "Apertura con tarjeta (0x00)"


# --- Sector 1 system signature ----------------------------------------------

def test_signature_match_on_valid_card():
    data = read_s70_card(FakeEncoder(_make_blocks()), DEFAULT_SIGNATURE)
    assert data is not None
    assert data.is_s70 is True
    assert data.signature_found == DEFAULT_SIGNATURE.key_a
    assert data.signature_match is True
    assert data.verdict == "Tarjeta S70 Data Card valida"


def test_signature_mismatch_warns_but_does_not_fail(caplog):
    foreign_sig = b"\xde\xad\xbe\xef\x00\x01"
    encoder = FakeEncoder(_make_blocks(sig_bytes=foreign_sig))
    with caplog.at_level(logging.WARNING, logger="setra_cards.cards.s70_reader"):
        data = read_s70_card(encoder, DEFAULT_SIGNATURE)
    assert data is not None  # warn, never hard-fail
    assert data.is_s70 is True
    assert data.signature_found == foreign_sig
    assert data.signature_match is False
    assert any("signature mismatch" in r.message for r in caplog.records)
    assert "ADVERTENCIA" in data.verdict
    assert "no coincide" in data.verdict
    assert data.signature_hex in data.verdict  # raw hex shown to operator


def test_signature_unreadable_sector1_is_none(caplog):
    encoder = FakeEncoder(_make_blocks(), auth_fail_sectors={1})
    with caplog.at_level(logging.WARNING, logger="setra_cards.cards.s70_reader"):
        data = read_s70_card(encoder, DEFAULT_SIGNATURE)
    assert data is not None
    assert data.signature_found is None
    assert data.signature_match is None
    assert data.signature_hex == "—"
    assert "firma de sistema no legible" in data.verdict
    assert any("cannot verify system signature" in r.message for r in caplog.records)


# --- Verdict: sector 0 unreadable vs marker absent ---------------------------

def test_verdict_sector0_unreadable():
    encoder = FakeEncoder(_make_blocks(), auth_fail_sectors={0})
    data = read_s70_card(encoder, DEFAULT_SIGNATURE)
    assert data is not None
    assert data.sector0_readable is False
    assert data.is_s70 is False
    assert data.verdict == "Sector 0 ilegible — no se pudo verificar la marca S70"


def test_verdict_marker_absent_on_readable_sector0():
    encoder = FakeEncoder(_make_blocks(marker=b"OTRACOSA"))
    data = read_s70_card(encoder, DEFAULT_SIGNATURE)
    assert data is not None
    assert data.sector0_readable is True
    assert data.is_s70 is False
    assert "Marca FDS70 ausente" in data.verdict
    assert "OTRACOSA" in data.verdict
    assert data.verdict != "Sector 0 ilegible — no se pudo verificar la marca S70"
