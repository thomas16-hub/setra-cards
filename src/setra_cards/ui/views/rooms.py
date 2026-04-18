"""Vista Habitaciones — grid con estados + CRUD."""
from __future__ import annotations

import flet as ft
from sqlalchemy.exc import IntegrityError

from setra_cards.services import rooms as rooms_service
from setra_cards.services.action_log import log as log_action
from setra_cards.services.auth import role_has_access
from setra_cards.storage.database import init_db
from setra_cards.storage.models import Room
from setra_cards.core.app_state import get_state
from setra_cards.ui import theme
from setra_cards.ui.components import (
    Badge,
    EmptyState,
    PageHeader,
    PrimaryButton,
    SecondaryButton,
    SectionCard,
    StatCard,
    show_toast,
)
from setra_cards.ui.components.basics import confirm_dialog
from setra_cards.ui.components.basics import _page_open, _page_close

STATE_LABELS = {
    "limpia":            ("Limpia",           theme.SUCCESS,   theme.SURFACE_ALT),
    "sucia":             ("Sucia",            theme.WARNING,   theme.SURFACE_ALT),
    "inspeccion":        ("Inspeccion",       theme.ACCENT,    theme.SURFACE_ALT),
    "mantenimiento":     ("Mantenimiento",    "#8E44AD",       theme.SURFACE_ALT),
    "fuera_de_servicio": ("Fuera de servicio",theme.ERROR,     theme.SURFACE_ALT),
}
STATE_ORDER = ["limpia", "sucia", "inspeccion", "mantenimiento", "fuera_de_servicio"]


def build(page: ft.Page) -> ft.Control:
    state = get_state()
    sf = init_db()

    op_role = state.operator.role if state.operator else None
    can_manage = role_has_access(op_role, "manager")

    search_query = {"val": ""}
    state_filter = {"val": "todas"}

    host = ft.Column(spacing=16, expand=True, scroll=ft.ScrollMode.AUTO)

    def refresh() -> None:
        with sf() as s:
            rooms = rooms_service.list_rooms(s)

        if state_filter["val"] != "todas":
            rooms = [r for r in rooms if r.state == state_filter["val"]]
        q = search_query["val"].strip().lower()
        if q:
            rooms = [r for r in rooms if q in r.display_number.lower()]

        host.controls.clear()
        host.controls.append(_summary_bar(rooms_by_state()))
        host.controls.append(_filter_bar(page, state_filter, search_query, refresh))

        if not rooms:
            host.controls.append(
                SectionCard(content=EmptyState(
                    "No hay habitaciones que coincidan",
                    "Cambia el filtro o agrega una habitacion nueva.",
                    icon=ft.Icons.HOTEL_OUTLINED,
                ))
            )
        else:
            host.controls.append(_room_grid(page, rooms, refresh))

        page.update()

    def rooms_by_state() -> dict[str, int]:
        with sf() as s:
            all_rooms = rooms_service.list_rooms(s)
        counts: dict[str, int] = {k: 0 for k in STATE_LABELS}
        for r in all_rooms:
            counts[r.state] = counts.get(r.state, 0) + 1
        counts["__total__"] = len(all_rooms)
        return counts

    def open_new_room(e: ft.ControlEvent | None = None) -> None:
        _open_room_dialog(page, state, None, refresh)

    header_actions: list[ft.Control] = []
    if can_manage:
        header_actions.append(
            PrimaryButton("Nueva habitacion", on_click=open_new_room, icon=ft.Icons.ADD)
        )
    header = PageHeader(
        title="Habitaciones",
        subtitle=f"{state.hotel.name}",
        actions=header_actions,
    )

    root = ft.Container(
        content=ft.Column(
            [header, host],
            spacing=0,
            expand=True,
        ),
        expand=True,
    )

    # render inicial
    refresh()
    return root


# --- Helpers de presentacion ---

def _summary_bar(counts: dict[str, int]) -> ft.Control:
    total = counts.get("__total__", 0)
    return ft.Row(
        [
            StatCard("Total", total, "configuradas", icon=ft.Icons.HOTEL_OUTLINED),
            StatCard("Limpias", counts.get("limpia", 0), "disponibles", color=theme.SUCCESS, icon=ft.Icons.CHECK_CIRCLE),
            StatCard("Sucias", counts.get("sucia", 0), "pendientes limpieza", color=theme.WARNING, icon=ft.Icons.CLEANING_SERVICES),
            StatCard("Mantenimiento", counts.get("mantenimiento", 0) + counts.get("fuera_de_servicio", 0), "fuera de operacion", color=theme.ERROR, icon=ft.Icons.BUILD),
        ],
        spacing=16,
    )


