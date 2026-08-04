from __future__ import annotations

import re
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


def resolve_asset(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_file():
        raise ValueError("image_not_found")
    return candidate
