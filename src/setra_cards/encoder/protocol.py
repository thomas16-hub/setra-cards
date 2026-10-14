"""Locstar encoder protocol constants and command definitions.

Protocol decoded by reverse engineering MF_System.exe (Delphi 7) v5.0.2.

TX format: <LEN> <CMD> [DATA...]
RX format: <LEN> <STATUS> [DATA...]
Baud: 9600, 8N1
"""

from dataclasses import dataclass

# --- Key types for MIFARE auth ---
KEY_TYPE_A = 0x60
KEY_TYPE_B = 0x61

# --- Well-known keys ---
KEY_FF = bytes([0xFF] * 6)
TRAILER_KEY_B = bytes([0xB4, 0xB4, 0xBC, 0xD1, 0xCB, 0xF8])
ACCESS_BITS = bytes([0x78, 0x77, 0x88, 0x69])
DEFAULT_TRAILER = KEY_FF + bytes([0xFF, 0x07, 0x80, 0x69]) + KEY_FF

# --- DES encryption ---
DES_KEY = b"god\x00\x00\x00\x00\x00"
BUILDING_ID = 0x01

# --- Card type bytes (block 45, byte 0) ---
# byte[0] = original card-type index * 0x10 (SETCARD.INI [0]..[20]).
# Verified 2026-10-14 against 94 cards emitted by MF_System v5.0.2
# (log/IssueLog.mdb, column card_date_hex). Before that, MASTER was
# wrongly 0x40 (= House Keeping) and LAUNDRY 0x20 (= Check Out).
CARD_TYPE_GUEST = 0x00
CARD_TYPE_CHECKOUT = 0x20
CARD_TYPE_FLOOR = 0x30
CARD_TYPE_HOUSEKEEPING = 0x40
CARD_TYPE_BUILDING = 0x50
CARD_TYPE_MASTER = 0x60
CARD_TYPE_EMERGENCY = 0x70
CARD_TYPE_SETTING = 0x80
CARD_TYPE_CLOCK = 0xA0
CARD_TYPE_AUTH = 0xC0

# SETRA's "Limpieza" card is a short-lived Master (opens every room, expires
# in hours). Same firmware type; the app tells them apart by its own log.
CARD_TYPE_LAUNDRY = CARD_TYPE_MASTER

CARD_TYPE_NAMES = {
    CARD_TYPE_GUEST: "Guest",
    CARD_TYPE_CHECKOUT: "Check Out",
    CARD_TYPE_FLOOR: "Floor",
    CARD_TYPE_HOUSEKEEPING: "House Keeping",
    CARD_TYPE_BUILDING: "Building",
    CARD_TYPE_MASTER: "Master",
    CARD_TYPE_EMERGENCY: "Emergency",
    CARD_TYPE_SETTING: "Setting",
    CARD_TYPE_CLOCK: "Clock",
    CARD_TYPE_AUTH: "Authorization",
}

# --- Encoder commands (TX bytes) ---
CMD_BEEP = bytes([0x01, 0x06])
CMD_REQUEST_ALL = bytes([0x02, 0x02, 0x52])
CMD_REQUEST_IDLE = bytes([0x02, 0x02, 0x26])
CMD_ANTICOLLISION = bytes([0x01, 0x03])
CMD_SELECT = bytes([0x01, 0x04])
CMD_HALT = bytes([0x01, 0x0B])

# --- ATQA card type detection ---
ATQA_S50 = bytes([0x04, 0x00])  # MIFARE Classic 1K
ATQA_S70 = bytes([0x02, 0x00])  # MIFARE Classic 4K

SAK_1K = 0x08
SAK_4K = 0x18


@dataclass(frozen=True)
class CardDetection:
    """Result of card detection (Request + Anticollision + Select)."""
    atqa: bytes
    uid: bytes
    sak: int

    @property
    def is_4k(self) -> bool:
        return self.atqa == ATQA_S70 or self.sak == SAK_4K

    @property
    def card_type_name(self) -> str:
        if self.sak == SAK_1K:
            return "MIFARE Classic 1K"
        if self.sak == SAK_4K:
            return "MIFARE Classic 4K"
        return f"Unknown (SAK=0x{self.sak:02X})"