def _filter_bar(page: ft.Page, state_filter: dict, search_query: dict, on_change) -> ft.Control:
    search = ft.TextField(
        hint_text="Buscar habitacion por numero...",
        prefix_icon=ft.Icons.SEARCH,
        border_radius=theme.INPUT_RADIUS,
        height=42,
        expand=True,
        content_padding=ft.Padding(12, 8, 12, 8),
        bgcolor=theme.SURFACE,
        border_color=theme.BORDER,
    )

    def on_search(e: ft.ControlEvent) -> None:
        search_query["val"] = e.control.value or ""
        on_change()

    search.on_change = on_search

    def make_chip(value: str, label: str) -> ft.Container:
        is_active = state_filter["val"] == value

        def on_click(e: ft.ControlEvent) -> None:
            state_filter["val"] = value
            on_change()

        return ft.Container(
            content=ft.Text(label, size=12, weight=ft.FontWeight.W_600,
                            color=theme.TEXT_INVERSE if is_active else theme.TEXT),
            padding=ft.Padding(14, 8, 14, 8),
            bgcolor=theme.PRIMARY if is_active else theme.SURFACE,
            border=ft.Border.all(1, theme.PRIMARY if is_active else theme.BORDER),
            border_radius=999,
            on_click=on_click,
            ink=True,
        )

    chips = [make_chip("todas", "Todas")] + [
        make_chip(k, STATE_LABELS[k][0]) for k in STATE_ORDER
    ]
    return ft.Row(
        [
            search,
            ft.Row(chips, spacing=6, scroll=ft.ScrollMode.AUTO),
        ],
        spacing=12,
    )


def _room_grid(page: ft.Page, rooms: list[Room], on_change) -> ft.Control:
    cards = [_room_card(page, r, on_change) for r in rooms]
    # GridView de Flet — 5 cols en ancho tipico
    return ft.GridView(
        controls=cards,
        runs_count=5,
        max_extent=220,
        spacing=12,
        run_spacing=12,
        child_aspect_ratio=1.1,
        expand=1,
    )


def _room_card(page: ft.Page, room: Room, on_change) -> ft.Container:
    label, fg, bg = STATE_LABELS.get(room.state, ("Otro", theme.TEXT_MUTED, "#F0F0F0"))

    def open_edit(e: ft.ControlEvent) -> None:
        _open_room_dialog(page, get_state(), room, on_change)

    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text(f"Hab. {room.display_number}",
                                        size=18, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                                ft.Text(f"Edif. {room.building} · Piso {room.floor}",
                                        size=11, color=theme.TEXT_MUTED),
                            ],
                            spacing=2,
                            tight=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.MORE_VERT,
                            icon_size=18,
                            icon_color=theme.TEXT_MUTED,
                            on_click=open_edit,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(expand=True),
                ft.Row([
                    Badge(label, color=fg, bg=bg),
                    ft.Text(f"#{room.sequential_id}", size=10, color=theme.TEXT_LIGHT),
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ],
            spacing=8,
            expand=True,
        ),
        padding=ft.Padding(16, 14, 10, 14),
        bgcolor=theme.SURFACE,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=theme.CARD_RADIUS,
        on_click=open_edit,
        ink=True,
    )


# --- Modal CRUD ---

