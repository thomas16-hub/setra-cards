"""Vista de login — operador + PIN."""
from __future__ import annotations

import flet as ft

from setra_cards.core.app_state import OperatorSession, get_state
from setra_cards.services.auth import authenticate, ensure_seed_admin, hash_new_pin, update_pin
from setra_cards.storage.database import init_db
from setra_cards.ui import theme
from setra_cards.ui.components.basics import show_toast


def _setra_mark(size: int = 72) -> ft.Container:
    """Logo SETRA — cuadro gold con letra S serif."""
    return ft.Container(
        content=ft.Text(
            "S",
            size=int(size * 0.56),
            weight=ft.FontWeight.W_700,
            color=theme.TEXT_INVERSE,
            font_family=theme.FONT_DISPLAY,
            text_align=ft.TextAlign.CENTER,
        ),
        width=size,
        height=size,
        bgcolor=theme.GOLD,
        border_radius=int(size * 0.24),
        alignment=ft.Alignment.CENTER,
        shadow=ft.BoxShadow(
            spread_radius=0,
            blur_radius=18,
            color="#00000055",
            offset=ft.Offset(0, 6),
        ),
    )


def build(page: ft.Page) -> ft.Control:
    sf = init_db()
    with sf() as s:
        ensure_seed_admin(s)
        from setra_cards.storage.models import Operator
        operators = s.query(Operator).filter(Operator.active == True).order_by(Operator.name).all()
        op_names = [op.name for op in operators]
        # Solo mostrar hint de credencial default si el Admin aún tiene PIN por defecto
        default_admin = (
            s.query(Operator)
            .filter(Operator.name == "Admin", Operator.must_change_pin == True)
            .first()
        )
        show_default_hint = default_admin is not None

    state = get_state()

    name_field = ft.Dropdown(
        label="Operador",
        value=op_names[0] if op_names else None,
        options=[ft.dropdown.Option(n) for n in op_names],
        width=340,
        border_radius=theme.INPUT_RADIUS,
        bgcolor=theme.SURFACE_ALT,
        border_color=theme.BORDER_STRONG,
        focused_border_color=theme.GOLD,
        color=theme.TEXT,
        label_style=ft.TextStyle(color=theme.TEXT_MUTED),
    )
    pin_field = ft.TextField(
        label="PIN",
        password=True,
        can_reveal_password=True,
        width=340,
        border_radius=theme.INPUT_RADIUS,
        bgcolor=theme.SURFACE_ALT,
        border_color=theme.BORDER_STRONG,
        focused_border_color=theme.GOLD,
        color=theme.TEXT,
        label_style=ft.TextStyle(color=theme.TEXT_MUTED),
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
        if op.must_change_pin:
            _open_change_pin_dialog(page, op.id)
        else:
            from setra_cards.ui.views.shell import open_shell
            open_shell(page)

    pin_field.on_submit = do_login

    card = ft.Container(
        content=ft.Column(
            [
                _setra_mark(76),
                ft.Container(height=18),
                ft.Text(
                    "SETRA HOLDINGS",
                    size=11,
                    weight=ft.FontWeight.W_700,
                    color=theme.GOLD,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Text(
                    "Setra CARDS",
                    size=34,
                    weight=ft.FontWeight.W_700,
                    color=theme.GOLD_LIGHT,
                    font_family=theme.FONT_DISPLAY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=2),
                ft.Text(
                    state.hotel.name,
                    size=13,
                    weight=ft.FontWeight.W_500,
                    color=theme.TEXT_MUTED,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=28),
                name_field,
                pin_field,
                error_txt,
                ft.Container(height=8),
                ft.FilledButton(
                    content=ft.Text(
                        "INGRESAR",
                        size=14,
                        weight=ft.FontWeight.W_700,
                        color=theme.TEXT_INVERSE,
                    ),
                    on_click=do_login,
                    width=340,
                    height=50,
                    style=ft.ButtonStyle(
                        bgcolor=theme.GOLD,
                        color=theme.TEXT_INVERSE,
                        shape=ft.RoundedRectangleBorder(radius=theme.BUTTON_RADIUS),
                    ),
                ),
                ft.Container(height=20),
                ft.Container(
                    visible=show_default_hint,
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.INFO_OUTLINE, size=13, color=theme.TEXT_LIGHT),
                            ft.Text(
                                "Usuario inicial:  Admin  /  PIN: 1234  (cambiar tras primer ingreso)",
                                size=11,
                                color=theme.TEXT_LIGHT,
                            ),
                        ],
                        spacing=6,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        bgcolor=theme.SURFACE,
        padding=ft.Padding(48, 44, 48, 40),
        border_radius=theme.CARD_RADIUS,
        border=ft.Border.all(1, theme.BORDER),
        width=460,
        shadow=ft.BoxShadow(
            spread_radius=0,
            blur_radius=40,
            color="#00000066",
            offset=ft.Offset(0, 12),
        ),
    )

    footer = ft.Container(
        content=ft.Text(
            "SETRA HOLDINGS  ·  Sistema operativo CLAUDIO",
            size=10,
            color=theme.TEXT_LIGHT,
            weight=ft.FontWeight.W_500,
        ),
        alignment=ft.Alignment.CENTER,
        padding=ft.Padding(0, 16, 0, 0),
    )

    return ft.Container(
        content=ft.Column(
            [card, footer],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=0,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        bgcolor=theme.BG,
        gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_CENTER,
            end=ft.Alignment.BOTTOM_CENTER,
            colors=[theme.BG, theme.BG_ALT],
        ),
    )


def _open_change_pin_dialog(page: ft.Page, operator_id: int) -> None:
    """Modal bloqueante que obliga al operador a cambiar su PIN antes de continuar."""
    sf = init_db()
    new_pin = ft.TextField(
        label="Nuevo PIN",
        password=True, can_reveal_password=True,
        hint_text="Mínimo 4 caracteres",
        border_radius=8,
        bgcolor=theme.SURFACE_ALT,
        border_color=theme.BORDER_STRONG,
        focused_border_color=theme.GOLD,
        color=theme.TEXT,
        label_style=ft.TextStyle(color=theme.TEXT_MUTED),
        autofocus=True,
    )
    confirm_pin = ft.TextField(
        label="Confirmar PIN",
        password=True, can_reveal_password=True,
        border_radius=8,
        bgcolor=theme.SURFACE_ALT,
        border_color=theme.BORDER_STRONG,
        focused_border_color=theme.GOLD,
        color=theme.TEXT,
        label_style=ft.TextStyle(color=theme.TEXT_MUTED),
    )

    def do_save(e=None) -> None:
        if not new_pin.value or len(new_pin.value) < 4:
            show_toast(page, "El PIN debe tener al menos 4 caracteres", "error")
            return
        if new_pin.value != confirm_pin.value:
            show_toast(page, "Los PINs no coinciden", "error")
            return
        try:
            with sf() as s:
                update_pin(s, operator_id, new_pin.value)
            # Actualizar estado en memoria
            state = get_state()
            if state.operator:
                from setra_cards.core.app_state import OperatorSession
                state.login(OperatorSession(
                    id=state.operator.id,
                    name=state.operator.name,
                    role=state.operator.role,
                    must_change_pin=False,
                ))
        except ValueError as exc:
            show_toast(page, str(exc), "error")
            return

        dlg.open = False
        page.update()
        from setra_cards.ui.views.shell import open_shell
        open_shell(page)

    confirm_pin.on_submit = do_save

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Cambio de PIN obligatorio", size=18, weight=ft.FontWeight.W_600, color=theme.GOLD_LIGHT),
        content=ft.Container(
            content=ft.Column([
                ft.Text(
                    "Este es tu primer inicio de sesión. Debes establecer un PIN personal antes de continuar.",
                    size=13, color=theme.TEXT_MUTED,
                ),
                ft.Container(height=8),
                new_pin,
                confirm_pin,
            ], spacing=12, tight=True),
            width=400,
        ),
        actions=[
            ft.FilledButton(
                content=ft.Text("Guardar PIN", size=13, weight=ft.FontWeight.W_600, color=theme.TEXT_INVERSE),
                on_click=do_save,
                style=ft.ButtonStyle(
                    bgcolor=theme.GOLD,
                    shape=ft.RoundedRectangleBorder(radius=8),
                ),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=12),
        bgcolor=theme.SURFACE,
    )
    if dlg not in page.overlay:
        page.overlay.append(dlg)
    dlg.open = True
    page.update()
