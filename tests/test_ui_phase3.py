"""Phase 3 UI: guided write window, date/time picker field, compact Hub."""
from __future__ import annotations

from datetime import date, datetime, timezone

import flet as ft

from setra_cards.services.card_service import CardResult
from setra_cards.ui.components import card_write, pickers
from setra_cards.ui.views import dashboard as dashboard_view


def iter_controls(ctrl):
    yield ctrl
    content = getattr(ctrl, "content", None)
    if content is not None:
        yield from iter_controls(content)
    for child in getattr(ctrl, "controls", None) or []:
        yield from iter_controls(child)


def texts(root) -> str:
    return " | ".join(str(c.value) for c in iter_controls(root) if isinstance(c, ft.Text) and c.value)


class FakePage:
    def __init__(self) -> None:
        self.overlay: list = []
        self.updates = 0

    def update(self) -> None:
        self.updates += 1


def _open_dialog(page: FakePage) -> ft.AlertDialog:
    return next(c for c in page.overlay if isinstance(c, ft.AlertDialog))


# --- pickers ----------------------------------------------------------------

class TestPickers:
    def test_format_day_is_spanish(self):
        assert pickers.format_day(date(2026, 10, 16)) == "vie 16 oct 2026"

    def test_format_moment(self):
        assert pickers.format_moment(datetime(2026, 10, 15, 12, 0)) == "jue 15 oct, 12:00"

    def test_picked_utc_midnight_keeps_the_tapped_day(self):
        # Converting to Colombia time would turn 16 Oct 00:00Z into the 15th.
        raw = datetime(2026, 10, 16, 0, 0, tzinfo=timezone.utc)
        assert pickers._picked_date(raw) == date(2026, 10, 16)

    def test_field_keeps_time_when_day_changes(self):
        page = FakePage()
        seen: list[datetime] = []
        field = pickers.DateTimeField(page, "Salida", datetime(2026, 10, 15, 12, 0), on_change=seen.append)
        field._on_date_picked(type("E", (), {"control": type("C", (), {"value": datetime(2026, 10, 18)})()})())
        assert field.value == datetime(2026, 10, 18, 12, 0)
        assert seen == [datetime(2026, 10, 18, 12, 0)]

    def test_field_keeps_day_when_time_changes(self):
        page = FakePage()
        field = pickers.DateTimeField(page, "Salida", datetime(2026, 10, 15, 12, 0))
        picked = datetime(2026, 1, 1, 13, 30).time()
        field._on_time_picked(type("E", (), {"control": type("C", (), {"value": picked})()})())
        assert field.value == datetime(2026, 10, 15, 13, 30)

    def test_set_value_does_not_fire_on_change(self):
        page = FakePage()
        seen: list = []
        field = pickers.DateTimeField(page, "Salida", datetime(2026, 10, 15, 12, 0), on_change=seen.append)
        field.set_value(datetime(2026, 10, 20, 12, 0, 45))
        assert field.value == datetime(2026, 10, 20, 12, 0)
        assert seen == []
        assert "mar 20 oct 2026" in texts(field.control)


# --- guided write window ----------------------------------------------------

class TestGuidedWrite:
    def test_success_shows_summary_and_next_step(self):
        page = FakePage()
        done: list = []
        result = CardResult(ok=True, card_type="Guest", uid_hex="3DA17F22", room_display="203",
                            expires_at=datetime(2026, 10, 16, 12, 0))
        card_write.run_card_write(page, title="Tarjeta de huésped", op=lambda: result,
                                  next_step="Pruébala en la puerta", on_done=done.append)
        dlg = _open_dialog(page)
        shown = texts(dlg.content)
        assert "Tarjeta grabada" in shown
        assert "Huésped" in shown and "203" in shown and "vie 16 oct, 12:00" in shown
        assert "Pruébala en la puerta" in shown
        assert done == [result]

    def test_failure_offers_retry_that_reruns_the_op(self):
        page = FakePage()
        calls: list[int] = []
        outcomes = [
            CardResult(ok=False, card_type="Guest", error="Se retiró la tarjeta durante la grabación"),
            CardResult(ok=True, card_type="Guest"),
        ]

        def op():
            calls.append(1)
            return outcomes[len(calls) - 1]

        card_write.run_card_write(page, title="Tarjeta de huésped", op=op)
        dlg = _open_dialog(page)
        assert "No se pudo completar" in texts(dlg.content)
        assert "Deja la tarjeta quieta" in texts(dlg.content)
        retry = dlg.actions[-1]
        retry.on_click(None)
        assert len(calls) == 2
        assert "Tarjeta grabada" in texts(dlg.content)

    def test_exception_becomes_a_failure_not_a_hang(self):
        page = FakePage()

        def boom():
            raise RuntimeError("puerto cerrado")

        card_write.run_card_write(page, title="Reloj", op=boom)
        dlg = _open_dialog(page)
        assert "puerto cerrado" in texts(dlg.content)
        assert dlg.actions, "the window must offer a way out"

    def test_error_hints(self):
        assert "USB" in card_write.error_hint("Encoder desconectado. Revise el cable")
        assert "plana" in card_write.error_hint("No se detectó ninguna tarjeta")
        assert card_write.error_hint("algo raro") == card_write.DEFAULT_HINT


# --- compact Hub -------------------------------------------------------------

class TestHubLayout:
    def test_rooms_grouped_by_floor_in_number_order(self):
        rooms = [
            {"floor": 2, "display_number": "210"}, {"floor": 1, "display_number": "110"},
            {"floor": 1, "display_number": "102"}, {"floor": 2, "display_number": "201"},
        ]
        grouped = dashboard_view._group_by_floor(rooms)
        assert [f for f, _ in grouped] == [1, 2]
        assert [r["display_number"] for r in grouped[0][1]] == ["102", "110"]

    def test_legend_counts_occupied_apart_from_housekeeping_state(self):
        rooms = [
            {"id": 1, "state": "limpia"}, {"id": 2, "state": "limpia"}, {"id": 3, "state": "sucia"},
        ]
        legend = dashboard_view._legend(rooms, {1: {}})
        shown = texts(legend)
        assert "Ocupada | 1" in shown
        assert "Limpia | 1" in shown
        assert "Sucia | 1" in shown
