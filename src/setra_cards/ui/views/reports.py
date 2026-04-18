"""Vista Reportes — estadísticas de emisiones + tabla filtrable + export CSV."""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import flet as ft

from setra_cards.services import reports as report_svc
from setra_cards.storage.database import init_db
from setra_cards.storage.models import CardLog
from setra_cards.ui import theme
from setra_cards.ui.components.basics import (
    Badge,
    EmptyState,
    PageHeader,
    show_toast,
)

CARD_TYPES = ["Todos", "Guest", "Master", "Auth", "Clock", "Setting", "Laundry", "Blank"]

TYPE_COLORS: dict[str, tuple[str, str]] = {
    "Guest":   (theme.INFO,    theme.SURFACE_ALT),
    "Master":  ("#8E44AD",     theme.SURFACE_ALT),
    "Auth":    (theme.SUCCESS, theme.SURFACE_ALT),
    "Clock":   (theme.WARNING, theme.SURFACE_ALT),
    "Setting": (theme.TEXT_MUTED, theme.SURFACE_ALT),
    "Laundry": ("#FF8C42",     theme.SURFACE_ALT),
    "Blank":   (theme.ERROR,   theme.SURFACE_ALT),
}


def build(page: ft.Page) -> ft.Control:
    sf = init_db()

    # ── estado filtros ──────────────────────────────────────────────────
    filters = {
        "date_from": datetime.now().replace(hour=0, minute=0, second=0) - timedelta(days=30),
        "date_to":   None,
        "card_type": "Todos",
        "room":      "Todas",
        "operator":  "Todos",
    }

    # ── hosts reactivos ─────────────────────────────────────────────────
    stats_row = ft.Row(spacing=10, scroll=ft.ScrollMode.AUTO, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    table_host = ft.Column(spacing=0, tight=True)
    count_label = ft.Text("", size=12, color=theme.TEXT_MUTED)

    # ── paginación ──────────────────────────────────────────────────────
    pagination = {"page": 0, "page_size": 50, "total": 0}
    prev_btn = ft.IconButton(ft.Icons.CHEVRON_LEFT, disabled=True)
    next_btn = ft.IconButton(ft.Icons.CHEVRON_RIGHT, disabled=True)
    page_label = ft.Text("", size=12, color=theme.TEXT_MUTED)

    # ── dropdowns de filtro (se rellenan en refresh) ────────────────────
    room_dd = ft.Dropdown(
        label="Habitación",
        value="Todas",
        width=180,
        border_radius=theme.INPUT_RADIUS,
        bgcolor=theme.SURFACE_ALT,
        border_color=theme.BORDER_STRONG,
        focused_border_color=theme.GOLD,
        color=theme.TEXT,
        label_style=ft.TextStyle(color=theme.TEXT_MUTED),
        text_size=13,
    )
    operator_dd = ft.Dropdown(
        label="Operador",
        value="Todos",
        width=180,
        border_radius=theme.INPUT_RADIUS,
        bgcolor=theme.SURFACE_ALT,
        border_color=theme.BORDER_STRONG,
        focused_border_color=theme.GOLD,
        color=theme.TEXT,
        label_style=ft.TextStyle(color=theme.TEXT_MUTED),
        text_size=13,
    )
    type_dd = ft.Dropdown(
        label="Tipo tarjeta",
        value="Todos",
        options=[ft.dropdown.Option(t) for t in CARD_TYPES],
        width=160,
        border_radius=theme.INPUT_RADIUS,
        bgcolor=theme.SURFACE_ALT,
        border_color=theme.BORDER_STRONG,
        focused_border_color=theme.GOLD,
        color=theme.TEXT,
        label_style=ft.TextStyle(color=theme.TEXT_MUTED),
        text_size=13,
    )

    # ── date pickers ────────────────────────────────────────────────────
    from_label = ft.Text(
        filters["date_from"].strftime("%d/%m/%Y"),
        size=13, color=theme.TEXT, weight=ft.FontWeight.W_500,
    )
    to_label = ft.Text("Hoy", size=13, color=theme.TEXT, weight=ft.FontWeight.W_500)

    def on_date_from(e: ft.ControlEvent) -> None:
        filters["date_from"] = e.control.value
        from_label.value = e.control.value.strftime("%d/%m/%Y") if e.control.value else "—"
        pagination["page"] = 0
        refresh()

    def on_date_to(e: ft.ControlEvent) -> None:
        filters["date_to"] = e.control.value
        to_label.value = e.control.value.strftime("%d/%m/%Y") if e.control.value else "Hoy"
        pagination["page"] = 0
        refresh()

    date_picker_from = ft.DatePicker(
        value=filters["date_from"],
        on_change=on_date_from,
        help_text="Desde",
    )
    date_picker_to = ft.DatePicker(on_change=on_date_to, help_text="Hasta")
    page.overlay.extend([date_picker_from, date_picker_to])

    def open_date_from(e: ft.ControlEvent) -> None:
        date_picker_from.open = True
        page.update()

    def open_date_to(e: ft.ControlEvent) -> None:
        date_picker_to.open = True
        page.update()

    # ── export CSV ──────────────────────────────────────────────────────
    # En Flet 0.84 el FilePicker va en page.services, NO en page.overlay
    file_picker = ft.FilePicker()
    if file_picker not in page.services:
        page.services.append(file_picker)
    _pending_csv: dict[str, str] = {}

    def do_save(e: ft.FilePickerResultEvent) -> None:
        if e.path:
            try:
                with open(e.path, "w", encoding="utf-8-sig", newline="") as f:
                    f.write(_pending_csv.get("data", ""))
                show_toast(page, f"CSV guardado: {os.path.basename(e.path)}", "success")
            except Exception as exc:
                show_toast(page, f"Error al guardar: {exc}", "error")

    file_picker.on_result = do_save

    def export_csv(e: ft.ControlEvent) -> None:
        with sf() as s:
            logs, _ = report_svc.get_filtered_logs(
                s,
                date_from=filters["date_from"],
                date_to=filters["date_to"],
                card_type=filters["card_type"],
                room_display=filters["room"],
                operator=filters["operator"],
                limit=10000,
            )
        csv_data = report_svc.export_csv(logs)
        _pending_csv["data"] = csv_data
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        file_picker.save_file(
            dialog_title="Guardar reporte CSV",
            file_name=f"reporte_emisiones_{ts}.csv",
            allowed_extensions=["csv"],
        )

    # ── render stats ────────────────────────────────────────────────────
    def _stat_pill(label: str, value: int, color: str = theme.TEXT, icon: str | None = None) -> ft.Container:
        children: list[ft.Control] = []
        if icon:
            children.append(ft.Icon(icon, size=16, color=color))
        children.append(ft.Text(label, size=11, color=theme.TEXT_MUTED, weight=ft.FontWeight.W_500))
        children.append(ft.Text(str(value), size=16, weight=ft.FontWeight.BOLD, color=color))
        return ft.Container(
            content=ft.Row(children, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, tight=True),
            padding=ft.Padding(14, 10, 14, 10),
            bgcolor=theme.SURFACE,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=theme.CARD_RADIUS,
        )

    def _render_stats(summary: report_svc.ReportSummary) -> None:
        stats_row.controls = [
            _stat_pill("Total", summary.total, icon=ft.Icons.CREDIT_CARD),
            _stat_pill("Huésped", summary.guest, color=theme.INFO, icon=ft.Icons.BADGE_OUTLINED),
            _stat_pill("Limpieza", summary.laundry, color="#FF8C42", icon=ft.Icons.CLEANING_SERVICES_OUTLINED),
            _stat_pill("Maestra", summary.master, color="#8E44AD", icon=ft.Icons.KEY),
            _stat_pill("Fallidas", summary.failed, color=theme.ERROR, icon=ft.Icons.ERROR_OUTLINE),
            _stat_pill("Habs. únicas", summary.unique_rooms, icon=ft.Icons.HOTEL_OUTLINED),
        ]

    # ── render tabla ────────────────────────────────────────────────────
    def _render_table(logs: list[CardLog], total: int) -> None:
        pagination["total"] = total
        p = pagination["page"]
        ps = pagination["page_size"]
        total_pages = max(1, (total + ps - 1) // ps)

        prev_btn.disabled = p == 0
        next_btn.disabled = p >= total_pages - 1
        page_label.value = f"Página {p + 1} de {total_pages}"
        count_label.value = f"{total} registros"

        if not logs:
            table_host.controls = [
                EmptyState(
                    "Sin emisiones",
                    "No hay registros para los filtros seleccionados.",
                    icon=ft.Icons.RECEIPT_LONG_OUTLINED,
                )
            ]
            return

        col_style = ft.TextStyle(color=theme.TEXT_MUTED, size=12, weight=ft.FontWeight.W_600)
        columns = [
            ft.DataColumn(ft.Text("Tipo", style=col_style)),
            ft.DataColumn(ft.Text("Habitación", style=col_style)),
            ft.DataColumn(ft.Text("Emitida", style=col_style)),
            ft.DataColumn(ft.Text("Expira", style=col_style)),
            ft.DataColumn(ft.Text("Operador", style=col_style)),
            ft.DataColumn(ft.Text("UID", style=col_style)),
            ft.DataColumn(ft.Text("Estado", style=col_style)),
        ]
        rows: list[ft.DataRow] = []
        for r in logs:
            color, bg = TYPE_COLORS.get(r.card_type, (theme.TEXT_MUTED, theme.SURFACE_ALT))
            status_badge = (
                Badge("OK", theme.SUCCESS, theme.SURFACE_ALT)
                if r.success
                else Badge("Error", theme.ERROR, theme.SURFACE_ALT)
            )
            rows.append(ft.DataRow(cells=[
                ft.DataCell(Badge(r.card_type, color, bg)),
                ft.DataCell(ft.Text(r.room_display or "—", size=13, color=theme.TEXT)),
                ft.DataCell(ft.Text(
                    r.issued_at.strftime("%d/%m %H:%M") if r.issued_at else "—",
                    size=12, color=theme.TEXT_MUTED,
                )),
                ft.DataCell(ft.Text(
                    r.expires_at.strftime("%d/%m %H:%M") if r.expires_at else "—",
                    size=12, color=theme.TEXT_MUTED,
                )),
                ft.DataCell(ft.Text(r.operator or "—", size=13, color=theme.TEXT)),
                ft.DataCell(ft.Text(r.uid_hex or "—", size=11, color=theme.TEXT_LIGHT, font_family="monospace")),
                ft.DataCell(status_badge),
            ]))

        table = ft.DataTable(
            columns=columns,
            rows=rows,
            heading_row_color=theme.SURFACE_ALT,
            heading_row_height=44,
            data_row_min_height=44,
            data_row_max_height=52,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=theme.CARD_RADIUS,
            divider_thickness=0.5,
            column_spacing=20,
            horizontal_lines=ft.BorderSide(0.5, theme.BORDER_SUBTLE),
        )

        table_host.controls = [
            ft.Container(
                content=ft.Row(
                    [table],
                    scroll=ft.ScrollMode.AUTO,
                ),
                bgcolor=theme.SURFACE,
                border_radius=theme.CARD_RADIUS,
                padding=ft.Padding(0, 0, 0, 0),
            )
        ]

    # ── refresh principal ────────────────────────────────────────────────
    def refresh() -> None:
        with sf() as s:
            summary = report_svc.get_report_summary(
                s,
                date_from=filters["date_from"],
                date_to=filters["date_to"],
            )
            rooms = report_svc.get_distinct_rooms(s)
            operators = report_svc.get_distinct_operators(s)
            logs, total = report_svc.get_filtered_logs(
                s,
                date_from=filters["date_from"],
                date_to=filters["date_to"],
                card_type=filters["card_type"],
                room_display=filters["room"],
                operator=filters["operator"],
                limit=pagination["page_size"],
                offset=pagination["page"] * pagination["page_size"],
            )

        # poblar dropdowns
        room_opts = [ft.dropdown.Option("Todas")] + [ft.dropdown.Option(r) for r in rooms]
        op_opts   = [ft.dropdown.Option("Todos")] + [ft.dropdown.Option(o) for o in operators]
        room_dd.options = room_opts
        room_dd.value = filters["room"]
        operator_dd.options = op_opts
        operator_dd.value = filters["operator"]

        _render_stats(summary)
        _render_table(logs, total)
        page.update()

    # ── handlers dropdowns ───────────────────────────────────────────────
    def on_type(e: ft.ControlEvent) -> None:
        filters["card_type"] = type_dd.value or "Todos"
        pagination["page"] = 0
        refresh()

    def on_room(e: ft.ControlEvent) -> None:
        filters["room"] = room_dd.value or "Todas"
        pagination["page"] = 0
        refresh()

    def on_operator(e: ft.ControlEvent) -> None:
        filters["operator"] = operator_dd.value or "Todos"
        pagination["page"] = 0
        refresh()

    type_dd.on_change = on_type
    room_dd.on_change = on_room
    operator_dd.on_change = on_operator

    def on_prev(e: ft.ControlEvent) -> None:
        if pagination["page"] > 0:
            pagination["page"] -= 1
            refresh()

    def on_next(e: ft.ControlEvent) -> None:
        ps = pagination["page_size"]
        total_pages = max(1, (pagination["total"] + ps - 1) // ps)
        if pagination["page"] < total_pages - 1:
            pagination["page"] += 1
            refresh()

    prev_btn.on_click = on_prev
    next_btn.on_click = on_next

    # ── reset filtros ────────────────────────────────────────────────────
    def reset_filters(e: ft.ControlEvent) -> None:
        filters["date_from"] = datetime.now().replace(hour=0, minute=0, second=0) - timedelta(days=30)
        filters["date_to"] = None
        filters["card_type"] = "Todos"
        filters["room"] = "Todas"
        filters["operator"] = "Todos"
        from_label.value = filters["date_from"].strftime("%d/%m/%Y")
        to_label.value = "Hoy"
        type_dd.value = "Todos"
        room_dd.value = "Todas"
        operator_dd.value = "Todos"
        pagination["page"] = 0
        refresh()

    # ── layout ───────────────────────────────────────────────────────────
    date_filter_row = ft.Row(
        [
            ft.Text("Desde:", size=12, color=theme.TEXT_MUTED),
            ft.Container(
                content=ft.Row([ft.Icon(ft.Icons.CALENDAR_TODAY, size=14, color=theme.GOLD), from_label], spacing=6),
                padding=ft.Padding(12, 8, 12, 8),
                bgcolor=theme.SURFACE_ALT,
                border=ft.Border.all(1, theme.BORDER_STRONG),
                border_radius=theme.INPUT_RADIUS,
                on_click=open_date_from,
                ink=True,
            ),
            ft.Text("Hasta:", size=12, color=theme.TEXT_MUTED),
            ft.Container(
                content=ft.Row([ft.Icon(ft.Icons.CALENDAR_TODAY, size=14, color=theme.GOLD), to_label], spacing=6),
                padding=ft.Padding(12, 8, 12, 8),
                bgcolor=theme.SURFACE_ALT,
                border=ft.Border.all(1, theme.BORDER_STRONG),
                border_radius=theme.INPUT_RADIUS,
                on_click=open_date_to,
                ink=True,
            ),
        ],
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    export_btn = ft.FilledButton(
        content=ft.Text("Exportar CSV", size=13, weight=ft.FontWeight.W_600, color=theme.TEXT_INVERSE),
        icon=ft.Icons.DOWNLOAD_OUTLINED,
        on_click=export_csv,
        style=ft.ButtonStyle(
            bgcolor=theme.GOLD,
            color=theme.TEXT_INVERSE,
            shape=ft.RoundedRectangleBorder(radius=theme.BUTTON_RADIUS),
        ),
    )
    clear_btn = ft.TextButton(
        content=ft.Text("Limpiar filtros", size=12, color=theme.TEXT_MUTED),
        icon=ft.Icons.FILTER_ALT_OFF_OUTLINED,
        on_click=reset_filters,
    )

    filters_bar = ft.Container(
        content=ft.Row(
            [date_filter_row, type_dd, room_dd, operator_dd, ft.Container(expand=True), clear_btn],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.AUTO,
        ),
        padding=ft.Padding(14, 12, 14, 12),
        bgcolor=theme.SURFACE,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=theme.CARD_RADIUS,
    )

    pagination_bar = ft.Row(
        [count_label, ft.Container(expand=True), prev_btn, page_label, next_btn],
        alignment=ft.MainAxisAlignment.END,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    refresh()

    return ft.Container(
        content=ft.Column(
            [
                PageHeader(
                    title="Reportes",
                    subtitle="Historial de emisiones de tarjetas",
                    actions=[export_btn],
                ),
                ft.Container(content=stats_row, height=60),
                ft.Container(height=10),
                filters_bar,
                ft.Container(height=10),
                pagination_bar,
                ft.Container(height=6),
                ft.Container(
                    content=table_host,
                    padding=ft.Padding(0, 0, 0, 20),
                ),
            ],
            spacing=0,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
    )
