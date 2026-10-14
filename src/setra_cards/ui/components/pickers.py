"""Date + time field backed by the native calendar and clock pickers.

Replaces the old free-text "YYYY-MM-DD" / "HH:MM" inputs: the operator picks
a day on a calendar and an hour on a clock, so no value can be mistyped.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Callable

import flet as ft

from setra_cards.ui import theme

WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def format_day(value: datetime | date) -> str:
    """'jue 16 oct 2026' — Spanish without depending on the OS locale."""
    return f"{WEEKDAYS[value.weekday()]} {value.day} {MONTHS[value.month - 1]} {value.year}"


def format_moment(value: datetime) -> str:
    """'jue 16 oct, 12:00' — compact form for summaries."""
    return f"{WEEKDAYS[value.weekday()]} {value.day} {MONTHS[value.month - 1]}, {value:%H:%M}"


def _picked_date(raw) -> date | None:
    """The picker may hand back a date or a (tz-aware) datetime at midnight.

    Year/month/day are the day the operator tapped in both cases; converting
    the timezone first would shift it back one day in Colombia (UTC-5).
    """
    if raw is None:
        return None
    return date(raw.year, raw.month, raw.day)


def _field_box(icon: str, text: ft.Text, on_click: Callable, expand: int) -> ft.Container:
    return ft.Container(
        content=ft.Row(
            [ft.Icon(icon, size=18, color=theme.GOLD), text],
            spacing=10,
        ),
        padding=ft.Padding(14, 13, 14, 13),
        bgcolor=theme.SURFACE_ALT,
        border=ft.Border.all(1, theme.BORDER_STRONG),
        border_radius=theme.INPUT_RADIUS,
        on_click=on_click,
        ink=True,
        expand=expand,
    )


class DateTimeField:
    """Two tappable boxes (day, hour) that open the native pickers.

    ``value`` is always a valid naive datetime. ``on_change`` fires after the
    operator picks a day or an hour, with the new datetime.
    """

    def __init__(
        self,
        page: ft.Page,
        label: str,
        value: datetime,
        on_change: Callable[[datetime], None] | None = None,
        max_days_ahead: int = 365,
    ) -> None:
        self._page = page
        self._value = value.replace(second=0, microsecond=0)
        self._on_change = on_change
        self._day_text = ft.Text("", size=14, color=theme.TEXT, weight=ft.FontWeight.W_500)
        self._time_text = ft.Text("", size=14, color=theme.TEXT, weight=ft.FontWeight.W_500)

        today = date.today()
        self._date_picker = ft.DatePicker(
            first_date=datetime(today.year, today.month, today.day),
            last_date=datetime.combine(today + timedelta(days=max_days_ahead), time()),
            help_text=label,
            cancel_text="Cancelar",
            confirm_text="Aceptar",
            locale=ft.Locale("es", "CO"),
            on_change=self._on_date_picked,
        )
        self._time_picker = ft.TimePicker(
            help_text=label,
            cancel_text="Cancelar",
            confirm_text="Aceptar",
            hour_label_text="Hora",
            minute_label_text="Minuto",
            hour_format=ft.TimePickerHourFormat.H24,
            on_change=self._on_time_picked,
        )
        self.control = ft.Column(
            [
                ft.Text(label, size=12, color=theme.TEXT_MUTED, weight=ft.FontWeight.W_600),
                ft.Row(
                    [
                        _field_box(ft.Icons.CALENDAR_MONTH_OUTLINED, self._day_text, self._open_date, 3),
                        _field_box(ft.Icons.SCHEDULE, self._time_text, self._open_time, 2),
                    ],
                    spacing=10,
                ),
            ],
            spacing=6,
            tight=True,
        )
        self._render()

    @property
    def value(self) -> datetime:
        return self._value

    def set_value(self, value: datetime) -> None:
        """Programmatic update (e.g. from a nights slider); no on_change."""
        self._value = value.replace(second=0, microsecond=0)
        self._render()

    def _render(self) -> None:
        self._day_text.value = format_day(self._value)
        self._time_text.value = f"{self._value:%H:%M}"

    def _attach(self, picker: ft.Control) -> None:
        if picker not in self._page.overlay:
            self._page.overlay.append(picker)

    def _open_date(self, e: ft.ControlEvent) -> None:
        self._attach(self._date_picker)
        self._date_picker.value = datetime.combine(self._value.date(), time())
        self._date_picker.open = True
        self._page.update()

    def _open_time(self, e: ft.ControlEvent) -> None:
        self._attach(self._time_picker)
        self._time_picker.value = self._value.time()
        self._time_picker.open = True
        self._page.update()

    def _on_date_picked(self, e: ft.ControlEvent) -> None:
        picked = _picked_date(e.control.value)
        if picked is None:
            return
        self._commit(datetime.combine(picked, self._value.time()))

    def _on_time_picked(self, e: ft.ControlEvent) -> None:
        picked = e.control.value
        if picked is None:
            return
        self._commit(self._value.replace(hour=picked.hour, minute=picked.minute))

    def _commit(self, value: datetime) -> None:
        self._value = value.replace(second=0, microsecond=0)
        self._render()
        if self._on_change is not None:
            self._on_change(self._value)
        self._page.update()
