from __future__ import annotations

import hashlib
import os
import warnings
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError


MAX_PREVIEW_IMAGE_BYTES = 25 * 1024 * 1024
MAX_PREVIEW_IMAGE_PIXELS = 40_000_000
# 预检卡片只展示小图；固定几档宽度，避免同一张图生成无数尺寸的缓存。
THUMBNAIL_WIDTHS = (160, 320, 640)
_FORMAT_METADATA = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}


@dataclass(frozen=True)
class DecodedPreviewImage:
    content: bytes
    content_hash: str
    content_type: str
    suffix: str
    width: int
    height: int


def _first_frame_as_png(image: Image.Image) -> tuple[bytes, str, str]:
    """Re-encode the first frame of an image as a single-frame PNG.

    Returns ``(payload, content_type, suffix)``.  Keeps alpha (RGBA) only when
    the source frame actually carries transparency, otherwise flattens to RGB to
    keep product photos small.
    """
    alpha = image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )
    frame = image.convert("RGBA") if alpha else image.convert("RGB")
    output = BytesIO()
    frame.save(output, format="PNG")
    return bytes(output.getvalue()), "image/png", ".png"


def validate_preview_image(
    content: bytes,
    declared_content_type: str,
) -> DecodedPreviewImage:
    """Decode a supported preview image into a single, persisted frame.

    JPEG, PNG and WebP are accepted verbatim (single-frame only).  ``GIF`` —
    including animated sources — is normalized to its first frame as PNG so that
    remote-source GIFs can materialize into the media library instead of failing
    permanently on the "JPEG, PNG or WebP" guard.
    """

    if not content or len(content) > MAX_PREVIEW_IMAGE_BYTES:
        raise ValueError("preview image must be between 1 byte and 25 MiB")
    raw = bytes(content)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(BytesIO(raw))
        with image:
            image_format = str(image.format or "").upper()
            width, height = (int(value) for value in image.size)
            if (
                width <= 0
                or height <= 0
                or width * height > MAX_PREVIEW_IMAGE_PIXELS
            ):
                raise ValueError("preview image exceeds the 40 MP limit")

            if image_format == "GIF":
                payload, expected_type, suffix = _first_frame_as_png(image)
            else:
                if image_format not in _FORMAT_METADATA:
                    raise ValueError("preview image must be JPEG, PNG or WebP")
                frame_count = int(getattr(image, "n_frames", 1))
                if frame_count != 1:
                    raise ValueError("preview image must be single-frame")
                # `load` forces complete pixel decoding so truncated/corrupt
                # payloads cannot be registered from header metadata alone.
                image.load()
                expected_type, suffix = _FORMAT_METADATA[image_format]
                payload = raw
    except ValueError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("preview image exceeds the 40 MP limit") from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("preview image could not be fully decoded") from exc

    # Only enforce the declared/decoded media-type match for formats accepted
    # verbatim; a GIF is deliberately re-encoded to PNG so its original
    # declared type (image/gif) no longer describes the stored payload.
    if image_format != "GIF":
        declared = str(declared_content_type or "").split(";", 1)[0].strip().casefold()
        if declared and declared != expected_type:
            raise ValueError("preview image content type does not match decoded bytes")
    return DecodedPreviewImage(
        content=payload,
        content_hash=hashlib.sha256(payload).hexdigest(),
        content_type=expected_type,
        suffix=suffix,
        width=width,
        height=height,
    )


def normalize_thumbnail_width(value: int | None) -> int | None:
    """把请求宽度对齐到固定档位；不传则不生成缩略图。"""
    if value is None:
        return None
    requested = int(value)
    return min(THUMBNAIL_WIDTHS, key=lambda width: (abs(width - requested), width))


def thumbnail_file_path(source: Path, width: int) -> Path:
    """缩略图与原图同目录（该目录已由 assets 校验过归属），文件名带宽度。"""
    return source.with_name(f"{source.stem}.thumb{int(width)}.jpg")


def ensure_preview_thumbnail(source: Path, width: int) -> Path:
    """把本地素材缩成固定宽度 JPEG 并落盘缓存。

    预检卡片一次要渲染几十上百张图，原图动辄几 MB，浏览器解码会明显卡顿；
    缩略图生成失败（损坏/超大图）时回退原图，不能因为一张坏图让整页报错。
    """
    target = thumbnail_file_path(source, width)
    try:
        if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
    except OSError:
        return source
    tmp = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
    try:
        with Image.open(source) as image:
            image.thumbnail((int(width), int(width) * 8))
            has_alpha = image.mode in {"RGBA", "LA", "PA"} or (
                image.mode == "P" and "transparency" in image.info
            )
            if has_alpha:
                rgba = image.convert("RGBA")
                frame = Image.new("RGB", rgba.size, (255, 255, 255))
                frame.paste(rgba, mask=rgba.split()[-1])
            else:
                frame = image.convert("RGB")
            frame.save(tmp, format="JPEG", quality=80, optimize=True)
        os.replace(tmp, target)
        return target
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        with suppress(OSError):
            tmp.unlink()
        return source

