"""GitHub fallback when the signed manifest channel is not published."""
import io
import json
import urllib.error

import pytest

from setra_cards.services import updater

SHA = "a" * 64


def _release(url: str, body: str = f"sha256: {SHA}") -> dict:
    return {
        "tag_name": "v9.9.9",
        "body": body,
        "assets": [{"name": "setra-cards-v9.9.9-update.zip", "browser_download_url": url}],
    }


@pytest.fixture
def fake_net(monkeypatch):
    responses: dict = {}

    def urlopen(req, timeout=None, context=None):
        payload = responses[req.full_url]
        if isinstance(payload, Exception):
            raise payload
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(updater.urllib.request, "urlopen", urlopen)
    return responses


def _manifest_404():
    return urllib.error.HTTPError(updater.MANIFEST_URL, 404, "Not Found", {}, None)


def test_missing_manifest_falls_back_to_github_release(fake_net):
    fake_net[updater.MANIFEST_URL] = _manifest_404()
    fake_net[updater.GITHUB_LATEST_URL] = _release(
        updater.GITHUB_DOWNLOAD_PREFIX + "v9.9.9/setra-cards-v9.9.9-update.zip"
    )
    result = updater.check_for_update()
    assert result.error is None
    assert result.has_update
    assert result.latest.source == "github"
    assert result.latest.sha256 == SHA


def test_github_asset_outside_the_repo_is_rejected(fake_net):
    fake_net[updater.MANIFEST_URL] = _manifest_404()
    fake_net[updater.GITHUB_LATEST_URL] = _release("https://evil.example/setra-cards.zip")
    result = updater.check_for_update()
    assert result.latest is None
    assert result.error == updater.ERR_UNTRUSTED_URL


def test_github_release_without_sha256_is_rejected(fake_net):
    fake_net[updater.MANIFEST_URL] = _manifest_404()
    fake_net[updater.GITHUB_LATEST_URL] = _release(
        updater.GITHUB_DOWNLOAD_PREFIX + "v9.9.9/setra-cards-v9.9.9-update.zip", body="sin hash"
    )
    result = updater.check_for_update()
    assert result.latest is None
    assert result.error == updater.ERR_BAD_MANIFEST


def test_github_info_skips_signature_but_keeps_prefix_guard():
    info = updater.UpdateInfo(
        version="9.9.9", asset_url="https://evil.example/x.zip",
        asset_name="x.zip", sha256=SHA, notes="", source="github",
    )
    with pytest.raises(ValueError, match="no confiable"):
        updater.apply_update(info)
