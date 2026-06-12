"""UI group E regression tests.

Covers:
- dashboard physical checkout only blanks GUEST cards (master protection)
- diagnostic read screen renders estado/badges/dates (operator self-diagnosis)
- per-handler busy guard (disable + spinner + finally)
- admin backup uses the SQLite online backup API (no torn copies)
- admin update copy no longer claims automatic updates
"""
from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import flet as ft
import pytest

from setra_cards.encoder.protocol import CARD_TYPE_GUEST
from setra_cards.ui import theme
from setra_cards.ui.components.basics import make_busy_guard, op_spinner
from setra_cards.ui.views import admin as admin_view
from setra_cards.ui.views import cards as cards_view
from setra_cards.ui.views import dashboard as dashboard_view


# --- helpers ---------------------------------------------------------------

def iter_controls(ctrl):
    """Depth-first walk over a Flet control tree."""
    yield ctrl
    content = getattr(ctrl, "content", None)
    if content is not None:
        yield from iter_controls(content)
    for child in getattr(ctrl, "controls", None) or []:
        yield from iter_controls(child)


def collect_texts(root) -> list[ft.Text]:
    return [c for c in iter_controls(root) if isinstance(c, ft.Text)]


def text_values(root) -> str:
    return " | ".join(str(t.value) for t in collect_texts(root) if t.value)


class FakePage:
    def __init__(self) -> None:
        self.updates = 0

    def update(self) -> None:
        self.updates += 1


# --- 1) checkout must not blank a master/laundry card -----------------------

