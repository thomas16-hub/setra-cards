"""Hub operativo — grid de habitaciones + check-in/checkout rapido."""
from __future__ import annotations

from datetime import datetime, timedelta

import flet as ft

from setra_cards.core.app_state import get_state
from setra_cards.encoder.protocol import CARD_TYPE_GUEST
from setra_cards.services import rooms as rooms_service
from setra_cards.services.action_log import log as log_action
from setra_cards.services.auth import role_has_access
from setra_cards.services.card_service import (
    CardResult,
    blank_existing_card,
    create_guest_card,
    invalidate_active_guest_cards,
)
from setra_cards.storage.database import init_db
from setra_cards.storage.models import CardLog, Guest, Room
from setra_cards.ui import theme
from setra_cards.ui.components.basics import (
    Badge,
    PrimaryButton,
    SecondaryButton,
    _page_close,
    _page_open,
    make_busy_guard,
    show_toast,
)
from setra_cards.ui.components.card_write import run_card_write
from setra_cards.ui.components.pickers import DateTimeField, format_moment

MAINTENANCE_COLOR = "#B07CD8"

# Room housekeeping state -> (label, colour). Order = legend order.
ROOM_STATES = {
    "limpia":            ("Limpia",            theme.SUCCESS),
    "sucia":             ("Sucia",             theme.WARNING),
    "inspeccion":        ("Inspección",        theme.INFO),
    "mantenimiento":     ("Mantenimiento",     MAINTENANCE_COLOR),
    "fuera_de_servicio": ("Fuera de servicio", theme.ERROR),
}
# Used by the room action dialog: (label, colour, background).
STATE_LABELS = {k: (label, color, theme.SURFACE_ALT) for k, (label, color) in ROOM_STATES.items()}

OCCUPIED_BG = "#2A2412"
TILE_WIDTH = 124
TILE_HEIGHT = 76


def _blank_guest_card_for_checkout(*, encoder, hotel, session, operator, room=None):
    """Physical checkout must only blank THIS room's GUEST card.

    expect_card_type refuses a Master/Laundry card left on the encoder;
    expect_room refuses another room's guest card (which would otherwise be
    wiped while this room is marked as checked out). No force path.
    """
    expect_room = (room["building"], room["floor"], room["room_no_id"]) if room else None
    return blank_existing_card(
        encoder=encoder,
        hotel=hotel,
        session=session,
        operator=operator,
        expect_card_type=CARD_TYPE_GUEST,
        expect_room=expect_room,
    )


def _finish_checkout(session, room: dict, operator: str, action: str) -> None:
    """Expire the room's guest cards, mark it dirty and audit, in one commit.

    Keyed by room id (a renamed room is still checked out); the audit row
    goes through action_log.log so it joins the tamper-evident hash chain,
    and that call commits the whole checkout.
    """
    invalidate_active_guest_cards(session, room["id"], legacy_display=room["display_number"])
    r = session.get(Room, room["id"])
    if r is None:
        raise ValueError("Habitación no existe")
    r.state = "sucia"
    log_action(session, action, operator, f"Hab. {room['display_number']}")


