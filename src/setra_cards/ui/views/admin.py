"""Administracion — encoder (auto-detect + manual), PIN, updates."""
from __future__ import annotations

import flet as ft

from setra_cards.core.app_state import get_state
from setra_cards.services import encoder_service
from setra_cards.ui import theme


def build(page: ft.Page) -> ft.Control:
    state = get_state()
    show_all = {"val": False}
    ports_list_host = ft.Column(spacing=0)
    status_text = ft.Text("", size=13, color=theme.TEXT_MUTED)

    def render_status() -> None:
        enc_ok = state.encoder is not None
        if enc_ok:
            status_text.value = f"Encoder conectado en {state.encoder_port}"
            status_text.color = theme.SUCCESS
        elif state.encoder_port:
            status_text.value = f"Encoder offline en {state.encoder_port}"
            status_text.color = theme.WARNING
        else:
            status_text.value = "Encoder no conectado"
            status_text.color = theme.TEXT_MUTED

    def render_ports() -> None:
        ports_list_host.controls.clear()
        ports = encoder_service.list_com_ports()
        visible = ports if show_all["val"] else [p for p in ports if p.looks_like_encoder]

        if not visible:
            ports_list_host.controls.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                "No se detecto ningun puerto compatible."
                                if not show_all["val"]
                                else "No se detecto ningun puerto COM.",
                                size=13, color=theme.TEXT_MUTED,
                            ),
                            ft.Text(
                                "Conecta el encoder USB y presiona Reescanear."
                                if ports or show_all["val"]
                                else "Prueba activar 'Mostrar todos' si tu encoder usa un chip poco comun.",
                                size=11, color=theme.TEXT_LIGHT,
                            ),
                        ],
                        spacing=4,
                    ),
                    padding=ft.Padding(16, 12, 16, 12),
                )
            )

        for p in visible:
            is_current = p.device == state.encoder_port
            ports_list_host.controls.append(_port_row(page, p, is_current, render_all))

        page.update()

    def render_all() -> None:
        render_status()
        render_ports()
        page.update()

    def do_autodetect(e: ft.ControlEvent) -> None:
        status_text.value = "Escaneando..."
        status_text.color = theme.TEXT_MUTED
        page.update()
        ok, msg = encoder_service.auto_connect(state)
        status_text.value = msg
        status_text.color = theme.SUCCESS if ok else theme.WARNING
        render_ports()

    def do_rescan(e: ft.ControlEvent) -> None:
        render_all()

    def toggle_show_all(e: ft.ControlEvent) -> None:
        show_all["val"] = bool(e.control.value)
        render_ports()

    auto_btn = ft.FilledButton(
        content=ft.Text("Detectar automaticamente"),
        icon=ft.Icons.AUTO_AWESOME,
        on_click=do_autodetect,
        style=ft.ButtonStyle(
            bgcolor=theme.PRIMARY,
            color=theme.SURFACE,
            shape=ft.RoundedRectangleBorder(radius=theme.BUTTON_RADIUS),
        ),
    )
    rescan_btn = ft.OutlinedButton(
        content=ft.Text("Reescanear"),
        icon=ft.Icons.REFRESH,
        on_click=do_rescan,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=theme.BUTTON_RADIUS)),
    )

    encoder_section = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Column(
                            [
                                ft.Text("Encoder USB", size=18, weight=ft.FontWeight.W_600, color=theme.TEXT),
                                status_text,
                            ],
                            spacing=2,
                            tight=True,
                            expand=True,
                        ),
                        ft.Row([auto_btn, rescan_btn], spacing=8),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(height=12),
                ft.Row(
                    [
                        ft.Text("Mostrar todos los puertos", size=13, color=theme.TEXT_MUTED),
                        ft.Switch(on_change=toggle_show_all, scale=0.8),
                    ],
                    alignment=ft.MainAxisAlignment.END,
                    spacing=4,
                ),
                ft.Container(
                    content=ports_list_host,
                    bgcolor=theme.SURFACE_ALT,
                    border=ft.Border.all(1, theme.BORDER),
                    border_radius=10,
                    padding=ft.Padding(4, 4, 4, 4),
                ),
            ],
            spacing=0,
        ),
        padding=ft.Padding(24, 20, 24, 20),
        bgcolor=theme.SURFACE,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=theme.CARD_RADIUS,
    )

    # Placeholder otras secciones (operadores, backup, updates — se llenan Dia 7)
    soon = ft.Container(
        content=ft.Column(
            [
                ft.Text("Operadores", size=16, weight=ft.FontWeight.W_600, color=theme.TEXT),
                ft.Text("Proximamente — Dia 5", size=12, color=theme.TEXT_MUTED),
                ft.Container(height=12),
                ft.Text("Backup de base de datos", size=16, weight=ft.FontWeight.W_600, color=theme.TEXT),
                ft.Text("Proximamente — Dia 7", size=12, color=theme.TEXT_MUTED),
                ft.Container(height=12),
                ft.Text("Actualizaciones", size=16, weight=ft.FontWeight.W_600, color=theme.TEXT),
                ft.Text("Proximamente — Dia 8", size=12, color=theme.TEXT_MUTED),
            ],
            spacing=4,
        ),
        padding=ft.Padding(24, 20, 24, 20),
        bgcolor=theme.SURFACE,
        border=ft.Border.all(1, theme.BORDER),
        border_radius=theme.CARD_RADIUS,
    )

    root = ft.Container(
        content=ft.Column(
            [
                ft.Text("Administracion", size=28, weight=ft.FontWeight.BOLD, color=theme.TEXT),
                ft.Container(height=18),
                encoder_section,
                ft.Container(height=16),
                soon,
            ],
            spacing=0,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
    )

    # render inicial — ejecutado cuando el control ya esta montado
    def _initial_render(e: object) -> None:
        render_all()

    root.on_change = None  # Flet no tiene on_mount; usamos did_mount abajo si hace falta
    # En lugar de hook, disparamos render inmediatamente: ports_list_host ya esta vacio
    render_status()
    render_ports()

    return root


def _port_row(page: ft.Page, info, is_current: bool, on_done) -> ft.Container:
    state = get_state()

    def use_this(e: ft.ControlEvent) -> None:
        ok, msg = encoder_service.connect_port(state, info.device)
        on_done()

    description = " — ".join([s for s in (info.description, info.manufacturer) if s]) or "Sin descripcion"

    right: ft.Control
    if is_current and state.encoder is not None:
        right = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.CHECK_CIRCLE, size=16, color=theme.SUCCESS),
                    ft.Text("En uso", size=12, color=theme.SUCCESS, weight=ft.FontWeight.W_600),
                ],
                spacing=4,
            ),
            padding=ft.Padding(8, 4, 8, 4),
        )
    else:
        right = ft.OutlinedButton(
            content=ft.Text("Usar este"),
            on_click=use_this,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        )

    return ft.Container(
        content=ft.Row(
            [
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(info.device, size=14, weight=ft.FontWeight.W_600, color=theme.TEXT),
                                ft.Container(
                                    content=ft.Text("Encoder?", size=10, color=theme.ACCENT, weight=ft.FontWeight.W_600),
                                    padding=ft.Padding(6, 2, 6, 2),
                                    bgcolor="#E8F2FF",
                                    border_radius=6,
                                ) if info.looks_like_encoder else ft.Container(),
                            ],
                            spacing=8,
                        ),
                        ft.Text(description, size=11, color=theme.TEXT_MUTED),
                    ],
                    spacing=2,
                    tight=True,
                    expand=True,
                ),
                right,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        padding=ft.Padding(12, 10, 12, 10),
        bgcolor=theme.SURFACE if is_current else None,
        border_radius=8,
    )
