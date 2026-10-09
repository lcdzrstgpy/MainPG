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