def build(page: ft.Page) -> ft.Control:
    state = get_state()
    sf = init_db()

    host = ft.Column(spacing=0, expand=True, scroll=ft.ScrollMode.AUTO)

    def refresh() -> None:
        now = datetime.now()
        with sf() as s:
            rooms = rooms_service.list_rooms(s)
            rooms_count = len(rooms)
            guests_count = s.query(Guest).count()

            # Una sola query para todos los CardLog activos
            active_log_rows = s.query(CardLog).filter(
                CardLog.card_type == "Guest",
                CardLog.success.is_(True),
                CardLog.expires_at > now,
            ).all()

            # Cargar nombres de huéspedes en la misma sesión — evita DetachedInstanceError
            guest_ids = {cl.guest_id for cl in active_log_rows if cl.guest_id}
            guest_names: dict[int, str] = {}
            if guest_ids:
                guests = s.query(Guest).filter(Guest.id.in_(guest_ids)).all()
                guest_names = {g.id: g.name for g in guests}

            # Keyed by room id: a renamed room keeps its guest. room_display is
            # only the fallback for old rows logged without a room_id.
            display_to_id = {r.display_number: r.id for r in rooms}
            active_by_room: dict[int, dict] = {}
            for cl in active_log_rows:
                rid = cl.room_id or display_to_id.get(cl.room_display or "")
                if rid and rid not in active_by_room:
                    active_by_room[rid] = {
                        "card_log_id": cl.id,
                        "guest_id": cl.guest_id,
                        "guest_name": guest_names.get(cl.guest_id, "") if cl.guest_id else "",
                        "expires_at": cl.expires_at,
                        "uid_hex": cl.uid_hex,
                    }

            # Snapshot de rooms también (evitar DetachedInstanceError en tiles)
            rooms_data = [
                {
                    "id": r.id,
                    "display_number": r.display_number,
                    "sequential_id": r.sequential_id,
                    "building": r.building,
                    "floor": r.floor,
                    "room_no_id": r.room_no_id,
                    "state": r.state,
                }
                for r in rooms
            ]

        occupied_count = sum(1 for rd in rooms_data if rd["id"] in active_by_room)
        free = rooms_count - occupied_count

        header = ft.Row(
            [
                ft.Column(
                    [
                        ft.Text("Hub", size=26, weight=ft.FontWeight.BOLD,
                                color=theme.GOLD_LIGHT, font_family=theme.FONT_DISPLAY),
                        ft.Text(state.hotel.name, size=12, color=theme.TEXT_MUTED),
                    ],
                    spacing=0,
                    tight=True,
                    expand=True,
                ),
                _encoder_pill(state),
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    icon_color=theme.TEXT_MUTED,
                    icon_size=20,
                    tooltip="Actualizar",
                    on_click=lambda e: refresh(),
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        )

        summary = ft.Row(
            [
                _metric(str(rooms_count), "habitaciones"),
                _metric(str(occupied_count), "ocupadas", theme.GOLD_LIGHT),
                _metric(str(free), "libres", theme.SUCCESS),
                _metric(str(guests_count), "huéspedes registrados"),
            ],
            spacing=28,
            wrap=True,
        )

        if rooms_data:
            body: ft.Control = ft.Column(
                [_legend(rooms_data, active_by_room), ft.Container(height=4)]
                + [
                    _floor_row(page, floor, floor_rooms, active_by_room, sf, state, refresh)
                    for floor, floor_rooms in _group_by_floor(rooms_data)
                ],
                spacing=10,
            )
        else:
            body = ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.HOTEL_OUTLINED, size=40, color=theme.TEXT_LIGHT),
                        ft.Text("Sin habitaciones configuradas", size=14, color=theme.TEXT_MUTED),
                        ft.Text("Ve a Habitaciones para agregarlas", size=12, color=theme.TEXT_LIGHT),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                ),
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding(0, 48, 0, 48),
            )

        host.controls = [
            header,
            ft.Container(height=10),
            summary,
            ft.Container(height=14),
            body,
        ]
        page.update()

    refresh()
    return ft.Container(content=host, expand=True, padding=theme.PADDING_LG)


def _encoder_pill(state) -> ft.Container:
    ok = state.encoder is not None
    color = theme.SUCCESS if ok else theme.WARNING
    text = f"Encoder {state.encoder_port}" if ok else "Encoder no conectado"
    return ft.Container(
        content=ft.Row(
            [ft.Icon(ft.Icons.USB if ok else ft.Icons.USB_OFF, size=15, color=color),
             ft.Text(text, size=12, color=color, weight=ft.FontWeight.W_600)],
            spacing=6,
            tight=True,
        ),
        padding=ft.Padding(12, 6, 12, 6),
        border=ft.Border.all(1, color),
        border_radius=999,
        tooltip=None if ok else "Ve a Administración y presiona Detectar automáticamente",
    )


def _metric(value: str, label: str, color: str = theme.TEXT) -> ft.Row:
    return ft.Row(
        [
            ft.Text(value, size=22, weight=ft.FontWeight.BOLD, color=color),
            ft.Text(label, size=12, color=theme.TEXT_MUTED),
        ],
        spacing=6,
        tight=True,
        vertical_alignment=ft.CrossAxisAlignment.END,
    )


def _display_sort_key(display: str) -> tuple:
    digits = "".join(ch for ch in display if ch.isdigit())
    return (int(digits) if digits else 0, display)


