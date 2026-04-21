"""Entrypoint Setra CARDS — arranca Flet con vista login."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import flet as ft

from setra_cards import __version__
from setra_cards.core.app_state import get_state
from setra_cards.services import encoder_service
from setra_cards.storage.database import app_data_dir, init_db


def _configure_logging() -> None:
    log_file = app_data_dir() / "setra-cards.log"
    handlers: list[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    if sys.stdout:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def build_app(page: ft.Page) -> None:
    from setra_cards.ui import theme
    from setra_cards.ui.views.login import build as login_build

    page.title = f"Setra CARDS v{__version__}"
    page.window.width = 1280
    page.window.height = 820
    page.window.min_width = 960
    page.window.min_height = 640
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 0
    page.bgcolor = theme.BG
    page.theme = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary=theme.GOLD,
            on_primary=theme.TEXT_INVERSE,
            secondary=theme.GOLD_LIGHT,
            surface=theme.SURFACE,
            on_surface=theme.TEXT,
            error=theme.ERROR,
        ),
    )
    page.fonts = {}

    page.add(login_build(page))


def _reset_admin() -> int:
    """Elimina al operador 'Admin' para que se recree con PIN=1234 al próximo login."""
    from setra_cards.storage.models import Operator
    sf = init_db()
    with sf() as s:
        admin = s.query(Operator).filter(Operator.name == "Admin").first()
        if admin:
            s.delete(admin)
            s.commit()
            print("OK: operador 'Admin' eliminado. Al abrir la app se recreara con PIN=1234.")
            print("    La app te pedira cambiar el PIN al primer login.")
            return 0
        # Si no hay Admin, puede haber un super_manager con otro nombre
        super_op = s.query(Operator).filter(Operator.role == "super_manager").first()
        if super_op:
            # Reset PIN del super_manager existente a 1234
            from setra_cards.services.auth import hash_new_pin
            h, salt = hash_new_pin("1234")
            super_op.pin_hash = h
            super_op.pin_salt = salt
            super_op.must_change_pin = True
            s.commit()
            print(f"OK: PIN de '{super_op.name}' (super_manager) reseteado a 1234.")
            print(f"    Usuario: {super_op.name}")
            print(f"    PIN:     1234")
            print(f"    La app te pedira cambiar el PIN al primer login.")
            return 0
        print("ERROR: no hay operador Admin ni super_manager. Abre la app normal — se creara Admin/1234.")
        return 1


def main() -> None:
    # Flag de recuperación: no arranca la UI, solo resetea el Admin y sale.
    if "--reset-admin" in sys.argv:
        _configure_logging()
        import ctypes
        try:
            ctypes.windll.kernel32.AllocConsole()
        except Exception:
            pass
        try:
            code = _reset_admin()
        except Exception as exc:
            print(f"ERROR: {exc}")
            code = 2
        input("\nPresiona ENTER para cerrar...")
        sys.exit(code)

    _configure_logging()
    logger = logging.getLogger(__name__)
    logger.info("Setra CARDS v%s arrancando", __version__)

    # Inicializar DB + intentar auto-conectar encoder en background
    init_db()

    state = get_state()
    # Intento best-effort (no bloquea arranque si falla)
    try:
        encoder_service.auto_connect(state)
    except Exception as exc:
        logger.warning("Auto-connect de encoder fallo: %s", exc)

    encoder_service.start_watchdog(state)

    ft.run(build_app)


if __name__ == "__main__":
    main()
