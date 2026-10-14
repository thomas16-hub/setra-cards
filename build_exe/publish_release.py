"""Build, sign and publish a Setra CARDS release on BOTH update channels.

    python build_exe/publish_release.py 2.1.3 --notes "Qué cambia (en español)"
    python build_exe/publish_release.py 2.1.3 --notes "..." --dry-run

Steps (any failure stops before anything is published):
 1. Check src/setra_cards/__init__.py already says the requested version.
 2. Run the test suite.
 3. Build with PyInstaller and package setra-cards-vX.Y.Z-update.zip
    (top folder Setra-CARDS/, plus Desinstalar.bat and Reset_PIN_Admin.bat).
 4. Sign f"{version}|{url}|{sha256}" with the OFFLINE Ed25519 key read from
    ~/.claude/credentials/setra-cards-updater-ed25519.md (never stored in the repo)
    and check it against the public key embedded in the app.
 5. FTP: upload the ZIP, check its size, THEN latest.json (the manifest must
    never point at a file that is not fully uploaded).
 6. Re-read latest.json over HTTPS and run the app's own manifest check.
 7. GitHub release vX.Y.Z marked latest, sha256 in the body: the channel that
    v2.0.10 PCs (and v2.1.2+ as fallback) read.

The GitHub API is only reachable by the apps while the repo is public: make it
public for the update window and private again afterwards.
"""
from __future__ import annotations

import argparse
import ftplib
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, r"C:\Users\tomas\Desktop\CLAUDIO\proyectos\hoteles-dashboard\scripts")

from setra_cards import __version__  # noqa: E402
from setra_cards.services import updater  # noqa: E402

KEY_FILE = Path.home() / ".claude" / "credentials" / "setra-cards-updater-ed25519.md"
REMOTE_DIR = "/public_html/setra-cards-flet"
REPO = "thomas16-hub/setra-cards"
EXTRA_FILES = ("Desinstalar.bat", "Reset_PIN_Admin.bat")


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def build_zip(version: str, work: Path) -> Path:
    dist, build = work / "dist", work / "work"
    run([sys.executable, "-m", "PyInstaller", "build_exe/setra-cards.spec", "--noconfirm",
         "--clean", "--distpath", str(dist), "--workpath", str(build)])
    pkg = work / "pkg"
    shutil.copytree(dist / "Setra-CARDS", pkg / "Setra-CARDS")
    for name in EXTRA_FILES:
        shutil.copy2(ROOT / "build_exe" / name, pkg / "Setra-CARDS" / name)
    base = work / f"setra-cards-v{version}-update"
    return Path(shutil.make_archive(str(base), "zip", root_dir=pkg, base_dir="Setra-CARDS"))


def sign(version: str, url: str, sha: str) -> str:
    from Crypto.Signature import eddsa

    text = KEY_FILE.read_text(encoding="utf-8")
    match = re.search(r"privada.*?```\s*([0-9a-f]{64})\s*```", text, re.S)
    if not match:
        sys.exit(f"Private key not found in {KEY_FILE}")
    key = eddsa.import_private_key(bytes.fromhex(match.group(1)))
    signature = eddsa.new(key, "rfc8032").sign(f"{version}|{url}|{sha}".encode()).hex()
    if not updater._verify_manifest_signature(version, url, sha, signature):
        sys.exit("Signature does not match the public key embedded in the app")
    return signature


def upload(zip_path: Path, manifest: dict) -> None:
    from _creds import load_cpanel

    host, user, pwd = load_cpanel()
    ftp = ftplib.FTP(host, timeout=60)
    ftp.login(user, pwd)
    ftp.set_pasv(True)
    try:
        ftp.mkd(REMOTE_DIR)
    except ftplib.error_perm:
        pass
    ftp.cwd(REMOTE_DIR)
    with zip_path.open("rb") as f:
        ftp.storbinary(f"STOR {zip_path.name}", f, blocksize=1 << 16)
    if ftp.size(zip_path.name) != zip_path.stat().st_size:
        sys.exit("Uploaded ZIP size mismatch: latest.json NOT updated")
    body = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
    ftp.storbinary("STOR latest.json", io.BytesIO(body))
    ftp.quit()


def verify_live(version: str) -> None:
    # Same headers as the app: the host answers 406 to Python's default UA.
    req = urllib.request.Request(
        updater.MANIFEST_URL,
        headers={"User-Agent": f"SetraCARDS/{__version__}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30, context=updater._SSL_CTX) as resp:  # nosec B310
        live = json.loads(resp.read().decode("utf-8"))
    if live.get("version") != version:
        sys.exit(f"Live manifest says {live.get('version')}, expected {version}")
    result = updater._check_manifest()
    if result.error or not result.latest or result.latest.version != version:
        sys.exit(f"App manifest check failed: {result.error}")
    print("Live manifest verified:", version)


def github_release(version: str, zip_path: Path, sha: str, notes: str) -> None:
    body = f"## Setra CARDS v{version}\n\n{notes}\n\nLos datos del hotel se conservan.\n\nsha256: {sha}\n"
    notes_file = zip_path.with_suffix(".md")
    notes_file.write_text(body, encoding="utf-8")
    run(["gh", "release", "create", f"v{version}", str(zip_path), "--repo", REPO,
         "--latest", "--title", f"v{version}", "--notes-file", str(notes_file)])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", help="X.Y.Z, must match setra_cards.__version__")
    parser.add_argument("--notes", required=True, help="Operator-facing notes (Spanish)")
    parser.add_argument("--dry-run", action="store_true", help="Build and sign, publish nothing")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    if args.version != __version__:
        sys.exit(f"__version__ is {__version__}; bump it to {args.version} first")
    if not args.skip_tests:
        run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])

    work = Path(tempfile.mkdtemp(prefix=f"setra-release-{args.version}-"))
    zip_path = build_zip(args.version, work)
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()
    url = updater.TRUSTED_DOWNLOAD_PREFIX + zip_path.name
    manifest = {
        "version": args.version,
        "url": url,
        "sha256": sha,
        "signature": sign(args.version, url, sha),
        "notes": args.notes,
    }
    print(f"Built {zip_path} ({zip_path.stat().st_size} bytes) sha256={sha}")
    if args.dry_run:
        print("Dry run: nothing published")
        return

    upload(zip_path, manifest)
    verify_live(args.version)
    github_release(args.version, zip_path, sha, args.notes)
    print("Published on the signed channel and on GitHub.")


if __name__ == "__main__":
    main()
