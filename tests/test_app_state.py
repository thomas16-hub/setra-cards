"""Tests de carga de configuracion del hotel (hotel.json)."""
import json
import logging

import pytest

from setra_cards.core.app_state import _load_hotel_config, _parse_hotel_json

GOD_KEY = b"god\x00\x00\x00\x00\x00"


def _write_hotel_json(path, des_key="god"):
    payload = {
        "name": "Hotel Test",
        "short_name": "Test",
        "city": "Medellin",
        "signature": {"sig_hi": 0x15, "sig_lo": 0x95, "prefix": 0xB8},
        "des_key": des_key,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_des_key_exact_god_literal(tmp_path):
    cfg = _parse_hotel_json(_write_hotel_json(tmp_path / "hotel.json", des_key="god"))
    assert cfg.des_key == GOD_KEY


def test_des_key_hex_parsed(tmp_path):
    cfg = _parse_hotel_json(_write_hotel_json(tmp_path / "hotel.json", des_key="0123456789abcdef"))
    assert cfg.des_key == bytes.fromhex("0123456789abcdef")


def test_des_key_god_prefix_no_longer_silently_defaults(tmp_path):
    """Bug original: startswith('god') mapeaba 'god1234' a la clave default sin avisar."""
    path = _write_hotel_json(tmp_path / "hotel.json", des_key="god12345")
    with pytest.raises(ValueError, match="des_key invalido"):
        _parse_hotel_json(path)


def test_des_key_wrong_length_rejected(tmp_path):
    path = _write_hotel_json(tmp_path / "hotel.json", des_key="0123ab")  # 3 bytes
    with pytest.raises(ValueError, match="8 bytes"):
        _parse_hotel_json(path)


def test_missing_hotel_json_falls_back_to_demo_with_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("setra_cards.core.app_state.install_root", lambda: tmp_path / "install")
    monkeypatch.setattr("setra_cards.core.app_state.app_data_dir", lambda: tmp_path / "appdata")
    with caplog.at_level(logging.WARNING, logger="setra_cards.core.app_state"):
        cfg = _load_hotel_config()
    assert cfg.name == "Hotel Demo"
    assert cfg.des_key == GOD_KEY
    assert "hotel.json NO encontrado" in caplog.text
    assert "Demo" in caplog.text


def test_corrupt_hotel_json_logs_error_with_path(tmp_path, monkeypatch, caplog):
    install = tmp_path / "install"
    install.mkdir()
    bad = install / "hotel.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr("setra_cards.core.app_state.install_root", lambda: install)
    monkeypatch.setattr("setra_cards.core.app_state.app_data_dir", lambda: tmp_path / "appdata")
    with caplog.at_level(logging.WARNING, logger="setra_cards.core.app_state"):
        cfg = _load_hotel_config()
    # Falls back to Demo but screams about the corrupt file, including its path
    assert cfg.name == "Hotel Demo"
    assert "hotel.json corrupto" in caplog.text
    assert str(bad) in caplog.text
