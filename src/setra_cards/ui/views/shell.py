"""Shell principal post-login: sidebar de navegacion + vista activa."""
from __future__ import annotations

import flet as ft

from setra_cards.core.app_state import get_state
from setra_cards.ui import theme


# Cada entrada: (key, label, icon, builder)  — builder importado lazy
NAV_ITEMS = [
    ("dashboard", "Inicio", ft.Icons.HOME_OUTLINED),
    ("rooms", "Habitaciones", ft.Icons.HOTEL_OUTLINED),
    ("guests", "Huespedes", ft.Icons.PEOPLE_OUTLINED),
    ("cards", "Emitir tarjeta", ft.Icons.CREDIT_CARD),
    ("staff", "Personal", ft.Icons.BADGE_OUTLINED),
    ("activity", "Actividad", ft.Icons.RECEIPT_LONG_OUTLINED),
    ("admin", "Administracion", ft.Icons.SETTINGS_OUTLINED),
]


def _resolve_view(key: str, page: ft.Page) -> ft.Control:
    """Importa lazy para evitar cargar todo al arrancar."""
    if key == "dashboard":
        from setra_cards.ui.views.dashboard import build as b
    elif key == "rooms":
        from setra_cards.ui.views.rooms import build as b
    elif key == "guests":
        from setra_cards.ui.views.guests import build as b
    elif key == "cards":
        from setra_cards.ui.views.cards import build as b
    elif key == "staff":
        from setra_cards.ui.views.staff import build as b
    elif key == "activity":
        from setra_cards.ui.views.activity import build as b
    elif key == "admin":
        from setra_cards.ui.views.admin import build as b
    else:
        from setra_cards.ui.views.placeholder import build_placeholder
        return build_placeholder("Desconocido", f"Vista '{key}' no existe")
    return b(page)


def open_shell(page: ft.Page) -> None:
    """Reemplaza la pagina con el layout principal."""
    state = get_state()
    content_host = ft.Container(expand=True, padding=theme.PADDING_LG, bgcolor=theme.BG)

    active_key = {"val": "dashboard"}

    # Referencias a los items de la sidebar para repintar selection
    nav_controls: list[ft.Container] = []

    def _build_nav_item(key: str, label: str, icon: str) -> ft.Container:
        def on_click(e: ft.ControlEvent) -> None:
            active_key["val"] = key
            _refresh_nav_selection()
            content_host.content = _resolve_view(key, page)
            page.update()

        c = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(icon, size=18, color=theme.TEXT_MUTED),
                    ft.Text(label, size=14, color=theme.TEXT, weight=ft.FontWeight.W_500),
                ],
                spacing=12,
            ),
            padding=ft.Padding(14, 10, 14, 10),
            border_radius=10,
            on_click=on_click,
            ink=True,
        )
        c.data = {"key": key, "icon": icon}
        return c

    def _refresh_nav_selection() -> None:
        for c in nav_controls:
            meta = c.data
            selected = meta["key"] == active_key["val"]
            c.bgcolor = theme.SURFACE if selected else None
            c.border = ft.Border.all(1, theme.BORDER) if selected else None
            row: ft.Row = c.content  # type: ignore[assignment]
            icon_ctrl: ft.Icon = row.controls[0]  # type: ignore[assignment]
            icon_ctrl.color = theme.TEXT if selected else theme.TEXT_MUTED
            text_ctrl: ft.Text = row.controls[1]  # type: ignore[assignment]
            text_ctrl.weight = ft.FontWeight.W_600 if selected else ft.FontWeight.W_500

    for key, label, icon in NAV_ITEMS:
        nav_controls.append(_build_nav_item(key, label, icon))

    def do_logout(e: ft.ControlEvent) -> None:
        state.logout()
        page.controls.clear()
        from setra_cards.ui.views.login import build as login_build
        page.add(login_build(page))
        page.update()

    operator_badge = ft.Container(
        content=ft.Row(
            [
                ft.CircleAvatar(
                    content=ft.Text(
                        (state.operator.name[0] if state.operator else "?").upper(),
                        size=13,
                        weight=ft.FontWeight.BOLD,
                        color=theme.SURFACE,
                    ),
                    bgcolor=theme.PRIMARY,
                    radius=16,
                ),
                ft.Column(
                    [
                        ft.Text(
                            state.operator.name if state.operator else "—",
                            size=13, weight=ft.FontWeight.W_600, color=theme.TEXT,
                        ),
                        ft.Text(
                            (state.operator.role if state.operator else ""),
                            size=11, color=theme.TEXT_MUTED,
                        ),
                    ],
                    spacing=0,
                    tight=True,
                ),
            ],
            spacing=10,
        ),
        padding=ft.Padding(10, 8, 10, 8),
    )

    sidebar = ft.Container(
        content=ft.Column(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(state.hotel.name, size=11, color=theme.TEXT_MUTED, weight=ft.FontWeight.W_500),
                            ft.Text("Setra CARDS", size=18, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                        ],
                        spacing=2,
                    ),
                    padding=ft.Padding(14, 24, 14, 18),
                ),
                *nav_controls,
                ft.Container(expand=True),  # spacer
                ft.Divider(height=1, color=theme.BORDER),
                operator_badge,
                ft.Container(
                    content=ft.TextButton(
                        content=ft.Text("Cerrar sesion"),
                        icon=ft.Icons.LOGOUT,
                        on_click=do_logout,
                        style=ft.ButtonStyle(color=theme.TEXT_MUTED),
                    ),
                    padding=ft.Padding(6, 0, 6, 10),
                ),
            ],
            spacing=2,
            expand=True,
        ),
        width=240,
        bgcolor=theme.SURFACE,
        border=ft.Border.only(right=ft.BorderSide(1, theme.BORDER)),
    )

    page.controls.clear()
    page.padding = 0
    page.bgcolor = theme.BG
    page.add(
        ft.Row(
            [sidebar, content_host],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
    )

    # Cargar dashboard inicial
    content_host.content = _resolve_view(active_key["val"], page)
    _refresh_nav_selection()
    page.update()
