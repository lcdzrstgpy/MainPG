"""combo_kit 本地受管资源（原图/成品图落盘，独立于其他模块目录）。"""
from __future__ import annotations

import hashlib
from pathlib import Path


class ComboKitAssets:
    """组合套装的本地图床：原图 + 6 张成品图，全部取自独立目录。

    不写入 product_processing 的 assets（assets/outputs），因此不参与
    /pp-media 图床挂载；本模块通过独立静态路由按 workspace 受限提供。
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.original_root = self.root / "originals"
        self.generated_root = self.root / "generated"
        for path in (self.original_root, self.generated_root):
            path.mkdir(parents=True, exist_ok=True)

    def _suffix(self, filename: str, content_type: str) -> str:
        safe = Path(str(filename or "")).suffix.lower()
        if safe in {".jpg", ".jpeg", ".png", ".webp"}:
            return safe
        if "png" in (content_type or ""):
            return ".png"
        if "webp" in (content_type or ""):
            return ".webp"
        return ".jpg"

    def save_original(
        self,
        content: bytes,
        filename: str,
        content_type: str = "",
        *,
        workspace_id: str = "local",
    ) -> dict[str, str]:
        if not content:
            raise ValueError("原始图片内容为空")
        digest = hashlib.sha256(content).hexdigest()
        suffix = self._suffix(filename, content_type)
        workspace_root = self._workspace_root(workspace_id, "originals")
        path = workspace_root / digest[:2] / f"{digest}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        return {"path": str(path), "sha256": digest, "suffix": suffix}

    def save_generated(
        self,
        content: bytes,
        *,
        stage: str,
        set_id: str,
        suffix: str = ".jpg",
        workspace_id: str = "local",
    ) -> str:
        if not content:
            raise ValueError("生成图片内容为空")
        safe_suffix = suffix if suffix in {".jpg", ".jpeg", ".png", ".webp"} else ".jpg"
        safe_stage = "".join(ch for ch in str(stage) if ch.isalnum() or ch in {"_", "-"}) or "generated"
        safe_set = "".join(ch for ch in str(set_id) if ch.isalnum() or ch in {"_", "-"}) or "set"
        workspace_root = self._workspace_root(workspace_id, "generated")
        set_root = workspace_root / safe_set
        set_root.mkdir(parents=True, exist_ok=True)
        path = set_root / f"{safe_stage}{safe_suffix}"
        path.write_bytes(content)
        return str(path)

    def require_original(self, raw_path: str, *, workspace_id: str) -> Path:
        return self._require_workspace_path(raw_path, workspace_id, "originals")

    def require_generated(self, raw_path: str, *, workspace_id: str) -> Path:
        return self._require_workspace_path(raw_path, workspace_id, "generated")

    def _workspace_root(self, workspace_id: str, kind: str) -> Path:
        safe_workspace = "".join(ch for ch in str(workspace_id) if ch.isalnum() or ch in {"_", "-"}) or "local"
        root = (self.root / kind / safe_workspace).resolve()
        base = (self.root / kind).resolve()
        if base != root and base not in root.parents:
            raise ValueError("workspace path escapes managed root")
        return root

    def _require_workspace_path(self, raw_path: str, workspace_id: str, kind: str) -> Path:
        if not raw_path or "://" in raw_path:
            raise ValueError("asset must be a managed local path")
        workspace_root = self._workspace_root(workspace_id, kind)
        path = Path(raw_path).resolve()
        if workspace_root != path and workspace_root not in path.parents:
            raise ValueError("asset is outside the workspace root")
        if not path.is_file():
            raise FileNotFoundError("asset does not exist")
        return path
