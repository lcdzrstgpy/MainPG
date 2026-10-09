from __future__ import annotations

import hashlib
import io
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 64_000_000
_FORMAT_DETAILS = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "WEBP": ("image/webp", ".webp"),
}


@dataclass(frozen=True)
class StoredImage:
    relative_path: str
    content_type: str
    byte_size: int
    sha256: str
    width: int
    height: int


class PodAssetStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save_image(self, workspace_id: str, owner_user_id: str, content: bytes) -> StoredImage:
        content_type, suffix, width, height = self.inspect_image(content)
        digest = hashlib.sha256(content).hexdigest()
        scope = hashlib.sha256(f"{workspace_id}\0{owner_user_id}".encode()).hexdigest()[:24]
        parent = (self.root / scope / digest[:2]).resolve()
        self._require_managed(parent)
        parent.mkdir(parents=True, exist_ok=True)
        target = (parent / f"{digest}{suffix}").resolve()
        self._require_managed(target)
        temporary_path: Path | None = None
        if not target.exists():
            try:
                with tempfile.NamedTemporaryFile(dir=parent, prefix=f".{digest}.", suffix=".tmp", delete=False) as handle:
                    temporary_path = Path(handle.name)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if target.exists():
                    temporary_path.unlink(missing_ok=True)
                else:
                    os.replace(temporary_path, target)
                temporary_path = None
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("stored POD image does not match its content hash")
        return StoredImage(
            relative_path=target.relative_to(self.root).as_posix(),
            content_type=content_type,
            byte_size=len(content),
            sha256=digest,
            width=width,
            height=height,
        )

    def inspect_image(self, content: bytes) -> tuple[str, str, int, int]:
        return inspect_pod_image(content)

    def read(self, relative_path: str) -> bytes:
        target = (self.root / relative_path).resolve()
        self._require_managed(target)
        if not target.is_file():
            raise FileNotFoundError("POD asset is unavailable")
        return target.read_bytes()

    def path(self, relative_path: str) -> Path:
        target = (self.root / relative_path).resolve()
        self._require_managed(target)
        if not target.is_file():
            raise FileNotFoundError("POD asset is unavailable")
        return target

    def remove(self, relative_path: str) -> None:
        """Best-effort removal of a stored image, plus empty parent cleanup."""
        target = (self.root / relative_path).resolve()
        self._require_managed(target)
        try:
            target.unlink(missing_ok=True)
        except OSError:
            return
        parent = target.parent
        while parent != self.root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def _require_managed(self, path: Path) -> None:
        if path != self.root and self.root not in path.parents:
            raise ValueError("POD asset path is outside the managed root")


def inspect_pod_image(content: bytes) -> tuple[str, str, int, int]:
    """Validate bounded decoded raster content before storage or AI result use."""
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError("image must be between 1 byte and 20 MB")
    try:
        with Image.open(io.BytesIO(content)) as image:
            image_format = str(image.format or "").upper()
            details = _FORMAT_DETAILS.get(image_format)
            if details is None:
                raise ValueError("only PNG, JPEG, and WEBP images are supported")
            width, height = image.size
            if width < 16 or height < 16 or width * height > MAX_IMAGE_PIXELS:
                raise ValueError("image dimensions are outside the supported range")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("uploaded content is not a valid image") from exc
    return details[0], details[1], width, height
