"""Cryptographic operations for Locstar cards.

- DES-ECB encryption of UID (block 44)
- XOR checksum (block 46, byte 15)
- Signature-derived KeyA for MIFARE trailer (block 47)
"""

from dataclasses import dataclass
from functools import reduce
from operator import xor

from Crypto.Cipher import DES

from setra_cards.encoder.protocol import (
    ACCESS_BITS,
    BUILDING_ID,
    DES_KEY,
    KEY_FF,
    TRAILER_KEY_B,
)


@dataclass(frozen=True)
class SystemSignature:
    """System signature derived from sys_codecheck.

    For sys_codecheck=01653B: sig_hi=0x95, sig_lo=0xB8, prefix=0x15.
    KeyA = [prefix, sig_hi, sig_lo] repeated = 15 95 B8 15 95 B8.
    """

    sig_hi: int
    sig_lo: int
    prefix: int

    @property
    def signature(self) -> bytes:
        return bytes([self.sig_hi, self.sig_lo])

    @property
    def key_a(self) -> bytes:
        unit = bytes([self.prefix, self.sig_hi, self.sig_lo])
        return unit + unit

    @property
    def auth_sig_block(self) -> bytes:
        """Extended signature for Authorization card block 46: 15 95 B8."""
        return bytes([self.prefix]) + self.signature


# Default installation signature (sys_codecheck=01653B)
DEFAULT_SIGNATURE = SystemSignature(sig_hi=0x95, sig_lo=0xB8, prefix=0x15)


def make_block44(uid: bytes, des_key: bytes = DES_KEY, building: int = BUILDING_ID) -> bytes:
    """DES-ECB encrypt UID to produce block 44 (16 bytes).

    Plaintext (8 bytes): [UID0, UID1, UID2, UID3, building, 0x00, 0x00, 0x00]
    Result: encrypted(8) + zeros(8)
    """
    if len(uid) != 4:
        raise ValueError(f"UID must be 4 bytes, got {len(uid)}")
    plaintext = bytes([uid[0], uid[1], uid[2], uid[3], building, 0x00, 0x00, 0x00])
    cipher = DES.new(des_key, DES.MODE_ECB)
    encrypted = cipher.encrypt(plaintext)
    return encrypted + bytes(8)


def decrypt_block44(block44: bytes, des_key: bytes = DES_KEY) -> bytes:
    """Decrypt block 44 to recover UID plaintext (8 bytes)."""
    cipher = DES.new(des_key, DES.MODE_ECB)
    return cipher.decrypt(block44[:8])


def xor_checksum(block45: bytes, block46_prefix: bytes) -> int:
    """XOR checksum: XOR of block45 (16 bytes) + block46 bytes 0-14 (15 bytes) = 31 bytes."""
    if len(block45) != 16:
        raise ValueError(f"block45 must be 16 bytes, got {len(block45)}")
    if len(block46_prefix) != 15:
        raise ValueError(f"block46_prefix must be 15 bytes, got {len(block46_prefix)}")
    return reduce(xor, block45 + block46_prefix, 0)


def make_trailer(sig: SystemSignature) -> bytes:
    """Build MIFARE sector trailer (16 bytes): KeyA + AccessBits + KeyB.

    KeyA derived from system signature.
    """
    return sig.key_a + ACCESS_BITS + TRAILER_KEY_B


def make_auth_trailer() -> bytes:
    """Build trailer for Authorization card (KeyA = FF FF FF FF FF FF)."""
    return KEY_FF + ACCESS_BITS + TRAILER_KEY_B
