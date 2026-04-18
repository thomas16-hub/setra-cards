"""Hub operativo — grid de habitaciones + check-in/checkout rapido."""
from __future__ import annotations

from datetime import datetime, timedelta

import flet as ft

from setra_cards.core.app_state import get_state
from setra_cards.services import rooms as rooms_service
from setra_cards.services.action_log import log as log_action
from setra_cards.services.auth import role_has_access
from setra_cards.services.card_service import (
    blank_existing_card,
    create_guest_card,
)
from setra_cards.storage.database import init_db
from setra_cards.storage.models import ActionLog, CardLog, Guest, Room
from setra_cards.ui import theme
from setra_cards.ui.components.basics import (
    Badge,
    PrimaryButton,
    SecondaryButton,
    _page_close,
    _page_open,
    show_toast,
)

STATE_LABELS = {
    "limpia":            ("Limpia",          theme.SUCCESS,    theme.SURFACE_ALT),
    "sucia":             ("Sucia",           theme.WARNING,    theme.SURFACE_ALT),
    "inspeccion":        ("Inspeccion",      theme.INFO,       theme.SURFACE_ALT),
    "mantenimiento":     ("Mantenimiento",   "#8E44AD",        theme.SURFACE_ALT),
    "fuera_de_servicio": ("Fuera servicio",  theme.ERROR,      theme.SURFACE_ALT),
}


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
                CardLog.success == True,
                CardLog.expires_at > now,
            ).all()

            # Cargar nombres de huéspedes en la misma sesión — evita DetachedInstanceError
            guest_ids = {cl.guest_id for cl in active_log_rows if cl.guest_id}
            guest_names: dict[int, str] = {}
            if guest_ids:
                guests = s.query(Guest).filter(Guest.id.in_(guest_ids)).all()
                guest_names = {g.id: g.name for g in guests}

            # Extraer dicts planos para operar fuera de la sesión
            active_by_room: dict[str, dict] = {}
            for cl in active_log_rows:
                if cl.room_display and cl.room_display not in active_by_room:
                    active_by_room[cl.room_display] = {
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
                    "state": r.state,
                }
                for r in rooms
            ]

        occupied_count = sum(1 for rd in rooms_data if rd["display_number"] in active_by_room)
        free = rooms_count - occupied_count
        active_cards_count = len(active_by_room)

        encoder_ok = state.encoder is not None
        encoder_port = state.encoder_port or "—"

        # ── Barra de estado encoder ──
        enc_bar = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(
                        ft.Icons.USB if encoder_ok else ft.Icons.USB_OFF,
                        size=16,
                        color=theme.SUCCESS if encoder_ok else theme.WARNING,
                    ),
                    ft.Text(
                        f"Encoder: {encoder_port}" if encoder_ok else "Encoder no conectado — ve a Administracion",
                        size=12,
                        color=theme.SUCCESS if encoder_ok else theme.WARNING,
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=8,
            ),
            padding=ft.Padding(14, 10, 14, 10),
            bgcolor=theme.SURFACE,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=10,
        )

        # ── Stats row ──
        def stat(label: str, val: str, color: str = theme.TEXT) -> ft.Container:
            return ft.Container(
                content=ft.Column(
                    [
                        ft.Text(val, size=26, weight=ft.FontWeight.BOLD, color=color),
                        ft.Text(label, size=11, color=theme.TEXT_MUTED),
                    ],
                    spacing=2,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding(20, 16, 20, 16),
                bgcolor=theme.SURFACE,
                border=ft.Border.all(1, theme.BORDER),
                border_radius=theme.CARD_RADIUS,
                expand=True,
            )

        stats_row = ft.Row(
            [
                stat("Habitaciones", str(rooms_count)),
                stat("Ocupadas", str(occupied_count), theme.ERROR if occupied_count else theme.TEXT),
                stat("Libres", str(free), theme.SUCCESS),
                stat("Tarjetas activas", str(active_cards_count), theme.GOLD),
                stat("Huespedes", str(guests_count)),
            ],
            spacing=12,
        )

        # ── Grid de habitaciones ──
        grid = ft.GridView(
            controls=[
                _room_tile(page, rd, active_by_room.get(rd["display_number"]), sf, state, refresh)
                for rd in rooms_data
            ],
            runs_count=6,
            max_extent=190,
            spacing=10,
            run_spacing=10,
            child_aspect_ratio=1.0,
        )

        if not rooms_data:
            grid = ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.HOTEL_OUTLINED, size=40, color=theme.TEXT_LIGHT),
                        ft.Text("Sin habitaciones configuradas", size=14, color=theme.TEXT_MUTED),
                        ft.Text("Ve a Habitaciones para agregar", size=12, color=theme.TEXT_LIGHT),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=8,
                ),
                alignment=ft.Alignment.CENTER,
                padding=ft.Padding(0, 48, 0, 48),
            )

        host.controls = [
            ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text("Hub Operativo", size=26, weight=ft.FontWeight.BOLD,
                                    color=theme.GOLD_LIGHT, font_family=theme.FONT_DISPLAY),
                            ft.Text(state.hotel.name, size=12, color=theme.TEXT_MUTED),
                        ],
                        spacing=2,
                        tight=True,
                        expand=True,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.REFRESH,
                        icon_color=theme.TEXT_MUTED,
                        icon_size=20,
                        tooltip="Actualizar",
                        on_click=lambda e: refresh(),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Container(height=12),
            enc_bar,
            ft.Container(height=12),
            stats_row,
            ft.Container(height=16),
            ft.Text("HABITACIONES", size=10, weight=ft.FontWeight.W_700, color=theme.TEXT_LIGHT),
            ft.Container(height=8),
            grid,
        ]
        page.update()

    refresh()
    return ft.Container(content=host, expand=True, padding=theme.PADDING_LG)


