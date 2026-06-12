"""Card write orchestration.

Handles the full sequence: detect card, authenticate sector, write 4 blocks.
Tries multiple key combinations to handle blank and pre-programmed cards.
"""

import logging
import time
from dataclasses import dataclass

from setra_cards.cards.builder import CardData
from setra_cards.cards.crypto import SystemSignature, make_block44
from setra_cards.cards.reader import DecodedCard, read_card
from setra_cards.encoder.driver import EncoderDriver
from setra_cards.encoder.protocol import (
    CARD_TYPE_LAUNDRY,
    CARD_TYPE_MASTER,
    DEFAULT_TRAILER,
    DES_KEY,
    KEY_FF,
    KEY_TYPE_A,
    KEY_TYPE_B,
    TRAILER_KEY_B,
)

logger = logging.getLogger(__name__)

# Global Master/Laundry cards MUST keep tail bytes 12-15 = 00 00 00 00
# (the lock firmware identifies them by card_type, not room_no_id).
GLOBAL_TAIL = b"\x00\x00\x00\x00"

# Operator-facing error when a different card is detected mid-write.
CARD_SWAPPED_ERROR = "La tarjeta cambió durante la grabación"


@dataclass(frozen=True)
class WriteResult:
    success: bool
    uid: bytes | None = None
    error: str | None = None


def _auth_keys_standard(sig: SystemSignature) -> list[tuple[str, bytes, int]]:
    """Key attempts for Clock/Setting/Guest cards.

    Sig-A first: on a re-issued (system-trailer) card it is the working key,
    so trying it first avoids two doomed KeyA attempts per block. Blank cards
    simply fall through to FF-A/FF-B.
    """
    return [
        ("Sig-A", sig.key_a, KEY_TYPE_A),
        ("TrailerB-B", TRAILER_KEY_B, KEY_TYPE_B),
        ("FF-A", KEY_FF, KEY_TYPE_A),
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
    original_uid: bytes | None = None,
) -> tuple[bool, str]:
    """Write the 4 blocks. Per block, try each key until auth+write succeed.

    If original_uid is given, every re-detect checks the UID still matches;
    a different card mid-write aborts everything with CARD_SWAPPED_ERROR.
    Returns (ok, error_detail).
    """

    def _uid_changed() -> bool:
        detection = encoder.detect_card()
        return (
            original_uid is not None
            and detection is not None
            and detection.uid != original_uid
        )

    for block_num, data, name in blocks:
        if _uid_changed():
            return False, CARD_SWAPPED_ERROR
        written = False
        last_err = ""
        for key_name, key, kt in key_list:
            encoder.load_key(kt, sector, key)
            if _uid_changed():
                return False, CARD_SWAPPED_ERROR
            if not encoder.auth_sector(sector, kt):
                last_err = f"auth {key_name} FAIL"
                continue
            if encoder.write_block(block_num, data):
                logger.debug("Write OK with %s for %s", key_name, name)
                written = True
                break
            last_err = f"auth {key_name} OK pero write FAIL"
            if _uid_changed():
                return False, CARD_SWAPPED_ERROR
        if not written:
            return False, f"{name}: {last_err}"
    return True, ""


def _verify_decoded(
    decoded: DecodedCard | None,
    expected_uid: bytes,
    expected_card_type: int,
    sector: int,
) -> str | None:
    """Validate a read-back card against what was just written.

    Returns an operator-facing (Spanish) error message, or None if the
    verification passes.
    """
    if (
        decoded is None
        or decoded.uid != expected_uid
        or not decoded.uid_match
        or not decoded.checksum_valid
        or decoded.card_type != expected_card_type
    ):
        return "Verificación falló: la tarjeta no se grabó correctamente"
    if expected_card_type in (CARD_TYPE_MASTER, CARD_TYPE_LAUNDRY):
        block45 = decoded.raw_blocks.get(sector * 4 + 1)
        if block45 is None or block45[12:16] != GLOBAL_TAIL:
            return "Verificación falló: tail maestra/limpieza incorrecto"
    return None


