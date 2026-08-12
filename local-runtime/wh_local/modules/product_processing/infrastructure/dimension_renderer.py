from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, StrictBytes


_FONT_PATH = Path("C:/Windows/Fonts/segoeuib.ttf")
_SAFE_MARGIN_RATIO = 0.05
_MAX_SOURCE_BYTES = 25 * 1024 * 1024
_MAX_SOURCE_PIXELS = 40_000_000
_MAX_ANNOTATIONS = 32
_ALLOWED_SOURCE_FORMATS = {"JPEG", "PNG", "WEBP"}


class DimensionAnnotation(BaseModel):
    key: Literal["length", "width", "height", "custom"]
    value_cm: float
    start: tuple[float, float]
    end: tuple[float, float]
    label: tuple[float, float]
    style: Literal["auto", "dark", "light"] = "auto"


class DimensionRenderRequest(BaseModel):
    """Pure render data; callers must resolve an authorized managed asset to bytes.

    Paths and URLs are intentionally absent, and StrictBytes prevents Pydantic from
    silently coercing a client-provided path or URL string into renderer input.
    """

    source_bytes: StrictBytes
    annotations: list[DimensionAnnotation]
    output_size: int = 2000
    fit: Literal["contain", "cover"] = "contain"


@dataclass(frozen=True)
class DimensionRenderOutput:
    master_png_bytes: bytes
    jpeg_bytes: bytes
    content_hash: str
    width: int
    height: int


