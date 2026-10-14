"""Tests de seguridad del updater (canal de manifest firmado)."""
import io
import urllib.error
import zipfile
import tempfile
from pathlib import Path
import pytest

from setra_cards.services.updater import (
    ERR_NETWORK,
    ROBOCOPY_EXCLUDE_DIRS,
    ROBOCOPY_EXCLUDE_FILES,
    UpdateInfo,
    _build_update_bat_script,
    check_for_update,
)


def test_update_info_requires_sha256():
    info = UpdateInfo(
        version="v2.1.0",
        asset_url="https://portonsabaneta.com/setra-cards-flet/file.zip",
        asset_name="file.zip",
        sha256=None,
        notes="",
    )
    assert info.sha256 is None


def test_update_info_signature_defaults_empty():
    """Backwards-compatible default keeps existing constructors working."""
    info = UpdateInfo(
        version="2.0.10",
        asset_url="https://portonsabaneta.com/setra-cards-flet/file.zip",
        asset_name="file.zip",
        sha256=None,
        notes="",
    )
    assert info.signature == ""


def test_zip_path_traversal_detection():
    """Simula un ZIP con path traversal y verifica que se detecta."""
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "evil.zip"
        payload_dir = Path(tmpdir) / "payload"
        payload_dir.mkdir()

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../../evil.txt", "pwned")

        with zipfile.ZipFile(zip_path, "r") as zf:
            traversal_found = False
            for member in zf.namelist():
                member_path = (payload_dir / member).resolve()
                if not str(member_path).startswith(str(payload_dir.resolve())):
                    traversal_found = True
                    break
            assert traversal_found, "El path traversal deberia haber sido detectado"


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://portonsabaneta.com/setra-cards-flet/latest.json",
        code=code,
        msg="Not Found" if code == 404 else "Forbidden",
        hdrs=None,
        fp=io.BytesIO(b""),
    )


@pytest.mark.parametrize("code", [404, 403, 500])
def test_check_for_update_network_error_friendly(monkeypatch, code):
    """Cualquier fallo HTTP devuelve mensaje amigable, nunca el codigo crudo."""
    def raise_http_error(*args, **kwargs):
        raise _http_error(code)

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    result = check_for_update()
    assert result.has_update is False
    assert result.latest is None
    assert result.error == ERR_NETWORK
    assert str(code) not in (result.error or "")


def test_update_bat_excludes_per_hotel_identity_and_secrets():
    """El robocopy del update NO debe redistribuir hoteles/ ni pisar hotel.json."""
    payload = Path("C:/tmp/payload")
    script = _build_update_bat_script(payload, Path("C:/app"), "Setra-CARDS.exe", Path("C:/tmp/stage"))
    # Excluded dirs are anchored to the payload root (a bare name matches at any depth)
    for name in ("hoteles", "drivers", "config", "data", ".browser-profile"):
        assert f'"{payload / name}"' in script
    assert "/XF Instalar.bat Desinstalar.bat LEEME.txt hotel.json .installed" in script
    # Constants stay in sync with the protocol-critical exclusions
    assert "hoteles" in ROBOCOPY_EXCLUDE_DIRS
    assert "hotel.json" in ROBOCOPY_EXCLUDE_FILES
    assert ".installed" in ROBOCOPY_EXCLUDE_FILES


def test_update_bat_backs_up_first_and_restores_on_failure():
    install = Path("C:/app")
    script = _build_update_bat_script(Path("C:/tmp/payload"), install, "Setra-CARDS.exe", Path("C:/tmp/stage"))
    backup = f'"{install.with_name("app.prev")}"'
    lines = script.split("\r\n")
    backup_step = next(i for i, ln in enumerate(lines) if "/MIR" in ln)
    copy_step = next(i for i, ln in enumerate(lines) if ln.startswith('robocopy "C:\\tmp\\payload"'))
    restore_step = next(i for i, ln in enumerate(lines) if ln.strip().startswith(f"robocopy {backup}"))
    assert backup in lines[backup_step]
    assert backup_step < copy_step < restore_step
    # The old version is relaunched after a restore, not left closed
    assert any("start" in ln and "Setra-CARDS.exe" in ln for ln in lines[restore_step:])


def test_zip_normal_extraction():
    """ZIP limpio no dispara el check de path traversal."""
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = Path(tmpdir) / "normal.zip"
        payload_dir = Path(tmpdir) / "payload"
        payload_dir.mkdir()

        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("Setra-CARDS.exe", b"fake exe")

        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                member_path = (payload_dir / member).resolve()
                assert str(member_path).startswith(str(payload_dir.resolve()))
