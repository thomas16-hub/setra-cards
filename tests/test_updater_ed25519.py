"""Tests for the signed-manifest update channel (Ed25519, offline).

These tests generate a THROWAWAY keypair at runtime — the real private key is
never referenced. We monkeypatch UPDATER_PUBKEY_HEX to the test public key and
monkeypatch urllib.request.urlopen so nothing ever hits the network.
"""
from __future__ import annotations

import binascii
import io
import json
import urllib.error

import pytest
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from setra_cards import __version__
from setra_cards.services import updater
from setra_cards.services.updater import (
    ERR_BAD_MANIFEST,
    ERR_BAD_SIGNATURE,
    ERR_NETWORK,
    ERR_UNTRUSTED_URL,
    TRUSTED_DOWNLOAD_PREFIX,
    _verify_manifest_signature,
    check_for_update,
)

_NEWER = "99.0.0"
_SHA = "a" * 64
_URL = TRUSTED_DOWNLOAD_PREFIX + f"setra-cards-v{_NEWER}.zip"


@pytest.fixture()
def test_keypair(monkeypatch):
    """Generate a throwaway Ed25519 keypair and embed its pubkey in the updater."""
    sk = ECC.generate(curve="Ed25519")
    pub_raw = sk.public_key().export_key(format="raw")
    pub_hex = binascii.hexlify(pub_raw).decode()
    monkeypatch.setattr(updater, "UPDATER_PUBKEY_HEX", pub_hex)
    return sk


def _sign(sk, version: str, url: str, sha256: str) -> str:
    message = f"{version}|{url}|{sha256}".encode()
    return binascii.hexlify(eddsa.new(sk, "rfc8032").sign(message)).decode()


def _fake_urlopen_returning(payload: dict):
    body = json.dumps(payload).encode("utf-8")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    def _fake(*args, **kwargs):
        return _Resp(body)

    return _fake


# --- _verify_manifest_signature unit-level ---


def test_verify_valid_signature(test_keypair):
    sig = _sign(test_keypair, _NEWER, _URL, _SHA)
    assert _verify_manifest_signature(_NEWER, _URL, _SHA, sig) is True


def test_verify_tampered_version(test_keypair):
    sig = _sign(test_keypair, _NEWER, _URL, _SHA)
    assert _verify_manifest_signature("88.0.0", _URL, _SHA, sig) is False


def test_verify_tampered_url(test_keypair):
    sig = _sign(test_keypair, _NEWER, _URL, _SHA)
    other = TRUSTED_DOWNLOAD_PREFIX + "evil.zip"
    assert _verify_manifest_signature(_NEWER, other, _SHA, sig) is False


def test_verify_tampered_sha(test_keypair):
    sig = _sign(test_keypair, _NEWER, _URL, _SHA)
    assert _verify_manifest_signature(_NEWER, _URL, "b" * 64, sig) is False


def test_verify_malformed_signature_not_hex(test_keypair):
    assert _verify_manifest_signature(_NEWER, _URL, _SHA, "zznothex") is False


def test_verify_malformed_signature_wrong_length(test_keypair):
    assert _verify_manifest_signature(_NEWER, _URL, _SHA, "ab" * 10) is False


def test_verify_empty_signature(test_keypair):
    assert _verify_manifest_signature(_NEWER, _URL, _SHA, "") is False


# --- check_for_update end-to-end (offline) ---


def test_check_accepts_valid_signed_manifest(test_keypair, monkeypatch):
    sig = _sign(test_keypair, _NEWER, _URL, _SHA)
    manifest = {"version": _NEWER, "url": _URL, "sha256": _SHA, "signature": sig, "notes": "ok"}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.error is None
    assert result.has_update is True
    assert result.latest is not None
    assert result.latest.version == _NEWER
    assert result.latest.asset_url == _URL
    assert result.latest.sha256 == _SHA
    assert result.latest.signature == sig


