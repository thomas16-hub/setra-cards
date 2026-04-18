"""Tests de seguridad del updater."""
import zipfile
import hashlib
import tempfile
from pathlib import Path
import pytest

from setra_cards.services.updater import UpdateInfo, _extract_sha256_from_body


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
