"""Vista de login — operador + PIN."""
from __future__ import annotations

import flet as ft

from setra_cards.core.app_state import OperatorSession, get_state
from setra_cards.services.auth import authenticate, ensure_seed_admin
from setra_cards.storage.database import init_db
from setra_cards.ui import theme


def build(page: ft.Page) -> ft.Control:
    sf = init_db()
    with sf() as s:
        ensure_seed_admin(s)

    state = get_state()

    name_field = ft.TextField(
        label="Operador",
        value="Admin",
        autofocus=True,
        width=320,
        border_radius=theme.INPUT_RADIUS,
        bgcolor=theme.SURFACE,
    )
    pin_field = ft.TextField(
        label="PIN",
        password=True,
        can_reveal_password=True,
        width=320,
        border_radius=theme.INPUT_RADIUS,
        bgcolor=theme.SURFACE,
    )
    error_txt = ft.Text("", color=theme.ERROR, size=13)

    def do_login(e: ft.ControlEvent | None = None) -> None:
        error_txt.value = ""
        with sf() as s:
            result = authenticate(s, name_field.value or "", pin_field.value or "")
        if not result.ok or not result.operator:
            error_txt.value = result.reason
            page.update()
            return
        op = result.operator
        state.login(OperatorSession(
            id=op.id, name=op.name, role=op.role, must_change_pin=op.must_change_pin,
        ))
        # Importar aqui para evitar ciclo: shell depende de login trigger
        from setra_cards.ui.views.shell import open_shell
        open_shell(page)

    pin_field.on_submit = do_login

    card = ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    state.hotel.name,
                    size=12,
                    weight=ft.FontWeight.W_500,
                    color=theme.TEXT_MUTED,
                ),
                ft.Container(height=8),
                ft.Text("Setra CARDS", size=32, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                ft.Text("Inicia sesion con tu operador", size=14, color=theme.TEXT_MUTED),
                ft.Container(height=24),
                name_field,
                pin_field,
                error_txt,
                ft.Container(height=8),
                ft.FilledButton(
                    content=ft.Text("Entrar", size=15, weight=ft.FontWeight.W_600),
                    on_click=do_login,
                    width=320,
                    height=48,
                    style=ft.ButtonStyle(
                        bgcolor=theme.PRIMARY,
                        color=theme.SURFACE,
                        shape=ft.RoundedRectangleBorder(radius=theme.BUTTON_RADIUS),
                    ),
                ),
                ft.Container(height=16),
                ft.Text(
                    "Usuario inicial: Admin / PIN: 1234",
                    size=11,
                    color=theme.TEXT_LIGHT,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        ),
        bgcolor=theme.SURFACE,
        padding=ft.Padding(40, 40, 40, 36),
        border_radius=theme.CARD_RADIUS,
        border=ft.Border.all(1, theme.BORDER),
        width=420,
    )

    return ft.Container(
        content=card,
        alignment=ft.Alignment.CENTER,
        expand=True,
        bgcolor=theme.BG,
    )
