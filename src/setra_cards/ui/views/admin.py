"""Administracion — encoder + operadores + backup + updates."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import flet as ft

from setra_cards import __version__
from setra_cards.core.app_state import get_state
from setra_cards.services import auth as auth_service
from setra_cards.services import encoder_service
from setra_cards.services import updater as updater_service
from setra_cards.services.action_log import log as log_action
from setra_cards.storage.database import app_data_dir, db_path, init_db
from setra_cards.storage.models import Operator
from setra_cards.ui import theme
from setra_cards.ui.components import (
    Badge,
    EmptyState,
    PageHeader,
    PrimaryButton,
    SecondaryButton,
    SectionCard,
    show_toast,
)
from setra_cards.ui.components.basics import confirm_dialog


def build(page: ft.Page) -> ft.Control:
    header = PageHeader(title="Administracion", subtitle="Configuracion y mantenimiento")

    return ft.Container(
        content=ft.Column(
            [
                header,
                _encoder_section(page),
                ft.Container(height=16),
                _operators_section(page),
                ft.Container(height=16),
                _backup_section(page),
                ft.Container(height=16),
                _updates_section(page),
                ft.Container(height=16),
                _about_section(page),
            ],
            spacing=0,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
    )


# --- Encoder ---

def _encoder_section(page: ft.Page) -> ft.Control:
    state = get_state()
    show_all = {"val": False}
    ports_host = ft.Column(spacing=0)
    status = ft.Text("", size=12, color=theme.TEXT_MUTED)

    def render_status() -> None:
        if state.encoder is not None:
            status.value = f"Conectado en {state.encoder_port}"
            status.color = theme.SUCCESS
        elif state.encoder_port:
            status.value = f"Offline en {state.encoder_port}"
            status.color = theme.WARNING
        else:
            status.value = "No conectado"
            status.color = theme.TEXT_MUTED

    def render_ports() -> None:
        ports_host.controls.clear()
        all_ports = encoder_service.list_com_ports()
        visible = all_ports if show_all["val"] else [p for p in all_ports if p.looks_like_encoder]
        if not visible:
            ports_host.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Text(
                            "No hay puertos compatibles." if not show_all["val"]
                            else "No hay puertos COM en el sistema.",
                            size=12, color=theme.TEXT_MUTED,
                        ),
                        ft.Text(
                            "Conecta el encoder USB y reescanea.",
                            size=11, color=theme.TEXT_LIGHT,
                        ),
                    ], spacing=4),
                    padding=ft.Padding(14, 12, 14, 12),
                )
            )
        for p in visible:
            is_current = p.device == state.encoder_port
            ports_host.controls.append(_port_row(page, p, is_current, refresh))
        page.update()

    def refresh() -> None:
        render_status()
        render_ports()

    def do_auto(e: ft.ControlEvent) -> None:
        status.value = "Escaneando..."
        status.color = theme.TEXT_MUTED
        page.update()
        ok, msg = encoder_service.auto_connect(state)
        show_toast(page, msg, "success" if ok else "warning")
        refresh()

    def toggle_show_all(e: ft.ControlEvent) -> None:
        show_all["val"] = bool(e.control.value)
        render_ports()

    refresh()

    return SectionCard(
        title="Encoder USB",
        actions=[
            SecondaryButton("Reescanear", icon=ft.Icons.REFRESH, on_click=lambda e: refresh()),
            PrimaryButton("Detectar automaticamente", icon=ft.Icons.AUTO_AWESOME, on_click=do_auto),
        ],
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.USB if state.encoder else ft.Icons.USB_OFF,
                            size=18,
                            color=theme.SUCCESS if state.encoder else theme.TEXT_MUTED,
                        ),
                        status,
                    ],
                    spacing=8,
                ),
                ft.Container(height=10),
                ft.Row(
                    [
                        ft.Text("Mostrar todos los puertos", size=12, color=theme.TEXT_MUTED),
                        ft.Switch(on_change=toggle_show_all, scale=0.7, active_color=theme.PRIMARY),
                    ],
                    alignment=ft.MainAxisAlignment.END,
                    spacing=4,
                ),
                ft.Container(
                    content=ports_host,
                    bgcolor=theme.SURFACE_ALT,
                    border=ft.Border.all(1, theme.BORDER),
                    border_radius=10,
                    padding=ft.Padding(4, 4, 4, 4),
                ),
            ],
            spacing=0,
        ),
    )


def _port_row(page: ft.Page, info, is_current: bool, on_done) -> ft.Container:
    state = get_state()
    description = " · ".join(filter(None, [info.description, info.manufacturer])) or "Sin descripcion"

    def use_this(e: ft.ControlEvent) -> None:
        ok, msg = encoder_service.connect_port(state, info.device)
        show_toast(page, msg, "success" if ok else "error")
        on_done()

    right = (
        Badge("En uso", theme.SUCCESS, "#E8F7EC")
        if is_current and state.encoder is not None
        else ft.OutlinedButton(
            content=ft.Text("Usar este"),
            on_click=use_this,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        )
    )
    left = ft.Column(
        [
            ft.Row(
                [
                    ft.Text(info.device, size=13, weight=ft.FontWeight.W_600, color=theme.TEXT),
                    Badge("Compatible", theme.ACCENT, "#E8F2FF") if info.looks_like_encoder else ft.Container(),
                ],
                spacing=8,
            ),
            ft.Text(description, size=11, color=theme.TEXT_MUTED),
        ],
        spacing=2,
        tight=True,
        expand=True,
    )
    return ft.Container(
        content=ft.Row([left, right], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        padding=ft.Padding(12, 10, 12, 10),
    )


# --- Operadores ---

def _operators_section(page: ft.Page) -> ft.Control:
    state = get_state()
    sf = init_db()
    host = ft.Column(spacing=0)

    def refresh() -> None:
        host.controls.clear()
        with sf() as s:
            ops = s.query(Operator).order_by(Operator.name).all()
        if not ops:
            host.controls.append(EmptyState(
                "Sin operadores",
                "Crea un operador nuevo para que otros usen la app.",
                icon=ft.Icons.PEOPLE_OUTLINED,
            ))
        else:
            for op in ops:
                host.controls.append(_op_row(page, op, refresh))
        page.update()

    def on_new(e: ft.ControlEvent) -> None:
        _open_operator_dialog(page, state, None, refresh)

    refresh()

    return SectionCard(
        title="Operadores",
        actions=[PrimaryButton("Nuevo operador", icon=ft.Icons.PERSON_ADD, on_click=on_new)],
        content=ft.Container(
            content=host,
            bgcolor=theme.SURFACE_ALT,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=10,
        ),
    )


def _op_row(page: ft.Page, op: Operator, on_change) -> ft.Control:
    state = get_state()
    role_colors = {
        "super_manager": (theme.ERROR, "#FEE4E2"),
        "manager": (theme.WARNING, "#FFF4D6"),
        "frontdesk": (theme.ACCENT, "#E8F2FF"),
    }
    fg, bg = role_colors.get(op.role, (theme.TEXT_MUTED, "#F0F0F0"))

    def on_edit(e: ft.ControlEvent) -> None:
        _open_operator_dialog(page, state, op, on_change)

    return ft.Container(
        content=ft.Row(
            [
                ft.CircleAvatar(
                    content=ft.Text(op.name[0].upper(), size=14, weight=ft.FontWeight.BOLD, color=theme.SURFACE),
                    bgcolor=theme.PRIMARY,
                    radius=18,
                ),
                ft.Column(
                    [
                        ft.Row([
                            ft.Text(op.name, size=13, weight=ft.FontWeight.W_600, color=theme.TEXT),
                            Badge(op.role.replace("_", " ").title(), fg, bg),
                            Badge("Activo", theme.SUCCESS, "#E8F7EC") if op.active
                            else Badge("Inactivo", theme.TEXT_MUTED, "#F0F0F0"),
                        ], spacing=6),
                        ft.Text(
                            "Debe cambiar PIN al iniciar sesion" if op.must_change_pin else "PIN configurado",
                            size=11,
                            color=theme.WARNING if op.must_change_pin else theme.TEXT_MUTED,
                        ),
                    ],
                    spacing=2,
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
        padding=ft.Padding(12, 10, 12, 10),
        border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
    )


def _open_operator_dialog(page: ft.Page, state, op: Operator | None, on_done) -> None:
    sf = init_db()
    is_new = op is None

    name = ft.TextField(label="Nombre del operador", value=op.name if op else "", autofocus=is_new,
                        border_radius=theme.INPUT_RADIUS, disabled=not is_new)
    role = ft.Dropdown(
        label="Rol",
        value=op.role if op else "frontdesk",
        options=[
            ft.dropdown.Option("frontdesk", "Frontdesk (recepcion)"),
            ft.dropdown.Option("manager", "Manager"),
            ft.dropdown.Option("super_manager", "Super manager (admin)"),
        ],
        border_radius=theme.INPUT_RADIUS,
    )
    pin = ft.TextField(
        label="PIN nuevo" if not is_new else "PIN inicial",
        password=True, can_reveal_password=True,
        hint_text="Dejar vacio para no cambiar" if not is_new else "Minimo 4 caracteres",
        border_radius=theme.INPUT_RADIUS,
    )
    active = ft.Switch(label="Activo", value=(op.active if op else True), active_color=theme.PRIMARY)

    def on_close(e: ft.ControlEvent | None = None) -> None:
        page.close(dlg)

    def on_save(e: ft.ControlEvent) -> None:
        with sf() as s:
            try:
                if is_new:
                    if not name.value or not pin.value:
                        show_toast(page, "Nombre y PIN son obligatorios", "error")
                        return
                    h, salt = auth_service.hash_new_pin(pin.value)
                    new_op = Operator(
                        name=name.value.strip(),
                        pin_hash=h, pin_salt=salt,
                        role=role.value or "frontdesk",
                        active=True, must_change_pin=True,
                    )
                    s.add(new_op)
                    s.commit()
                    log_action(s, "operator_create", state.operator.name if state.operator else "?", new_op.name)
                    show_toast(page, f"Operador '{new_op.name}' creado", "success")
                else:
                    existing = s.get(Operator, op.id)
                    if not existing:
                        return
                    existing.role = role.value or existing.role
                    existing.active = bool(active.value)
                    if pin.value:
                        h, salt = auth_service.hash_new_pin(pin.value)
                        existing.pin_hash = h
                        existing.pin_salt = salt
                        existing.must_change_pin = False
                    s.commit()
                    log_action(s, "operator_update", state.operator.name if state.operator else "?", existing.name)
                    show_toast(page, "Operador actualizado", "success")
            except ValueError as exc:
                show_toast(page, str(exc), "error")
                return
        page.close(dlg)
        on_done()

    def on_delete(e: ft.ControlEvent) -> None:
        if is_new or not op:
            return
        if op.name.lower() == "admin":
            show_toast(page, "No se puede eliminar el operador Admin", "error")
            return

        def do_delete() -> None:
            with sf() as s:
                existing = s.get(Operator, op.id)
                if existing:
                    s.delete(existing)
                    s.commit()
                    log_action(s, "operator_delete", state.operator.name if state.operator else "?", op.name)
            show_toast(page, f"Operador '{op.name}' eliminado", "info")
            page.close(dlg)
            on_done()

        confirm_dialog(
            page, f"Eliminar operador {op.name}?",
            "Esta accion no se puede deshacer.",
            on_confirm=do_delete, confirm_label="Eliminar", danger=True,
        )

    actions: list[ft.Control] = [ft.TextButton(content=ft.Text("Cancelar"), on_click=on_close)]
    if not is_new:
        actions.append(ft.TextButton(content=ft.Text("Eliminar", color=theme.ERROR), on_click=on_delete))
    actions.append(PrimaryButton("Guardar" if not is_new else "Crear", on_click=on_save))

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Editar operador" if not is_new else "Nuevo operador",
                      size=18, weight=ft.FontWeight.W_600, color=theme.TEXT),
        content=ft.Container(
            content=ft.Column([name, role, pin, active], spacing=12, tight=True),
            width=420,
        ),
        actions=actions,
        actions_alignment=ft.MainAxisAlignment.END,
        shape=ft.RoundedRectangleBorder(radius=theme.CARD_RADIUS),
    )
    page.open(dlg)


# --- Backup ---

def _backup_section(page: ft.Page) -> ft.Control:
    state = get_state()

    def do_backup(e: ft.ControlEvent) -> None:
        try:
            backups_dir = app_data_dir() / "backups"
            backups_dir.mkdir(parents=True, exist_ok=True)
            src = db_path()
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dst = backups_dir / f"setra-cards_{stamp}.db"
            shutil.copy2(src, dst)
            show_toast(page, f"Backup creado: {dst.name}", "success")
            init_db_ref = init_db()
            with init_db_ref() as s:
                log_action(s, "backup_manual", state.operator.name if state.operator else "?", dst.name)
        except Exception as exc:
            show_toast(page, f"Error: {exc}", "error")

    backups_dir = app_data_dir() / "backups"
    existing = []
    if backups_dir.exists():
        existing = sorted(backups_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]

    rows: list[ft.Control]
    if existing:
        rows = []
        for b in existing:
            size_kb = b.stat().st_size // 1024
            mtime = datetime.fromtimestamp(b.stat().st_mtime)
            rows.append(
                ft.Container(
                    content=ft.Row([
                        ft.Column([
                            ft.Text(b.name, size=12, weight=ft.FontWeight.W_500, color=theme.TEXT),
                            ft.Text(f"{mtime:%Y-%m-%d %H:%M} · {size_kb} KB",
                                    size=10, color=theme.TEXT_MUTED),
                        ], spacing=2, tight=True, expand=True),
                    ]),
                    padding=ft.Padding(10, 8, 10, 8),
                    border=ft.Border.only(bottom=ft.BorderSide(1, theme.BORDER)),
                )
            )
        backups_view = ft.Container(
            content=ft.Column(rows, spacing=0, tight=True),
            bgcolor=theme.SURFACE_ALT,
            border=ft.Border.all(1, theme.BORDER),
            border_radius=10,
        )
    else:
        backups_view = EmptyState("Sin backups",
                                  "Presiona 'Crear backup' para respaldar la base de datos.",
                                  icon=ft.Icons.BACKUP)

    return SectionCard(
        title="Respaldo de base de datos",
        actions=[PrimaryButton("Crear backup ahora", icon=ft.Icons.BACKUP, on_click=do_backup)],
        content=ft.Column(
            [
                ft.Text(
                    f"Ruta: {db_path()}",
                    size=11, color=theme.TEXT_MUTED, selectable=True,
                ),
                ft.Container(height=10),
                backups_view,
            ],
            spacing=0,
        ),
    )


# --- Actualizaciones OTA ---

def _updates_section(page: ft.Page) -> ft.Control:
    status = ft.Container()

    def do_check(e: ft.ControlEvent) -> None:
        status.content = ft.Row([
            ft.ProgressRing(width=16, height=16, stroke_width=2, color=theme.TEXT_MUTED),
            ft.Text("Consultando GitHub...", size=12, color=theme.TEXT_MUTED),
        ], spacing=10)
        page.update()

        result = updater_service.check_for_update()
        if result.error:
            status.content = ft.Text(f"Error: {result.error}", size=12, color=theme.ERROR)
        elif result.has_update and result.latest:
            def do_apply(e2: ft.ControlEvent) -> None:
                def really_apply() -> None:
                    try:
                        show_toast(page, "Actualizando... la app se reiniciara", "info")
                        updater_service.apply_update(result.latest)
                    except Exception as exc:
                        show_toast(page, f"Error aplicando: {exc}", "error")

                confirm_dialog(
                    page, f"Instalar version {result.latest.version}?",
                    "La aplicacion se cerrara y volvera a abrir con la nueva version.",
                    on_confirm=really_apply, confirm_label="Instalar",
                )

            notes_preview = (result.latest.notes or "").strip()
            if len(notes_preview) > 200:
                notes_preview = notes_preview[:200] + "..."
            status.content = ft.Container(
                content=ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.NEW_RELEASES, size=18, color=theme.ACCENT),
                        ft.Text(f"Nueva version disponible: {result.latest.version}",
                                size=13, weight=ft.FontWeight.W_600, color=theme.ACCENT),
                    ], spacing=8),
                    ft.Text(f"Instalada: {result.current_version}", size=11, color=theme.TEXT_MUTED),
                    ft.Text(notes_preview or "Sin notas", size=11, color=theme.TEXT_MUTED),
                    ft.Container(height=8),
                    PrimaryButton("Instalar ahora", icon=ft.Icons.DOWNLOAD, on_click=do_apply),
                ], spacing=4),
                padding=ft.Padding(12, 10, 12, 10),
                bgcolor="#E8F2FF",
                border=ft.Border.all(1, theme.ACCENT),
                border_radius=10,
            )
        else:
            status.content = ft.Row([
                ft.Icon(ft.Icons.CHECK_CIRCLE, size=18, color=theme.SUCCESS),
                ft.Text(f"Estas en la ultima version ({result.current_version})",
                        size=12, color=theme.SUCCESS, weight=ft.FontWeight.W_500),
            ], spacing=8)
        page.update()

    return SectionCard(
        title="Actualizaciones",
        actions=[PrimaryButton("Buscar actualizacion", icon=ft.Icons.REFRESH, on_click=do_check)],
        content=ft.Column([
            ft.Text(
                "La app se actualiza automaticamente desde GitHub Releases. "
                "Tambien puedes forzar la busqueda manualmente.",
                size=12, color=theme.TEXT_MUTED,
            ),
            ft.Container(height=10),
            status,
        ], spacing=0),
    )


# --- Acerca de ---

def _about_section(page: ft.Page) -> ft.Control:
    state = get_state()
    return SectionCard(
        title="Acerca de la aplicacion",
        content=ft.Column(
            [
                ft.Row([
                    ft.Text("Version:", size=12, color=theme.TEXT_MUTED),
                    ft.Text(__version__, size=12, color=theme.TEXT, weight=ft.FontWeight.W_600),
                ], spacing=8),
                ft.Row([
                    ft.Text("Hotel:", size=12, color=theme.TEXT_MUTED),
                    ft.Text(state.hotel.name, size=12, color=theme.TEXT, weight=ft.FontWeight.W_600),
                ], spacing=8),
                ft.Row([
                    ft.Text("Datos:", size=12, color=theme.TEXT_MUTED),
                    ft.Text(str(app_data_dir()), size=12, color=theme.TEXT, selectable=True),
                ], spacing=8),
                ft.Container(height=8),
                ft.Text(
                    "Actualizaciones automaticas desde GitHub Releases se configuran en el build final.",
                    size=11, color=theme.TEXT_LIGHT,
                ),
            ],
            spacing=6,
        ),
    )
