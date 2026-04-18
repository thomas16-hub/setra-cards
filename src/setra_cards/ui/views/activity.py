"""Vista Actividad — log unificado (emisiones + acciones admin)."""
from __future__ import annotations

from datetime import datetime, timedelta

import flet as ft

from setra_cards.services import card_service
from setra_cards.services import action_log as action_log_service
from setra_cards.storage.database import init_db
from setra_cards.storage.models import ActionLog, CardLog
from setra_cards.ui import theme
from setra_cards.ui.components import (
    EmptyState,
    PageHeader,
    SectionCard,
    show_toast,
    Badge,
)


ACTION_ICONS = {
    "card_guest": (ft.Icons.BADGE_OUTLINED, theme.ACCENT),
    "card_master": (ft.Icons.KEY, "#8E44AD"),
    "card_auth": (ft.Icons.VERIFIED, theme.SUCCESS),
    "card_clock": (ft.Icons.SCHEDULE, theme.WARNING),
    "card_setting": (ft.Icons.SETTINGS_INPUT_COMPONENT, theme.TEXT_MUTED),
    "card_blank": (ft.Icons.DELETE_SWEEP_OUTLINED, theme.ERROR),
    "room_create": (ft.Icons.HOTEL_OUTLINED, theme.SUCCESS),
    "room_update": (ft.Icons.EDIT_OUTLINED, theme.TEXT_MUTED),
    "room_delete": (ft.Icons.DELETE_OUTLINE, theme.ERROR),
    "guest_create": (ft.Icons.PERSON_ADD, theme.SUCCESS),
    "guest_update": (ft.Icons.EDIT_OUTLINED, theme.TEXT_MUTED),
    "guest_delete": (ft.Icons.PERSON_REMOVE, theme.ERROR),
    "login": (ft.Icons.LOGIN, theme.TEXT_MUTED),
    "pin_change": (ft.Icons.LOCK_RESET, theme.WARNING),
}


def build(page: ft.Page) -> ft.Control:
    sf = init_db()

    filter_kind = {"val": "all"}  # all | cards | admin
    host = ft.Container(expand=True)

    def refresh() -> None:
        with sf() as s:
            cards = card_service.recent_emissions(s, limit=200)
            actions = action_log_service.recent(s, limit=200)

        entries = _unify(cards, actions, filter_kind["val"])
        if not entries:
            host.content = SectionCard(content=EmptyState(
                "Sin actividad registrada",
                "Las acciones aparecen aqui a medida que ocurren.",
                icon=ft.Icons.RECEIPT_LONG_OUTLINED,
            ))
        else:
            host.content = SectionCard(content=_timeline(entries), padding=theme.PADDING_MD)
        page.update()

    def chip(value: str, label: str) -> ft.Container:
        is_active = filter_kind["val"] == value

        def on_click(e: ft.ControlEvent) -> None:
            filter_kind["val"] = value
            refresh()

        return ft.Container(
            content=ft.Text(label, size=12, weight=ft.FontWeight.W_600,
                            color=theme.SURFACE if is_active else theme.TEXT),
            padding=ft.Padding(14, 8, 14, 8),
            bgcolor=theme.PRIMARY if is_active else theme.SURFACE,
            border=ft.Border.all(1, theme.PRIMARY if is_active else theme.BORDER),
            border_radius=999,
            on_click=on_click,
            ink=True,
        )

    header = PageHeader(
        title="Actividad",
        subtitle="Emisiones y cambios administrativos recientes",
    )
    chips = ft.Row(
        [chip("all", "Todo"), chip("cards", "Tarjetas"), chip("admin", "Administrativas")],
        spacing=8,
    )

    refresh()
    return ft.Container(
        content=ft.Column(
            [
                header,
                chips,
                ft.Container(height=14),
                host,
            ],
            spacing=0,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
    )


def _unify(cards: list[CardLog], actions: list[ActionLog], kind: str) -> list[tuple[datetime, dict]]:
    entries: list[tuple[datetime, dict]] = []
    if kind in ("all", "cards"):
        for c in cards:
            entries.append((
                c.issued_at,
                {
                    "type": "card",
                    "icon_key": f"card_{c.card_type.lower()}",
                    "title": f"{c.card_type}" + (f" · Hab. {c.room_display}" if c.room_display else ""),
                    "subtitle": (
                        f"UID: {c.uid_hex}" if c.uid_hex else ""
                    ) + (f" · Hasta {c.expires_at:%Y-%m-%d %H:%M}" if c.expires_at else ""),
                    "operator": c.operator,
                    "success": c.success,
                    "error": c.error_message,
                },
            ))
    if kind in ("all", "admin"):
        for a in actions:
            if a.action.startswith("card_"):
                continue  # ya los mostramos via cards
            entries.append((
                a.timestamp,
                {
                    "type": "admin",
                    "icon_key": a.action,
                    "title": _humanize_action(a.action),
                    "subtitle": a.detail or "",
                    "operator": a.operator,
                    "success": True,
                    "error": None,
                },
            ))
    entries.sort(key=lambda x: x[0] or datetime.min, reverse=True)
    return entries


def _humanize_action(action: str) -> str:
    mapping = {
        "room_create": "Habitacion creada",
        "room_update": "Habitacion editada",
        "room_delete": "Habitacion eliminada",
        "guest_create": "Huesped creado",
        "guest_update": "Huesped editado",
        "guest_delete": "Huesped eliminado",
        "login": "Inicio de sesion",
        "pin_change": "Cambio de PIN",
    }
    return mapping.get(action, action)


def _timeline(entries: list[tuple[datetime, dict]]) -> ft.Control:
    rows: list[ft.Control] = []
    for ts, e in entries:
        icon_key = e["icon_key"]
        icon, color = ACTION_ICONS.get(icon_key, (ft.Icons.CIRCLE, theme.TEXT_MUTED))

        status = Badge("OK", theme.SUCCESS, "#E8F7EC") if e["success"] else Badge("Error", theme.ERROR, "#FEE4E2")
        rows.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Container(
                            content=ft.Icon(icon, size=18, color=color),
                            width=40, height=40,
                            bgcolor=theme.SURFACE_ALT,
                            border_radius=20,
                            alignment=ft.Alignment.CENTER,
                        ),
                        ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(e["title"], size=13, weight=ft.FontWeight.W_600, color=theme.TEXT),
                                        status,
                                    ],
                                    spacing=8,
                                ),
                                ft.Text(e["subtitle"] or "—", size=11, color=theme.TEXT_MUTED),
                                ft.Text(
                                    f"{ts.strftime('%Y-%m-%d %H:%M:%S') if ts else '—'} · por {e['operator']}",
                                    size=10, color=theme.TEXT_LIGHT,
                                ),
                            ],
                            spacing=2,
                            tight=True,
                            expand=True,
                        ),
                    ],
                    spacing=14,
                ),
                padding=ft.Padding(12, 12, 12, 12),
                border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
            )
        )
    return ft.Column(rows, spacing=0, tight=True)
