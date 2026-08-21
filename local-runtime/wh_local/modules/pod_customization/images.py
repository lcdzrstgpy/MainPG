from __future__ import annotations

import io
import hashlib
import math
from dataclasses import dataclass
from typing import Callable, Sequence

from PIL import Image, ImageStat, UnidentifiedImageError

from .contracts import Calibration


@dataclass(frozen=True)
class PatternAssessment:
    accepted: bool
    rejection_reason: str
    fingerprint: str


class PatternQualityGate:
    def __init__(
        self,
        *,
        text_inspector: Callable[[bytes], Sequence[str]] | None = None,
        duplicate_distance: int = 0,
    ) -> None:
        self.text_inspector = text_inspector
        self.duplicate_distance = max(0, duplicate_distance)

    def assess(self, content: bytes, *, accepted_fingerprints: Sequence[str]) -> PatternAssessment:
        try:
            with Image.open(io.BytesIO(content)) as source:
                source.load()
                if source.width < 64 or source.height < 64:
                    return PatternAssessment(False, "invalid", "")
                image = source.convert("RGB")
        except (UnidentifiedImageError, OSError, ValueError):
            return PatternAssessment(False, "invalid", "")
        grayscale = image.convert("L")
        if sum(ImageStat.Stat(grayscale).stddev) < 2.0:
            return PatternAssessment(False, "invalid", "")
        fingerprint = _fingerprint(image)
        if any(_fingerprint_distance(fingerprint, existing) <= self.duplicate_distance for existing in accepted_fingerprints):
            return PatternAssessment(False, "duplicate", fingerprint)
        if self.text_inspector is not None:
            try:
                visible_text = [str(value).strip() for value in self.text_inspector(content) if str(value).strip()]
            except Exception:
                return PatternAssessment(False, "text_check_failed", fingerprint)
            if visible_text:
                return PatternAssessment(False, "text_error", fingerprint)
        return PatternAssessment(True, "", fingerprint)


def split_grid_2x2(content: bytes) -> list[bytes]:
    try:
        with Image.open(io.BytesIO(content)) as source:
            source.load()
            image = source.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("AI result is not a valid 2x2 grid image") from exc
    if image.width < 128 or image.height < 128:
        raise ValueError("2x2 grid image is too small")
    half_width = image.width // 2
    half_height = image.height // 2
    boxes = (
        (0, 0, half_width, half_height),
        (half_width, 0, image.width, half_height),
        (0, half_height, half_width, image.height),
        (half_width, half_height, image.width, image.height),
    )
    return [_encode_png(image.crop(box)) for box in boxes]


def compose_fixed_scene(
    template_image: bytes,
    pattern_image: bytes,
    calibration: Calibration,
    *,
    overlay_images: Sequence[bytes] = (),
) -> bytes:
    base = _open_rgba(template_image, "fixed scene template")
    pattern = _open_rgba(pattern_image, "POD pattern")
    rect = calibration.mask
    left = max(0, min(base.width - 1, round(rect.x * base.width)))
    top = max(0, min(base.height - 1, round(rect.y * base.height)))
    right = max(left + 1, min(base.width, round((rect.x + rect.width) * base.width)))
    bottom = max(top + 1, min(base.height, round((rect.y + rect.height) * base.height)))
    target_width, target_height = right - left, bottom - top
    scale = max(target_width / pattern.width, target_height / pattern.height)
    resized = pattern.resize(
        (max(target_width, math.ceil(pattern.width * scale)), max(target_height, math.ceil(pattern.height * scale))),
        Image.Resampling.LANCZOS,
    )
    excess_x = max(0, resized.width - target_width)
    excess_y = max(0, resized.height - target_height)
    crop_x = round(excess_x * calibration.anchor.x)
    crop_y = round(excess_y * calibration.anchor.y)
    tile = resized.crop((crop_x, crop_y, crop_x + target_width, crop_y + target_height))
    base.alpha_composite(tile, dest=(left, top))
    for raw_overlay in overlay_images:
        overlay = _open_rgba(raw_overlay, "fixed scene overlay")
        if overlay.size != base.size:
            overlay = overlay.resize(base.size, Image.Resampling.LANCZOS)
        base.alpha_composite(overlay)
    return _encode_png(base)


def _open_rgba(content: bytes, label: str) -> Image.Image:
    try:
        with Image.open(io.BytesIO(content)) as source:
            source.load()
            return source.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError(f"{label} is not a valid image") from exc


def _encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def _fingerprint(image: Image.Image) -> str:
    normalized = image.resize((64, 64), Image.Resampling.LANCZOS)
    exact_key = hashlib.sha256(normalized.tobytes()).hexdigest()
    color = ImageStat.Stat(image.resize((1, 1), Image.Resampling.BOX)).mean
    color_key = "".join(f"{max(0, min(15, round(channel / 17))):x}" for channel in color[:3])
    gray = image.convert("L").resize((16, 16), Image.Resampling.LANCZOS)
    pixels = list(gray.get_flattened_data())
    mean = sum(pixels) / len(pixels)
    bits = 0
    for pixel in pixels:
        bits = (bits << 1) | int(pixel > mean)
    return f"{exact_key}:{color_key}:{bits:064x}"


def _fingerprint_distance(left: str, right: str) -> int:
    try:
        left_exact, left_color, left_shape = left.split(":", 2)
        right_exact, right_color, right_shape = right.split(":", 2)
        if left_exact == right_exact:
            return 0
        color_distance = sum(abs(int(a, 16) - int(b, 16)) for a, b in zip(left_color, right_color, strict=True))
        if color_distance > 4:
            return 257
        return max(1, (int(left_shape, 16) ^ int(right_shape, 16)).bit_count())
    except (TypeError, ValueError):
        return 257
