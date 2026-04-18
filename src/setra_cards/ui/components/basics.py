"""Componentes UI basicos — PageHeader, SectionCard, buttons, badges, toasts."""
from __future__ import annotations

from typing import Callable

import flet as ft

from setra_cards.ui import theme


def PageHeader(
    title: str,
    subtitle: str | None = None,
    actions: list[ft.Control] | None = None,
) -> ft.Control:
    """Encabezado estandar de vista."""
    left = ft.Column(
        [
            ft.Text(title, size=28, weight=ft.FontWeight.BOLD, color=theme.TEXT),
            ft.Text(subtitle, size=13, color=theme.TEXT_MUTED) if subtitle else ft.Container(),
        ],
        spacing=2,
        tight=True,
    )
    right = ft.Row(actions or [], spacing=8)
    return ft.Container(
        content=ft.Row([left, right], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        padding=ft.Padding(0, 0, 0, 18),
    )


def SectionCard(
    title: str | None = None,
    content: ft.Control | None = None,
    actions: list[ft.Control] | None = None,
    padding: int = theme.PADDING_LG,
) -> ft.Container:
    """Card con titulo opcional y contenido arbitrario."""
    children: list[ft.Control] = []
    if title or actions:
        children.append(
            ft.Row(
                [
                    ft.Text(title or "", size=16, weight=ft.FontWeight.W_600, color=theme.TEXT),
                    ft.Row(actions or [], spacing=8),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            )
        )
        children.append(ft.Container(height=10))
    if content is not None:
        children.append(content)

    return ft.Container(
        content=ft.Column(children, spacing=0),
        padding=ft.Padding(padding, padding, padding, padding),
        bgcolor=theme.SURFACE,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=theme.CARD_RADIUS,
    )


def PrimaryButton(
    label: str,
    on_click: Callable | None = None,
    icon: str | None = None,
    disabled: bool = False,
    width: int | None = None,
) -> ft.FilledButton:
    return ft.FilledButton(
        content=ft.Text(label, size=14, weight=ft.FontWeight.W_600),
        icon=icon,
        on_click=on_click,
        disabled=disabled,
        width=width,
        style=ft.ButtonStyle(
            bgcolor=theme.PRIMARY,
            color=theme.SURFACE,
            shape=ft.RoundedRectangleBorder(radius=theme.BUTTON_RADIUS),
            padding=ft.Padding(18, 12, 18, 12),
        ),
    )


def SecondaryButton(
    label: str,
    on_click: Callable | None = None,
    icon: str | None = None,
    disabled: bool = False,
    width: int | None = None,
    danger: bool = False,
) -> ft.OutlinedButton:
    return ft.OutlinedButton(
        content=ft.Text(label, size=14, weight=ft.FontWeight.W_500),
        icon=icon,
        on_click=on_click,
        disabled=disabled,
        width=width,
        style=ft.ButtonStyle(
            color=theme.ERROR if danger else theme.TEXT,
            side=ft.BorderSide(1, theme.ERROR if danger else theme.BORDER_STRONG),
            shape=ft.RoundedRectangleBorder(radius=theme.BUTTON_RADIUS),
            padding=ft.Padding(18, 12, 18, 12),
        ),
    )


def Badge(text: str, color: str = theme.TEXT_MUTED, bg: str = "#F0F0F0") -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=11, color=color, weight=ft.FontWeight.W_600),
        padding=ft.Padding(8, 3, 8, 3),
        bgcolor=bg,
        border_radius=8,
    )


def StatCard(
    title: str,
    value: str | int,
    subtitle: str = "",
    color: str = theme.TEXT,
    icon: str | None = None,
) -> ft.Container:
    left = ft.Column(
        [
            ft.Text(title, size=12, color=theme.TEXT_MUTED, weight=ft.FontWeight.W_500),
            ft.Text(str(value), size=28, weight=ft.FontWeight.BOLD, color=color),
            ft.Text(subtitle, size=11, color=theme.TEXT_MUTED) if subtitle else ft.Container(),
        ],
        spacing=2,
        tight=True,
        expand=True,
    )
    right: ft.Control = ft.Container()
    if icon:
        right = ft.Container(
            content=ft.Icon(icon, size=22, color=theme.TEXT_MUTED),
            width=44,
            height=44,
            bgcolor=theme.SURFACE_ALT,
            border_radius=12,
            alignment=ft.Alignment.CENTER,
        )
    return ft.Container(
        content=ft.Row(
            [left, right],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.START,
        ),
        padding=ft.Padding(20, 18, 20, 18),
        bgcolor=theme.SURFACE,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=theme.CARD_RADIUS,
        expand=True,
    )


def EmptyState(
    title: str,
    message: str = "",
    icon: str = ft.Icons.INBOX_OUTLINED,
    action: ft.Control | None = None,
) -> ft.Container:
    children: list[ft.Control] = [
        ft.Container(
            content=ft.Icon(icon, size=40, color=theme.TEXT_LIGHT),
            width=80,
            height=80,
            bgcolor=theme.SURFACE_ALT,
            border_radius=40,
            alignment=ft.Alignment.CENTER,
        ),
        ft.Container(height=14),
        ft.Text(title, size=16, weight=ft.FontWeight.W_600, color=theme.TEXT),
    ]
    if message:
        children.append(ft.Text(message, size=13, color=theme.TEXT_MUTED, text_align=ft.TextAlign.CENTER))
    if action is not None:
        children.append(ft.Container(height=14))
        children.append(action)

    return ft.Container(
        content=ft.Column(
            children,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=2,
        ),
        alignment=ft.Alignment.CENTER,
        padding=ft.Padding(24, 48, 24, 48),
    )


def show_toast(page: ft.Page, message: str, kind: str = "info") -> None:
    """Toast no intrusivo. kind: info | success | error | warning."""
    colors = {
        "info": (theme.TEXT, "#EEF2F7"),
        "success": ("#FFFFFF", theme.SUCCESS),
        "error": ("#FFFFFF", theme.ERROR),
        "warning": (theme.TEXT, "#FFF4D6"),
    }
    fg, bg = colors.get(kind, colors["info"])
    page.open(
        ft.SnackBar(
            content=ft.Text(message, color=fg, size=13, weight=ft.FontWeight.W_500),
            bgcolor=bg,
            duration=3500,
            behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=10),
        )
    )


def confirm_dialog(
    page: ft.Page,
    title: str,
    message: str,
    on_confirm: Callable[[], None],
    confirm_label: str = "Confirmar",
    danger: bool = False,
) -> None:
    """Dialogo modal de confirmacion."""
    def on_close(e: ft.ControlEvent | None = None) -> None:
        page.close(dlg)

    def on_ok(e: ft.ControlEvent) -> None:
        page.close(dlg)
        on_confirm()

    confirm_btn = (
        SecondaryButton(confirm_label, on_click=on_ok, danger=True)
        if danger
        else PrimaryButton(confirm_label, on_click=on_ok)
    )

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(title, size=18, weight=ft.FontWeight.W_600, color=theme.TEXT),
        content=ft.Text(message, size=13, color=theme.TEXT_MUTED),
        actions=[
            ft.TextButton(content=ft.Text("Cancelar"), on_click=on_close),
            confirm_btn,
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
    )
    page.open(dlg)
