"""Vista Huespedes — tabla con buscador + CRUD."""
from __future__ import annotations

import flet as ft

from setra_cards.core.app_state import get_state
from setra_cards.services import guests as guests_service
from setra_cards.services.action_log import log as log_action
from setra_cards.storage.database import init_db
from setra_cards.storage.models import Guest
from setra_cards.ui import theme
from setra_cards.ui.components import (
    EmptyState,
    PageHeader,
    PrimaryButton,
    SectionCard,
    show_toast,
)
from setra_cards.ui.components.basics import confirm_dialog
from setra_cards.ui.components.basics import _page_open, _page_close


def build(page: ft.Page) -> ft.Control:
    state = get_state()
    sf = init_db()
    query = {"val": ""}
    table_host = ft.Container(expand=True)

    def refresh() -> None:
        with sf() as s:
            items = guests_service.search_guests(s, query["val"])
        table_host.content = (
            _guest_table(page, items, refresh)
            if items
            else EmptyState(
                "Sin huespedes registrados" if not query["val"] else "Sin resultados",
                "Crea un huesped nuevo o busca por nombre, documento o telefono." if not query["val"]
                else "Prueba con otros criterios de busqueda.",
                icon=ft.Icons.PEOPLE_OUTLINE,
            )
        )
        page.update()

    def on_new(e: ft.ControlEvent) -> None:
        _open_guest_dialog(page, state, None, refresh)

    search = ft.TextField(
        hint_text="Buscar por nombre, documento o telefono...",
        prefix_icon=ft.Icons.SEARCH,
        border_radius=theme.INPUT_RADIUS,
        height=44,
        content_padding=ft.Padding(12, 10, 12, 10),
        bgcolor=theme.SURFACE,
        border_color=theme.BORDER,
        expand=True,
        on_change=lambda e: (query.update({"val": e.control.value or ""}), refresh()),
    )

    header = PageHeader(
        title="Huespedes",
        subtitle=f"{state.hotel.name}",
        actions=[PrimaryButton("Nuevo huesped", on_click=on_new, icon=ft.Icons.PERSON_ADD)],
    )

    refresh()

    return ft.Container(
        content=ft.Column(
            [
                header,
                ft.Container(content=search, padding=ft.Padding(0, 0, 0, 14)),
                table_host,
            ],
            spacing=0,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
    )


def _guest_table(page: ft.Page, guests: list[Guest], on_change) -> ft.Control:
    def row(g: Guest) -> ft.DataRow:
        def on_edit(e: ft.ControlEvent) -> None:
            _open_guest_dialog(page, get_state(), g, on_change)

        return ft.DataRow(
            cells=[
                ft.DataCell(ft.Text(g.name, size=13, weight=ft.FontWeight.W_500, color=theme.TEXT)),
                ft.DataCell(ft.Text(g.document or "—", size=13, color=theme.TEXT_MUTED)),
                ft.DataCell(ft.Text(g.phone or "—", size=13, color=theme.TEXT_MUTED)),
                ft.DataCell(ft.Text(g.email or "—", size=13, color=theme.TEXT_MUTED)),
                ft.DataCell(ft.Text(
                    g.updated_at.strftime("%Y-%m-%d %H:%M") if g.updated_at else "—",
                    size=12, color=theme.TEXT_MUTED,
                )),
                ft.DataCell(
                    ft.IconButton(
                        icon=ft.Icons.EDIT_OUTLINED,
                        icon_size=18,
                        icon_color=theme.TEXT_MUTED,
                        on_click=on_edit,
                        tooltip="Editar",
                    )
                ),
            ],
            on_select_change=lambda e: on_edit(e) if e.data == "true" else None,
        )

    table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("Nombre", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT_MUTED)),
            ft.DataColumn(ft.Text("Documento", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT_MUTED)),
            ft.DataColumn(ft.Text("Telefono", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT_MUTED)),
            ft.DataColumn(ft.Text("Email", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT_MUTED)),
            ft.DataColumn(ft.Text("Actualizado", size=12, weight=ft.FontWeight.W_600, color=theme.TEXT_MUTED)),
            ft.DataColumn(ft.Text("")),
        ],
        rows=[row(g) for g in guests],
        heading_row_color=theme.SURFACE_ALT,
        heading_row_height=44,
        data_row_min_height=46,
        data_row_max_height=56,
        divider_thickness=0,
        column_spacing=24,
        expand=True,
    )

    return SectionCard(content=table, padding=theme.PADDING_MD)


def _open_guest_dialog(page: ft.Page, state, guest: Guest | None, on_done) -> None:
    sf = init_db()
    is_new = guest is None

    name = ft.TextField(label="Nombre completo", value=guest.name if guest else "", autofocus=is_new, border_radius=theme.INPUT_RADIUS)
    doc = ft.TextField(label="Documento", value=(guest.document or "") if guest else "", border_radius=theme.INPUT_RADIUS)
    phone = ft.TextField(label="Telefono", value=(guest.phone or "") if guest else "", border_radius=theme.INPUT_RADIUS)
    email = ft.TextField(label="Email (opcional)", value=(guest.email or "") if guest else "", border_radius=theme.INPUT_RADIUS)
    notes = ft.TextField(label="Notas (opcional)", value=(guest.notes or "") if guest else "",
                         multiline=True, min_lines=2, max_lines=4, border_radius=theme.INPUT_RADIUS)

    def on_close(e: ft.ControlEvent | None = None) -> None:
        _page_close(page, dlg)

    def on_save(e: ft.ControlEvent) -> None:
        with sf() as s:
            try:
                if is_new:
                    g = guests_service.create_guest(
                        s, name=name.value or "", document=doc.value, phone=phone.value, email=email.value, notes=notes.value,
                    )
                    log_action(s, "guest_create", state.operator.name if state.operator else "?", g.name)
                    show_toast(page, f"Huesped '{g.name}' creado", "success")
                else:
                    guests_service.update_guest(
                        s, guest.id, name=name.value, document=doc.value, phone=phone.value, email=email.value, notes=notes.value,
                    )
                    log_action(s, "guest_update", state.operator.name if state.operator else "?", guest.name)
                    show_toast(page, "Huesped actualizado", "success")
            except ValueError as exc:
                show_toast(page, str(exc), "error")
                return
        _page_close(page, dlg)
        on_done()

    def on_delete(e: ft.ControlEvent) -> None:
        if is_new or not guest:
            return

        def do_delete() -> None:
            with sf() as s:
                guests_service.delete_guest(s, guest.id)
                log_action(s, "guest_delete", state.operator.name if state.operator else "?", guest.name)
            show_toast(page, f"Huesped '{guest.name}' eliminado", "info")
            _page_close(page, dlg)
            on_done()

        confirm_dialog(
            page, f"Eliminar a {guest.name}?",
            "Las tarjetas emitidas a este huesped quedaran registradas sin asociacion.",
            on_confirm=do_delete, confirm_label="Eliminar", danger=True,
        )

    actions: list[ft.Control] = [ft.TextButton(content=ft.Text("Cancelar"), on_click=on_close)]
    if not is_new:
        actions.append(
            ft.TextButton(content=ft.Text("Eliminar", color=theme.ERROR), on_click=on_delete)
        )
    actions.append(PrimaryButton("Guardar" if not is_new else "Crear", on_click=on_save))

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Editar huesped" if not is_new else "Nuevo huesped",
                      size=18, weight=ft.FontWeight.W_600, color=theme.TEXT),
        content=ft.Container(
            content=ft.Column([name, doc, phone, email, notes], spacing=12, tight=True),
            width=460,
        ),
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
    )
    _page_open(page, dlg)
