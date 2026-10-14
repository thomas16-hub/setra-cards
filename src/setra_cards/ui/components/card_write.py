"""Guided card-write window.

One modal for every card operation: it says "don't remove the card" while the
encoder works, then shows an unmistakable result: what was written and what
to do next, or what failed and a Retry button. The operation itself is the
same service call as before; this only changes what the operator sees.
"""
from __future__ import annotations

from typing import Callable, Sequence

import flet as ft

from setra_cards.services.card_service import CardResult
from setra_cards.ui import theme
from setra_cards.ui.components.basics import PrimaryButton, _page_close, _page_open
from setra_cards.ui.components.pickers import format_moment

DIALOG_WIDTH = 440

# (substring in the error, what the operator should do). First match wins.
ERROR_HINTS: tuple[tuple[str, str], ...] = (
    ("desconectado", "Revisa el cable USB del encoder. La app lo vuelve a buscar sola."),
    ("retiró", "Deja la tarjeta quieta sobre el encoder hasta que termine y presiona Reintentar."),
    ("no se detect", "Coloca la tarjeta plana sobre el encoder y presiona Reintentar."),
    ("no se pudo leer", "Coloca la tarjeta plana sobre el encoder y presiona Reintentar."),
    ("ninguna llave", "La tarjeta es de otro sistema o está dañada. Prueba con otra tarjeta."),
    ("gerente", "Pide a un gerente que inicie sesión para emitir esta tarjeta."),
    ("base de datos", "La tarjeta sí quedó grabada. Avisa a soporte: falló el registro."),
)
DEFAULT_HINT = "Vuelve a colocar la tarjeta y presiona Reintentar."

# Service card_type -> what the operator calls it.
CARD_TYPE_LABELS = {
    "Guest": "Huésped",
    "Master": "Maestra",
    "Laundry": "Limpieza",
    "Setting": "Setting",
    "Clock": "Reloj",
    "Auth": "Autorización",
    "Blank": "Borrado",
}


def error_hint(error: str) -> str:
    low = (error or "").lower()
    return next((hint for key, hint in ERROR_HINTS if key in low), DEFAULT_HINT)


def result_rows(result: CardResult) -> list[tuple[str, str]]:
    """Label/value pairs summarising a successful write."""
    rows: list[tuple[str, str]] = [("Tipo", CARD_TYPE_LABELS.get(result.card_type, result.card_type))]
    if result.room_display:
        rows.append(("Habitación", str(result.room_display)))
    if result.expires_at:
        rows.append(("Válida hasta", format_moment(result.expires_at)))
    if result.uid_hex:
        rows.append(("Tarjeta (UID)", result.uid_hex))
    return rows


def _status_icon(icon: str, color: str) -> ft.Container:
    return ft.Container(
        content=ft.Icon(icon, size=34, color=color),
        width=64,
        height=64,
        border_radius=32,
        bgcolor=theme.SURFACE_ALT,
        border=ft.Border.all(2, color),
        alignment=ft.Alignment.CENTER,
    )


def _detail_table(rows: Sequence[tuple[str, str]]) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Container(ft.Text(label, size=12, color=theme.TEXT_MUTED, no_wrap=True), width=110),
                        ft.Text(value, size=13, color=theme.TEXT, weight=ft.FontWeight.W_600,
                                selectable=True, expand=True),
                    ],
                    spacing=8,
                )
                for label, value in rows
            ],
            spacing=6,
            tight=True,
        ),
        padding=ft.Padding(14, 12, 14, 12),
        bgcolor=theme.SURFACE_ALT,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=10,
    )


def _centered(controls: list[ft.Control]) -> ft.Column:
    return ft.Column(
        controls,
        spacing=10,
        tight=True,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
    )