def _group_by_floor(rooms_data: list[dict]) -> list[tuple[int, list[dict]]]:
    floors: dict[int, list[dict]] = {}
    for rd in rooms_data:
        floors.setdefault(rd["floor"], []).append(rd)
    return [
        (floor, sorted(rooms, key=lambda r: _display_sort_key(r["display_number"])))
        for floor, rooms in sorted(floors.items())
    ]


def _swatch(color: str, occupied: bool = False) -> ft.Container:
    if occupied:
        return ft.Container(width=14, height=14, bgcolor=OCCUPIED_BG,
                            border=ft.Border.all(2, theme.GOLD), border_radius=4)
    return ft.Container(width=10, height=10, bgcolor=color, border_radius=5)


def _legend(rooms_data: list[dict], active_by_room: dict) -> ft.Control:
    """What each colour means, with how many rooms are in that state now."""
    occupied = sum(1 for rd in rooms_data if rd["id"] in active_by_room)
    items: list[ft.Control] = [_legend_item(_swatch(theme.GOLD, occupied=True), "Ocupada", occupied)]
    for key, (label, color) in ROOM_STATES.items():
        count = sum(1 for rd in rooms_data if rd["state"] == key and rd["id"] not in active_by_room)
        items.append(_legend_item(_swatch(color), label, count))
    return ft.Row(items, spacing=18, run_spacing=6, wrap=True)


def _legend_item(swatch: ft.Control, label: str, count: int) -> ft.Row:
    return ft.Row(
        [swatch,
         ft.Text(label, size=12, color=theme.TEXT_MUTED),
         ft.Text(str(count), size=12, color=theme.TEXT, weight=ft.FontWeight.W_700)],
        spacing=6,
        tight=True,
        opacity=1.0 if count else 0.45,
    )


def _floor_row(page, floor: int, rooms: list[dict], active_by_room: dict, sf, state, refresh) -> ft.Control:
    occupied = sum(1 for rd in rooms if rd["id"] in active_by_room)
    return ft.Row(
        [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(f"PISO {floor}", size=11, weight=ft.FontWeight.W_700, color=theme.TEXT_MUTED),
                        ft.Text(f"{occupied}/{len(rooms)} ocup.", size=10, color=theme.TEXT_LIGHT),
                    ],
                    spacing=2,
                    tight=True,
                ),
                width=70,
                padding=ft.Padding(0, 10, 0, 0),
            ),
            ft.Row(
                [_room_tile(page, rd, active_by_room.get(rd["id"]), sf, state, refresh) for rd in rooms],
                spacing=8,
                run_spacing=8,
                wrap=True,
                expand=True,
            ),
        ],
        vertical_alignment=ft.CrossAxisAlignment.START,
        spacing=8,
    )


def _room_tile(page, room: dict, active_info: "dict | None", sf, state, refresh) -> ft.Container:
    """room y active_info son dicts planos — sin ORM detached."""
    label, state_color = ROOM_STATES.get(room["state"], ("Otro", theme.TEXT_MUTED))
    occupied = active_info is not None

    if occupied:
        guest = active_info["guest_name"] or "Ocupada"
        until = active_info["expires_at"]
        lower: list[ft.Control] = [
            ft.Text(guest, size=11, color=theme.GOLD_LIGHT, weight=ft.FontWeight.W_600,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(f"sale {until:%d/%m %H:%M}" if until else "", size=10, color=theme.TEXT_MUTED),
        ]
    else:
        lower = [ft.Text(label, size=11, color=state_color, weight=ft.FontWeight.W_600)]

    def on_click(e: ft.ControlEvent) -> None:
        _open_room_action(page, room, active_info, sf, state, refresh)

    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(room["display_number"], size=18, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                        _swatch(state_color),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(expand=True),
                *lower,
            ],
            spacing=1,
        ),
        width=TILE_WIDTH,
        height=TILE_HEIGHT,
        padding=ft.Padding(10, 8, 10, 8),
        bgcolor=OCCUPIED_BG if occupied else theme.SURFACE,
        border=ft.Border.all(1.5 if occupied else 1, theme.GOLD if occupied else theme.BORDER),
        border_radius=10,
        tooltip=f"Hab. {room['display_number']} · {label}" + (" · ocupada" if occupied else ""),
        on_click=on_click,
        ink=True,
    )


