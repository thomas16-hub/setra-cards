"""Entrypoint Setra CARDS — arranca Flet con vista login."""
from __future__ import annotations

import logging
import sys
import threading

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
    """Recovery flag: resets (or creates) the seed admin with a one-time random PIN.

    Prefers an existing super_manager, then the 'Admin' row, then creates a new
    'Admin'. The fresh PIN is printed to the console — it is never 1234 anymore.
    """
    from setra_cards.services.auth import generate_random_pin, hash_new_pin
    from setra_cards.storage.models import Operator

    sf = init_db()
    with sf() as s:
        op = (
            s.query(Operator)
            .filter(Operator.role == "super_manager")
            .order_by(Operator.active.desc())
            .first()
        )
        if op is None:
            op = s.query(Operator).filter(Operator.name == "Admin").first()
            if op is not None:
                # No super_manager exists at all — promote so recovery grants admin access
                op.role = "super_manager"

        pin = generate_random_pin()
        h, salt = hash_new_pin(pin)
        if op is None:
            op = Operator(
                name="Admin",
                pin_hash=h,
                pin_salt=salt,
                role="super_manager",
                active=True,
                must_change_pin=True,
            )
            s.add(op)
            verb = "creado"
        else:
            op.pin_hash = h
            op.pin_salt = salt
            op.active = True
            op.must_change_pin = True
            verb = "reseteado"
        s.commit()
        print(f"OK: operador '{op.name}' (super_manager) {verb}.")
        print(f"    Usuario:      {op.name}")
        print(f"    PIN temporal: {pin}")
        print("    La app te pedira cambiar el PIN al primer login.")
        return 0


def _start_background_update_check() -> None:
    """Non-blocking daemon thread: checks for updates once at startup.

    Log-only notification — installing still requires the operator to use the
    "Buscar actualizacion" button in the Admin view.
    """
    logger = logging.getLogger(__name__)

    def _check() -> None:
        try:
            from setra_cards.services import updater
            result = updater.check_for_update()
            if result.has_update and result.latest:
                logger.info(
                    "Update available: %s (installed: %s) — install via Administracion > Buscar actualizacion",
                    result.latest.version,
                    result.current_version,
                )
            elif result.error:
                logger.info("Startup update check failed: %s", result.error)
            else:
                logger.info("Startup update check: already on latest (%s)", result.current_version)
        except Exception as exc:
            # Never let the startup check take the app down
            logger.warning("Startup update check crashed: %s", exc)

    threading.Thread(target=_check, name="startup-update-check", daemon=True).start()


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

    # Best-effort startup update check (log-only; install stays manual in Admin)
    _start_background_update_check()

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
