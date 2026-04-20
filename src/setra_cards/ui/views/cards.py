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
from setra_cards.services import staff as staff_service
from setra_cards.services.auth import role_has_access
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

# (key, label, icon, descripcion, min_role). min_role=None → todos los roles
CARD_KINDS = [
    ("guest",    "Huesped",      ft.Icons.BADGE_OUTLINED,             "Abre una habitacion hasta el checkout",   None),
    ("laundry",  "Limpieza",     ft.Icons.CLEANING_SERVICES_OUTLINED, "Acceso para personal de limpieza por horas", None),
    ("master",   "Maestra",      ft.Icons.KEY,                        "Abre todas las habitaciones",             "manager"),
    ("auth",     "Autorizacion", ft.Icons.VERIFIED,                   "Programar cerraduras nuevas",             "manager"),
    ("clock",    "Reloj",        ft.Icons.SCHEDULE,                   "Sincroniza hora en las cerraduras",       "manager"),
    ("setting",  "Setting",      ft.Icons.SETTINGS_INPUT_COMPONENT,   "Asigna numero a una cerradura",           "manager"),
    ("blank",    "Borrar",       ft.Icons.DELETE_SWEEP_OUTLINED,      "Borra una tarjeta existente",             None),
    ("read",     "Leer",         ft.Icons.SEARCH,                     "Diagnostico de una tarjeta",              None),
    ("s70",      "S70 Audit",    ft.Icons.HISTORY_EDU_OUTLINED,       "Lee eventos de apertura de una cerradura", "manager"),
]


def build(page: ft.Page) -> ft.Control:
    state = get_state()
    op_role = state.operator.role if state.operator else None
    allowed_kinds = [
        k for k in CARD_KINDS
        if k[4] is None or role_has_access(op_role, k[4])
    ]
    default_kind = allowed_kinds[0][0] if allowed_kinds else "guest"
    selected = {"kind": default_kind}
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
        [_kind_chip(k, selected, on_select_kind) for k in allowed_kinds],
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
    bg = theme.SURFACE_ALT
    border_color = theme.SUCCESS if ok else theme.WARNING
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
        border=ft.Border.all(1, border_color),
        border_radius=10,
    )


def _kind_chip(kind_tuple: tuple, selected: dict, on_click_cb) -> ft.Container:
    key, label, icon, _desc = kind_tuple[0], kind_tuple[1], kind_tuple[2], kind_tuple[3]
    is_active = selected["kind"] == key

    def on_click(e: ft.ControlEvent) -> None:
        on_click_cb(key)

    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(icon, size=16, color=theme.TEXT_INVERSE if is_active else theme.TEXT_MUTED),
                ft.Text(label, size=13, weight=ft.FontWeight.W_600,
                        color=theme.TEXT_INVERSE if is_active else theme.TEXT),
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
    if kind == "laundry":
        return _laundry_form(page, result_host)
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
    if kind == "s70":
        return _s70_form(page, result_host)
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


# --- Form: Limpieza ---

