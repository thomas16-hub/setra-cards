"""Dashboard inicial — resumen de estado (encoder, totales)."""
from __future__ import annotations

import flet as ft

from setra_cards.core.app_state import get_state
from setra_cards.storage.database import init_db
from setra_cards.storage.models import CardLog, Guest, Room
from setra_cards.ui import theme


def build(page: ft.Page) -> ft.Control:
    state = get_state()
    sf = init_db()

    with sf() as s:
        rooms_count = s.query(Room).count()
        guests_count = s.query(Guest).count()
        cards_count = s.query(CardLog).count()

    encoder_ok = state.encoder is not None
    encoder_port = state.encoder_port or "—"

    def card(title: str, value: str, sub: str = "", color: str = theme.TEXT) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, size=12, color=theme.TEXT_MUTED, weight=ft.FontWeight.W_500),
                    ft.Text(value, size=28, weight=ft.FontWeight.BOLD, color=color),
                    ft.Text(sub, size=11, color=theme.TEXT_MUTED) if sub else ft.Container(),
                ],
                spacing=4,
                tight=True,
            ),
            padding=ft.Padding(20, 18, 20, 18),
            bgcolor=theme.SURFACE,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=theme.CARD_RADIUS,
            expand=True,
        )

    encoder_card = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.USB if encoder_ok else ft.Icons.USB_OFF,
                            size=20,
                            color=theme.SUCCESS if encoder_ok else theme.WARNING,
                        ),
                        ft.Text(
                            "Encoder conectado" if encoder_ok else "Encoder no conectado",
                            size=14, weight=ft.FontWeight.W_600,
                            color=theme.SUCCESS if encoder_ok else theme.WARNING,
                        ),
                    ],
                    spacing=8,
                ),
                ft.Text(
                    f"Puerto: {encoder_port}" if encoder_ok else "Ve a Administracion para conectar el encoder",
                    size=12,
                    color=theme.TEXT_MUTED,
                ),
            ],
            spacing=6,
        ),
        padding=ft.Padding(20, 16, 20, 16),
        bgcolor=theme.SURFACE,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=theme.CARD_RADIUS,
    )

    return ft.Container(
        content=ft.Column(
            [
                ft.Text("Inicio", size=28, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                ft.Text(f"{state.hotel.name}", size=14, color=theme.TEXT_MUTED),
                ft.Container(height=18),
                encoder_card,
                ft.Container(height=16),
                ft.Row(
                    [
                        card("Habitaciones", str(rooms_count), "configuradas"),
                        card("Huespedes", str(guests_count), "en sistema"),
                        card("Tarjetas emitidas", str(cards_count), "historico total"),
                    ],
                    spacing=16,
                ),
            ],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
    )
