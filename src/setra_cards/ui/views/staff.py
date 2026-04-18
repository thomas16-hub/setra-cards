"""Vista Personal — lista + CRUD + asignaciones."""
from __future__ import annotations

import flet as ft

from setra_cards.core.app_state import get_state
from setra_cards.services import staff as staff_service
from setra_cards.services import rooms as rooms_service
from setra_cards.services.action_log import log as log_action
from setra_cards.storage.database import init_db
from setra_cards.storage.models import Staff
from setra_cards.ui import theme
from setra_cards.ui.components import (
    Badge,
    EmptyState,
    PageHeader,
    PrimaryButton,
    SectionCard,
    show_toast,
)
from setra_cards.ui.components.basics import confirm_dialog

ROLE_COLORS = {
    "recepcion": (theme.ACCENT, "#E8F2FF"),
    "limpieza": (theme.SUCCESS, "#E8F7EC"),
    "mantenimiento": (theme.WARNING, "#FFF2D6"),
    "admin": ("#8E44AD", "#F2E8F8"),
}


def build(page: ft.Page) -> ft.Control:
    state = get_state()
    sf = init_db()
    host = ft.Container(expand=True)

    def refresh() -> None:
        with sf() as s:
            items = staff_service.list_staff(s)
        if not items:
            host.content = SectionCard(content=EmptyState(
                "Sin personal registrado",
                "Agrega empleados (recepcion, limpieza, mantenimiento) para emitir sus tarjetas.",
                icon=ft.Icons.BADGE_OUTLINED,
                action=PrimaryButton("Agregar primer empleado", icon=ft.Icons.PERSON_ADD,
                                     on_click=lambda e: _open_staff_dialog(page, state, None, refresh)),
            ))
        else:
            host.content = _staff_grid(page, items, refresh)
        page.update()

    header = PageHeader(
        title="Personal",
        subtitle="Empleados del hotel y sus tarjetas",
        actions=[
            PrimaryButton(
                "Nuevo empleado",
                on_click=lambda e: _open_staff_dialog(page, state, None, refresh),
                icon=ft.Icons.PERSON_ADD,
            ),
        ],
    )

    refresh()
    return ft.Container(
        content=ft.Column([header, host], spacing=0, expand=True, scroll=ft.ScrollMode.AUTO),
        expand=True,
    )


def _staff_grid(page: ft.Page, items: list[Staff], on_change) -> ft.Control:
    cards = [_staff_card(page, s, on_change) for s in items]
    return ft.GridView(
        controls=cards,
        runs_count=3,
        max_extent=360,
        spacing=14,
        run_spacing=14,
        child_aspect_ratio=1.8,
        expand=1,
    )


def _staff_card(page: ft.Page, s: Staff, on_change) -> ft.Container:
    fg, bg = ROLE_COLORS.get(s.role, (theme.TEXT_MUTED, "#F0F0F0"))
    rooms = staff_service.assigned_room_list(s)
    rooms_label = f"{len(rooms)} habitaciones asignadas" if rooms else "Sin asignaciones"

    def on_edit(e: ft.ControlEvent) -> None:
        _open_staff_dialog(page, get_state(), s, on_change)

    status_chip = (
        Badge("Activo", theme.SUCCESS, "#E8F7EC")
        if s.active
        else Badge("Inactivo", theme.TEXT_MUTED, "#F0F0F0")
    )

    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.CircleAvatar(
                            content=ft.Text(
                                s.name[0].upper() if s.name else "?",
                                size=16, weight=ft.FontWeight.BOLD, color=theme.SURFACE,
                            ),
                            bgcolor=theme.PRIMARY,
                            radius=20,
                        ),
                        ft.Column(
                            [
                                ft.Text(s.name, size=14, weight=ft.FontWeight.W_600, color=theme.TEXT),
                                ft.Row([
                                    Badge(s.role.capitalize(), fg, bg),
                                    status_chip,
                                ], spacing=6),
                            ],
                            spacing=4,
                            tight=True,
                            expand=True,
                        ),
                        ft.IconButton(
                            icon=ft.Icons.EDIT_OUTLINED,
                            icon_size=18,
                            icon_color=theme.TEXT_MUTED,
                            on_click=on_edit,
                        ),
                    ],
                    spacing=12,
                ),
                ft.Container(height=8),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.PHONE_OUTLINED, size=14, color=theme.TEXT_LIGHT),
                        ft.Text(s.phone or "Sin telefono", size=12, color=theme.TEXT_MUTED),
                    ],
                    spacing=6,
                ),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.HOTEL_OUTLINED, size=14, color=theme.TEXT_LIGHT),
                        ft.Text(rooms_label, size=12, color=theme.TEXT_MUTED),
                    ],
                    spacing=6,
                ),
            ],
            spacing=4,
            tight=True,
        ),
        padding=ft.Padding(16, 14, 16, 14),
        bgcolor=theme.SURFACE,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=theme.CARD_RADIUS,
        on_click=on_edit,
        ink=True,
    )


