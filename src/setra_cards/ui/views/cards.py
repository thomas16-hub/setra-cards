"""Vista Emision de tarjetas.

Flujo principal: seleccionar tipo de tarjeta -> formulario especifico ->
grabar (con estado de encoder en vivo) -> feedback visual.

Tipos soportados:
- Huesped: habitacion + huesped opcional + fecha/hora checkout
- Maestra: fecha/hora expiracion
- Autorizacion: sin campos (instantanea)
- Reloj: sin campos (sincroniza hora)
- Setting: habitacion (grabar en cerradura)
- Borrar: lee y borra la tarjeta
- Leer: diagnostico
"""
from __future__ import annotations

from datetime import datetime, timedelta

import flet as ft

from setra_cards.core.app_state import get_state
from setra_cards.services import card_service
from setra_cards.services import rooms as rooms_service
from setra_cards.services import guests as guests_service
from setra_cards.storage.database import init_db
from setra_cards.ui import theme
from setra_cards.ui.components import (
    EmptyState,
    PageHeader,
    PrimaryButton,
    SecondaryButton,
    SectionCard,
    show_toast,
)

CARD_KINDS = [
    ("guest", "Huesped", ft.Icons.BADGE_OUTLINED, "Abre una habitacion hasta el checkout"),
    ("master", "Maestra", ft.Icons.KEY, "Abre todas las habitaciones"),
    ("auth", "Autorizacion", ft.Icons.VERIFIED, "Programar cerraduras nuevas"),
    ("clock", "Reloj", ft.Icons.SCHEDULE, "Sincroniza hora en las cerraduras"),
    ("setting", "Setting", ft.Icons.SETTINGS_INPUT_COMPONENT, "Asigna numero a una cerradura"),
    ("blank", "Borrar", ft.Icons.DELETE_SWEEP_OUTLINED, "Borra una tarjeta existente"),
    ("read", "Leer", ft.Icons.SEARCH, "Diagnostico de una tarjeta"),
]


def build(page: ft.Page) -> ft.Control:
    state = get_state()
    selected = {"kind": "guest"}
    form_host = ft.Container(expand=True)
    result_host = ft.Container()

    def render_form() -> None:
        form_host.content = _form_for_kind(page, selected["kind"], result_host)
        page.update()

    def on_select_kind(kind: str) -> None:
        selected["kind"] = kind
        result_host.content = None
        render_form()

    encoder_banner = _encoder_status_banner(state)

    kind_chips = ft.Row(
        [_kind_chip(k, selected, on_select_kind) for k in CARD_KINDS],
        wrap=True,
        spacing=8,
        run_spacing=8,
    )

    header = PageHeader(
        title="Emitir tarjeta",
        subtitle="Selecciona el tipo y configura los detalles",
    )

    render_form()
    return ft.Container(
        content=ft.Column(
            [
                header,
                encoder_banner,
                ft.Container(height=14),
                kind_chips,
                ft.Container(height=14),
                form_host,
                result_host,
            ],
            spacing=0,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
    )


def _encoder_status_banner(state) -> ft.Control:
    ok = state.encoder is not None
    icon = ft.Icons.USB if ok else ft.Icons.USB_OFF
    fg = theme.SUCCESS if ok else theme.WARNING
    bg = "#E8F7EC" if ok else "#FFF4D6"
    title = f"Encoder conectado en {state.encoder_port}" if ok else "Encoder no conectado"
    sub = "Coloca la tarjeta en el lector y presiona Grabar" if ok else "Ve a Administracion y presiona 'Detectar automaticamente'"
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(icon, size=20, color=fg),
                ft.Column(
                    [
                        ft.Text(title, size=13, weight=ft.FontWeight.W_600, color=fg),
                        ft.Text(sub, size=11, color=theme.TEXT_MUTED),
                    ],
                    spacing=2,
                    tight=True,
                ),
            ],
            spacing=12,
        ),
        padding=ft.Padding(14, 10, 14, 10),
        bgcolor=bg,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=10,
    )


