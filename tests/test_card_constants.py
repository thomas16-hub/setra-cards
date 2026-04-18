"""Verifica que las constantes de tipo de tarjeta coincidan con el protocolo Locstar."""
from setra_cards.encoder.protocol import (
    CARD_TYPE_GUEST,
    CARD_TYPE_MASTER,
    CARD_TYPE_AUTH,
    CARD_TYPE_CLOCK,
    CARD_TYPE_SETTING,
    CARD_TYPE_LAUNDRY,
)


def test_card_type_values():
    assert CARD_TYPE_GUEST == 0x00
    assert CARD_TYPE_LAUNDRY == 0x20
    assert CARD_TYPE_MASTER == 0x40
    assert CARD_TYPE_SETTING == 0x80
    assert CARD_TYPE_AUTH == 0xC0
    assert CARD_TYPE_CLOCK == 0xA0


def test_no_duplicate_values():
    values = [CARD_TYPE_GUEST, CARD_TYPE_LAUNDRY, CARD_TYPE_MASTER,
              CARD_TYPE_SETTING, CARD_TYPE_AUTH, CARD_TYPE_CLOCK]
    assert len(values) == len(set(values)), "Hay constantes de tarjeta con valores duplicados"