def run_card_write(
    page: ft.Page,
    *,
    title: str,
    op: Callable[[], CardResult],
    details: Sequence[tuple[str, str]] = (),
    next_step: str = "",
    working_text: str = "Grabando tarjeta…",
    success_text: str = "Tarjeta grabada",
    on_done: Callable[[CardResult], None] | None = None,
) -> None:
    """Open the guided window and run ``op`` (blocking) inside it.

    ``details`` are shown while working (what is being written). ``next_step``
    is the instruction after a successful write, e.g. "Pasa la tarjeta por la
    cerradura". ``on_done`` runs after every attempt, success or failure.
    """
    body = ft.Container(width=DIALOG_WIDTH, padding=ft.Padding(4, 8, 4, 4))
    running = {"v": False}

    def close(e: ft.ControlEvent | None = None) -> None:
        if not running["v"]:
            _page_close(page, dlg)

    def show_working() -> None:
        parts: list[ft.Control] = [
            ft.Container(height=6),
            ft.ProgressRing(width=46, height=46, stroke_width=4, color=theme.GOLD),
            ft.Container(height=4),
            ft.Text(working_text, size=17, weight=ft.FontWeight.W_700, color=theme.TEXT),
            ft.Text("No retires la tarjeta del encoder", size=13, color=theme.WARNING,
                    weight=ft.FontWeight.W_600),
        ]
        if details:
            parts.append(ft.Container(height=4))
            parts.append(_detail_table(details))
        body.content = _centered(parts)
        dlg.actions = []

    def show_success(result: CardResult) -> None:
        parts: list[ft.Control] = [
            _status_icon(ft.Icons.CHECK_ROUNDED, theme.SUCCESS),
            ft.Text(result.message or success_text, size=17, weight=ft.FontWeight.W_700,
                    color=theme.SUCCESS, text_align=ft.TextAlign.CENTER),
            _detail_table(result_rows(result)),
        ]
        if next_step:
            parts.append(ft.Row(
                [
                    ft.Icon(ft.Icons.ARROW_FORWARD, size=16, color=theme.GOLD),
                    ft.Text(next_step, size=13, color=theme.TEXT, expand=True),
                ],
                spacing=8,
            ))
        body.content = _centered(parts)
        dlg.actions = [PrimaryButton("Listo", on_click=close, icon=ft.Icons.CHECK)]

    def show_failure(result: CardResult) -> None:
        error = result.error or "Error desconocido"
        body.content = _centered([
            _status_icon(ft.Icons.CLOSE_ROUNDED, theme.ERROR),
            ft.Text("No se pudo completar", size=17, weight=ft.FontWeight.W_700,
                    color=theme.ERROR),
            ft.Text(error, size=13, color=theme.TEXT, text_align=ft.TextAlign.CENTER,
                    selectable=True),
            ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.LIGHTBULB_OUTLINE, size=16, color=theme.GOLD),
                        ft.Text(error_hint(error), size=12, color=theme.TEXT_MUTED, expand=True),
                    ],
                    spacing=8,
                ),
                padding=ft.Padding(12, 10, 12, 10),
                bgcolor=theme.SURFACE_ALT,
                border_radius=10,
            ),
        ])
        dlg.actions = [
            ft.TextButton(content=ft.Text("Cerrar"), on_click=close),
            PrimaryButton("Reintentar", on_click=attempt, icon=ft.Icons.REFRESH),
        ]

    def attempt(e: ft.ControlEvent | None = None) -> None:
        if running["v"]:
            return
        running["v"] = True
        show_working()
        page.update()
        try:
            result = op()
        except Exception as exc:  # noqa: BLE001 — the window must never hang
            result = CardResult(ok=False, card_type=title, error=f"Error interno: {exc}")
        finally:
            running["v"] = False
        if result.ok:
            show_success(result)
        else:
            show_failure(result)
        page.update()
        if on_done is not None:
            on_done(result)

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(title, size=18, weight=ft.FontWeight.W_700, color=theme.TEXT),
        content=body,
        actions=[],
        actions_alignment=ft.MainAxisAlignment.END,
        bgcolor=theme.SURFACE,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
    )
    show_working()
    _page_open(page, dlg)
    attempt()
