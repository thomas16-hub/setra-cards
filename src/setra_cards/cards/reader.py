"""Card reading and decoding.

Reads sector data from a MIFARE card and decodes the Locstar fields.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime

from setra_cards.cards.crypto import SystemSignature, decrypt_block44, xor_checksum
from setra_cards.encoder.driver import EncoderDriver
from setra_cards.encoder.protocol import (
    CARD_TYPE_NAMES,
    KEY_FF,
    KEY_TYPE_A,
    KEY_TYPE_B,
    TRAILER_KEY_B,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecodedCard:
    uid: bytes
    card_type: int
    card_type_name: str
    room: int
    date1: datetime | None
    date2: datetime | None
    building: int
    floor: int
    room_no_id: int
    signature: bytes
    checksum_valid: bool
    uid_match: bool
    raw_blocks: dict[int, bytes]


def _decode_date(data: bytes, offset: int) -> datetime | None:
    """Decode 5 bytes [YY, MM, DD, HH, MM] from offset."""
    try:
        y, m, d, h, mn = data[offset : offset + 5]
        if y == 0 and m == 0:
            return None
        return datetime(2000 + y, m, d, h, mn)
    except (ValueError, IndexError):
        return None


def read_card(
    encoder: EncoderDriver,
    sector: int,
    sig: SystemSignature,
    des_key: bytes | None = None,
) -> DecodedCard | None:
    """Read and decode a Locstar card from the given sector."""
    detection = encoder.detect_card()
    if not detection:
        return None
    uid = detection.uid
    encoder.halt()
    encoder.beep()
    time.sleep(0.18)  # Let beep complete before MIFARE ops interrupt it

    # Try keys in order: FF-A, Sig-A, TrailerB-B
    key_attempts = [
        ("FF-A", KEY_FF, KEY_TYPE_A),
        ("Sig-A", sig.key_a, KEY_TYPE_A),
        ("TrailerB-B", TRAILER_KEY_B, KEY_TYPE_B),
    ]

    raw_blocks: dict[int, bytes] = {}
    for key_name, key, kt in key_attempts:
        encoder.load_key(kt, sector, key)
        encoder.detect_card()
        if encoder.auth_sector(sector, kt):
            logger.debug("Read auth OK with %s", key_name)
            for i in range(4):
                block_num = sector * 4 + i
                data = encoder.read_block(block_num)
                if data:
                    raw_blocks[block_num] = data
            break

    if not raw_blocks:
        return None

    b44 = raw_blocks.get(sector * 4)
    b45 = raw_blocks.get(sector * 4 + 1)
    b46 = raw_blocks.get(sector * 4 + 2)

    if not b44 or not b45 or not b46:
        return None

    # Decrypt block 44 and verify UID
    from setra_cards.encoder.protocol import DES_KEY as _DEFAULT_KEY
    plain = decrypt_block44(b44, des_key=des_key if des_key is not None else _DEFAULT_KEY)
    uid_match = plain[:4] == uid

    # Decode block 45
    card_type = b45[0]
    room = b45[1]
    date1 = _decode_date(b45, 2)
    date2 = _decode_date(b45, 7)
    building = b45[12]
    floor = b45[13]
    room_no_id = b45[14]

    # Verify checksum
    expected_xor = xor_checksum(b45, b46[:15])
    checksum_valid = expected_xor == b46[15]

    signature_bytes = bytes([b46[13], b46[14]])

    encoder.halt()
    encoder.beep()

    return DecodedCard(
        uid=uid,
        card_type=card_type,
        card_type_name=CARD_TYPE_NAMES.get(card_type, f"Unknown(0x{card_type:02X})"),
        room=room,
        date1=date1,
        date2=date2,
        building=building,
        floor=floor,
        room_no_id=room_no_id,
        signature=signature_bytes,
        checksum_valid=checksum_valid,
        uid_match=uid_match,
        raw_blocks=raw_blocks,
    )