def _kind_chip(kind_tuple: tuple, selected: dict, on_click_cb) -> ft.Container:
    key, label, icon, _desc = kind_tuple
    is_active = selected["kind"] == key

    def on_click(e: ft.ControlEvent) -> None:
        on_click_cb(key)

    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(icon, size=16, color=theme.SURFACE if is_active else theme.TEXT_MUTED),
                ft.Text(label, size=13, weight=ft.FontWeight.W_600,
                        color=theme.SURFACE if is_active else theme.TEXT),
            ],
            spacing=6,
            tight=True,
        ),
        padding=ft.Padding(14, 8, 14, 8),
        bgcolor=theme.PRIMARY if is_active else theme.SURFACE,
        border=ft.Border.all(1, theme.PRIMARY if is_active else theme.BORDER),
        border_radius=999,
        on_click=on_click,
        ink=True,
    )


def _form_for_kind(page: ft.Page, kind: str, result_host: ft.Container) -> ft.Control:
    if kind == "guest":
        return _guest_form(page, result_host)
    if kind == "master":
        return _master_form(page, result_host)
    if kind == "setting":
        return _setting_form(page, result_host)
    if kind in ("auth", "clock"):
        return _instant_form(page, result_host, kind)
    if kind == "blank":
        return _instant_form(page, result_host, "blank")
    if kind == "read":
        return _read_form(page, result_host)
    return ft.Container()


def _find_kind(kind_key: str) -> tuple:
    return next(k for k in CARD_KINDS if k[0] == kind_key)


# --- Form: Huesped ---

def _guest_form(page: ft.Page, result_host: ft.Container) -> ft.Control:
    state = get_state()
    sf = init_db()

    with sf() as s:
        rooms = rooms_service.list_rooms(s)
        guest_options = guests_service.list_guests(s, limit=200)

    room_dd = ft.Dropdown(
        label="Habitacion",
        options=[ft.dropdown.Option(str(r.id), f"Hab. {r.display_number}") for r in rooms] or
                [ft.dropdown.Option("", "Sin habitaciones — crea una primero")],
        border_radius=theme.INPUT_RADIUS,
        expand=True,
    )
    guest_dd = ft.Dropdown(
        label="Huesped (opcional)",
        options=[ft.dropdown.Option("0", "— Sin asociar —")] +
                [ft.dropdown.Option(str(g.id), f"{g.name}" + (f" · {g.document}" if g.document else "")) for g in guest_options],
        value="0",
        border_radius=theme.INPUT_RADIUS,
        expand=True,
    )

    now = datetime.now()
    default_checkout = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)

    checkout_date = ft.TextField(
        label="Fecha de salida (YYYY-MM-DD)",
        value=default_checkout.strftime("%Y-%m-%d"),
        border_radius=theme.INPUT_RADIUS,
    )
    checkout_time = ft.TextField(
        label="Hora salida (HH:MM)",
        value=default_checkout.strftime("%H:%M"),
        border_radius=theme.INPUT_RADIUS,
    )

    days_slider = ft.Slider(
        min=1, max=14, value=1, divisions=13, label="{value} noche(s)",
        active_color=theme.PRIMARY,
    )

    def on_days_change(e: ft.ControlEvent) -> None:
        days = int(e.control.value)
        co = (datetime.now() + timedelta(days=days)).replace(hour=12, minute=0, second=0, microsecond=0)
        checkout_date.value = co.strftime("%Y-%m-%d")
        checkout_time.value = co.strftime("%H:%M")
        page.update()

    days_slider.on_change = on_days_change

    def on_submit(e: ft.ControlEvent) -> None:
        if not state.encoder:
            show_toast(page, "Encoder no conectado", "error")
            return
        if not room_dd.value:
            show_toast(page, "Selecciona una habitacion", "error")
            return
        try:
            checkout = datetime.strptime(f"{checkout_date.value} {checkout_time.value}", "%Y-%m-%d %H:%M")
        except Exception:
            show_toast(page, "Fecha u hora invalida (formato: YYYY-MM-DD HH:MM)", "error")
            return
        if checkout <= datetime.now():
            show_toast(page, "La fecha de salida debe ser futura", "error")
            return

        with sf() as s:
            room = rooms_service.get_room(s, int(room_dd.value))
            if not room:
                show_toast(page, "Habitacion no encontrada", "error")
                return
            guest = None
            if guest_dd.value and guest_dd.value != "0":
                guest = guests_service.get_guest(s, int(guest_dd.value))

            result = card_service.create_guest_card(
                encoder=state.encoder,
                hotel=state.hotel,
                session=s,
                room=room,
                guest=guest,
                valid_from=datetime.now(),
                valid_until=checkout,
                operator=state.operator.name if state.operator else "?",
            )
        _show_card_result(result_host, result)
        page.update()

    return SectionCard(
        title="Tarjeta de huesped",
        content=ft.Column(
            [
                ft.Row([room_dd, guest_dd], spacing=12),
                ft.Container(height=8),
                ft.Text("Duracion", size=12, color=theme.TEXT_MUTED, weight=ft.FontWeight.W_600),
                days_slider,
                ft.Row([checkout_date, checkout_time], spacing=12),
                ft.Container(height=14),
                ft.Row([PrimaryButton("Grabar tarjeta", icon=ft.Icons.CREDIT_CARD, on_click=on_submit)]),
            ],
            spacing=10,
        ),
    )


