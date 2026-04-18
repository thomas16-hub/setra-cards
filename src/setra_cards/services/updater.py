"""Auto-updater — consulta GitHub Releases y aplica actualizaciones.

Flujo:
1. Al arrancar o cuando el usuario pulsa "Buscar actualizacion":
   - GET https://api.github.com/repos/thomas16-hub/setra-cards/releases/latest
   - Extrae `tag_name` (ej. v2.1.0) y el asset .zip
2. Si la version es mayor que __version__:
   - Descarga el ZIP a temp
   - Verifica hash si esta disponible en release body
   - Lanza apply_update.bat que cierra la app, reemplaza archivos y relanza
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from setra_cards import __version__

logger = logging.getLogger(__name__)

GITHUB_REPO = "thomas16-hub/setra-cards"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
TIMEOUT = 15
ASSET_NAME_PATTERN = re.compile(r"setra-cards.*\.zip$", re.IGNORECASE)


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    asset_url: str
    asset_name: str
    sha256: str | None
    notes: str


@dataclass(frozen=True)
class CheckResult:
    current_version: str
    latest: UpdateInfo | None
    has_update: bool
    error: str | None = None


def _parse_version(v: str) -> tuple[int, ...]:
    digits = []
    for chunk in v.lstrip("v").split("."):
        try:
            digits.append(int(re.sub(r"[^0-9].*$", "", chunk)))
        except ValueError:
            break
    return tuple(digits) if digits else (0,)


def _extract_sha256_from_body(body: str) -> str | None:
    m = re.search(r"sha256[:\s]+([a-f0-9]{64})", body or "", re.IGNORECASE)
    return m.group(1).lower() if m else None


def check_for_update() -> CheckResult:
    try:
        req = urllib.request.Request(
            RELEASES_API,
            headers={
                "User-Agent": f"SetraCARDS/{__version__}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        tag = str(data.get("tag_name", ""))
        assets = data.get("assets") or []
        zip_asset = next((a for a in assets if ASSET_NAME_PATTERN.match(a.get("name", ""))), None)
        if not zip_asset:
            return CheckResult(__version__, None, False, error="No hay asset .zip en el release")

        latest = UpdateInfo(
            version=tag,
            asset_url=zip_asset.get("browser_download_url", ""),
            asset_name=zip_asset.get("name", ""),
            sha256=_extract_sha256_from_body(data.get("body", "")),
            notes=data.get("body") or "",
        )
        has = _parse_version(latest.version) > _parse_version(__version__)
        return CheckResult(__version__, latest, has)
    except Exception as exc:
        logger.warning("Error consultando GitHub: %s", exc)
        return CheckResult(__version__, None, False, error=str(exc))


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[3]


def apply_update(info: UpdateInfo) -> None:
    """Descarga, extrae y relanza la app con el .exe nuevo."""
    # Validar SHA-256 disponible ANTES de descargar — fail-fast sin tocar disco
    if not info.sha256:
        raise ValueError("El release no incluye SHA-256 — actualización rechazada por seguridad")
    expected_sha = info.sha256.lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
        raise ValueError(f"SHA-256 inválido en el release: {info.sha256}")

    install_dir = _install_root()
    exe_name = Path(sys.executable).name if getattr(sys, "frozen", False) else "Setra-CARDS.exe"

    staging = Path(tempfile.mkdtemp(prefix="setra-update-"))
    zip_path = staging / info.asset_name
    payload_dir = staging / "payload"
    payload_dir.mkdir()

    logger.info("Descargando %s -> %s", info.asset_url, zip_path)
    hasher = hashlib.sha256()
    req = urllib.request.Request(info.asset_url, headers={"User-Agent": f"SetraCARDS/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT * 4) as resp, zip_path.open("wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                hasher.update(chunk)
                f.write(chunk)

        actual = hasher.hexdigest().lower()
        if actual != expected_sha:
            # Borrar payload potencialmente malicioso inmediatamente
            shutil.rmtree(staging, ignore_errors=True)
            raise ValueError(f"SHA-256 no coincide: esperado {expected_sha}, recibido {actual}")
    except ValueError:
        raise
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            member_path = (payload_dir / member).resolve()
            if not str(member_path).startswith(str(payload_dir.resolve())):
                raise ValueError(f"ZIP path traversal detectado: {member}")
        zf.extractall(payload_dir)

    # Si el ZIP tiene una carpeta unica, aplanar
    entries = list(payload_dir.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        inner = entries[0]
        for item in inner.iterdir():
            shutil.move(str(item), str(payload_dir / item.name))
        inner.rmdir()

    bat = staging / "apply_update.bat"
    bat.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul 2>&1\r\n"
        "title Setra CARDS - Actualizando\r\n"
        "echo Aplicando actualizacion...\r\n"
        "timeout /t 3 /nobreak >nul\r\n"
        f'taskkill /F /IM "{exe_name}" >nul 2>&1\r\n'
        "timeout /t 2 /nobreak >nul\r\n"
        f'robocopy "{payload_dir}" "{install_dir}" /E /XD config data .browser-profile /NFL /NDL /NJH /NJS /NP >nul\r\n'
        "if errorlevel 8 (\r\n"
        "  echo ERROR aplicando actualizacion. Revisa el log.\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        f'start "" "{install_dir}\\{exe_name}"\r\n'
        f'rmdir /S /Q "{staging}" >nul 2>&1\r\n'
        "exit /b 0\r\n",
        encoding="utf-8",
    )

    logger.info("Lanzando apply_update.bat")
    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        creationflags=0x00000010 if os.name == "nt" else 0,
        close_fds=True,
    )
    # Dar tiempo al bat a arrancar antes de morir
    import threading as _t
    _t.Timer(1.0, lambda: os._exit(0)).start()