def _laundry_form(page: ft.Page, result_host: ft.Container) -> ft.Control:
    """Tarjeta de limpieza con asignación lógica de habitaciones.

    El protocolo Locstar graba room=0x00 (acceso global a TODO el hotel).
    La tarjeta física SIEMPRE abre todas las habitaciones. La selección de
    habitaciones asignadas aquí se registra en el audit log para trazabilidad
    (qué limpiador debía cubrir qué habitaciones), pero NO limita la tarjeta.
    """
    state = get_state()
    sf = init_db()
    hours_val = {"v": 8}

    with sf() as s:
        staff_list = [
            st for st in staff_service.list_staff(s, active_only=True)
            if st.role == "limpieza"
        ]
        staff_info = [
            {
                "id": st.id,
                "name": st.name,
                "assigned": staff_service.assigned_room_list(st),
            }
            for st in staff_list
        ]
        all_rooms = rooms_service.list_rooms(s)
        room_displays = [r.display_number for r in all_rooms]

    selected_staff = {"id": None}
    selected_rooms: set[str] = set()

    hours_display = ft.Text("8 horas", size=14, color=theme.GOLD_LIGHT, weight=ft.FontWeight.W_600)
    expires_display = ft.Text(
        (datetime.now() + timedelta(hours=8)).strftime("%d/%m/%Y %H:%M"),
        size=12, color=theme.TEXT_MUTED,
    )
    rooms_count_text = ft.Text("0 habitaciones seleccionadas", size=12, color=theme.TEXT_MUTED)
    rooms_grid_host = ft.Container()

    def on_hours_change(e):
        h = int(float(e.control.value))
        hours_val["v"] = h
        hours_display.value = f"{h} hora{'s' if h != 1 else ''}"
        expires_display.value = (datetime.now() + timedelta(hours=h)).strftime("%d/%m/%Y %H:%M")
        page.update()

    hours_slider = ft.Slider(
        min=1, max=24, divisions=23, value=8,
        active_color=theme.GOLD, thumb_color=theme.GOLD,
        on_change=on_hours_change,
    )

    def update_count():
        n = len(selected_rooms)
        rooms_count_text.value = f"{n} habitación{'es' if n != 1 else ''} registrada{'s' if n != 1 else ''} en el audit log"

    def render_rooms_grid():
        chips = []
        for disp in room_displays:
            is_sel = disp in selected_rooms

            def make_toggle(d):
                def toggle(e):
                    if d in selected_rooms:
                        selected_rooms.discard(d)
                    else:
                        selected_rooms.add(d)
                    render_rooms_grid()
                    update_count()
                    page.update()
                return toggle

            chips.append(ft.Container(
                content=ft.Text(disp, size=12, weight=ft.FontWeight.W_600,
                                color=theme.TEXT_INVERSE if is_sel else theme.TEXT),
                padding=ft.Padding(10, 6, 10, 6),
                bgcolor=theme.GOLD if is_sel else theme.SURFACE_ALT,
                border=ft.Border.all(1, theme.GOLD if is_sel else theme.BORDER),
                border_radius=8,
                on_click=make_toggle(disp),
                ink=True,
            ))
        rooms_grid_host.content = ft.Row(chips, wrap=True, spacing=6, run_spacing=6)

    def on_staff_change(e):
        val = e.control.value
        if val and val != "0":
            selected_staff["id"] = int(val)
            match = next((s for s in staff_info if s["id"] == int(val)), None)
            if match:
                selected_rooms.clear()
                selected_rooms.update(match["assigned"])
        else:
            selected_staff["id"] = None
            selected_rooms.clear()
        render_rooms_grid()
        update_count()
        page.update()

    staff_dd = ft.Dropdown(
        label="Personal de limpieza (opcional)",
        options=[ft.dropdown.Option("0", "— Sin asignar staff —")] +
                [ft.dropdown.Option(str(s["id"]), f"{s['name']} ({len(s['assigned'])} hab.)") for s in staff_info],
        value="0",
        border_radius=theme.INPUT_RADIUS,
    )
    staff_dd.on_change = on_staff_change

    def select_all(e):
        selected_rooms.update(room_displays)
        render_rooms_grid()
        update_count()
        page.update()

    def clear_all(e):
        selected_rooms.clear()
        render_rooms_grid()
        update_count()
        page.update()

    def do_emit(e: ft.ControlEvent) -> None:
        if not state.encoder:
            show_toast(page, "Encoder no conectado", "error")
            return
        staff_name = None
        if selected_staff["id"]:
            match = next((s for s in staff_info if s["id"] == selected_staff["id"]), None)
            if match:
                staff_name = match["name"]
        with sf() as s:
            result = card_service.create_laundry_card(
                encoder=state.encoder,
                hotel=state.hotel,
                session=s,
                hours=hours_val["v"],
                operator=state.operator.name if state.operator else "?",
                staff_name=staff_name,
                assigned_rooms=sorted(selected_rooms) if selected_rooms else None,
            )
        _show_card_result(result_host, result)
        page.update()

    render_rooms_grid()
    update_count()

    return SectionCard(
        title="Tarjeta de limpieza",
        content=ft.Column(
            [
                ft.Container(
                    content=ft.Row([
                        ft.Icon(ft.Icons.INFO_OUTLINE, size=16, color=theme.WARNING),
                        ft.Text(
                            "La tarjeta física SIEMPRE abre todas las habitaciones (protocolo Locstar). "
                            "Las habitaciones seleccionadas se guardan en el audit log como asignación "
                            "lógica del limpiador para trazabilidad.",
                            size=11, color=theme.WARNING,
                        ),
                    ], spacing=8),
                    bgcolor=theme.SURFACE_ALT,
                    border=ft.Border.all(1, theme.WARNING),
                    border_radius=8,
                    padding=ft.Padding(12, 10, 12, 10),
                ),
                ft.Container(height=12),
                staff_dd,
                ft.Container(height=8),
                ft.Row([
                    ft.Text("Habitaciones asignadas (audit log)", size=12,
                            color=theme.TEXT_MUTED, weight=ft.FontWeight.W_600, expand=True),
                    SecondaryButton("Todas", on_click=select_all, icon=ft.Icons.DONE_ALL),
                    SecondaryButton("Limpiar", on_click=clear_all, icon=ft.Icons.CLEAR),
                ], spacing=6),
                rooms_count_text,
                ft.Container(
                    content=rooms_grid_host,
                    bgcolor=theme.SURFACE_ALT,
                    border=ft.Border.all(1, theme.BORDER),
                    border_radius=8,
                    padding=ft.Padding(10, 10, 10, 10),
                ),
                ft.Container(height=12),
                ft.Row([
                    ft.Text("Duración:", size=13, color=theme.TEXT_MUTED),
                    hours_display,
                ], spacing=8),
                hours_slider,
                ft.Row([
                    ft.Icon(ft.Icons.SCHEDULE_OUTLINED, size=14, color=theme.TEXT_MUTED),
                    ft.Text("Vence: ", size=12, color=theme.TEXT_MUTED),
                    expires_display,
                ], spacing=4),
                ft.Container(height=12),
                PrimaryButton("Emitir tarjeta limpieza", on_click=do_emit,
                              icon=ft.Icons.CLEANING_SERVICES_OUTLINED),
            ],
            spacing=8,
            tight=True,
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
        if exp <= datetime.now():
            show_toast(page, "La fecha de vencimiento debe ser futura", "error")
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


# --- Form: S70 Audit Trail ---

def _s70_form(page: ft.Page, result_host: ft.Container) -> ft.Control:
    state = get_state()
    sf = init_db()

    header_info = ft.Column([], spacing=2, tight=True)
    events_list = ft.Column([], spacing=4, tight=True, scroll=ft.ScrollMode.AUTO)
    events_host = ft.Container(
        content=events_list,
        padding=ft.Padding(0, 8, 0, 0),
    )

    def on_submit(e: ft.ControlEvent) -> None:
        if not state.encoder:
            show_toast(page, "Encoder no conectado", "error")
            return
        op_name = state.operator.name if state.operator else "?"
        with sf() as s:
            ok, msg, info = card_service.read_s70_data_card(
                encoder=state.encoder,
                hotel=state.hotel,
                session=s,
                operator=op_name,
            )
        if not ok:
            show_toast(page, msg, "error")
            header_info.controls = [ft.Text(msg, size=12, color=theme.ERROR)]
            events_list.controls = []
            page.update()
            return

        # Header
        header_info.controls = [
            ft.Row([
                ft.Icon(ft.Icons.HISTORY_EDU_OUTLINED, size=16, color=theme.GOLD),
                ft.Text(f"S70 UID: {info['s70_uid']}", size=13,
                        weight=ft.FontWeight.W_600, color=theme.TEXT),
            ], spacing=6),
            ft.Text(
                f"Edificio {info['building']} · Piso {info['floor']} · "
                f"{len(info['events'])} eventos ({info['new_count']} nuevos) · "
                f"Sectores leidos: {info['sectors_read']}",
                size=11, color=theme.TEXT_MUTED,
            ),
        ]

        # Events
        if not info["events"]:
            events_list.controls = [
                ft.Text("Sin eventos en la tarjeta.", size=12, color=theme.TEXT_MUTED)
            ]
        else:
            rows = []
            for ev in info["events"]:
                badge_color = theme.SUCCESS if ev["event_type"] == 0 else theme.WARNING
                lines: list[ft.Control] = [
                    ft.Row([
                        ft.Icon(ft.Icons.DOOR_FRONT_DOOR_OUTLINED, size=14, color=badge_color),
                        ft.Text(ev["timestamp"], size=12, weight=ft.FontWeight.W_600,
                                color=theme.TEXT),
                        ft.Text(ev["event_type_name"], size=11, color=theme.TEXT_MUTED),
                    ], spacing=8),
                ]
                detail_bits = []
                if ev.get("guest_name"):
                    detail_bits.append(f"Huesped: {ev['guest_name']}")
                if ev.get("room_display"):
                    detail_bits.append(f"Hab. {ev['room_display']}")
                if ev.get("operator"):
                    detail_bits.append(f"Operador: {ev['operator']}")
                if ev.get("card_type_match"):
                    detail_bits.append(f"Tipo: {ev['card_type_match']}")
                detail_bits.append(f"UID: {ev['card_uid']}")
                lines.append(
                    ft.Text(" · ".join(detail_bits), size=11, color=theme.TEXT_MUTED)
                )
                rows.append(
                    ft.Container(
                        content=ft.Column(lines, spacing=2, tight=True),
                        padding=ft.Padding(12, 8, 12, 8),
                        bgcolor=theme.SURFACE_ALT,
                        border=ft.Border.all(1, theme.BORDER),
                        border_radius=8,
                    )
                )
            events_list.controls = rows

        show_toast(page, msg, "success")
        page.update()

    return SectionCard(
        title="S70 Audit Trail — Lectura de eventos de apertura",
        content=ft.Column(
            [
                ft.Text(
                    "Coloca una tarjeta S70 (pasada previamente por una cerradura) en el encoder. "
                    "Se extraen los eventos de apertura y se cruzan con la emision de tarjetas "
                    "para identificar que huesped/operador entro a cada hora.",
                    size=12, color=theme.TEXT_MUTED,
                ),
                ft.Container(height=8),
                ft.Row([PrimaryButton("Leer S70", icon=ft.Icons.HISTORY_EDU_OUTLINED, on_click=on_submit)]),
                ft.Container(height=12),
                ft.Container(
                    content=header_info,
                    padding=ft.Padding(14, 12, 14, 12),
                    bgcolor=theme.SURFACE_ALT,
                    border=ft.Border.all(1, theme.BORDER),
                    border_radius=10,
                ),
                events_host,
            ],
            spacing=8,
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
            bgcolor=theme.SURFACE_ALT,
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
            bgcolor=theme.SURFACE_ALT,
            border=ft.Border.all(1, theme.ERROR),
            border_radius=10,
            margin=ft.Margin(0, 14, 0, 0),
        )
