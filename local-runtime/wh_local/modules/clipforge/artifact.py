"""Resolve the one immutable ClipForge app root for both development and installs.

The sidecar entry point is always ``<app_root>/server.js``. Development runs and
installed runs therefore differ only in how ``app_root`` is found:

1. ``WH_CLIPFORGE_APP_ROOT`` (explicit override, used by tests and tooling);
2. ``<install_root>/clipforge/app`` (the installer copies a verified artifact here);
3. ``<data_root>/clipforge/current.json`` pointing below ``<data_root>/clipforge/artifacts``.

Anything else is reported as ``missing``/``invalid`` with a stable, path-free
message so the HTTP layer never leaks absolute paths into a browser response.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ClipForgeBuildState = Literal["available", "missing", "invalid"]

APP_ROOT_ENV = "WH_CLIPFORGE_APP_ROOT"

_METADATA_FILE = "mainpg-sidecar.json"

# 与 Node 侧校验器保持同一子集：入口、清单与运行期目录，缺一不可。
_REQUIRED_ENTRIES = (
    "server.js",
    ".next/BUILD_ID",
    ".next/required-server-files.json",
    ".next/server/middleware-manifest.json",
    "node_modules/next/package.json",
    "public",
    ".next/static",
    "drizzle",
)

_MISSING_MESSAGE = "AI 视频服务尚未安装，请先构建并发布 sidecar 产物"
_INCOMPLETE_MESSAGE = "AI 视频服务产物不完整，请重新构建并发布"
_INVALID_MESSAGE = "AI 视频服务产物无效，请重新构建并发布"
_RUNTIME_MESSAGE = "AI 视频服务产物运行时不匹配（需要 Node ABI 产物），请重新构建并发布"
_READY_MESSAGE = "AI 视频服务产物已就绪"


@dataclass(frozen=True)
class ClipForgeBuild:
    state: ClipForgeBuildState
    app_root: Path | None
    artifact_id: str | None
    message: str


def resolve_clipforge_build(install_root: Path, data_root: Path) -> ClipForgeBuild:
    """Locate the deploy root that the sidecar must start from."""
    override = os.environ.get(APP_ROOT_ENV, "").strip()
    if override:
        return _inspect(Path(override))

    packaged = Path(install_root) / "clipforge" / "app"
    if (packaged / "server.js").is_file():
        return _inspect(packaged)

    pointer = Path(data_root) / "clipforge" / "current.json"
    if pointer.is_file():
        return _resolve_from_pointer(pointer)

    return ClipForgeBuild("missing", None, None, _MISSING_MESSAGE)


def _resolve_from_pointer(pointer: Path) -> ClipForgeBuild:
    artifacts_root = (pointer.parent / "artifacts").resolve()
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ClipForgeBuild("invalid", None, None, _INVALID_MESSAGE)
    if not isinstance(payload, dict):
        return ClipForgeBuild("invalid", None, None, _INVALID_MESSAGE)

    relative_path = payload.get("relativePath")
    if not isinstance(relative_path, str) or not relative_path.strip():
        return ClipForgeBuild("invalid", None, None, _INVALID_MESSAGE)

    candidate = (pointer.parent / relative_path).resolve()
    # 指针不得逃出 artifacts 根：越界指针会指向源码树或任意目录，必须判为无效。
    if candidate != artifacts_root and artifacts_root not in candidate.parents:
        return ClipForgeBuild("invalid", None, None, _INVALID_MESSAGE)

    return _inspect(candidate)


def _inspect(root: Path) -> ClipForgeBuild:
    root = Path(root)
    if not root.is_dir():
        return ClipForgeBuild("missing", None, None, _MISSING_MESSAGE)

    if any(not (root / entry).exists() for entry in _REQUIRED_ENTRIES):
        return ClipForgeBuild("invalid", None, None, _INCOMPLETE_MESSAGE)

    try:
        metadata = json.loads((root / _METADATA_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ClipForgeBuild("invalid", None, None, _INVALID_MESSAGE)
    if not isinstance(metadata, dict):
        return ClipForgeBuild("invalid", None, None, _INVALID_MESSAGE)
    # MainPG sidecar 必须保持 Node ABI；Electron ABI 产物绝不能当作 sidecar 启动。
    if metadata.get("runtime") != "node":
        return ClipForgeBuild("invalid", None, None, _RUNTIME_MESSAGE)

    artifact_id = metadata.get("artifactId")
    if not isinstance(artifact_id, str) or not artifact_id:
        return ClipForgeBuild("invalid", None, None, _INVALID_MESSAGE)

    return ClipForgeBuild("available", root.resolve(), artifact_id, _READY_MESSAGE)