def _open_room_action(page, room: dict, active_info: "dict | None", sf, state, refresh) -> None:
    """Panel rápido de acción. room y active_info son dicts planos."""
    occupied = active_info is not None
    room_state = room["state"]
    label, fg, _ = STATE_LABELS.get(room_state, ("Otro", theme.TEXT_MUTED, theme.SURFACE_ALT))

    can_checkin = role_has_access(state.operator.role if state.operator else None, "frontdesk")
    can_checkout = role_has_access(state.operator.role if state.operator else None, "frontdesk")

    def do_close(e=None):
        _page_close(page, dlg)

    def do_checkin(e=None):
        _page_close(page, dlg)
        _open_checkin_form(page, room, sf, state, refresh)

    checkout_busy = {"v": False}

    def do_checkout(e=None):
        has_encoder = state.encoder is not None
        if has_encoder:
            # Checkout físico: dos pasos — primero borrar tarjeta, luego confirmar DB
            def confirm_physical():
                # Per-handler busy flag (no cross-service lock).
                if checkout_busy["v"]:
                    return
                _page_close(page, dlg)
                # Snapshot once: watchdog can clear/replace state.encoder mid-op.
                enc = state.encoder
                if enc is None:
                    show_toast(page, "Encoder no conectado — usa Checkout sin encoder", "error")
                    return
                checkout_busy["v"] = True
                try:
                    _do_physical_checkout(enc)
                finally:
                    checkout_busy["v"] = False

            def _do_physical_checkout(enc):
                op = state.operator.name if state.operator else "?"
                with sf() as s:
                    result = _blank_guest_card_for_checkout(
                        encoder=enc,
                        hotel=state.hotel,
                        session=s,
                        operator=op,
                        room=room,
                    )
                    if not result.ok:
                        # Tarjeta no borrada — no continuar con checkout lógico.
                        # The service error already says what is on the encoder.
                        show_toast(
                            page,
                            f"Checkout no realizado: {result.error or 'error del encoder'}. "
                            "Si la tarjeta del huésped se perdió, usa Checkout sin encoder.",
                            "error",
                        )
                        return
                    # Tarjeta borrada OK → expirar CardLog + cambiar estado + audit (atómico)
                    try:
                        _finish_checkout(s, room, op, "checkout")
                    except Exception as exc:
                        s.rollback()
                        show_toast(page, f"Error en checkout: {exc}", "error")
                        return
                    show_toast(page, f"Checkout hab. {room['display_number']}", "success")
                refresh()

            _open_confirm(
                page, f"Checkout hab. {room['display_number']}?",
                "Se borrará la tarjeta física y la habitación quedará sucia.",
                confirm_physical,
            )
        else:
            # Checkout lógico: sin encoder, advertencia fuerte
            def confirm_logical():
                _page_close(page, dlg)
                op = state.operator.name if state.operator else "?"
                with sf() as s:
                    try:
                        _finish_checkout(s, room, op, "checkout_logico")
                    except Exception as exc:
                        s.rollback()
                        show_toast(page, f"Error en checkout lógico: {exc}", "error")
                        return
                    show_toast(
                        page,
                        f"Checkout lógico hab. {room['display_number']} — tarjeta física puede seguir activa",
                        "warning",
                    )
                refresh()

            _open_confirm(
                page, f"Checkout SIN encoder — Hab. {room['display_number']}",
                "⚠ La tarjeta física NO se borrará. El huésped podría seguir abriendo la habitación. "
                "Usa esto solo si la tarjeta ya fue destruida o perdida.",
                confirm_logical,
            )

    def do_state(new_state: str):
        _page_close(page, dlg)
        with sf() as s:
            rooms_service.update_room(s, room["id"], state=new_state)
            log_action(s, "room_state", state.operator.name if state.operator else "?",
                       f"Hab. {room['display_number']} → {new_state}")
        show_toast(page, f"Hab. {room['display_number']} → {new_state}", "info")
        refresh()

    guest_info = ft.Container()
    if active_info:
        checkout_str = (
            active_info["expires_at"].strftime("%d/%m/%Y %H:%M")
            if active_info["expires_at"] else "—"
        )
        guest_info = ft.Container(
            content=ft.Column(
                [
                    ft.Row([
                        ft.Icon(ft.Icons.PERSON, size=14, color=theme.GOLD),
                        ft.Text(active_info["guest_name"] or "Huésped", size=13, color=theme.GOLD_LIGHT,
                                weight=ft.FontWeight.W_600),
                    ], spacing=6),
                    ft.Row([
                        ft.Icon(ft.Icons.SCHEDULE, size=14, color=theme.TEXT_MUTED),
                        ft.Text(f"Checkout: {checkout_str}", size=12, color=theme.TEXT_MUTED),
                    ], spacing=6),
                ],
                spacing=4,
                tight=True,
            ),
            bgcolor=theme.SURFACE_ALT,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
        )

    state_btns = ft.Row(
        [
            ft.TextButton(
                content=ft.Text(STATE_LABELS[k][0], size=12),
                on_click=lambda e, k=k: do_state(k),
                style=ft.ButtonStyle(color=STATE_LABELS[k][1]),
            )
            for k in ("limpia", "sucia", "inspeccion", "mantenimiento")
        ],
        wrap=True,
        spacing=4,
    )

    def do_change_room(e=None):
        _page_close(page, dlg)
        _open_change_room_form(page, room, active_info, sf, state, refresh)

    action_row = ft.Row(spacing=8)
    if not occupied and can_checkin:
        action_row.controls.append(PrimaryButton("Check-in", on_click=do_checkin, icon=ft.Icons.LOGIN))
    if occupied and can_checkout:
        action_row.controls.append(
            SecondaryButton("Cambiar hab.", on_click=do_change_room, icon=ft.Icons.SWAP_HORIZ)
        )
        action_row.controls.append(
            SecondaryButton("Checkout", on_click=do_checkout, icon=ft.Icons.LOGOUT, danger=True)
        )
    action_row.controls.append(ft.TextButton(content=ft.Text("Cerrar"), on_click=do_close))

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row(
            [
                ft.Text(f"Hab. {room['display_number']}", size=18,
                        weight=ft.FontWeight.W_700, color=theme.TEXT),
                Badge(label, color=fg),
                Badge("Ocupada", theme.GOLD_LIGHT, theme.GOLD_BG) if occupied
                else Badge("Libre", theme.SUCCESS, theme.SURFACE_ALT),
            ],
            spacing=8,
        ),
        content=ft.Container(
            content=ft.Column(
                [guest_info, ft.Container(height=4), state_btns],
                spacing=10,
                tight=True,
            ),
            width=440,
            padding=ft.Padding(4, 8, 4, 4),
        ),
        actions=[action_row],
        actions_alignment=ft.MainAxisAlignment.END,
        bgcolor=theme.SURFACE,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
    )
    _page_open(page, dlg)