def _open_staff_dialog(page: ft.Page, state, staff: Staff | None, on_done) -> None:
    sf = init_db()
    is_new = staff is None

    name = ft.TextField(label="Nombre completo", value=staff.name if staff else "",
                        autofocus=is_new, border_radius=theme.INPUT_RADIUS)
    role = ft.Dropdown(
        label="Rol",
        value=staff.role if staff else "recepcion",
        options=[
            ft.dropdown.Option("recepcion", "Recepcion"),
            ft.dropdown.Option("limpieza", "Limpieza"),
            ft.dropdown.Option("mantenimiento", "Mantenimiento"),
            ft.dropdown.Option("admin", "Admin"),
        ],
        border_radius=theme.INPUT_RADIUS,
    )
    doc = ft.TextField(label="Documento", value=(staff.document or "") if staff else "",
                       border_radius=theme.INPUT_RADIUS)
    phone = ft.TextField(label="Telefono", value=(staff.phone or "") if staff else "",
                         border_radius=theme.INPUT_RADIUS)
    active = ft.Switch(label="Activo", value=(staff.active if staff else True), active_color=theme.PRIMARY)

    # Asignacion de habitaciones (checkbox multi)
    with sf() as s:
        all_rooms = rooms_service.list_rooms(s)
    assigned = set(staff_service.assigned_room_list(staff)) if staff else set()

    room_checks = [
        ft.Checkbox(
            label=f"Hab. {r.display_number}",
            value=r.display_number in assigned,
            active_color=theme.PRIMARY,
        )
        for r in all_rooms
    ]
    rooms_col = ft.Column(room_checks, spacing=2, scroll=ft.ScrollMode.AUTO, height=180) if room_checks else \
        ft.Text("No hay habitaciones para asignar", size=12, color=theme.TEXT_MUTED)

    def collect_assigned() -> list[str]:
        return [r.display_number for r, chk in zip(all_rooms, room_checks) if chk.value]

    def on_close(e: ft.ControlEvent | None = None) -> None:
        page.close(dlg)

    def on_save(e: ft.ControlEvent) -> None:
        with sf() as s:
            try:
                if is_new:
                    new_staff = staff_service.create_staff(
                        s,
                        name=name.value or "",
                        role=role.value or "recepcion",
                        document=doc.value,
                        phone=phone.value,
                        assigned_rooms=collect_assigned(),
                    )
                    log_action(s, "staff_create", state.operator.name if state.operator else "?", new_staff.name)
                    show_toast(page, f"Empleado '{new_staff.name}' agregado", "success")
                else:
                    staff_service.update_staff(
                        s, staff.id,
                        name=name.value, role=role.value,
                        document=doc.value, phone=phone.value,
                        assigned_rooms=collect_assigned(),
                        active=active.value,
                    )
                    log_action(s, "staff_update", state.operator.name if state.operator else "?", staff.name)
                    show_toast(page, "Empleado actualizado", "success")
            except ValueError as exc:
                show_toast(page, str(exc), "error")
                return
        page.close(dlg)
        on_done()

    def on_delete(e: ft.ControlEvent) -> None:
        if is_new or not staff:
            return

        def do_delete() -> None:
            with sf() as s:
                staff_service.delete_staff(s, staff.id)
                log_action(s, "staff_delete", state.operator.name if state.operator else "?", staff.name)
            show_toast(page, f"Empleado '{staff.name}' eliminado", "info")
            page.close(dlg)
            on_done()

        confirm_dialog(
            page, f"Eliminar a {staff.name}?",
            "Se eliminara el empleado. Las tarjetas emitidas quedan registradas.",
            on_confirm=do_delete, confirm_label="Eliminar", danger=True,
        )

    actions: list[ft.Control] = [ft.TextButton(content=ft.Text("Cancelar"), on_click=on_close)]
    if not is_new:
        actions.append(ft.TextButton(content=ft.Text("Eliminar", color=theme.ERROR), on_click=on_delete))
    actions.append(PrimaryButton("Guardar" if not is_new else "Crear", on_click=on_save))

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Editar empleado" if not is_new else "Nuevo empleado",
                      size=18, weight=ft.FontWeight.W_600, color=theme.TEXT),
        content=ft.Container(
            content=ft.Column(
                [
                    name,
                    ft.Row([role, active], spacing=12),
                    ft.Row([doc, phone], spacing=12),
                    ft.Container(height=4),
                    ft.Text("Habitaciones asignadas", size=12, color=theme.TEXT_MUTED, weight=ft.FontWeight.W_600),
                    ft.Container(
                        content=rooms_col,
                        padding=ft.Padding(10, 8, 10, 8),
                        bgcolor=theme.SURFACE_ALT,
                        border=ft.Border.all(1, theme.BORDER),
                        border_radius=10,
                    ),
                ],
                spacing=10,
                tight=True,
            ),
            width=480,
        ),
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
    )
    page.open(dlg)