class TestCheckoutBlankGuard:
    def test_checkout_helper_passes_guest_expectation(self, monkeypatch):
        captured: dict = {}

        def fake_blank(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(ok=True, error=None, uid_hex="AA:BB")

        monkeypatch.setattr(dashboard_view, "blank_existing_card", fake_blank)
        result = dashboard_view._blank_guest_card_for_checkout(
            encoder=object(), hotel=object(), session=object(), operator="tester",
        )
        assert result.ok is True
        assert captured["expect_card_type"] == CARD_TYPE_GUEST
        assert "force" not in captured  # checkout has no force escape hatch

    def test_physical_checkout_uses_guarded_helper(self):
        src = inspect.getsource(dashboard_view._open_room_action)
        assert "_blank_guest_card_for_checkout(" in src
        # The raw, unguarded service call must be gone from the checkout path.
        assert "blank_existing_card(" not in src.replace(
            "_blank_guest_card_for_checkout(", ""
        )

    def test_generic_admin_blank_has_no_type_expectation(self):
        # The "Borrar tarjeta" button must still wipe ANY card type:
        # no expect_card_type= keyword passed to the service.
        src = inspect.getsource(cards_view._instant_form)
        assert "blank_existing_card" in src
        assert "expect_card_type=" not in src


# --- 2) diagnostic read screen rendering ------------------------------------

def _base_info(**overrides) -> dict:
    info = {
        "card_type_byte": 0x40,
        "card_type_name": "Master",
        "tipo": "Maestra",
        "uid": "aa:bb:cc:dd",
        "room": 0,
        "building": 0,
        "floor": 0,
        "room_no_id": 0,
        "valid_from": datetime(2026, 1, 2, 12, 0),
        "valid_until": datetime(2026, 7, 2, 23, 59),
        "checksum_valid": True,
        "uid_match": True,
        "signature": "AB CD",
        "expired": False,
    }
    info.update(overrides)
    return info


class TestRenderReadResult:
    def test_global_master_estado_is_green_and_prominent(self):
        estado = "GLOBAL — abre todas las puertas (correcta)"
        info = _base_info(estado=estado)
        root = cards_view._render_read_result(True, "Tarjeta leida", info)
        estado_texts = [t for t in collect_texts(root) if t.value == estado]
        assert estado_texts, "estado banner must be rendered"
        assert estado_texts[0].color == theme.SUCCESS

    def test_room_bound_master_estado_is_red(self):
        estado = "LIGADA a edif 1 piso 2 hab 3 — tarjeta vieja, RE-EMITIR"
        info = _base_info(estado=estado, building=1, floor=2, room_no_id=3)
        root = cards_view._render_read_result(True, "Tarjeta leida", info)
        estado_texts = [t for t in collect_texts(root) if t.value == estado]
        assert estado_texts, "estado banner must be rendered"
        assert estado_texts[0].color == theme.ERROR

    def test_expired_card_shows_vencida_badge_and_dates(self):
        info = _base_info(
            tipo="Huésped", card_type_name="Guest", expired=True,
            building=1, floor=2, room_no_id=3,
        )
        root = cards_view._render_read_result(True, "Tarjeta leida — VENCIDA", info)
        joined = text_values(root)
        assert "VENCIDA" in joined
        assert "02/01/2026 12:00" in joined  # Valida desde
        assert "02/07/2026 23:59" in joined  # Vence

    def test_corrupt_and_foreign_card_badges(self):
        info = _base_info(checksum_valid=False, uid_match=False)
        root = cards_view._render_read_result(True, "Tarjeta CORRUPTA (checksum inválido)", info)
        joined = text_values(root)
        assert "CHECKSUM INVALIDO" in joined
        assert "UID/DES NO COINCIDE" in joined

    def test_blank_card_badge_without_dates(self):
        info = _base_info(valid_from=None, valid_until=None)
        root = cards_view._render_read_result(True, "Tarjeta EN BLANCO", info)
        joined = text_values(root)
        assert "EN BLANCO" in joined
        assert "Vence" not in joined

    def test_failed_read_shows_warning_message(self):
        msg = "No se pudo leer la tarjeta — coloquela en el encoder"
        root = cards_view._render_read_result(False, msg, {})
        assert msg in text_values(root)

    def test_tipo_is_rendered(self):
        info = _base_info()
        root = cards_view._render_read_result(True, "Tarjeta leida", info)
        assert "Maestra" in text_values(root)


# --- 3) per-handler busy guard ----------------------------------------------

class TestBusyGuard:
    def test_disables_button_and_shows_spinner_during_op(self):
        page = FakePage()
        button = SimpleNamespace(disabled=False)
        spinner = SimpleNamespace(visible=False)
        run = make_busy_guard(page, button, spinner)
        seen: dict = {}

        def op():
            seen["disabled"] = button.disabled
            seen["spinner"] = spinner.visible

        run(op)
        assert seen == {"disabled": True, "spinner": True}
        assert button.disabled is False
        assert spinner.visible is False
        assert page.updates >= 2  # one on enter, one on exit

    def test_restores_state_when_op_raises(self):
        page = FakePage()
        button = SimpleNamespace(disabled=False)
        run = make_busy_guard(page, button)

        def op():
            raise ValueError("boom")

        with pytest.raises(ValueError):
            run(op)
        assert button.disabled is False  # finally re-enabled

    def test_reentrant_call_is_ignored_while_busy(self):
        page = FakePage()
        button = SimpleNamespace(disabled=False)
        run = make_busy_guard(page, button)
        calls = {"inner": 0}

        def inner():
            calls["inner"] += 1

        def outer():
            run(inner)  # must be ignored: guard is busy

        run(outer)
        assert calls["inner"] == 0
        # After the guard is free again the handler works normally.
        run(inner)
        assert calls["inner"] == 1

    def test_op_spinner_is_hidden_by_default(self):
        spinner = op_spinner()
        assert spinner.visible is False


# --- 4) SQLite online backup -------------------------------------------------

class TestBackup:
    def test_create_backup_file_copies_all_rows(self, tmp_path):
        src = tmp_path / "live.db"
        conn = sqlite3.connect(str(src))
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            conn.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])
            conn.commit()
            # Source connection stays OPEN — copy2 here could tear the file;
            # the online backup API must still produce a consistent snapshot.
            dst = tmp_path / "backup.db"
            admin_view._create_backup_file(src, dst)
        finally:
            conn.close()

        out = sqlite3.connect(str(dst))
        try:
            rows = out.execute("SELECT v FROM t ORDER BY id").fetchall()
        finally:
            out.close()
        assert rows == [("a",), ("b",), ("c",)]

    def test_backup_section_no_longer_uses_shutil_copy(self):
        src = inspect.getsource(admin_view)
        assert "shutil.copy" not in src
        assert "_create_backup_file" in inspect.getsource(admin_view._backup_section)


# --- 5) admin copy + port release wiring -------------------------------------

class TestAdminCopyAndRelease:
    def test_false_autoupdate_claim_removed(self):
        src = inspect.getsource(admin_view)
        assert "se actualiza automaticamente desde GitHub Releases" not in src
        assert "Actualizaciones automaticas desde GitHub Releases se configuran" not in src
        assert "soporte SETRA" in src

    def test_release_port_action_wired(self):
        src = inspect.getsource(admin_view._encoder_section)
        assert "Liberar puerto" in src
        assert "encoder_service.disconnect(" in src
        assert "encoder_service.stop_watchdog(" in src
