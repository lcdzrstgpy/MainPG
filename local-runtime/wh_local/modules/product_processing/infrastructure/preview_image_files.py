from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, UnidentifiedImageError


MAX_PREVIEW_IMAGE_BYTES = 25 * 1024 * 1024
MAX_PREVIEW_IMAGE_PIXELS = 40_000_000
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


def validate_preview_image(
    content: bytes,
    declared_content_type: str,
) -> DecodedPreviewImage:
    """Fully decode a supported, single-frame preview image without rewriting it."""

    if not content or len(content) > MAX_PREVIEW_IMAGE_BYTES:
        raise ValueError("preview image must be between 1 byte and 25 MiB")
    raw = bytes(content)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(BytesIO(raw))
        with image:
            image_format = str(image.format or "").upper()
            if image_format not in _FORMAT_METADATA:
                raise ValueError("preview image must be JPEG, PNG or WebP")
            width, height = (int(value) for value in image.size)
            if (
                width <= 0
                or height <= 0
                or width * height > MAX_PREVIEW_IMAGE_PIXELS
            ):
                raise ValueError("preview image exceeds the 40 MP limit")
            frame_count = int(getattr(image, "n_frames", 1))
            if frame_count != 1:
                raise ValueError("preview image must be single-frame")
            # `load` forces complete pixel decoding so truncated/corrupt payloads
            # cannot be registered from header metadata alone.
            image.load()
            expected_type, suffix = _FORMAT_METADATA[image_format]
    except ValueError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("preview image exceeds the 40 MP limit") from exc
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("preview image could not be fully decoded") from exc

    declared = str(declared_content_type or "").split(";", 1)[0].strip().casefold()
    if declared and declared != expected_type:
        raise ValueError("preview image content type does not match decoded bytes")
    return DecodedPreviewImage(
        content=raw,
        content_hash=hashlib.sha256(raw).hexdigest(),
        content_type=expected_type,
        suffix=suffix,
        width=width,
        height=height,
    )