# --- Form: Maestra ---

def _master_form(page: ft.Page, result_host: ft.Container) -> ft.Control:
    state = get_state()
    sf = init_db()
    default_exp = datetime.now() + timedelta(days=30)

    exp_date = ft.TextField(label="Vence (YYYY-MM-DD)",
                            value=default_exp.strftime("%Y-%m-%d"),
                            border_radius=theme.INPUT_RADIUS)
    exp_time = ft.TextField(label="Hora (HH:MM)",
                            value="23:59",
                            border_radius=theme.INPUT_RADIUS)

    def on_submit(e: ft.ControlEvent) -> None:
        if not state.encoder:
            show_toast(page, "Encoder no conectado", "error")
            return
        try:
            exp = datetime.strptime(f"{exp_date.value} {exp_time.value}", "%Y-%m-%d %H:%M")
        except Exception:
            show_toast(page, "Fecha u hora invalida", "error")
            return
        with sf() as s:
            result = card_service.create_master_card(
                encoder=state.encoder, hotel=state.hotel, session=s,
                valid_until=exp,
                operator=state.operator.name if state.operator else "?",
            )
        _show_card_result(result_host, result)
        page.update()

    return SectionCard(
        title="Tarjeta maestra (abre todas las habitaciones)",
        content=ft.Column(
            [
                ft.Row([exp_date, exp_time], spacing=12),
                ft.Container(height=12),
                ft.Row([PrimaryButton("Grabar tarjeta maestra", icon=ft.Icons.KEY, on_click=on_submit)]),
            ],
            spacing=10,
        ),
    )


# --- Form: Setting (grabar a cerradura nueva) ---

def _setting_form(page: ft.Page, result_host: ft.Container) -> ft.Control:
    state = get_state()
    sf = init_db()

    with sf() as s:
        rooms = rooms_service.list_rooms(s)

    room_dd = ft.Dropdown(
        label="Habitacion",
        options=[ft.dropdown.Option(str(r.id), f"Hab. {r.display_number}") for r in rooms],
        border_radius=theme.INPUT_RADIUS,
        expand=True,
    )

    def on_submit(e: ft.ControlEvent) -> None:
        if not state.encoder:
            show_toast(page, "Encoder no conectado", "error")
            return
        if not room_dd.value:
            show_toast(page, "Selecciona una habitacion", "error")
            return
        with sf() as s:
            room = rooms_service.get_room(s, int(room_dd.value))
            if not room:
                show_toast(page, "Habitacion no encontrada", "error")
                return
            result = card_service.create_setting_card(
                encoder=state.encoder, hotel=state.hotel, session=s, room=room,
                operator=state.operator.name if state.operator else "?",
            )
        _show_card_result(result_host, result)
        page.update()

    return SectionCard(
        title="Setting — asignar habitacion a una cerradura",
        content=ft.Column(
            [
                ft.Text("Coloca la tarjeta en el encoder y selecciona la habitacion.",
                        size=12, color=theme.TEXT_MUTED),
                ft.Container(height=8),
                room_dd,
                ft.Container(height=12),
                ft.Row([PrimaryButton("Grabar Setting", icon=ft.Icons.SETTINGS_INPUT_COMPONENT, on_click=on_submit)]),
            ],
            spacing=10,
        ),
    )


# --- Form: instant (Auth, Clock, Blank) ---

