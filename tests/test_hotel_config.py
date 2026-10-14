"""hotel.json loading: real config vs the Demo fallback the UI must flag."""
import json

import setra_cards.core.app_state as app_state


def _point_config_dirs(monkeypatch, tmp_path):
    install = tmp_path / "install"
    data = tmp_path / "data"
    install.mkdir()
    data.mkdir()
    monkeypatch.setattr(app_state, "install_root", lambda: install)
    monkeypatch.setattr(app_state, "app_data_dir", lambda: data)
    return install


def test_missing_hotel_json_falls_back_and_is_flagged(monkeypatch, tmp_path):
    _point_config_dirs(monkeypatch, tmp_path)
    cfg = app_state._load_hotel_config()
    assert cfg.is_fallback is True
    assert cfg.name == "Hotel Demo"


def test_corrupt_hotel_json_falls_back_and_is_flagged(monkeypatch, tmp_path):
    install = _point_config_dirs(monkeypatch, tmp_path)
    (install / "hotel.json").write_text("{not json", encoding="utf-8")
    assert app_state._load_hotel_config().is_fallback is True


def test_real_hotel_json_is_not_flagged(monkeypatch, tmp_path):
    install = _point_config_dirs(monkeypatch, tmp_path)
    (install / "hotel.json").write_text(json.dumps({
        "name": "Hotel Ruiseñor",
        "signature": {"sig_hi": 149, "sig_lo": 184, "prefix": 21},
        "des_key": "god",
        "checkout_time": "13:00",
    }), encoding="utf-8")
    cfg = app_state._load_hotel_config()
    assert cfg.is_fallback is False
    assert cfg.checkout_time == "13:00"