def _room_tile(page, room: dict, active_info: "dict | None", sf, state, refresh) -> ft.Container:
    """room y active_info son dicts planos — sin ORM detached."""
    room_state = room["state"]
    label, fg, _ = STATE_LABELS.get(room_state, ("Otro", theme.TEXT_MUTED, theme.SURFACE_ALT))

    occupied = active_info is not None
    guest_name = active_info["guest_name"] if active_info else ""
    checkout_str = (
        active_info["expires_at"].strftime("%d/%m %H:%M")
        if active_info and active_info["expires_at"] else ""
    )

    STATE_TILE = {
        "limpia":            ("#0D2B1A", "#1A7A4A", theme.SUCCESS),
        "sucia":             ("#2B1F0A", "#7A5A1A", theme.WARNING),
        "inspeccion":        ("#0A1F2B", "#1A5A7A", theme.INFO),
        "mantenimiento":     ("#1F0A2B", "#5A1A7A", "#8E44AD"),
        "fuera_de_servicio": ("#2B0A0A", "#7A1A1A", theme.ERROR),
    }
    tile_bg, tile_border, state_color = STATE_TILE.get(room_state, (theme.SURFACE, theme.BORDER, theme.TEXT_MUTED))

    if occupied:
        tile_bg = "#1A1400"
        tile_border = theme.GOLD
        dot_color = theme.GOLD
    else:
        dot_color = state_color

    def on_click(e: ft.ControlEvent) -> None:
        _open_room_action(page, room, active_info, sf, state, refresh)

    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(room["display_number"], size=20, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                        ft.Container(width=10, height=10, bgcolor=dot_color, border_radius=5),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Row(
                    [
                        ft.Text(f"Piso {room['floor']}", size=10, color=theme.TEXT_LIGHT),
                        ft.Text(f"#{room['sequential_id']}", size=10, color=theme.TEXT_LIGHT),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(expand=True),
                ft.Text(guest_name, size=11, color=theme.GOLD_LIGHT, weight=ft.FontWeight.W_600,
                        overflow=ft.TextOverflow.ELLIPSIS) if guest_name else ft.Container(),
                ft.Text(f"⏱ {checkout_str}", size=10, color=theme.TEXT_MUTED) if checkout_str else ft.Container(),
                Badge(label, color=state_color, bg=tile_bg),
            ],
            spacing=4,
            expand=True,
        ),
        padding=ft.Padding(12, 10, 12, 10),
        bgcolor=tile_bg,
        border=ft.Border.all(1, tile_border),
        border_radius=theme.CARD_RADIUS,
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

    def do_checkout(e=None):
        has_encoder = state.encoder is not None
        if has_encoder:
            # Checkout físico: dos pasos — primero borrar tarjeta, luego confirmar DB
            def confirm_physical():
                _page_close(page, dlg)
                from sqlalchemy import update as sql_update
                op = state.operator.name if state.operator else "?"
                with sf() as s:
                    result = blank_existing_card(
                        encoder=state.encoder,
                        hotel=state.hotel,
                        session=s,
                        operator=op,
                    )
                    if not result.ok:
                        # Tarjeta no borrada — no continuar con checkout lógico
                        show_toast(
                            page,
                            f"No se pudo borrar la tarjeta física: {result.error or 'error encoder'}. "
                            "Coloca la tarjeta en el encoder e intenta de nuevo. "
                            "Si la tarjeta ya no existe, usa Checkout sin encoder.",
                            "error",
                        )
                        return
                    # Tarjeta borrada OK → expirar CardLog + cambiar estado + audit (atómico)
                    try:
                        now = datetime.now()
                        s.execute(
                            sql_update(CardLog)
                            .where(
                                CardLog.room_display == room["display_number"],
                                CardLog.success == True,
                                CardLog.expires_at > now,
                            )
                            .values(expires_at=now)
                        )
                        r = s.get(Room, room["id"])
                        if r is None:
                            raise ValueError("Habitacion no existe")
                        r.state = "sucia"
                        s.add(ActionLog(
                            action="checkout",
                            operator=op,
                            detail=f"Hab. {room['display_number']}",
                        ))
                        s.commit()
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
                from sqlalchemy import update as sql_update
                op = state.operator.name if state.operator else "?"
                with sf() as s:
                    try:
                        now = datetime.now()
                        s.execute(
                            sql_update(CardLog)
                            .where(
                                CardLog.room_display == room["display_number"],
                                CardLog.success == True,
                                CardLog.expires_at > now,
                            )
                            .values(expires_at=now)
                        )
                        r = s.get(Room, room["id"])
                        if r is None:
                            raise ValueError("Habitacion no existe")
                        r.state = "sucia"
                        s.add(ActionLog(
                            action="checkout_logico",
                            operator=op,
                            detail=f"Hab. {room['display_number']}",
                        ))
                        s.commit()
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

    action_row = ft.Row(spacing=8)
    if not occupied and can_checkin:
        action_row.controls.append(PrimaryButton("Check-in", on_click=do_checkin, icon=ft.Icons.LOGIN))
    if occupied and can_checkout:
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
    days_val = {"v": 1}

    checkout_time = state.hotel.checkout_time if state.hotel else "12:00"
    try:
        h, m = [int(x) for x in checkout_time.split(":")]
    except Exception:
        h, m = 12, 0

    # Guest dropdown
    with sf() as s:
        guests = s.query(Guest).order_by(Guest.name).all()
        guest_options = [ft.dropdown.Option("", "— Sin registrar —")] + [
            ft.dropdown.Option(str(g.id), g.name) for g in guests
        ]

    guest_dd = ft.Dropdown(
        label="Huesped",
        options=guest_options,
        value="",
        border_radius=theme.INPUT_RADIUS,
    )

    days_display = ft.Text(f"1 noche", size=13, color=theme.GOLD_LIGHT,
                           weight=ft.FontWeight.W_600)
    checkout_display = ft.Text(
        (datetime.now() + timedelta(days=1)).replace(hour=h, minute=m).strftime("%d/%m/%Y %H:%M"),
        size=12, color=theme.TEXT_MUTED,
    )

    def on_days_change(e):
        d = int(float(e.control.value))
        days_val["v"] = d
        days_display.value = f"{d} noche{'s' if d > 1 else ''}"
        co = datetime.now() + timedelta(days=d)
        co = co.replace(hour=h, minute=m, second=0)
        checkout_display.value = co.strftime("%d/%m/%Y %H:%M")
        page.update()

    days_slider = ft.Slider(
        min=1, max=30, divisions=29, value=1,
        active_color=theme.GOLD, thumb_color=theme.GOLD,
        on_change=on_days_change,
    )

    def do_close(e=None):
        _page_close(page, dlg)

    def do_confirm(e=None):
        if not state.encoder:
            show_toast(page, "Encoder no conectado", "error")
            return
        _page_close(page, dlg)
        d = days_val["v"]
        guest_id = int(guest_dd.value) if guest_dd.value else None
        from types import SimpleNamespace
        room_ns = SimpleNamespace(
            id=room["id"],
            display_number=room["display_number"],
            sequential_id=room["sequential_id"],
            building=room["building"],
            floor=room["floor"],
        )
        op_name = state.operator.name if state.operator else "?"
        with sf() as s:
            guest = s.get(Guest, guest_id) if guest_id else None
            result = create_guest_card(
                encoder=state.encoder,
                hotel=state.hotel,
                session=s,
                room=room_ns,
                guest=guest,
                valid_from=datetime.now(),
                valid_until=datetime.now().replace(hour=h, minute=m) + timedelta(days=d),
                operator=op_name,
            )
            if result.ok:
                log_action(s, "checkin", op_name,
                           f"Hab. {room['display_number']}, {d}d")
        if result.ok:
            show_toast(page, f"Check-in hab. {room['display_number']} OK — UID {result.uid_hex}", "success")
        else:
            show_toast(page, result.error or "Error al emitir tarjeta", "error")
        refresh()

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(f"Check-in — Hab. {room['display_number']}", size=18,
                      weight=ft.FontWeight.W_700, color=theme.TEXT),
        content=ft.Container(
            content=ft.Column(
                [
                    guest_dd,
                    ft.Container(height=4),
                    ft.Row([
                        ft.Text("Noches:", size=13, color=theme.TEXT_MUTED),
                        days_display,
                    ], spacing=8),
                    days_slider,
                    ft.Row([
                        ft.Icon(ft.Icons.SCHEDULE, size=14, color=theme.TEXT_MUTED),
                        ft.Text("Checkout: ", size=12, color=theme.TEXT_MUTED),
                        checkout_display,
                    ], spacing=4),
                ],
                spacing=10,
                tight=True,
            ),
            width=460,
            padding=ft.Padding(4, 8, 4, 4),
        ),
        actions=[
            ft.TextButton(content=ft.Text("Cancelar"), on_click=do_close),
            PrimaryButton("Emitir tarjeta", on_click=do_confirm, icon=ft.Icons.CREDIT_CARD),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
        bgcolor=theme.SURFACE,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
    )
    _page_open(page, dlg)


def _open_confirm(page, title: str, message: str, on_confirm) -> None:
    def do_ok(e=None):
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
