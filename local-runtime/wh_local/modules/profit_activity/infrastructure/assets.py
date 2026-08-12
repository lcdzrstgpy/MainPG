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


def resolve_asset(path: str) -> Path:
    candidate = Path(path)
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
