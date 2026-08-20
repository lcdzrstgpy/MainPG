"""Build a signed incremental file patch for MainPG onedir updates.

Compares an old dist directory against a new dist directory and emits:
  - the changed files laid out by relative path (the upload payload), and
  - a signed patch-manifest.json (Ed25519) whose canonical payload matches
    wh_local.app_update.canonical_patch_payload.

Usage:
    python -m wh_local.runtime.patch_manifest_builder \
        --from-dir dist/MainPG-old \
        --to-dir   dist/MainPG \
        --from-version 1.1.0 \
        --to-version   1.2.0 \
        --file-base-url https://workbench.haocoming.top/mainpg/windows/patch/1.2.0 \
        --private-key-path /path/to/signing.key \
        --output-dir dist/patch-1.2.0
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .release_manifest import load_private_key

PATCH_CONTRACT_VERSION = "wh-patch-manifest-v1"
# Files regenerated on every build that must never travel inside a patch.
EXCLUDED_REL_PATHS = {"version.json", "MainPG-Updater.exe", "updates"}

_PATCH_PAYLOAD_FIELDS = (
    "contract_version",
    "from_version",
    "to_version",
    "published_at",
    "file_base_url",
    "files",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def collect_files(root: Path) -> dict[str, str]:
    """Map relative forward-slash paths to sha256 for every file under root."""
    result: dict[str, str] = {}
    if not root.is_dir():
        return result
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in EXCLUDED_REL_PATHS or rel.startswith("updates/"):
            continue
        result[rel] = file_sha256(path)
    return result


def canonical_patch_bytes(payload: dict[str, Any]) -> bytes:
    """Identical to wh_local.app_update.canonical_patch_payload."""
    return json.dumps(
        {name: payload[name] for name in _PATCH_PAYLOAD_FIELDS},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def build_patch(
    *,
    from_dir: Path,
    to_dir: Path,
    from_version: str,
    to_version: str,
    file_base_url: str,
    private_key: Ed25519PrivateKey,
    output_dir: Path,
) -> dict[str, Any]:
    old = collect_files(from_dir)
    new = collect_files(to_dir)
    relatives = sorted(set(old) | set(new))

    files: list[dict[str, Any]] = []
    copied: list[Path] = []
    for rel in relatives:
        if rel in old and rel in new:
            if old[rel] != new[rel]:
                files.append({"path": rel, "action": "replace", "sha256": new[rel], "size": (to_dir / rel).stat().st_size})
                copied.append(to_dir / rel)
        elif rel in new:
            files.append({"path": rel, "action": "add", "sha256": new[rel], "size": (to_dir / rel).stat().st_size})
            copied.append(to_dir / rel)
        elif rel in old:
            files.append({"path": rel, "action": "delete", "sha256": "", "size": 0})

    if not files:
        raise SystemExit("no file differences between the two dist directories")

    payload: dict[str, Any] = {
        "contract_version": PATCH_CONTRACT_VERSION,
        "from_version": from_version,
        "to_version": to_version,
        "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "file_base_url": file_base_url,
        "files": files,
    }
    manifest: dict[str, Any] = dict(payload)
    manifest["signature"] = base64.b64encode(private_key.sign(canonical_patch_bytes(payload))).decode("ascii")

    output_dir.mkdir(parents=True, exist_ok=True)
    for source in copied:
        target = output_dir / source.relative_to(to_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    (output_dir / "patch-manifest.json").write_bytes(canonical_patch_bytes(manifest) + b"\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a signed incremental patch for MainPG.")
    parser.add_argument("--from-dir", required=True, type=Path, help="previous dist/MainPG directory")
    parser.add_argument("--to-dir", required=True, type=Path, help="new dist/MainPG directory")
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    parser.add_argument("--file-base-url", required=True)
    parser.add_argument("--private-key-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    manifest = build_patch(
        from_dir=args.from_dir,
        to_dir=args.to_dir,
        from_version=args.from_version,
        to_version=args.to_version,
        file_base_url=args.file_base_url,
        private_key=load_private_key(args.private_key_path),
        output_dir=args.output_dir,
    )
    print(json.dumps({"to_version": manifest["to_version"], "files": len(manifest["files"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
