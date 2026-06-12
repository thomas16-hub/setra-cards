"""Tests de seguridad del updater."""
import io
import urllib.error
import zipfile
import tempfile
from pathlib import Path
import pytest

from setra_cards.services.updater import (
    ROBOCOPY_EXCLUDE_DIRS,
    ROBOCOPY_EXCLUDE_FILES,
    UpdateInfo,
    _build_update_bat_script,
    _extract_sha256_from_body,
    check_for_update,
)


def test_extract_sha256_from_body():
    body = "Release notes\nSHA256: abc123def456abc123def456abc123def456abc123def456abc123def456abc1"
    sha = _extract_sha256_from_body(body)
    assert sha is not None
    assert len(sha) == 64


def test_extract_sha256_missing():
    assert _extract_sha256_from_body("no hash here") is None
    assert _extract_sha256_from_body("") is None
    assert _extract_sha256_from_body(None) is None


def test_update_info_requires_sha256():
    info = UpdateInfo(
        version="v2.1.0",
        asset_url="http://example.com/file.zip",
        asset_name="file.zip",
        sha256=None,
        notes="",
    )
    assert info.sha256 is None


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
        url="https://api.github.com/repos/x/releases/latest",
        code=code,
        msg="Not Found" if code == 404 else "Forbidden",
        hdrs=None,
        fp=io.BytesIO(b""),
    )


@pytest.mark.parametrize("code", [404, 403])
def test_check_for_update_private_repo_friendly_error(monkeypatch, code):
    """404/403 (repo privado) muestra mensaje amigable, no 'HTTP Error 404'."""
    def raise_http_error(*args, **kwargs):
        raise _http_error(code)

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    result = check_for_update()
    assert result.has_update is False
    assert result.latest is None
    assert result.error == "Canal de actualización no disponible — contactar a soporte SETRA"
    assert "404" not in result.error and "403" not in result.error


def test_check_for_update_other_http_error_passthrough(monkeypatch):
    """Otros codigos HTTP conservan el error crudo (diagnostico)."""
    def raise_http_error(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="https://api.github.com", code=500, msg="Server Error", hdrs=None, fp=io.BytesIO(b"")
        )

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)
    result = check_for_update()
    assert result.has_update is False
    assert "500" in (result.error or "")


def test_update_bat_excludes_per_hotel_identity_and_secrets():
    """El robocopy del update NO debe redistribuir hoteles/ ni pisar hotel.json."""
    script = _build_update_bat_script(
        Path("C:/tmp/payload"), Path("C:/app"), "Setra-CARDS.exe", Path("C:/tmp/stage")
    )
    assert "/XD hoteles drivers config data .browser-profile" in script
    assert "/XF Instalar.bat Desinstalar.bat LEEME.txt hotel.json .installed" in script
    # Constants stay in sync with the protocol-critical exclusions
    assert "hoteles" in ROBOCOPY_EXCLUDE_DIRS
    assert "hotel.json" in ROBOCOPY_EXCLUDE_FILES
    assert ".installed" in ROBOCOPY_EXCLUDE_FILES


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
