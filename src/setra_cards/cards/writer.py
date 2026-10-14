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
# Operator-facing error when the card leaves the encoder mid-operation.
CARD_REMOVED_ERROR = (
    "Se retiró la tarjeta durante la grabación. Vuelva a colocarla y repita; "
    "si no abre, grábela de nuevo"
)
# The serial port closed (USB unplugged): not a card problem.
ENCODER_DISCONNECTED_ERROR = (
    "Encoder desconectado. Revise el cable USB; la app lo vuelve a buscar sola en unos segundos"
)
NO_CARD_ERROR = "No se detectó ninguna tarjeta"


def no_card_error(encoder: EncoderDriver) -> str:
    """Tell 'no card on the encoder' apart from 'encoder unplugged'."""
    return NO_CARD_ERROR if getattr(encoder, "is_open", True) else ENCODER_DISCONNECTED_ERROR


# Read-back found different bytes than the ones just written.
VERIFY_MISMATCH_ERROR = (
    "La tarjeta no aceptó los datos nuevos (conserva los anteriores). Repita la grabación"
)


# detect_card() returns None on any late/short frame, so one miss is not proof
# the card left the encoder: retry before aborting a write as "removed".
DETECT_RETRIES = 3
DETECT_RETRY_PAUSE_S = 0.05

# Read-back checks (write verify, blank confirm) only log until they are
# validated against the real encoder at the hotel.
VERIFY_BLOCKING_WRITE = False
VERIFY_BLOCKING_BLANK = False


def _detect_uid(encoder: EncoderDriver) -> bytes | None:
    for attempt in range(DETECT_RETRIES):
        detection = encoder.detect_card()
        if detection is not None:
            return detection.uid
        if attempt < DETECT_RETRIES - 1:
            time.sleep(DETECT_RETRY_PAUSE_S)
    return None


def _reselect(encoder: EncoderDriver, original_uid: bytes | None) -> str | None:
    """Re-select the card before the next auth, as v2.0.10 did.

    A missed detection is NOT treated as "card removed": on the real encoder
    the select right after a halt or a failed auth often gets no answer, and
    v2.1.0 aborted every write because of it. Only a detection that returns
    a DIFFERENT UID aborts (swapped card).
    """
    detection = encoder.detect_card()
    if original_uid is not None and detection is not None and detection.uid != original_uid:
        return CARD_SWAPPED_ERROR
    return None


@dataclass(frozen=True)
class WriteResult:
    success: bool
    uid: bytes | None = None
    error: str | None = None


def _auth_keys_standard(sig: SystemSignature) -> list[tuple[str, bytes, int]]:
    """Key attempts for every card type except Authorization.

    Same order as v2.0.10 (proven on the real encoder). Moving TrailerB-B
    first is pending until the frame trace from the hotel shows whether the
    encoder really answers 01 01 to a refused Sig-A write.
    """
    return [
        ("FF-A", KEY_FF, KEY_TYPE_A),
        ("Sig-A", sig.key_a, KEY_TYPE_A),
        ("TrailerB-B", TRAILER_KEY_B, KEY_TYPE_B),
        ("FF-B", KEY_FF, KEY_TYPE_B),
    ]


def _auth_keys_auth_card() -> list[tuple[str, bytes, int]]:
    """Key attempts for Authorization card (only FF and TrailerB, v2.0.10 order)."""
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

    Sequence identical to v2.0.10. If original_uid is given, a re-detect that
    returns a different card aborts with CARD_SWAPPED_ERROR; a missed
    detection does not abort. An unplugged encoder aborts with
    ENCODER_DISCONNECTED_ERROR. Returns (ok, error_detail).
    """

    def _card_problem() -> str | None:
        if not getattr(encoder, "is_open", True):
            return ENCODER_DISCONNECTED_ERROR
        return _reselect(encoder, original_uid)

    for block_num, data, name in blocks:
        problem = _card_problem()
        if problem:
            return False, problem
        written = False
        last_err = ""
        for key_name, key, kt in key_list:
            encoder.load_key(kt, sector, key)
            problem = _card_problem()
            if problem:
                return False, problem
            if not encoder.auth_sector(sector, kt):
                last_err = f"auth {key_name} FAIL"
                continue
            if encoder.write_block(block_num, data):
                logger.info("Write OK with %s for %s", key_name, name)
                written = True
                break
            last_err = f"auth {key_name} OK pero write FAIL"
            encoder.detect_card()
        if not written:
            return False, f"{name}: {last_err}"
    return True, ""


def _verify_decoded(
    decoded: DecodedCard | None,
    expected_uid: bytes,
    expected_card_type: int,
    sector: int,
    expected_blocks: CardData | None = None,
) -> str | None:
    """Validate a read-back card against what was just written.

    With expected_blocks, block45 and block46 must match byte for byte: an
    old card of the same type also passes the type/checksum checks, so
    without the byte comparison a refused write looks like a success.
    Returns an operator-facing (Spanish) error message, or None if OK.
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
    if expected_blocks is not None and (
        decoded.raw_blocks.get(sector * 4 + 1) != expected_blocks.block45
        or decoded.raw_blocks.get(sector * 4 + 2) != expected_blocks.block46
    ):
        return VERIFY_MISMATCH_ERROR
    return None


