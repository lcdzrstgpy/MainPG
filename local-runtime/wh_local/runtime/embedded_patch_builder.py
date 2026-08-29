"""Append a server-verifiable incremental patch payload to an Inno Setup EXE.

Python's zipfile append mode supports a non-ZIP prefix, so the regular Inno
installer remains the executable prefix while the release server can open the
same file as a ZIP and read the final patch payload. The server never trusts
the descriptor hashes blindly: it streams and recalculates every payload hash
before signing the public patch manifest with the server-only Ed25519 key.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any

from .patch_manifest_builder import collect_files


EMBEDDED_PATCH_CONTRACT_VERSION = "mainpg-embedded-patch-v1"
EMBEDDED_PATCH_COMMENT = b"MAINPG_EMBEDDED_PATCH_V1"
EMBEDDED_PATCH_ROOT = "mainpg-patch"
EMBEDDED_PATCH_DESCRIPTOR = f"{EMBEDDED_PATCH_ROOT}/descriptor.json"


def build_descriptor(
    *,
    from_dir: Path,
    to_dir: Path,
    from_version: str,
    to_version: str,
) -> tuple[dict[str, Any], list[Path]]:
    old = collect_files(from_dir)
    new = collect_files(to_dir)
    entries: list[dict[str, Any]] = []
    payloads: list[Path] = []
    for relative in sorted(set(old) | set(new)):
        if relative in old and relative in new and old[relative] == new[relative]:
            continue
        if relative not in new:
            entries.append({"path": relative, "action": "delete", "sha256": "", "size": 0})
            continue
        source = to_dir / relative
        size = source.stat().st_size
        if size <= 0:
            raise RuntimeError(f"embedded patch does not support empty files: {relative}")
        entries.append(
            {
                "path": relative,
                "action": "add" if relative not in old else "replace",
                "sha256": new[relative],
                "size": size,
            }
        )
        payloads.append(source)
    if not entries:
        raise RuntimeError("no file differences available for the embedded patch")
    return {
        "contract_version": EMBEDDED_PATCH_CONTRACT_VERSION,
        "from_version": from_version,
        "to_version": to_version,
        "files": entries,
    }, payloads


def append_embedded_patch(
    *,
    installer: Path,
    from_dir: Path,
    to_dir: Path,
    from_version: str,
    to_version: str,
) -> dict[str, Any]:
    descriptor, payloads = build_descriptor(
        from_dir=from_dir,
        to_dir=to_dir,
        from_version=from_version,
        to_version=to_version,
    )
    with zipfile.ZipFile(
        installer,
        mode="a",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        archive.comment = EMBEDDED_PATCH_COMMENT
        archive.writestr(
            EMBEDDED_PATCH_DESCRIPTOR,
            json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        for source in payloads:
            relative = source.relative_to(to_dir).as_posix()
            archive.write(source, f"{EMBEDDED_PATCH_ROOT}/files/{relative}")
    return descriptor


def main() -> None:
    parser = argparse.ArgumentParser(description="Append an incremental patch payload to a MainPG installer.")
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--from-dir", required=True, type=Path)
    parser.add_argument("--to-dir", required=True, type=Path)
    parser.add_argument("--from-version", required=True)
    parser.add_argument("--to-version", required=True)
    args = parser.parse_args()
    descriptor = append_embedded_patch(
        installer=args.installer,
        from_dir=args.from_dir,
        to_dir=args.to_dir,
        from_version=args.from_version,
        to_version=args.to_version,
    )
    print(json.dumps({"from_version": descriptor["from_version"], "to_version": descriptor["to_version"], "files": len(descriptor["files"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