def _open_checkin_form(page, room: dict, sf, state, refresh) -> None:
    max_nights = 30
    checkout_time = state.hotel.checkout_time if state.hotel else "12:00"
    try:
        h, m = [int(x) for x in checkout_time.split(":")]
    except Exception:
        h, m = 12, 0

    def checkout_after(nights: int) -> datetime:
        return (datetime.now() + timedelta(days=nights)).replace(hour=h, minute=m, second=0, microsecond=0)

    # Guest dropdown
    with sf() as s:
        guests = s.query(Guest).order_by(Guest.name).all()
        guest_options = [ft.dropdown.Option("", "— Sin registrar —")] + [
            ft.dropdown.Option(str(g.id), g.name) for g in guests
        ]

    guest_dd = ft.Dropdown(
        label="Huésped",
        options=guest_options,
        value="",
        border_radius=theme.INPUT_RADIUS,
    )

    days_display = ft.Text("1 noche", size=13, color=theme.GOLD_LIGHT,
                           weight=ft.FontWeight.W_600)

    def nights_label(nights: int) -> str:
        return f"{nights} noche{'s' if nights != 1 else ''}"

    def on_checkout_picked(value: datetime) -> None:
        nights = (value.date() - datetime.now().date()).days
        if 1 <= nights <= max_nights:
            days_slider.value = nights
        days_display.value = nights_label(max(nights, 0))

    checkout_field = DateTimeField(page, "Salida", checkout_after(1), on_change=on_checkout_picked)

    def on_days_change(e):
        d = int(float(e.control.value))
        days_display.value = nights_label(d)
        checkout_field.set_value(checkout_after(d))
        page.update()

    days_slider = ft.Slider(
        min=1, max=max_nights, divisions=max_nights - 1, value=1,
        active_color=theme.GOLD, thumb_color=theme.GOLD,
        on_change=on_days_change,
    )

    def do_close(e=None):
        _page_close(page, dlg)

    def _do_checkin() -> None:
        # Snapshot once: the watchdog can clear/replace state.encoder mid-op.
        enc = state.encoder
        if enc is None:
            show_toast(page, "Encoder no conectado", "error")
            return
        checkout = checkout_field.value
        if checkout <= datetime.now():
            show_toast(page, "La salida debe ser en el futuro", "error")
            return
        _page_close(page, dlg)
        guest_id = int(guest_dd.value) if guest_dd.value else None
        guest_label = next((o.text for o in guest_dd.options if o.key == guest_dd.value), "")
        from types import SimpleNamespace
        room_ns = SimpleNamespace(
            id=room["id"],
            display_number=room["display_number"],
            sequential_id=room["sequential_id"],
            building=room["building"],
            floor=room["floor"],
            room_no_id=room.get("room_no_id", 1),
        )
        op_name = state.operator.name if state.operator else "?"

        def write():
            with sf() as s:
                guest = s.get(Guest, guest_id) if guest_id else None
                result = create_guest_card(
                    encoder=enc,
                    hotel=state.hotel,
                    session=s,
                    room=room_ns,
                    guest=guest,
                    valid_from=datetime.now(),
                    valid_until=checkout,
                    operator=op_name,
                )
                if result.ok:
                    nights = (checkout.date() - datetime.now().date()).days
                    log_action(s, "checkin", op_name,
                               f"Hab. {room['display_number']}, {nights}d")
            return result

        run_card_write(
            page,
            title=f"Check-in · Hab. {room['display_number']}",
            op=write,
            details=[("Habitación", f"Hab. {room['display_number']}"),
                     ("Huésped", guest_label if guest_id else "Sin registrar"),
                     ("Salida", format_moment(checkout))],
            next_step=f"Entrega la tarjeta y pruébala en la puerta de la Hab. {room['display_number']}.",
            on_done=lambda r: refresh(),
        )

    def do_confirm(e=None):
        run_busy(_do_checkin)

    emit_btn = PrimaryButton("Grabar tarjeta", on_click=do_confirm, icon=ft.Icons.CREDIT_CARD)
    run_busy = make_busy_guard(page, emit_btn)

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(f"Check-in · Hab. {room['display_number']}", size=18,
                      weight=ft.FontWeight.W_700, color=theme.TEXT),
        content=ft.Container(
            content=ft.Column(
                [
                    guest_dd,
                    ft.Container(height=4),
                    ft.Row([
                        ft.Text("Noches", size=13, color=theme.TEXT_MUTED),
                        days_display,
                    ], spacing=8),
                    days_slider,
                    checkout_field.control,
                ],
                spacing=10,
                tight=True,
            ),
            width=460,
            padding=ft.Padding(4, 8, 4, 4),
        ),
        actions=[
            ft.TextButton(content=ft.Text("Cancelar"), on_click=do_close),
            emit_btn,
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        bgcolor=theme.SURFACE,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
    )
    _page_open(page, dlg)


