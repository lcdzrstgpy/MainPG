from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, StrictBytes


_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)
_FONT_PATH = next((path for path in _FONT_CANDIDATES if path.is_file()), None)
_EXPORT_PADDING_RATIO = 0.005
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
    style: Literal["auto", "dark", "light", "gray_dashed"] = "auto"
    line_width: Literal["thin", "normal", "thick"] = "normal"
    endpoint_style: Literal["arrow", "bar", "none"] = "arrow"
    unit: Literal["cm", "mm", "in", "ft"] = "cm"


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


@dataclass(frozen=True)
class DimensionSourceInfo:
    width: int
    height: int
    content_type: str
    suffix: str


class DimensionRenderer:
    def inspect_source(self, content: bytes) -> DimensionSourceInfo:
        image, source_format = self._open_source(content)
        return DimensionSourceInfo(
            width=image.width,
            height=image.height,
            content_type={"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}[source_format],
            suffix={"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[source_format],
        )

    def render(self, request: DimensionRenderRequest) -> DimensionRenderOutput:
        self._validate_request(request)

        source = self._decode_source(request.source_bytes)
        canvas = self._compose_source(source, request.output_size, request.fit)
        draw = ImageDraw.Draw(canvas)
        font = _load_font(max(24, round(request.output_size * 0.044)))
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
            distance = math.hypot(
                annotation.end[0] - annotation.start[0],
                annotation.end[1] - annotation.start[1],
            )
            if distance < 0.01:
                raise ValueError("dimension_annotation_too_short")

    @staticmethod
    def _decode_source(content: bytes) -> Image.Image:
        image, _source_format = DimensionRenderer._open_source(content)
        return image

    @staticmethod
    def _open_source(content: bytes) -> tuple[Image.Image, str]:
        if not content:
            raise ValueError("dimension_source_invalid")
        if len(content) > _MAX_SOURCE_BYTES:
            raise ValueError("dimension_source_too_large")
        try:
            with Image.open(BytesIO(content)) as opened:
                source_format = str(opened.format or "").upper()
                if source_format not in _ALLOWED_SOURCE_FORMATS:
                    raise ValueError("dimension_source_format_invalid")
                if int(getattr(opened, "n_frames", 1)) != 1:
                    raise ValueError("dimension_source_animated")
                if opened.width * opened.height > _MAX_SOURCE_PIXELS:
                    raise ValueError("dimension_source_pixels_exceeded")
                opened.load()
                return ImageOps.exif_transpose(opened).convert("RGB"), source_format
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
        label = _format_dimension(annotation.value_cm, annotation.unit)
        stroke_width = max(2, round(size * 0.0025))
        label_point = _fit_label_inside_safe_margin(
            draw,
            _pixel_point(annotation.label, size),
            label,
            font=font,
            stroke_width=stroke_width,
            size=size,
        )
        color, contrast = _annotation_colors(canvas, label_point, annotation.style)
        line_width_scale = {"thin": 0.65, "normal": 1.0, "thick": 1.65}[annotation.line_width]
        line_width = max(3, round(size * 0.0045 * line_width_scale))
        arrow_length = max(18, round(size * 0.022))
        arrow_half_width = max(10, round(size * 0.011))

        if annotation.style == "gray_dashed":
            _draw_dashed_line(
                draw,
                start,
                end,
                fill=color,
                width=line_width,
                dash_length=max(12, round(size * 0.014)),
                gap_length=max(8, round(size * 0.009)),
            )
        else:
            draw.line((start, end), fill=color, width=line_width)
        if annotation.endpoint_style == "arrow":
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
        elif annotation.endpoint_style == "bar":
            _draw_endpoint_bar(
                draw,
                point=start,
                toward=end,
                half_length=arrow_half_width,
                width=line_width,
                fill=color,
            )
            _draw_endpoint_bar(
                draw,
                point=end,
                toward=start,
                half_length=arrow_half_width,
                width=line_width,
                fill=color,
            )

        draw.text(
            label_point,
            label,
            font=font,
            fill=color,
            anchor="mm",
            stroke_width=stroke_width,
            stroke_fill=contrast,
        )


def _fit_label_inside_safe_margin(
    draw: ImageDraw.ImageDraw,
    point: tuple[int, int],
    label: str,
    *,
    font: ImageFont.FreeTypeFont,
    stroke_width: int,
    size: int,
) -> tuple[int, int]:
    """Move only the label center by the minimum pixels needed for safe output."""

    bounds = draw.textbbox(
        point,
        label,
        font=font,
        anchor="mm",
        stroke_width=stroke_width,
    )
    safe = max(stroke_width + 1, round(size * _EXPORT_PADDING_RATIO))
    usable = size - (safe * 2)
    if bounds[2] - bounds[0] > usable or bounds[3] - bounds[1] > usable:
        raise ValueError("dimension_label_outside_safe_margin")

    offset_x = max(safe - bounds[0], min(0, size - safe - bounds[2]))
    offset_y = max(safe - bounds[1], min(0, size - safe - bounds[3]))
    adjusted = point[0] + offset_x, point[1] + offset_y
    adjusted_bounds = draw.textbbox(
        adjusted,
        label,
        font=font,
        anchor="mm",
        stroke_width=stroke_width,
    )
    if (
        adjusted_bounds[0] < safe
        or adjusted_bounds[1] < safe
        or adjusted_bounds[2] > size - safe
        or adjusted_bounds[3] > size - safe
    ):
        raise ValueError("dimension_label_outside_safe_margin")
    return adjusted


def _pixel_point(point: tuple[float, float], size: int) -> tuple[int, int]:
    return round(point[0] * (size - 1)), round(point[1] * (size - 1))


def _load_font(size: int) -> ImageFont.ImageFont:
    if _FONT_PATH is not None:
        return ImageFont.truetype(str(_FONT_PATH), size)
    return ImageFont.load_default(size=size)


def _format_dimension(value_cm: float, unit: Literal["cm", "mm", "in", "ft"]) -> str:
    converted = {
        "cm": value_cm,
        "mm": value_cm * 10,
        "in": value_cm / 2.54,
        "ft": value_cm / 30.48,
    }[unit]
    precision = 1 if unit == "mm" else 2
    number = f"{converted:.{precision}f}".rstrip("0").rstrip(".")
    return f"{number} {unit}"


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    fill: tuple[int, int, int],
    width: int,
    dash_length: int,
    gap_length: int,
) -> None:
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    distance = math.hypot(delta_x, delta_y)
    if distance <= 0:
        return
    unit_x = delta_x / distance
    unit_y = delta_y / distance
    cursor = 0.0
    while cursor < distance:
        dash_end = min(distance, cursor + dash_length)
        draw.line(
            (
                (round(start[0] + unit_x * cursor), round(start[1] + unit_y * cursor)),
                (round(start[0] + unit_x * dash_end), round(start[1] + unit_y * dash_end)),
            ),
            fill=fill,
            width=width,
        )
        cursor += dash_length + gap_length


def _annotation_colors(
    image: Image.Image,
    label_point: tuple[int, int],
    style: Literal["auto", "dark", "light", "gray_dashed"],
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if style == "gray_dashed":
        return (123, 135, 148), (255, 255, 255)
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


def _draw_endpoint_bar(
    draw: ImageDraw.ImageDraw,
    *,
    point: tuple[int, int],
    toward: tuple[int, int],
    half_length: int,
    width: int,
    fill: tuple[int, int, int],
) -> None:
    delta_x = toward[0] - point[0]
    delta_y = toward[1] - point[1]
    magnitude = math.hypot(delta_x, delta_y)
    if magnitude <= 0:
        return
    perpendicular_x = (-delta_y / magnitude) * half_length
    perpendicular_y = (delta_x / magnitude) * half_length
    draw.line(
        (
            (round(point[0] - perpendicular_x), round(point[1] - perpendicular_y)),
            (round(point[0] + perpendicular_x), round(point[1] + perpendicular_y)),
        ),
        fill=fill,
        width=width,
    )