def _instant_form(page: ft.Page, result_host: ft.Container, kind: str) -> ft.Control:
    state = get_state()
    sf = init_db()

    labels = {
        "auth": ("Tarjeta de autorizacion", "Graba una tarjeta para autorizar cerraduras nuevas.", "Grabar Auth", ft.Icons.VERIFIED),
        "clock": ("Tarjeta de reloj", "Sincroniza la fecha/hora en las cerraduras.", "Grabar Clock", ft.Icons.SCHEDULE),
        "blank": ("Borrar tarjeta", "Coloca la tarjeta que quieres borrar en el encoder.", "Borrar tarjeta", ft.Icons.DELETE_SWEEP_OUTLINED),
    }
    title, hint, btn_label, icon = labels[kind]

    def on_submit(e: ft.ControlEvent) -> None:
        if not state.encoder:
            show_toast(page, "Encoder no conectado", "error")
            return
        op = state.operator.name if state.operator else "?"
        with sf() as s:
            if kind == "auth":
                result = card_service.create_auth_card(encoder=state.encoder, hotel=state.hotel, session=s, operator=op)
            elif kind == "clock":
                result = card_service.create_clock_card(encoder=state.encoder, hotel=state.hotel, session=s, operator=op)
            else:
                result = card_service.blank_existing_card(encoder=state.encoder, hotel=state.hotel, session=s, operator=op)
        _show_card_result(result_host, result)
        page.update()

    return SectionCard(
        title=title,
        content=ft.Column(
            [
                ft.Text(hint, size=12, color=theme.TEXT_MUTED),
                ft.Container(height=12),
                ft.Row([PrimaryButton(btn_label, icon=icon, on_click=on_submit)]),
            ],
            spacing=10,
        ),
    )


# --- Form: Read ---

def _read_form(page: ft.Page, result_host: ft.Container) -> ft.Control:
    state = get_state()
    output = ft.Text("", size=12, color=theme.TEXT, selectable=True)

    def on_submit(e: ft.ControlEvent) -> None:
        if not state.encoder:
            show_toast(page, "Encoder no conectado", "error")
            return
        ok, msg, info = card_service.read_existing_card(encoder=state.encoder, hotel=state.hotel)
        lines = [msg]
        if info:
            for k, v in info.items():
                if v is not None:
                    lines.append(f"  {k}: {v}")
        output.value = "\n".join(lines)
        output.color = theme.SUCCESS if ok else theme.WARNING
        result_host.content = None
        page.update()

    return SectionCard(
        title="Leer tarjeta (diagnostico)",
        content=ft.Column(
            [
                ft.Text("Coloca cualquier tarjeta en el encoder para ver su contenido.", size=12, color=theme.TEXT_MUTED),
                ft.Container(height=12),
                ft.Row([PrimaryButton("Leer tarjeta", icon=ft.Icons.SEARCH, on_click=on_submit)]),
                ft.Container(height=12),
                ft.Container(
                    content=output,
                    padding=ft.Padding(14, 12, 14, 12),
                    bgcolor=theme.SURFACE_ALT,
                    border=ft.Border.all(1, theme.BORDER),
                    border_radius=10,
                ),
            ],
            spacing=10,
        ),
    )


# --- Resultado visual ---

def _show_card_result(host: ft.Container, result) -> None:
    if result.ok:
        host.content = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK_CIRCLE, size=28, color=theme.SUCCESS),
                    ft.Column(
                        [
                            ft.Text(result.message or "Tarjeta grabada", size=14, weight=ft.FontWeight.W_600, color=theme.SUCCESS),
                            ft.Text(
                                " · ".join(filter(None, [
                                    f"Tipo: {result.card_type}",
                                    f"UID: {result.uid_hex}" if result.uid_hex else None,
                                    f"Hasta: {result.expires_at:%Y-%m-%d %H:%M}" if result.expires_at else None,
                                ])),
                                size=11, color=theme.TEXT_MUTED,
                            ),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                ],
                spacing=12,
            ),
            padding=ft.Padding(16, 14, 16, 14),
            bgcolor="#E8F7EC",
            border=ft.Border.all(1, theme.SUCCESS),
            border_radius=10,
            margin=ft.Margin(0, 14, 0, 0),
        )
    else:
        host.content = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.ERROR_OUTLINE, size=28, color=theme.ERROR),
                    ft.Column(
                        [
                            ft.Text(f"Error al grabar ({result.card_type})",
                                    size=14, weight=ft.FontWeight.W_600, color=theme.ERROR),
                            ft.Text(result.error or "Error desconocido", size=11, color=theme.TEXT_MUTED),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                ],
                spacing=12,
            ),
            padding=ft.Padding(16, 14, 16, 14),
            bgcolor="#FEE4E2",
            border=ft.Border.all(1, theme.ERROR),
            border_radius=10,
            margin=ft.Margin(0, 14, 0, 0),
        )