class DimensionRenderer:
    def render(self, request: DimensionRenderRequest) -> DimensionRenderOutput:
        self._validate_request(request)
        if not _FONT_PATH.is_file():
            raise ValueError("dimension_font_missing")

        source = self._decode_source(request.source_bytes)
        canvas = self._compose_source(source, request.output_size, request.fit)
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.truetype(
            str(_FONT_PATH), max(24, round(request.output_size * 0.044))
        )
        for annotation in request.annotations:
            self._draw_annotation(canvas, draw, font, annotation)

        master_buffer = BytesIO()
        canvas.save(master_buffer, format="PNG")
        jpeg_buffer = BytesIO()
        canvas.save(jpeg_buffer, format="JPEG", quality=95, subsampling=0)
        master_png_bytes = master_buffer.getvalue()
        jpeg_bytes = jpeg_buffer.getvalue()
        return DimensionRenderOutput(
            master_png_bytes=master_png_bytes,
            jpeg_bytes=jpeg_bytes,
            content_hash=hashlib.sha256(jpeg_bytes).hexdigest(),
            width=request.output_size,
            height=request.output_size,
        )

    @staticmethod
    def _validate_request(request: DimensionRenderRequest) -> None:
        if request.output_size < 2000 or request.output_size > 4096:
            raise ValueError("dimension_output_size_invalid")
        if not request.source_bytes:
            raise ValueError("dimension_source_invalid")
        if len(request.source_bytes) > _MAX_SOURCE_BYTES:
            raise ValueError("dimension_source_too_large")
        if not request.annotations:
            raise ValueError("dimension_annotations_empty")
        if len(request.annotations) > _MAX_ANNOTATIONS:
            raise ValueError("dimension_annotations_too_many")
        for annotation in request.annotations:
            if not math.isfinite(annotation.value_cm) or annotation.value_cm <= 0:
                raise ValueError("dimension_value_invalid")
            for point in (annotation.start, annotation.end, annotation.label):
                if len(point) != 2 or any(
                    not math.isfinite(value) or value < 0 or value > 1
                    for value in point
                ):
                    raise ValueError("dimension_coordinate_invalid")
            if not all(
                _SAFE_MARGIN_RATIO <= value <= 1 - _SAFE_MARGIN_RATIO
                for value in annotation.label
            ):
                raise ValueError("dimension_label_outside_safe_margin")
            distance = math.hypot(
                annotation.end[0] - annotation.start[0],
                annotation.end[1] - annotation.start[1],
            )
            if distance < 0.01:
                raise ValueError("dimension_annotation_too_short")

    @staticmethod
    def _decode_source(content: bytes) -> Image.Image:
        try:
            with Image.open(BytesIO(content)) as opened:
                if opened.format not in _ALLOWED_SOURCE_FORMATS:
                    raise ValueError("dimension_source_format_invalid")
                if int(getattr(opened, "n_frames", 1)) != 1:
                    raise ValueError("dimension_source_animated")
                if opened.width * opened.height > _MAX_SOURCE_PIXELS:
                    raise ValueError("dimension_source_pixels_exceeded")
                opened.load()
                return ImageOps.exif_transpose(opened).convert("RGB")
        except ValueError:
            raise
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("dimension_source_invalid") from exc

    @staticmethod
    def _compose_source(
        source: Image.Image, output_size: int, fit: Literal["contain", "cover"]
    ) -> Image.Image:
        canvas = Image.new("RGB", (output_size, output_size), "white")
        if fit == "cover":
            scale = max(output_size / source.width, output_size / source.height)
        else:
            scale = min(output_size / source.width, output_size / source.height)
        target_size = (
            max(1, round(source.width * scale)),
            max(1, round(source.height * scale)),
        )
        resized = source.resize(target_size, Image.Resampling.LANCZOS)
        if fit == "cover":
            left = max(0, (resized.width - output_size) // 2)
            top = max(0, (resized.height - output_size) // 2)
            resized = resized.crop(
                (left, top, left + output_size, top + output_size)
            )
            canvas.paste(resized, (0, 0))
        else:
            left = (output_size - resized.width) // 2
            top = (output_size - resized.height) // 2
            canvas.paste(resized, (left, top))
        return canvas

    @staticmethod
    def _draw_annotation(
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        font: ImageFont.FreeTypeFont,
        annotation: DimensionAnnotation,
    ) -> None:
        size = canvas.width
        start = _pixel_point(annotation.start, size)
        end = _pixel_point(annotation.end, size)
        label_point = _pixel_point(annotation.label, size)
        color, contrast = _annotation_colors(canvas, label_point, annotation.style)
        line_width = max(4, round(size * 0.0045))
        arrow_length = max(18, round(size * 0.022))
        arrow_half_width = max(10, round(size * 0.011))

        draw.line((start, end), fill=color, width=line_width)
        _draw_arrow_head(
            draw,
            tip=start,
            toward=end,
            length=arrow_length,
            half_width=arrow_half_width,
            fill=color,
        )
        _draw_arrow_head(
            draw,
            tip=end,
            toward=start,
            length=arrow_length,
            half_width=arrow_half_width,
            fill=color,
        )

        label = f"{annotation.value_cm:.2f}".rstrip("0").rstrip(".") + " cm"
        stroke_width = max(2, round(size * 0.0025))
        bounds = draw.textbbox(
            label_point,
            label,
            font=font,
            anchor="mm",
            stroke_width=stroke_width,
        )
        safe = round(size * _SAFE_MARGIN_RATIO)
        if (
            bounds[0] < safe
            or bounds[1] < safe
            or bounds[2] > size - safe
            or bounds[3] > size - safe
        ):
            raise ValueError("dimension_label_outside_safe_margin")
        draw.text(
            label_point,
            label,
            font=font,
            fill=color,
            anchor="mm",
            stroke_width=stroke_width,
            stroke_fill=contrast,
        )


def _pixel_point(point: tuple[float, float], size: int) -> tuple[int, int]:
    return round(point[0] * (size - 1)), round(point[1] * (size - 1))


def _annotation_colors(
    image: Image.Image,
    label_point: tuple[int, int],
    style: Literal["auto", "dark", "light"],
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if style == "dark":
        return (20, 20, 20), (255, 255, 255)
    if style == "light":
        return (255, 255, 255), (20, 20, 20)
    red, green, blue = image.getpixel(label_point)
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    if luminance >= 145:
        return (20, 20, 20), (255, 255, 255)
    return (255, 255, 255), (20, 20, 20)


def _draw_arrow_head(
    draw: ImageDraw.ImageDraw,
    *,
    tip: tuple[int, int],
    toward: tuple[int, int],
    length: int,
    half_width: int,
    fill: tuple[int, int, int],
) -> None:
    delta_x = toward[0] - tip[0]
    delta_y = toward[1] - tip[1]
    magnitude = math.hypot(delta_x, delta_y)
    unit_x = delta_x / magnitude
    unit_y = delta_y / magnitude
    base_x = tip[0] + unit_x * length
    base_y = tip[1] + unit_y * length
    perpendicular_x = -unit_y * half_width
    perpendicular_y = unit_x * half_width
    draw.polygon(
        [
            tip,
            (round(base_x + perpendicular_x), round(base_y + perpendicular_y)),
            (round(base_x - perpendicular_x), round(base_y - perpendicular_y)),
        ],
        fill=fill,
    )
