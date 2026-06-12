"""Build the signed update manifest (latest.json) for Setra CARDS.

The update channel is a signed JSON manifest hosted on portonsabaneta.com. The
Ed25519 PRIVATE key stays OFFLINE — this script NEVER reads, references, or
embeds it. The operator signs the canonical message by hand and pastes the
resulting signature back in.

Two-step workflow:

  1) Print the canonical message to sign (computes the ZIP SHA-256 for you):

       python build_exe/make_manifest.py prepare \\
           --version 2.1.0 \\
           --zip dist/setra-cards-v2.1.0.zip

     Sign that exact message OFFLINE with the private key in
     ~/.claude/credentials (Ed25519, RFC 8032). The result is 64 bytes / 128 hex.

  2) Emit latest.json with the signature you produced:

       python build_exe/make_manifest.py build \\
           --version 2.1.0 \\
           --zip dist/setra-cards-v2.1.0.zip \\
           --signature <128-hex> \\
           --notes "Correcciones varias" \\
           --out dist/latest.json

  3) Upload BOTH the .zip and latest.json by FTP to /setra-cards-flet/ on
     portonsabaneta.com (the directory the app polls; distinct from the old
     /setra-cards/ prototype tree). Do NOT upload the private key anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# Must match setra_cards.services.updater.TRUSTED_DOWNLOAD_PREFIX.
TRUSTED_DOWNLOAD_PREFIX = "https://portonsabaneta.com/setra-cards-flet/"


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def _canonical_message(version: str, url: str, sha256: str) -> str:
    return f"{version}|{url}|{sha256}"


def _url_for(zip_path: Path) -> str:
    return TRUSTED_DOWNLOAD_PREFIX + zip_path.name


def cmd_prepare(args: argparse.Namespace) -> int:
    zip_path = Path(args.zip)
    if not zip_path.is_file():
        print(f"ERROR: no existe el ZIP: {zip_path}", file=sys.stderr)
        return 2
    sha = _sha256(zip_path)
    url = _url_for(zip_path)
    message = _canonical_message(args.version, url, sha)
    print("SHA-256:", sha)
    print("URL:    ", url)
    print()
    print("Firmar OFFLINE (Ed25519 RFC 8032) EXACTAMENTE este mensaje:")
    print("-" * 60)
    print(message)
    print("-" * 60)
    print("La firma resultante son 64 bytes = 128 hex.")
    print("La llave privada vive SOLO en ~/.claude/credentials — nunca la subas.")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    zip_path = Path(args.zip)
    if not zip_path.is_file():
        print(f"ERROR: no existe el ZIP: {zip_path}", file=sys.stderr)
        return 2
    signature = args.signature.strip().lower()
    if not re.fullmatch(r"[a-f0-9]{128}", signature):
        print("ERROR: signature debe ser 128 hex (64 bytes Ed25519)", file=sys.stderr)
        return 2
    sha = _sha256(zip_path)
    url = _url_for(zip_path)
    manifest = {
        "version": args.version,
        "url": url,
        "sha256": sha,
        "signature": signature,
        "notes": args.notes or "",
    }
    out = Path(args.out)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"OK: {out} generado.")
    print(f"  version:   {manifest['version']}")
    print(f"  url:       {manifest['url']}")
    print(f"  sha256:    {manifest['sha256']}")
    print("Subir por FTP el .zip y latest.json a /setra-cards-flet/ en portonsabaneta.com")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the signed update manifest")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prepare", help="Print the canonical message to sign offline")
    p_prep.add_argument("--version", required=True)
    p_prep.add_argument("--zip", required=True)
    p_prep.set_defaults(func=cmd_prepare)

    p_build = sub.add_parser("build", help="Emit latest.json with the offline signature")
    p_build.add_argument("--version", required=True)
    p_build.add_argument("--zip", required=True)
    p_build.add_argument("--signature", required=True, help="128-hex Ed25519 signature")
    p_build.add_argument("--notes", default="")
    p_build.add_argument("--out", default="dist/latest.json")
    p_build.set_defaults(func=cmd_build)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