def _halt_and_rewake(encoder: EncoderDriver) -> None:
    """Halt the card, then absorb the command the encoder drops right after.

    Hotel trace (2026-10-14): the first command sent after a Halt (01 0B)
    always gets no reply, whatever it is (beep, request, load key). Read-back
    checks that halted and then loaded a key or selected the card in one go
    always failed. A throwaway detection (with retries) re-activates it.
    """
    encoder.halt()
    _detect_uid(encoder)


def _trailer_key_a_works(encoder: EncoderDriver, sector: int, key_a: bytes) -> bool:
    """True if the sector authenticates with the KeyA just written."""
    for _attempt in (1, 2):
        _halt_and_rewake(encoder)
        encoder.load_key(KEY_TYPE_A, sector, key_a)
        encoder.detect_card()
        if encoder.auth_sector(sector, KEY_TYPE_A):
            return True
    return False


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
    decoded = None
    # One retry: a single dropped read must not fail a card that took the data.
    for _attempt in (1, 2):
        _halt_and_rewake(encoder)
        try:
            decoded = read_card(encoder, sector, sig, des_key=des_key)
        except Exception:
            logger.exception("Read-back verification raised unexpectedly")
            decoded = None
        if decoded is not None:
            break
    error = _verify_decoded(decoded, expected_uid, card.card_type, sector, expected_blocks=card)
    if not error and not _trailer_key_a_works(encoder, sector, card.trailer[:6]):
        # Data reads back fine through FF-A even when the trailer write was
        # refused, but the lock authenticates with the trailer's KeyA.
        error = "La tarjeta no quedó con la llave del sistema. Repita la grabación"
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
    expected_uid: bytes | None = None,
) -> WriteResult:
    """Write a complete card to MIFARE sector.

    Sequence per block: re-detect → load key → auth → write.
    Tries multiple keys to handle blank and pre-programmed cards.
    On a key/auth failure it blanks the sector and retries once; a removed
    or swapped card never triggers that auto-blank.
    expected_uid: refuse to write unless this exact card is on the encoder
    (callers that read the card first, e.g. change room, pass its UID).
    """
    detection = encoder.detect_card()
    if not detection:
        return WriteResult(success=False, error=no_card_error(encoder))
    if expected_uid is not None and detection.uid != expected_uid:
        return WriteResult(success=False, uid=detection.uid, error=CARD_SWAPPED_ERROR)

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

    # A swapped or removed card must never trigger auto-blank (it would erase
    # the wrong card, or fail anyway with a confusing technical message).
    if not ok and err in (CARD_SWAPPED_ERROR, CARD_REMOVED_ERROR, ENCODER_DISCONNECTED_ERROR):
        return WriteResult(success=False, uid=uid, error=err)

    # Attempt 2: on failure, blank the sector and retry once
    if not ok:
        logger.info("Write failed (%s). Trying auto-blank + rewrite...", err)
        blank_res = blank_card(encoder, sector, expected_uid=uid)
        if not blank_res.success:
            return WriteResult(
                success=False, uid=uid,
                error=f"No se pudo escribir ni borrar la tarjeta: {err} / blank: {blank_res.error}",
            )
        # After blank the card has FF keys — retry with the full key list
        ok, err = _write_blocks_with_keys(encoder, sector, blocks, key_list, original_uid=uid)
        if not ok:
            if err in (CARD_SWAPPED_ERROR, CARD_REMOVED_ERROR, ENCODER_DISCONNECTED_ERROR):
                return WriteResult(success=False, uid=uid, error=err)
            return WriteResult(
                success=False, uid=uid,
                error=f"Write fallo incluso tras auto-blank: {err}",
            )
        logger.info("Auto-blank + rewrite succeeded after initial failure")

    # Read-back verification runs in SHADOW mode: result is only logged.
    # It was never validated on the real encoder; it becomes blocking once
    # the hotel trace shows it passes on cards that do open the lock.
    if VERIFY_BLOCKING_WRITE:
        verify_error = _verify_after_write(encoder, sector, sig, des_key, card, uid)
        if verify_error:
            return WriteResult(success=False, uid=uid, error=verify_error)
    else:
        try:
            shadow = _verify_after_write(encoder, sector, sig, des_key, card, uid)
            logger.info("Shadow verify UID=%s: %s", uid.hex(":"), shadow or "OK")
        except Exception:
            logger.exception("Shadow verify raised")

    logger.info("Card programmed OK: UID=%s", uid.hex(":"))
    encoder.halt()
    encoder.beep()
    time.sleep(0.12)
    encoder.beep()
    return WriteResult(success=True, uid=uid)


