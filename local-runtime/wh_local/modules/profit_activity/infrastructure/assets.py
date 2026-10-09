from __future__ import annotations

import re
import tempfile
import uuid
from pathlib import Path


def save_asset(root: Path, *, site: str, skc: str, kind: str, filename: str, content: bytes) -> str:
    if not content:
        return ""
    safe_skc = re.sub(r"[^A-Za-z0-9_-]+", "_", skc).strip("_") or "unknown"
    extension = Path(filename or "image.bin").suffix.lower()[:12] or ".bin"
    target = root / "assets" / site / safe_skc / kind
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{uuid.uuid4().hex}{extension}"
    path.write_bytes(content)
    return str(path)


def resolve_asset(path: str, *, require_managed: bool = False) -> Path:
    """把持久化的路径解析成可读文件。

    require_managed=True 时额外拒绝受管目录之外的文件。必须开启这个开关的
    场景：路径来自用户可控字段（如 ``source_groups_json.image_paths``），
    直接交给 FileResponse 就等于把服务进程能读到的任意本地文件（主库、
    密钥、其它工作区数据）暴露出去。

    判据刻意**只看"是否位于某层 assets 目录树下"，不绑定具体 root**：
    ``_asset_root`` 来自用户可配置的 save_root，若按当前 root 做前缀校验，
    用户改过输出目录后历史图片会全部失效。save_asset 的产物结构是
    ``<root>/assets/<site>/<skc>/<kind>/<file>``，故要求 assets 之后至少
    还有两级，可同时挡住裸文件路径与 ``..`` 穿越。
    """
    if not path:
        raise ValueError("image_not_found")
    candidate = Path(path)
    if require_managed:
        try:
            candidate = candidate.resolve()
        except (OSError, ValueError, RuntimeError) as exc:
            raise ValueError("image_not_found") from exc
        parts = candidate.parts
        marker = "assets"
        if marker not in parts or len(parts) - parts.index(marker) < 3:
            raise ValueError("image_not_found")
    if not candidate.is_file():
        raise ValueError("image_not_found")
    return candidate


def ensure_writable_directory(path: Path) -> Path:
    """Create and probe a user-configured local output directory."""
    directory = path.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    probe: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".profit_activity_", delete=False) as handle:
            probe = Path(handle.name)
            handle.write(b"ok")
    finally:
        if probe is not None:
            try:
                probe.unlink()
            except FileNotFoundError:
                pass
    return directory
