"""Entrypoint para el .exe empaquetado de Setra CARDS."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _crash_log() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "setra-cards-startup.log"
    return Path.cwd() / "setra-cards-startup.log"


def main() -> None:
    try:
        from setra_cards.main import main as app_main
        app_main()
    except SystemExit:
        raise
    except BaseException:
        try:
            _crash_log().write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
                0,
                f"Setra CARDS no pudo arrancar.\n\nDetalles en:\n{_crash_log()}",
                "Setra CARDS - Error",
                0x10,
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
