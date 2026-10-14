"""Updater — queries a SIGNED manifest on portonsabaneta.com and applies updates.

The old channel (GitHub Releases) broke when the repo went private: anonymous
API calls return 404/403. The channel is now a small JSON manifest hosted at
portonsabaneta.com, every release SIGNED OFFLINE with an Ed25519 private key
that NEVER touches this machine. Only the 32-byte PUBLIC key is embedded here.

Updates are NOT fully automatic: installing always requires the operator to
press "Buscar actualizacion" (or "Instalar ahora") in the Admin view. At
startup, main() runs a best-effort, non-blocking check that only LOGS when a
new version is available — it never downloads or installs on its own.

Manifest schema (latest.json):
    {
      "version": "2.1.0",
      "url": "https://portonsabaneta.com/setra-cards-flet/setra-cards-v2.1.0.zip",
      "sha256": "<64 hex>",
      "signature": "<128 hex = Ed25519 signature>",
      "notes": "..."
    }

The signature covers the canonical message bytes:
    f"{version}|{url}|{sha256}".encode()

Flow:
1. Startup check (log-only) or operator presses "Buscar actualizacion":
   - GET https://portonsabaneta.com/setra-cards-flet/latest.json
   - VERIFY the Ed25519 signature with the embedded public key BEFORE trusting
     anything. Reject if the signature fails, if the manifest is malformed, or
     if the download URL is not under the trusted prefix (SSRF guard).
2. If the version is newer than __version__ and the operator confirms:
   - Downloads the ZIP to temp
   - Stream-verifies the published SHA-256 (mandatory)
   - RE-VERIFIES the manifest signature before launching the swap
   - Launches apply_update.bat which closes the app, swaps files and relaunches
"""
from __future__ import annotations

import binascii
import hashlib
import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from setra_cards import __version__

logger = logging.getLogger(__name__)

# Signed-manifest channel on portonsabaneta. The path is DISTINCT from the old
# Flet/Locstar prototype's /setra-cards/ tree so the two app trees never collide.
MANIFEST_URL = "https://portonsabaneta.com/setra-cards-flet/latest.json"
TRUSTED_DOWNLOAD_PREFIX = "https://portonsabaneta.com/setra-cards-flet/"

# Ed25519 PUBLIC key (32-byte hex). The matching PRIVATE key stays OFFLINE: the
# operator signs each release by hand and never copies the secret to any PC.
UPDATER_PUBKEY_HEX = "d63c3018aba85c2a7aff8eedb30f25acc0d59d3021b71e9f1fcec3c2cccadc79"

# Signature verification is mandatory. There is no unsigned fallback path.
REQUIRE_SIGNED = True

TIMEOUT = 15

# Spanish operator-facing messages (technical detail stays in the logs).
ERR_BAD_SIGNATURE = "Firma del manifest inválida — actualización rechazada"
ERR_UNTRUSTED_URL = "URL de descarga no confiable — actualización rechazada"
ERR_NETWORK = "Canal de actualización no disponible — contactar a soporte SETRA"
ERR_BAD_MANIFEST = "Manifest de actualización inválido — contactar a soporte SETRA"