def _verify_after_write(
    encoder: EncoderDriver,
    sector: int,
    sig: SystemSignature,
    des_key: bytes,
    card: CardData,
    expected_uid: bytes,
) -> str | None:
    """Read the card back and verify it matches what was written.

    Returns an operator-facing error message, or None on success.
    """
    encoder.halt()
    try:
        decoded = read_card(encoder, sector, sig, des_key=des_key)
    except Exception:
        logger.exception("Read-back verification raised unexpectedly")
        return "Verificación falló: la tarjeta no se grabó correctamente"
    error = _verify_decoded(decoded, expected_uid, card.card_type, sector)
    if error:
        logger.warning(
            "Post-write verification failed for UID=%s: %s",
            expected_uid.hex(":"), error,
        )
    return error


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
        return WriteResult(success=False, error="No se detectó ninguna tarjeta")

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

    # Attempt 1: direct write
    ok, err = _write_blocks_with_keys(encoder, sector, blocks, key_list, original_uid=uid)

    # A swapped card must never trigger auto-blank (it would erase the wrong card)
    if not ok and err == CARD_SWAPPED_ERROR:
        return WriteResult(success=False, uid=uid, error=CARD_SWAPPED_ERROR)

    # Attempt 2: on failure, blank the sector and retry once
    if not ok:
        logger.info("Write failed (%s). Trying auto-blank + rewrite...", err)
        blank_res = blank_card(encoder, sector)
        if not blank_res.success:
            return WriteResult(
                success=False, uid=uid,
                error=f"No se pudo escribir ni borrar la tarjeta: {err} / blank: {blank_res.error}",
            )
        # After blank the card has FF keys — retry with the full key list
        ok, err = _write_blocks_with_keys(encoder, sector, blocks, key_list, original_uid=uid)
        if not ok:
            if err == CARD_SWAPPED_ERROR:
                return WriteResult(success=False, uid=uid, error=CARD_SWAPPED_ERROR)
            return WriteResult(
                success=False, uid=uid,
                error=f"Write fallo incluso tras auto-blank: {err}",
            )
        logger.info("Auto-blank + rewrite succeeded after initial failure")

    # Read-back verification: never report success on a card that did not
    # actually take the data (or kept a stale tail on master/laundry).
    verify_error = _verify_after_write(encoder, sector, sig, des_key, card, uid)
    if verify_error:
        return WriteResult(success=False, uid=uid, error=verify_error)

    logger.info("Card programmed and verified OK: UID=%s", uid.hex(":"))
    encoder.halt()
    encoder.beep()
    time.sleep(0.12)
    encoder.beep()
    return WriteResult(success=True, uid=uid)


def _auth_with_any_key(
    encoder: EncoderDriver,
    sector: int,
    key_attempts: list[tuple[str, bytes, int]],
) -> bool:
    """Try each key (re-detecting before auth) until one authenticates."""
    for _key_name, key, kt in key_attempts:
        encoder.load_key(kt, sector, key)
        encoder.detect_card()
        if encoder.auth_sector(sector, kt):
            return True
    return False


def blank_card(encoder: EncoderDriver, sector: int) -> WriteResult:
    """Restore card sector to blank state (zeros + default trailer)."""
    detection = encoder.detect_card()
    if not detection:
        return WriteResult(success=False, error="No se detectó ninguna tarjeta")
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

    if not _auth_with_any_key(encoder, sector, key_attempts):
        return WriteResult(
            success=False, uid=uid,
            error="No se pudo autenticar la tarjeta: ninguna llave funcionó",
        )

    # Blank data blocks (0-2) — re-auth before each block write
    zeros = bytes(16)
    for i in range(3):
        block = sector * 4 + i
        encoder.detect_card()
        if not _auth_with_any_key(encoder, sector, key_attempts):
            return WriteResult(
                success=False, uid=uid,
                error=f"Fallo de autenticación antes de borrar el bloque {block}",
            )
        if not encoder.write_block(block, zeros):
            return WriteResult(
                success=False, uid=uid,
                error=f"Fallo de escritura al borrar el bloque {block}",
            )

    # Restore default trailer — a failure here leaves the data blocks zeroed
    # with a non-default trailer (dead card), so retry once before giving up.
    trailer_block = sector * 4 + 3
    trailer_restored = False
    for attempt in (1, 2):
        encoder.detect_card()
        if not _auth_with_any_key(encoder, sector, key_attempts):
            logger.warning("Trailer restore attempt %d: auth failed", attempt)
            continue
        if encoder.write_block(trailer_block, DEFAULT_TRAILER):
            trailer_restored = True
            break
        logger.warning("Trailer restore attempt %d: write failed", attempt)

    if not trailer_restored:
        return WriteResult(
            success=False, uid=uid,
            error=(
                "No se pudo restaurar el trailer tras borrar los datos "
                "(reintento agotado). Vuelva a intentar el borrado con la misma tarjeta."
            ),
        )

    encoder.halt()
    encoder.beep()
    time.sleep(0.12)
    encoder.beep()
    return WriteResult(success=True, uid=uid)
