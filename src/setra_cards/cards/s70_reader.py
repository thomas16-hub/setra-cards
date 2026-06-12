"""S70 Data Collection card reader.

Reads audit trail events from Locstar S70 data cards.
These cards collect lock access logs when presented to a lock.

Card structure:
- Sector 0: Identity — UID + "FDS70V01" marker + card info
- Sector 1: System signature (15 95 B8) + building/floor/zone
- Sectors 2-15: Event storage (11 bytes per event + 5 padding)

Event format (16 bytes per block):
  [0]    Event type (0x00 = card access)
  [1-4]  UID of the card that opened the lock
  [5]    Year - 2000
  [6]    Month
  [7]    Day
  [8]    Hour
  [9]    Minute
  [10]   Extra byte
  [11-15] FF padding
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from setra_cards.cards.crypto import SystemSignature
from setra_cards.encoder.driver import EncoderDriver
from setra_cards.encoder.protocol import KEY_FF, KEY_TYPE_A, KEY_TYPE_B, TRAILER_KEY_B

logger = logging.getLogger(__name__)

# Only 0x00 has been verified against real lock audit trails (PROTOCOL.md §7).
# Any other type byte gets a raw-hex label marked "(sin verificar)" — we do
# NOT invent semantics for unverified bytes (they previously contradicted the
# card-type convention: 0x20 Laundry, 0x40 Master, 0x80 Setting, 0xC0 Auth).
EVENT_TYPE_NAMES = {
    0x00: "Apertura con tarjeta (0x00)",
}


def event_type_label(event_type: int) -> str:
    """Operator-facing label for an event type byte (Spanish).

    Always includes the raw hex byte. Unverified types are labeled
    "Tipo 0xNN (sin verificar)" instead of an invented name.
    """
    verified_name = EVENT_TYPE_NAMES.get(event_type)
    if verified_name is not None:
        return verified_name
    return f"Tipo 0x{event_type:02X} (sin verificar)"


S70_MARKER = b"FDS70V01"

# Sector 1, block 4, bytes 0-5: system signature = KeyA repeated
# (PROTOCOL.md §7: "Block 4: [15 95 B8 15 95 B8] [0B] [FF*9]").
SIGNATURE_BLOCK = 4
SIGNATURE_LEN = 6


@dataclass(frozen=True)
class S70Event:
    """Single audit trail event from lock."""
    event_type: int
    event_type_name: str
    card_uid: bytes
    timestamp: datetime | None
    extra: int
    raw: bytes

    @property
    def card_uid_hex(self) -> str:
        return self.card_uid.hex(":")

    @property
    def timestamp_str(self) -> str:
        if self.timestamp:
            return self.timestamp.strftime("%d/%m/%Y %H:%M")
        return "—"


@dataclass(frozen=True)
class S70CardData:
    """Decoded S70 data collection card."""
    uid: bytes
    is_s70: bool
    marker: str
    card_number: int
    collection_date: datetime | None
    building: int
    floor: int
    zone: int
    events: tuple[S70Event, ...]
    sectors_read: int
    raw_blocks: dict[int, bytes] = field(default_factory=dict)
    sector0_readable: bool = True
    signature_found: bytes | None = None
    signature_match: bool | None = None

    @property
    def uid_hex(self) -> str:
        return self.uid.hex(":")

    @property
    def event_count(self) -> int:
        return len(self.events)

    @property
    def collection_date_str(self) -> str:
        if self.collection_date:
            return self.collection_date.strftime("%d/%m/%Y")
        return "—"

    @property
    def signature_hex(self) -> str:
        if self.signature_found is None:
            return "—"
        return self.signature_found.hex(" ").upper()

    @property
    def verdict(self) -> str:
        """Operator-facing verdict (Spanish).

        Distinguishes "sector 0 unreadable" from "marker absent", and
        appends a signature warning (never a hard fail) when the sector 1
        system signature does not match this hotel.
        """
        if not self.sector0_readable:
            return "Sector 0 ilegible — no se pudo verificar la marca S70"
        if not self.is_s70:
            return (
                f"Marca FDS70 ausente (sector 0 leido, marca: '{self.marker}') "
                "— no es una tarjeta S70 Data Card"
            )
        if self.signature_match is False:
            return (
                "Tarjeta S70 Data Card valida — ADVERTENCIA: la firma de "
                f"sistema ({self.signature_hex}) no coincide con este hotel"
            )
        if self.signature_match is None:
            return (
                "Tarjeta S70 Data Card valida — firma de sistema no legible "
                "(sector 1)"
            )
        return "Tarjeta S70 Data Card valida"


def _decode_event(block_data: bytes) -> S70Event | None:
    """Decode a single 16-byte event block. Returns None if block is empty/padding."""
    if len(block_data) < 16:
        return None

    # Skip empty blocks (all zeros or all FF)
    if block_data == b"\x00" * 16 or block_data == b"\xff" * 16:
        return None

    # Skip blocks where the UID area is all FF (unused event slot)
    if block_data[1:5] == b"\xff\xff\xff\xff":
        return None

    event_type = block_data[0]
    card_uid = block_data[1:5]
    extra = block_data[10]

    # Decode timestamp — validate ranges before constructing datetime to avoid silent failures
    timestamp = None
    try:
        y, m, d, h, mn = block_data[5], block_data[6], block_data[7], block_data[8], block_data[9]
        if (y > 0 or m > 0) and 1 <= m <= 12 and 1 <= d <= 31 and 0 <= h <= 23 and 0 <= mn <= 59:
            timestamp = datetime(2000 + y, m, d, h, mn)
    except (ValueError, IndexError):
        pass

    return S70Event(
        event_type=event_type,
        event_type_name=event_type_label(event_type),
        card_uid=card_uid,
        timestamp=timestamp,
        extra=extra,
        raw=block_data,
    )


def _try_auth_sector(
    encoder: EncoderDriver,
    sector: int,
    sig: SystemSignature,
) -> bool:
    """Try to authenticate a sector with known keys. Returns True on success."""
    key_attempts = [
        ("FF-A", KEY_FF, KEY_TYPE_A),
        ("Sig-A", sig.key_a, KEY_TYPE_A),
        ("TrailerB-B", TRAILER_KEY_B, KEY_TYPE_B),
    ]
    for key_name, key, kt in key_attempts:
        encoder.load_key(kt, sector, key)
        encoder.detect_card()
        if encoder.auth_sector(sector, kt):
            logger.debug("S70 sector %d auth OK with %s", sector, key_name)
            return True
    logger.warning("S70 sector %d: auth failed with all keys (FF-A, Sig-A, TrailerB-B)", sector)
    return False


def read_s70_card(
    encoder: EncoderDriver,
    sig: SystemSignature,
    max_sectors: int = 16,
) -> S70CardData | None:
    """Read and decode an S70 data collection card.

    Scans sectors 0-15 (or up to max_sectors), collecting identity info
    and audit trail events.
    """
    detection = encoder.detect_card()
    if not detection:
        logger.warning("S70 read: no card detected on encoder")
        return None
    uid = detection.uid
    logger.info("S70 read: card detected UID=%s ATQA=%s SAK=0x%02X",
                uid.hex(":"), detection.atqa.hex(), detection.sak)

    raw_blocks: dict[int, bytes] = {}
    sectors_read = 0

    # Read all sectors (0 through max_sectors-1)
    for sector in range(max_sectors):
        if not _try_auth_sector(encoder, sector, sig):
            continue
        sectors_read += 1
        for i in range(3):  # Skip trailer (block 3 of each sector)
            block_num = sector * 4 + i
            data = encoder.read_block(block_num)
            if data:
                raw_blocks[block_num] = data

    if not raw_blocks:
        return None

    # Decode sector 0 — Identity. Track readability so the verdict can
    # distinguish "sector 0 unreadable" from "marker absent".
    sector0_readable = 0 in raw_blocks
    block0 = raw_blocks.get(0, b"\x00" * 16)
    block1 = raw_blocks.get(1, b"\x00" * 16)

    # Check for FDS70V01 marker in block 0
    marker_bytes = block0[8:16]
    try:
        marker = marker_bytes.decode("ascii", errors="replace")
    except Exception:
        marker = ""

    is_s70 = sector0_readable and marker.startswith("FDS70")

    # Sector 1, block 4: system signature (KeyA repeated). Compare against
    # this hotel's sig.key_a and warn — never hard-fail — on mismatch.
    sig_block = raw_blocks.get(SIGNATURE_BLOCK)
    signature_found: bytes | None = None
    signature_match: bool | None = None
    if sig_block is not None and len(sig_block) >= SIGNATURE_LEN:
        signature_found = bytes(sig_block[:SIGNATURE_LEN])
        signature_match = signature_found == sig.key_a
        if not signature_match:
            logger.warning(
                "S70 system signature mismatch: card has %s, hotel expects %s "
                "(card likely from another installation)",
                signature_found.hex(" "), sig.key_a.hex(" "),
            )
    else:
        logger.warning("S70 sector 1 block %d unreadable: cannot verify system signature",
                       SIGNATURE_BLOCK)

    # Block 1: [card_num] [??*4] [YY] [MM] [DD] [building] [floor] [zone] [00] [FF*4]
    card_number = block1[0]
    collection_date = None
    try:
        y, m, d = block1[5], block1[6], block1[7]
        if y > 0 or m > 0:
            collection_date = datetime(2000 + y, m, d)
    except (ValueError, IndexError):
        pass
    building = block1[8] if len(block1) > 8 else 0
    floor = block1[9] if len(block1) > 9 else 0
    zone = block1[10] if len(block1) > 10 else 0

    # Decode events from sectors 2-15 (skip sector 0=identity, sector 1=signature)
    events: list[S70Event] = []
    for sector in range(2, max_sectors):
        for i in range(3):  # 3 data blocks per sector (skip trailer)
            block_num = sector * 4 + i
            block_data = raw_blocks.get(block_num)
            if block_data:
                event = _decode_event(block_data)
                if event:
                    events.append(event)

    # Sort events by timestamp (most recent first)
    events.sort(key=lambda e: e.timestamp or datetime.min, reverse=True)

    return S70CardData(
        uid=uid,
        is_s70=is_s70,
        marker=marker.strip(),
        card_number=card_number,
        collection_date=collection_date,
        building=building,
        floor=floor,
        zone=zone,
        events=tuple(events),
        sectors_read=sectors_read,
        raw_blocks=raw_blocks,
        sector0_readable=sector0_readable,
        signature_found=signature_found,
        signature_match=signature_match,
    )