def _open_change_room_form(page, room: dict, active_info, sf, state, refresh) -> None:
    """Dialogo de cambio de habitacion mid-stay."""
    from setra_cards.services.card_service import change_guest_room

    # Listar habitaciones disponibles (no ocupadas, estado 'limpia')
    with sf() as s:
        rooms_raw = rooms_service.list_rooms(s)
        # Filtrar: no la actual, estado limpia, sin CardLog vigente
        now = datetime.now()
        active_room_ids = set(
            r[0] for r in s.query(CardLog.room_id)
            .filter(
                CardLog.card_type == "Guest",
                CardLog.success.is_(True),
                CardLog.expires_at > now,
            ).distinct().all()
        )
        available = [
            {"id": r.id, "display_number": r.display_number,
             "building": r.building, "floor": r.floor, "state": r.state}
            for r in rooms_raw
            if r.id != room["id"] and r.id not in active_room_ids and r.state == "limpia"
        ]

    if not available:
        show_toast(page, "No hay habitaciones disponibles (limpias y libres)", "error")
        return

    guest_name = active_info["guest_name"] if active_info else "(sin huésped)"
    checkout_str = (
        active_info["expires_at"].strftime("%d/%m/%Y %H:%M")
        if active_info and active_info["expires_at"] else "—"
    )

    target_dd = ft.Dropdown(
        label="Nueva habitación",
        options=[
            ft.dropdown.Option(str(r["id"]),
                               f"Hab. {r['display_number']} (edif {r['building']} piso {r['floor']})")
            for r in available
        ],
        value=str(available[0]["id"]),
        border_radius=theme.INPUT_RADIUS,
    )

    def do_close(e=None):
        _page_close(page, dlg)

    def _do_change_room() -> None:
        # Snapshot once: the watchdog can clear/replace state.encoder mid-op.
        enc = state.encoder
        if enc is None:
            show_toast(page, "Encoder no conectado", "error")
            return
        new_room_id = int(target_dd.value) if target_dd.value else None
        if not new_room_id:
            return
        _page_close(page, dlg)
        op_name = state.operator.name if state.operator else "?"
        target = next((o.text for o in target_dd.options if o.key == target_dd.value), "")

        def write():
            with sf() as s:
                old_r = s.get(Room, room["id"])
                new_r = s.get(Room, new_room_id)
                if not old_r or not new_r:
                    return CardResult(ok=False, card_type="Guest", error="Habitaciones no encontradas")
                return change_guest_room(
                    encoder=enc,
                    hotel=state.hotel,
                    session=s,
                    old_room=old_r,
                    new_room=new_r,
                    operator=op_name,
                )

        run_card_write(
            page,
            title=f"Cambiar hab. {room['display_number']}",
            op=write,
            details=[("De", f"Hab. {room['display_number']}"), ("A", target)],
            working_text="Reescribiendo tarjeta…",
            success_text="Tarjeta reescrita",
            next_step="Pásala por la cerradura de la nueva habitación.",
            on_done=lambda r: refresh(),
        )

    def do_confirm(e=None):
        run_busy(_do_change_room)

    rewrite_btn = PrimaryButton("Reescribir tarjeta", on_click=do_confirm, icon=ft.Icons.SWAP_HORIZ)
    run_busy = make_busy_guard(page, rewrite_btn)

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(f"Cambiar hab. {room['display_number']}", size=18,
                      weight=ft.FontWeight.W_700, color=theme.TEXT),
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Icon(ft.Icons.PERSON, size=14, color=theme.GOLD),
                                ft.Text(guest_name, size=13, color=theme.GOLD_LIGHT,
                                        weight=ft.FontWeight.W_600),
                            ], spacing=6),
                            ft.Row([
                                ft.Icon(ft.Icons.SCHEDULE, size=14, color=theme.TEXT_MUTED),
                                ft.Text(f"Checkout actual: {checkout_str}", size=12,
                                        color=theme.TEXT_MUTED),
                            ], spacing=6),
                        ], spacing=4, tight=True),
                        bgcolor=theme.SURFACE_ALT,
                        border=ft.Border.all(1, theme.BORDER),
                        border_radius=8,
                        padding=ft.Padding(12, 10, 12, 10),
                    ),
                    ft.Container(height=6),
                    target_dd,
                    ft.Container(height=6),
                    ft.Text(
                        "Coloca la tarjeta del huésped en el encoder. Se reescribirá "
                        "conservando huésped y salida. Luego pásala por la cerradura "
                        "de la nueva habitación.",
                        size=11,
                        color=theme.TEXT_MUTED,
                    ),
                ],
                spacing=8,
                tight=True,
            ),
            width=460,
            padding=ft.Padding(4, 8, 4, 4),
        ),
        actions=[
            ft.TextButton(content=ft.Text("Cancelar"), on_click=do_close),
            rewrite_btn,
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        bgcolor=theme.SURFACE,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
    )
    _page_open(page, dlg)


def _open_confirm(page, title: str, message: str, on_confirm) -> None:
    fired = {"v": False}

    def do_ok(e=None):
        # Fire-once: a double-click must not run on_confirm twice.
        if fired["v"]:
            return
        fired["v"] = True
        _page_close(page, dlg)
        on_confirm()

    def do_cancel(e=None):
        _page_close(page, dlg)

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(title, size=16, weight=ft.FontWeight.W_600, color=theme.TEXT),
        content=ft.Text(message, size=13, color=theme.TEXT_MUTED),
        actions=[
            ft.TextButton(content=ft.Text("Cancelar"), on_click=do_cancel),
            SecondaryButton("Confirmar", on_click=do_ok, danger=True),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        bgcolor=theme.SURFACE,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
    )
    _page_open(page, dlg)
