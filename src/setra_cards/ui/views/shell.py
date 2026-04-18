"""Shell principal post-login: sidebar de navegacion + vista activa."""
from __future__ import annotations

import flet as ft

from setra_cards.core.app_state import get_state
from setra_cards.services.auth import role_has_access
from setra_cards.ui import theme


# Cada entrada: (key, label, icon, min_role)
# min_role=None → visible para todos los roles
_ALL_NAV_ITEMS = [
    ("dashboard", "Hub",            ft.Icons.GRID_VIEW_OUTLINED,      None),
    ("rooms",     "Habitaciones",   ft.Icons.HOTEL_OUTLINED,           None),
    ("guests",    "Huespedes",      ft.Icons.PEOPLE_OUTLINED,          None),
    ("cards",     "Emitir tarjeta", ft.Icons.CREDIT_CARD,              None),
    ("staff",     "Personal",       ft.Icons.BADGE_OUTLINED,           "manager"),
    ("activity",  "Actividad",      ft.Icons.RECEIPT_LONG_OUTLINED,    "manager"),
    ("reports",   "Reportes",       ft.Icons.BAR_CHART_OUTLINED,       "manager"),
    ("admin",     "Administracion", ft.Icons.SETTINGS_OUTLINED,        "manager"),
]


def _resolve_view(key: str, page: ft.Page) -> ft.Control:
    """Importa lazy para evitar cargar todo al arrancar."""
    state = get_state()
    op_role = state.operator.role if state.operator else None
    # Guard server-side: verificar permiso aun si alguien llama directo
    min_role = next((mr for k, _, _, mr in _ALL_NAV_ITEMS if k == key), None)
    if min_role and not role_has_access(op_role, min_role):
        from setra_cards.ui.views.placeholder import build_placeholder
        return build_placeholder("Acceso restringido", "Tu rol no tiene permiso para esta vista")

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
    elif key == "reports":
        from setra_cards.ui.views.reports import build as b
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
            c.bgcolor = theme.GOLD_BG if selected else None
            c.border = ft.Border(left=ft.BorderSide(3, theme.GOLD)) if selected else None
            row: ft.Row = c.content  # type: ignore[assignment]
            icon_ctrl: ft.Icon = row.controls[0]  # type: ignore[assignment]
            icon_ctrl.color = theme.GOLD if selected else theme.TEXT_MUTED
            text_ctrl: ft.Text = row.controls[1]  # type: ignore[assignment]
            text_ctrl.color = theme.GOLD_LIGHT if selected else theme.TEXT
            text_ctrl.weight = ft.FontWeight.W_700 if selected else ft.FontWeight.W_500

    op_role = state.operator.role if state.operator else None
    NAV_ITEMS = [
        (key, label, icon)
        for key, label, icon, min_role in _ALL_NAV_ITEMS
        if min_role is None or role_has_access(op_role, min_role)
    ]
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
                        color=theme.TEXT_INVERSE,
                    ),
                    bgcolor=theme.GOLD,
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
        padding=ft.Padding(14, 10, 14, 10),
    )

    brand_header = ft.Container(
        content=ft.Row(
            [
                ft.Container(
                    content=ft.Text(
                        "S",
                        size=22,
                        weight=ft.FontWeight.W_700,
                        color=theme.TEXT_INVERSE,
                        font_family=theme.FONT_DISPLAY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    width=42,
                    height=42,
                    bgcolor=theme.GOLD,
                    border_radius=10,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Column(
                    [
                        ft.Text(
                            "Setra CARDS",
                            size=17,
                            weight=ft.FontWeight.W_700,
                            color=theme.GOLD_LIGHT,
                            font_family=theme.FONT_DISPLAY,
                        ),
                        ft.Text(
                            "SETRA HOLDINGS",
                            size=9,
                            color=theme.GOLD,
                            weight=ft.FontWeight.W_700,
                        ),
                    ],
                    spacing=0,
                    tight=True,
                ),
            ],
            spacing=11,
        ),
        padding=ft.Padding(16, 22, 14, 14),
    )

    hotel_chip = ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    "HOTEL CONFIGURADO",
                    size=9,
                    color=theme.TEXT_LIGHT,
                    weight=ft.FontWeight.W_700,
                ),
                ft.Text(
                    state.hotel.name,
                    size=12,
                    color=theme.TEXT,
                    weight=ft.FontWeight.W_600,
                ),
            ],
            spacing=2,
            tight=True,
        ),
        padding=ft.Padding(14, 10, 14, 10),
        margin=ft.Margin(12, 0, 12, 12),
        bgcolor=theme.SURFACE_ALT,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=10,
    )

    sidebar = ft.Container(
        content=ft.Column(
            [
                brand_header,
                hotel_chip,
                *nav_controls,
                ft.Container(expand=True),
                ft.Divider(height=1, color=theme.BORDER),
                operator_badge,
                ft.Container(
                    content=ft.TextButton(
                        content=ft.Text("Cerrar sesion", color=theme.TEXT_MUTED),
                        icon=ft.Icons.LOGOUT,
                        on_click=do_logout,
                        style=ft.ButtonStyle(color=theme.TEXT_MUTED),
                    ),
                    padding=ft.Padding(10, 0, 10, 10),
                ),
            ],
            spacing=2,
            expand=True,
        ),
        width=250,
        bgcolor=theme.BG_ALT,
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
