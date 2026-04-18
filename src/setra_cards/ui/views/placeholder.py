"""Vista placeholder mientras se implementan las secciones pendientes."""
from __future__ import annotations

import flet as ft

from setra_cards.ui import theme


def build_placeholder(title: str, note: str) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(title, size=28, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                ft.Container(height=8),
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.CONSTRUCTION, size=18, color=theme.WARNING),
                            ft.Text(note, size=14, color=theme.TEXT_MUTED),
                        ],
                        spacing=8,
                    ),
                    padding=ft.Padding(16, 12, 16, 12),
                    bgcolor=theme.SURFACE,
                    border=ft.Border.all(1, theme.BORDER),
                    border_radius=theme.CARD_RADIUS,
                ),
            ],
            spacing=0,
        ),
        expand=True,
    )
