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


def main() -> None:
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
