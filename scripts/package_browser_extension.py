#!/usr/bin/env python3
"""Package a browser extension version directory into an installable zip.

The archive keeps manifest.json at the root so Chrome/Edge can load it directly,
and is the artifact uploaded to the update-admin plugin publishing channel.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_PREFIX = "W-H-browser-extension"


def find_latest_source(prefix: str) -> Path:
    candidates = sorted(
        (
            path
            for path in REPO.glob(f"{prefix}-v*")
            if path.is_dir() and (path / "manifest.json").is_file()
        ),
        key=lambda path: path.name,
    )
    if not candidates:
        raise SystemExit(f"未找到插件版本目录：{prefix}-v*/manifest.json")
    return candidates[-1]


def read_manifest(source: Path) -> dict:
    try:
        data = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"读取 manifest.json 失败：{exc}") from exc
    if data.get("manifest_version") != 3:
        raise SystemExit("manifest.json 的 manifest_version 必须为 3")
    version = str(data.get("version") or "")
    if not version or not source.name.endswith(f"-v{version}"):
        raise SystemExit(f"目录名 {source.name} 与 manifest 版本 {version!r} 不一致")
    return data


def build_zip(source: Path, output: Path) -> None:
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_dir():
                continue
            archive.write(path, path.relative_to(source).as_posix())


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="打包浏览器插件为可发布 zip")
    parser.add_argument(
        "source",
        nargs="?",
        help="插件版本目录，默认取仓库内最新 W-H-browser-extension-v* 目录",
    )
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="zip 文件名前缀")
    parser.add_argument("--output-dir", default=str(REPO), help="zip 输出目录")
    args = parser.parse_args()

    source = Path(args.source).resolve() if args.source else find_latest_source(args.prefix)
    manifest = read_manifest(source)
    version = manifest["version"]
    output = Path(args.output_dir).resolve() / f"{args.prefix}-v{version}.zip"
    build_zip(source, output)

    print(f"source : {source}")
    print(f"name   : {manifest.get('name')}")
    print(f"version: {version}")
    print(f"output : {output}")
    print(f"size   : {output.stat().st_size}")
    print(f"sha256 : {sha256_of(output)}")


if __name__ == "__main__":
    main()
