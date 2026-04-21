"""Card write orchestration.

Handles the full sequence: detect card, authenticate sector, write 4 blocks.
Tries multiple key combinations to handle blank and pre-programmed cards.
"""

import logging
import time
from dataclasses import dataclass

from setra_cards.cards.builder import CardData
from setra_cards.cards.crypto import SystemSignature, make_block44
from setra_cards.encoder.driver import EncoderDriver
from setra_cards.encoder.protocol import (
    DEFAULT_TRAILER,
    DES_KEY,
    KEY_FF,
    KEY_TYPE_A,
    KEY_TYPE_B,
    TRAILER_KEY_B,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WriteResult:
    success: bool
    uid: bytes | None = None
    error: str | None = None


def _auth_keys_standard(sig: SystemSignature) -> list[tuple[str, bytes, int]]:
    """Key attempts for Clock/Setting/Guest cards."""
    return [
        ("FF-A", KEY_FF, KEY_TYPE_A),
        ("Sig-A", sig.key_a, KEY_TYPE_A),
        ("TrailerB-B", TRAILER_KEY_B, KEY_TYPE_B),
        ("FF-B", KEY_FF, KEY_TYPE_B),
    ]


def _auth_keys_auth_card() -> list[tuple[str, bytes, int]]:
    """Key attempts for Authorization card (only FF and TrailerB)."""
    return [
        ("FF-A", KEY_FF, KEY_TYPE_A),
        ("TrailerB-B", TRAILER_KEY_B, KEY_TYPE_B),
    ]


def _write_blocks_with_keys(
    encoder: EncoderDriver,
    sector: int,
    blocks: list[tuple[int, bytes, str]],
    key_list: list[tuple[str, bytes, int]],
) -> tuple[bool, str]:
    """Escribe los 4 bloques. Por bloque prueba cada key hasta conseguir
    auth+write exitosos. Retorna (ok, error_detail)."""
    for block_num, data, name in blocks:
        encoder.detect_card()
        written = False
        last_err = ""
        for key_name, key, kt in key_list:
            encoder.load_key(kt, sector, key)
            encoder.detect_card()
            if not encoder.auth_sector(sector, kt):
                last_err = f"auth {key_name} FAIL"
                continue
            if encoder.write_block(block_num, data):
                logger.debug("Write OK with %s for %s", key_name, name)
                written = True
                break
            last_err = f"auth {key_name} OK pero write FAIL"
            encoder.detect_card()
        if not written:
            return False, f"{name}: {last_err}"
    return True, ""


def write_card(
    encoder: EncoderDriver,
    sector: int,
    sig: SystemSignature,
    card: CardData,
    des_key: bytes = DES_KEY,
    building: int = 1,
) -> WriteResult:
    """Write a complete card to MIFARE sector.

    Sequence per block: re-detect → load key → auth → write.
    Tries multiple keys to handle blank and pre-programmed cards.
    Si falla, hace blank automatico y reintenta una vez.
    """
    detection = encoder.detect_card()
    if not detection:
        return WriteResult(success=False, error="No card detected")

    uid = detection.uid
    block44 = make_block44(uid, des_key=des_key, building=building)
    encoder.halt()
    encoder.beep()
    time.sleep(0.18)  # Let beep complete before MIFARE ops interrupt it

    is_auth = card.card_type == 0xC0
    key_list = _auth_keys_auth_card() if is_auth else _auth_keys_standard(sig)

    blocks = [
        (sector * 4, block44, "block 44"),
        (sector * 4 + 1, card.block45, "block 45"),
        (sector * 4 + 2, card.block46, "block 46"),
        (sector * 4 + 3, card.trailer, "trailer"),
    ]

    # Intento 1: write directo
    ok, err = _write_blocks_with_keys(encoder, sector, blocks, key_list)

    # Intento 2: si fallo, hacer blank y reintentar
    if not ok:
        logger.info("Write fallo (%s). Intentando auto-blank + rewrite...", err)
        blank_res = blank_card(encoder, sector)
        if not blank_res.success:
            return WriteResult(
                success=False, uid=uid,
                error=f"No se pudo escribir ni borrar la tarjeta: {err} / blank: {blank_res.error}",
            )
        # Tras blank, la tarjeta tiene keys FF — reintentar con key_list entero
        ok, err = _write_blocks_with_keys(encoder, sector, blocks, key_list)
        if not ok:
            return WriteResult(
                success=False, uid=uid,
                error=f"Write fallo incluso tras auto-blank: {err}",
            )
        logger.info("Auto-blank + rewrite exitoso tras fallo inicial")

    logger.info("Card programmed OK: UID=%s", uid.hex(":"))
    encoder.halt()
    encoder.beep()
    time.sleep(0.12)
    encoder.beep()
    return WriteResult(success=True, uid=uid)


def blank_card(encoder: EncoderDriver, sector: int) -> WriteResult:
    """Restore card sector to blank state (zeros + default trailer)."""
    detection = encoder.detect_card()
    if not detection:
        return WriteResult(success=False, error="No card detected")
    uid = detection.uid
    encoder.halt()
    encoder.beep()
    time.sleep(0.18)

    # Auth with any available key
    key_attempts = [
        ("TrailerB-B", TRAILER_KEY_B, KEY_TYPE_B),
        ("FF-B", KEY_FF, KEY_TYPE_B),
        ("FF-A", KEY_FF, KEY_TYPE_A),
    ]

    authed = False
    for key_name, key, kt in key_attempts:
        encoder.load_key(kt, sector, key)
        encoder.detect_card()
        if encoder.auth_sector(sector, kt):
            authed = True
            break

    if not authed:
        return WriteResult(success=False, uid=uid, error="Auth failed — no key worked")

    # Blank data blocks (0-2) — re-auth before each block write
    zeros = bytes(16)
    for i in range(3):
        block = sector * 4 + i
        encoder.detect_card()
        block_authed = False
        for key_name, key, kt in key_attempts:
            encoder.load_key(kt, sector, key)
            encoder.detect_card()
            if encoder.auth_sector(sector, kt):
                block_authed = True
                break
        if not block_authed:
            return WriteResult(success=False, uid=uid, error=f"Auth failed before blanking block {block}")
        if not encoder.write_block(block, zeros):
            return WriteResult(success=False, uid=uid, error=f"Write failed for block {block}")

    # Restore default trailer
    encoder.detect_card()
    trailer_authed = False
    for key_name, key, kt in key_attempts:
        encoder.load_key(kt, sector, key)
        encoder.detect_card()
        if encoder.auth_sector(sector, kt):
            trailer_authed = True
            break

    trailer_block = sector * 4 + 3
    if not trailer_authed:
        return WriteResult(success=False, uid=uid, error="Auth failed before trailer restore — data blocks blanked but trailer unchanged")
    if not encoder.write_block(trailer_block, DEFAULT_TRAILER):
        return WriteResult(success=False, uid=uid, error="Trailer write failed — data blocks blanked")

    encoder.halt()
    encoder.beep()
    time.sleep(0.12)
    encoder.beep()
    return WriteResult(success=True, uid=uid)