def test_check_rejects_tampered_version(test_keypair, monkeypatch):
    # Signature was made for _NEWER; manifest claims a different version.
    sig = _sign(test_keypair, _NEWER, _URL, _SHA)
    manifest = {"version": "88.0.0", "url": _URL, "sha256": _SHA, "signature": sig, "notes": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.has_update is False
    assert result.latest is None
    assert result.error == ERR_BAD_SIGNATURE


def test_check_rejects_tampered_url(test_keypair, monkeypatch):
    sig = _sign(test_keypair, _NEWER, _URL, _SHA)
    other = TRUSTED_DOWNLOAD_PREFIX + "swapped.zip"
    manifest = {"version": _NEWER, "url": other, "sha256": _SHA, "signature": sig, "notes": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.error == ERR_BAD_SIGNATURE


def test_check_rejects_tampered_sha(test_keypair, monkeypatch):
    sig = _sign(test_keypair, _NEWER, _URL, _SHA)
    manifest = {"version": _NEWER, "url": _URL, "sha256": "c" * 64, "signature": sig, "notes": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.error == ERR_BAD_SIGNATURE


def test_check_rejects_malformed_signature(test_keypair, monkeypatch):
    manifest = {"version": _NEWER, "url": _URL, "sha256": _SHA, "signature": "notvalid", "notes": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.error == ERR_BAD_SIGNATURE


def test_check_rejects_url_outside_trusted_prefix(test_keypair, monkeypatch):
    evil_url = "https://evil.example.com/setra-cards-flet/x.zip"
    # Even a perfectly valid signature over the evil URL must be rejected by the
    # prefix guard, which runs BEFORE signature verification.
    sig = _sign(test_keypair, _NEWER, evil_url, _SHA)
    manifest = {"version": _NEWER, "url": evil_url, "sha256": _SHA, "signature": sig, "notes": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.has_update is False
    assert result.latest is None
    assert result.error == ERR_UNTRUSTED_URL


def test_check_rejects_lookalike_prefix(test_keypair, monkeypatch):
    # A subdomain/path that merely contains the trusted host must NOT pass.
    evil_url = "https://portonsabaneta.com.evil.com/setra-cards-flet/x.zip"
    sig = _sign(test_keypair, _NEWER, evil_url, _SHA)
    manifest = {"version": _NEWER, "url": evil_url, "sha256": _SHA, "signature": sig, "notes": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.error == ERR_UNTRUSTED_URL


def test_check_rejects_old_prototype_path(test_keypair, monkeypatch):
    # The deleted prototype lived under /setra-cards/ — must NOT be trusted here.
    old_url = "https://portonsabaneta.com/setra-cards/x.zip"
    sig = _sign(test_keypair, _NEWER, old_url, _SHA)
    manifest = {"version": _NEWER, "url": old_url, "sha256": _SHA, "signature": sig, "notes": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.error == ERR_UNTRUSTED_URL


def test_check_rejects_bad_manifest_missing_fields(test_keypair, monkeypatch):
    manifest = {"version": "", "url": "", "sha256": "", "signature": "", "notes": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.error == ERR_BAD_MANIFEST


def test_check_rejects_invalid_sha_format(test_keypair, monkeypatch):
    manifest = {"version": _NEWER, "url": _URL, "sha256": "nothex", "signature": "x", "notes": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.error == ERR_BAD_MANIFEST


def test_check_network_error_returns_spanish_not_http_code(monkeypatch):
    def _raise(*args, **kwargs):
        raise urllib.error.HTTPError(
            url=updater.MANIFEST_URL, code=404, msg="Not Found", hdrs=None, fp=io.BytesIO(b"")
        )

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    result = check_for_update()
    assert result.has_update is False
    assert result.error == ERR_NETWORK
    assert "404" not in (result.error or "")


def test_check_connection_error_returns_spanish(monkeypatch):
    def _raise(*args, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _raise)
    result = check_for_update()
    assert result.error == ERR_NETWORK


def test_check_no_update_when_same_version(test_keypair, monkeypatch):
    sig = _sign(test_keypair, __version__, _URL, _SHA)
    manifest = {"version": __version__, "url": _URL, "sha256": _SHA, "signature": sig, "notes": ""}
    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen_returning(manifest))

    result = check_for_update()
    assert result.error is None
    assert result.has_update is False
    assert result.latest is not None
