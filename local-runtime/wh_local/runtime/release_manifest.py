"""Canonical Ed25519 signing for MainPG update release manifests."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

UNSIGNED_FIELDS = (
    "version",
    "mandatory",
    "installer_url",
    "sha256",
    "release_notes",
    "published_at",
)


def canonical_manifest_bytes(payload: dict[str, Any]) -> bytes:
    """Return the stable UTF-8 JSON representation used as signing input."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def create_signed_manifest(
    *,
    version: str,
    mandatory: bool,
    installer_url: str,
    sha256: str,
    release_notes: str,
    published_at: str,
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    """Construct the public manifest and sign every field except ``signature``."""
    manifest: dict[str, Any] = {
        "version": version,
        "mandatory": mandatory,
        "installer_url": installer_url,
        "sha256": sha256,
        "release_notes": release_notes,
        "published_at": published_at,
    }
    manifest["signature"] = base64.b64encode(private_key.sign(canonical_manifest_bytes(manifest))).decode("ascii")
    return manifest


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load an Ed25519 key from PEM or a base64-encoded 32-byte seed file."""
    raw = path.read_bytes().strip()
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except ValueError:
        try:
            key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw, validate=True))
        except (ValueError, TypeError) as error:
            raise ValueError("signing key must be an Ed25519 PEM or base64 32-byte seed") from error
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing key is not an Ed25519 private key")
    return key


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a signed MainPG release manifest.")
    parser.add_argument("--version", required=True)
    parser.add_argument("--mandatory", choices=("true", "false"), default="false")
    parser.add_argument("--installer-url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--release-notes", default="")
    parser.add_argument("--published-at", required=True)
    parser.add_argument("--private-key-path", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    manifest = create_signed_manifest(
        version=args.version,
        mandatory=args.mandatory == "true",
        installer_url=args.installer_url,
        sha256=args.sha256,
        release_notes=args.release_notes,
        published_at=args.published_at,
        private_key=load_private_key(args.private_key_path),
    )
    args.output.write_bytes(canonical_manifest_bytes(manifest) + b"\n")


if __name__ == "__main__":
    main()