class _CardChanged(Exception):
    """The card on the encoder is no longer the one the blank started on."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _check_same_card(encoder: EncoderDriver, uid: bytes) -> None:
    """Re-select before auth; abort only on a different UID or unplugged encoder.

    A missed detection is tolerated, like v2.0.10 (see _reselect).
    """
    if not getattr(encoder, "is_open", True):
        raise _CardChanged(ENCODER_DISCONNECTED_ERROR)
    problem = _reselect(encoder, uid)
    if problem:
        raise _CardChanged(problem)


def _auth_with_any_key(
    encoder: EncoderDriver,
    sector: int,
    key_attempts: list[tuple[str, bytes, int]],
    uid: bytes,
) -> bool:
    """Try each key (re-detecting before auth) until one authenticates.

    Raises _CardChanged if the card is removed or swapped: erasing whatever
    card happens to be on the encoder is exactly what this must never do.
    """
    for _key_name, key, kt in key_attempts:
        encoder.load_key(kt, sector, key)
        _check_same_card(encoder, uid)
        if encoder.auth_sector(sector, kt):
            return True
    return False


def blank_card(
    encoder: EncoderDriver, sector: int, expected_uid: bytes | None = None,
) -> WriteResult:
    """Restore card sector to blank state (zeros + default trailer).

    expected_uid: only blank this exact card (callers that inspected the
    card first must pass it, so a swap cannot wipe a different card).
    """
    detection = encoder.detect_card()
    if not detection:
        return WriteResult(success=False, error=no_card_error(encoder))
    uid = detection.uid
    if expected_uid is not None and uid != expected_uid:
        return WriteResult(success=False, uid=uid, error=CARD_SWAPPED_ERROR)
    try:
        return _blank_sector(encoder, sector, uid)
    except _CardChanged as exc:
        return WriteResult(success=False, uid=uid, error=exc.message)


def _blank_sector(encoder: EncoderDriver, sector: int, uid: bytes) -> WriteResult:
    encoder.halt()
    encoder.beep()
    time.sleep(0.18)

    # Auth with any available key
    key_attempts = [
        ("TrailerB-B", TRAILER_KEY_B, KEY_TYPE_B),
        ("FF-B", KEY_FF, KEY_TYPE_B),
        ("FF-A", KEY_FF, KEY_TYPE_A),
    ]

    if not _auth_with_any_key(encoder, sector, key_attempts, uid):
        return WriteResult(
            success=False, uid=uid,
            error="No se pudo autenticar la tarjeta: ninguna llave funcionó",
        )

    # Blank data blocks (0-2) — re-auth before each block write
    zeros = bytes(16)
    for i in range(3):
        block = sector * 4 + i
        if not _auth_with_any_key(encoder, sector, key_attempts, uid):
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
        if not _auth_with_any_key(encoder, sector, key_attempts, uid):
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

    # Confirm the erase: a refused write still answers with an "ok-looking"
    # status, and checkout trusts this result to free the room.
    blank_ok = _sector_reads_blank(encoder, sector)
    if not VERIFY_BLOCKING_BLANK:
        logger.info("Shadow blank confirm UID=%s: %s", uid.hex(":"), "OK" if blank_ok else "NOT BLANK")
    elif not blank_ok:
        return WriteResult(
            success=False, uid=uid,
            error="La tarjeta no quedó borrada (sigue con datos). Repita el borrado",
        )

    encoder.halt()
    encoder.beep()
    time.sleep(0.12)
    encoder.beep()
    return WriteResult(success=True, uid=uid)


def _sector_reads_blank(encoder: EncoderDriver, sector: int) -> bool:
    """True if the restored transport trailer (KeyA FF) opens zeroed data."""
    for _attempt in (1, 2):
        _halt_and_rewake(encoder)
        encoder.load_key(KEY_TYPE_A, sector, KEY_FF)
        encoder.detect_card()
        if not encoder.auth_sector(sector, KEY_TYPE_A):
            continue
        blocks = [encoder.read_block(sector * 4 + i) for i in (1, 2)]
        if all(b is not None for b in blocks):
            return all(b == bytes(16) for b in blocks)
    return False