def _make_ssl_context() -> ssl.SSLContext:
    """Contexto SSL con CA bundle de certifi como fallback.

    En PCs Windows con root certs desactualizados, urllib falla con
    CERTIFICATE_VERIFY_FAILED. certifi trae el bundle oficial de Mozilla.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        logger.warning("certifi no disponible, usando contexto SSL por defecto")
        return ssl.create_default_context()


_SSL_CTX = _make_ssl_context()


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    asset_url: str
    asset_name: str
    sha256: str | None
    notes: str
    signature: str = ""


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


def _verify_manifest_signature(
    version: str, url: str, sha256: str, signature_hex: str
) -> bool:
    """Verify the Ed25519 signature over f"{version}|{url}|{sha256}".

    Uses pycryptodome's documented Ed25519 path: import the raw 32-byte public
    key with eddsa.import_public_key, then verify with eddsa.new(key,'rfc8032').
    Any malformed input (bad hex, wrong length, bad signature) is treated as a
    verification failure: returns False and logs a warning — never raises.
    """
    try:
        from Crypto.PublicKey import ECC  # noqa: F401  (ensures backend is present)
        from Crypto.Signature import eddsa

        pub_raw = binascii.unhexlify(UPDATER_PUBKEY_HEX)
        if len(pub_raw) != 32:
            logger.warning("Embedded updater pubkey is not 32 bytes — rejecting")
            return False

        signature = binascii.unhexlify(signature_hex)
        if len(signature) != 64:
            logger.warning("Manifest signature is not 64 bytes — rejecting")
            return False

        message = f"{version}|{url}|{sha256}".encode()
        pubkey = eddsa.import_public_key(pub_raw)
        verifier = eddsa.new(pubkey, "rfc8032")
        verifier.verify(message, signature)
        return True
    except (ValueError, binascii.Error, TypeError) as exc:
        # ValueError covers a failed verification AND malformed key/signature.
        logger.warning("Manifest signature verification failed: %s", exc)
        return False
    except Exception as exc:  # pragma: no cover — defensive, never propagate
        logger.warning("Manifest signature verification crashed: %s", exc)
        return False


def _asset_name_from_url(url: str) -> str:
    name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    return name or "setra-cards-update.zip"


def check_for_update() -> CheckResult:
    try:
        req = urllib.request.Request(
            MANIFEST_URL,  # hardcoded https URL, never operator-controlled
            headers={
                "User-Agent": f"SetraCARDS/{__version__}",
                "Accept": "application/json",
            },
        )
        # B310: scheme is a hardcoded https:// literal (MANIFEST_URL), not user input.
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as resp:  # nosec B310
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Never leak raw HTTP codes to the operator; keep them in the log.
        logger.warning("Manifest check failed: HTTP %s — %s", exc.code, exc)
        return CheckResult(__version__, None, False, error=ERR_NETWORK)
    except Exception as exc:
        logger.warning("Manifest check failed: %s", exc)
        return CheckResult(__version__, None, False, error=ERR_NETWORK)

    if not isinstance(data, dict):
        logger.warning("Manifest is not a JSON object")
        return CheckResult(__version__, None, False, error=ERR_BAD_MANIFEST)

    version = str(data.get("version") or "")
    url = str(data.get("url") or "")
    sha256 = str(data.get("sha256") or "").lower()
    signature_hex = str(data.get("signature") or "")
    notes = str(data.get("notes") or "")

    if not version or not url or not re.fullmatch(r"[a-f0-9]{64}", sha256):
        logger.warning("Manifest missing/invalid version/url/sha256")
        return CheckResult(__version__, None, False, error=ERR_BAD_MANIFEST)

    # SSRF guard: only ever download from the exact trusted prefix.
    if not url.startswith(TRUSTED_DOWNLOAD_PREFIX):
        logger.warning("Manifest url outside trusted prefix: %s", url)
        return CheckResult(__version__, None, False, error=ERR_UNTRUSTED_URL)

    # Verify signature BEFORE trusting any field. No unsigned path exists.
    if REQUIRE_SIGNED and not _verify_manifest_signature(version, url, sha256, signature_hex):
        return CheckResult(__version__, None, False, error=ERR_BAD_SIGNATURE)

    latest = UpdateInfo(
        version=version,
        asset_url=url,
        asset_name=_asset_name_from_url(url),
        sha256=sha256,
        notes=notes,
        signature=signature_hex,
    )
    has = _parse_version(latest.version) > _parse_version(__version__)
    return CheckResult(__version__, latest, has)


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[3]


# Per-PC identity and secrets must NEVER be clobbered nor redistributed by an
# update payload: `hoteles/` carries the keys of ALL hotels (the release ZIP
# ships it for the installer only), `hotel.json` + `.installed` are this PC's
# identity, and installer artifacts are not runtime files.
ROBOCOPY_EXCLUDE_DIRS = ("hoteles", "drivers", "config", "data", ".browser-profile")
ROBOCOPY_EXCLUDE_FILES = ("Instalar.bat", "Desinstalar.bat", "LEEME.txt", "hotel.json", ".installed")


def backup_dir_for(install_dir: Path) -> Path:
    """Sibling folder holding the previous version (overwritten each update)."""
    return install_dir.with_name(install_dir.name + ".prev")


def _excluded_dirs(root: Path) -> str:
    # Anchored to the top level of `root`: a bare name in /XD matches at ANY
    # depth, so a package folder called "data" inside _internal would never
    # be updated.
    return " ".join(f'"{root / name}"' for name in ROBOCOPY_EXCLUDE_DIRS)


def _build_update_bat_script(payload_dir: Path, install_dir: Path, exe_name: str, staging: Path) -> str:
    """Build the apply_update.bat content (extracted for testability).

    1. Mirror the current install to <install>.prev (abort, untouched, if that fails).
    2. Copy the new version over the install.
    3. If the copy fails, restore <install>.prev and relaunch the old version:
       a half-copied install used to leave the front desk without the app.
    Robocopy exit codes 0-7 are success, 8+ are failures.
    """
    backup = backup_dir_for(install_dir)
    xf = " ".join(ROBOCOPY_EXCLUDE_FILES)
    # /R:3 /W:2: robocopy's default is 1,000,000 retries 30 s apart, so one
    # file still locked by the closing app would hang the update forever.
    quiet = "/R:3 /W:2 /NFL /NDL /NJH /NJS /NP >nul"
    relaunch = f'start "" "{install_dir}\\{exe_name}"\r\n'
    return (
        "@echo off\r\n"
        "chcp 65001 >nul 2>&1\r\n"
        "title Setra CARDS - Actualizando\r\n"
        "echo Aplicando actualizacion...\r\n"
        "timeout /t 3 /nobreak >nul\r\n"
        f'taskkill /F /IM "{exe_name}" >nul 2>&1\r\n'
        "timeout /t 2 /nobreak >nul\r\n"
        "echo Respaldando la version actual...\r\n"
        f'robocopy "{install_dir}" "{backup}" /MIR {quiet}\r\n'
        "if errorlevel 8 (\r\n"
        "  echo No se pudo respaldar la version actual. La actualizacion se cancelo.\r\n"
        f"  {relaunch}"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "echo Copiando la version nueva...\r\n"
        f'robocopy "{payload_dir}" "{install_dir}" /E /XD {_excluded_dirs(payload_dir)} /XF {xf} {quiet}\r\n'
        "if errorlevel 8 (\r\n"
        "  echo ERROR copiando la version nueva. Restaurando la version anterior...\r\n"
        f'  robocopy "{backup}" "{install_dir}" /E /XD {_excluded_dirs(backup)} /XF {xf} {quiet}\r\n'
        f"  {relaunch}"
        "  echo Se restauro la version anterior. Avise a soporte SETRA.\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        f"{relaunch}"
        # This .bat lives inside staging. "(goto) 2>nul" detaches cmd from the
        # file first, so deleting its own folder does not end in "cannot find
        # the path" (verified by running the script against real folders).
        f'(goto) 2>nul & rmdir /S /Q "{staging}" >nul 2>&1 & exit /b 0\r\n'
    )


def apply_update(info: UpdateInfo) -> None:
    """Descarga, extrae y relanza la app con el .exe nuevo."""
    # Validar SHA-256 disponible ANTES de descargar — fail-fast sin tocar disco
    if not info.sha256:
        raise ValueError("El release no incluye SHA-256 — actualización rechazada por seguridad")
    expected_sha = info.sha256.lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
        raise ValueError(f"SHA-256 inválido en el release: {info.sha256}")

    # SSRF guard (defence in depth): only download from the trusted prefix.
    if not info.asset_url.startswith(TRUSTED_DOWNLOAD_PREFIX):
        raise ValueError(ERR_UNTRUSTED_URL)

    # Re-verify the signature before doing anything destructive. check_for_update
    # already verified it, but re-check so a stale/forged UpdateInfo can't slip
    # straight into apply_update from another path.
    if REQUIRE_SIGNED and not _verify_manifest_signature(
        info.version, info.asset_url, expected_sha, info.signature
    ):
        raise ValueError(ERR_BAD_SIGNATURE)

    install_dir = _install_root()
    exe_name = Path(sys.executable).name if getattr(sys, "frozen", False) else "Setra-CARDS.exe"

    staging = Path(tempfile.mkdtemp(prefix="setra-update-"))
    zip_path = staging / info.asset_name
    payload_dir = staging / "payload"
    payload_dir.mkdir()

    logger.info("Descargando %s -> %s", info.asset_url, zip_path)
    hasher = hashlib.sha256()
    # B310: asset_url already validated against TRUSTED_DOWNLOAD_PREFIX (https only).
    req = urllib.request.Request(info.asset_url, headers={"User-Agent": f"SetraCARDS/{__version__}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT * 4, context=_SSL_CTX) as resp, zip_path.open("wb") as f:  # nosec B310
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

    # Re-verify the signature AFTER the download, before launching the swap, now
    # that the SHA we trusted is the one we actually received.
    if REQUIRE_SIGNED and not _verify_manifest_signature(
        info.version, info.asset_url, expected_sha, info.signature
    ):
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError(ERR_BAD_SIGNATURE)

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
        _build_update_bat_script(payload_dir, install_dir, exe_name, staging),
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