def _num_stepper(label: str, initial: int, min_val: int = 1, max_val: int = 255) -> tuple[ft.Container, dict]:
    """Control numérico con botones - y + para valores enteros."""
    val = {"v": initial}
    display_text = ft.Text(str(initial), size=18, weight=ft.FontWeight.W_700,
                           color=theme.TEXT, text_align=ft.TextAlign.CENTER, width=40)

    def decrement(e=None):
        if val["v"] > min_val:
            val["v"] -= 1
            display_text.value = str(val["v"])
            display_text.update()

    def increment(e=None):
        if val["v"] < max_val:
            val["v"] += 1
            display_text.value = str(val["v"])
            display_text.update()

    btn_style = ft.ButtonStyle(
        shape=ft.CircleBorder(),
        padding=ft.Padding(0, 0, 0, 0),
        bgcolor=theme.SURFACE_ALT,
    )
    ctrl = ft.Container(
        content=ft.Column([
            ft.Text(label, size=11, color=theme.TEXT_MUTED, weight=ft.FontWeight.W_600),
            ft.Container(height=4),
            ft.Row([
                ft.IconButton(ft.Icons.REMOVE, icon_size=18, icon_color=theme.TEXT_MUTED,
                              on_click=decrement, style=btn_style),
                display_text,
                ft.IconButton(ft.Icons.ADD, icon_size=18, icon_color=theme.GOLD,
                              on_click=increment, style=btn_style),
            ], spacing=4, alignment=ft.MainAxisAlignment.CENTER, tight=True),
        ], spacing=0, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding(12, 10, 12, 10),
        bgcolor=theme.SURFACE_ALT,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=theme.INPUT_RADIUS,
        expand=True,
    )
    return ctrl, val


def _open_room_dialog(page: ft.Page, state, room: Room | None, on_done) -> None:
    sf = init_db()
    is_new = room is None
    op_role = state.operator.role if state.operator else None
    can_manage = role_has_access(op_role, "manager")
    readonly = not can_manage

    if is_new and readonly:
        show_toast(page, "Tu rol no permite crear habitaciones", "error")
        return

    display = ft.TextField(
        label="Numero de habitacion",
        value=room.display_number if room else "",
        autofocus=is_new,
        border_radius=theme.INPUT_RADIUS,
        disabled=readonly,
    )
    building_ctrl, building_val = _num_stepper("Edificio", room.building if room else 1)
    floor_ctrl, floor_val = _num_stepper("Piso", room.floor if room else 1)
    if readonly:
        building_ctrl.disabled = True
        floor_ctrl.disabled = True
    state_dd = ft.Dropdown(
        label="Estado",
        value=room.state if room else "limpia",
        options=[ft.dropdown.Option(k, STATE_LABELS[k][0]) for k in STATE_ORDER],
        border_radius=theme.INPUT_RADIUS,
        disabled=readonly,
    )
    notes = ft.TextField(
        label="Notas (opcional)",
        value=(room.notes or "") if room else "",
        multiline=True,
        min_lines=2,
        max_lines=4,
        border_radius=theme.INPUT_RADIUS,
        disabled=readonly,
    )

    def on_close(e: ft.ControlEvent | None = None) -> None:
        _page_close(page, dlg)

    def on_save(e: ft.ControlEvent) -> None:
        b = building_val["v"]
        f = floor_val["v"]
        with sf() as s:
            try:
                if is_new:
                    r = rooms_service.create_room(
                        s,
                        display_number=display.value or "",
                        building=b,
                        floor=f,
                        state=state_dd.value or "limpia",
                        notes=notes.value or None,
                    )
                    log_action(s, "room_create", state.operator.name if state.operator else "?",
                               f"Hab. {r.display_number}")
                    show_toast(page, f"Habitacion {r.display_number} creada", "success")
                else:
                    rooms_service.update_room(
                        s,
                        room.id,
                        display_number=display.value or "",
                        building=b,
                        floor=f,
                        state=state_dd.value,
                        notes=notes.value,
                    )
                    log_action(s, "room_update", state.operator.name if state.operator else "?",
                               f"Hab. {display.value}")
                    show_toast(page, "Habitacion actualizada", "success")
            except ValueError as exc:
                show_toast(page, str(exc), "error")
                return
            except IntegrityError as exc:
                s.rollback()
                show_toast(page, f"Conflicto al guardar: {exc.orig}", "error")
                return
        _page_close(page, dlg)
        on_done()

    def on_delete(e: ft.ControlEvent) -> None:
        if is_new or not room:
            return

        def do_delete() -> None:
            with sf() as s:
                rooms_service.delete_room(s, room.id)
                log_action(s, "room_delete", state.operator.name if state.operator else "?",
                           f"Hab. {room.display_number}")
            show_toast(page, f"Habitacion {room.display_number} eliminada", "info")
            _page_close(page, dlg)
            on_done()

        confirm_dialog(
            page,
            f"Eliminar habitacion {room.display_number}?",
            "Esta accion no se puede deshacer. El historico de tarjetas se conserva.",
            on_confirm=do_delete,
            confirm_label="Eliminar",
            danger=True,
        )

    actions: list[ft.Control] = [ft.TextButton(content=ft.Text("Cerrar" if readonly else "Cancelar"), on_click=on_close)]
    if not is_new and can_manage:
        actions.append(
            ft.TextButton(
                content=ft.Text("Eliminar", color=theme.ERROR),
                on_click=on_delete,
            )
        )
    if can_manage:
        actions.append(PrimaryButton("Guardar" if not is_new else "Crear", on_click=on_save))

    form_fields: list[ft.Control] = [
        display,
        ft.Row([building_ctrl, floor_ctrl], spacing=12),
        notes,
    ]
    if not is_new:
        form_fields.insert(0, ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.KEY_OUTLINED, size=14, color=theme.GOLD),
                ft.Text(
                    f"ID encoder: {room.sequential_id}",
                    size=13,
                    color=theme.GOLD_LIGHT,
                    weight=ft.FontWeight.W_600,
                ),
            ], spacing=6),
            bgcolor=theme.SURFACE_ALT,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=8,
            padding=ft.Padding(12, 8, 12, 8),
        ))
        form_fields.insert(3, state_dd)

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(
            "Editar habitacion" if not is_new else "Nueva habitacion",
            size=18, weight=ft.FontWeight.W_600, color=theme.TEXT,
        ),
        content=ft.Container(
            content=ft.Column(form_fields, spacing=12, tight=True),
            width=460,
            padding=ft.Padding(4, 8, 4, 4),
        ),
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
        bgcolor=theme.SURFACE,
        title_text_style=ft.TextStyle(color=theme.TEXT),
    )
    _page_open(page, dlg)
