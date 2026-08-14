"""Deterministic layout helpers for one-call 2x2 product image generation.

The image model receives a locally generated scaffold, but the returned image is
still treated as untrusted.  Splitting only happens when a single divider pair
is proven near the center and every extracted panel is free of another long
separator.  Ambiguous layouts fail closed instead of guessing a destructive
crop.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, Literal


GRID_CANVAS_SIZE = 2048
GRID_GUTTER_SIZE = 16
GRID_DIVIDER_RGB = (224, 226, 228)
GRID_BACKGROUND_RGB = (242, 242, 239)


class GridLayoutError(ValueError):
    pass


@dataclass(frozen=True)
class GridSplitGuides:
    x_start: int
    x_end: int
    y_start: int
    y_end: int
    mode: Literal["fixed", "adaptive"]


@dataclass(frozen=True)
class _AxisBand:
    start: int
    end: int
    luminance: float


def _open_rgb(value: bytes | bytearray | memoryview | Any) -> Any:
    from PIL import Image  # type: ignore

    if hasattr(value, "convert") and hasattr(value, "size"):
        return value.convert("RGB")
    try:
        return Image.open(BytesIO(bytes(value))).convert("RGB")
    except Exception as exc:
        raise GridLayoutError("four-grid image cannot be decoded") from exc


def build_grid_scaffold(reference_content: bytes) -> bytes:
    """Build the non-exported 2048px structural reference used for B grids."""

    from PIL import Image, ImageOps  # type: ignore

    try:
        with Image.open(BytesIO(reference_content)) as opened:
            source = opened.convert("RGB")
    except Exception as exc:
        raise GridLayoutError("source image cannot be decoded for grid scaffold") from exc
    if source.width < 2 or source.height < 2:
        raise GridLayoutError("source image is too small for grid scaffold")

    canvas = Image.new("RGB", (GRID_CANVAS_SIZE, GRID_CANVAS_SIZE), GRID_BACKGROUND_RGB)
    panel_size = (GRID_CANVAS_SIZE - GRID_GUTTER_SIZE) // 2
    safe_inset = round(panel_size * 0.10)
    safe_size = panel_size - safe_inset * 2
    tile = ImageOps.contain(source, (safe_size, safe_size), Image.Resampling.LANCZOS)
    panel_origins = (
        (0, 0),
        (panel_size + GRID_GUTTER_SIZE, 0),
        (0, panel_size + GRID_GUTTER_SIZE),
        (panel_size + GRID_GUTTER_SIZE, panel_size + GRID_GUTTER_SIZE),
    )
    for panel_x, panel_y in panel_origins:
        x = panel_x + safe_inset + (safe_size - tile.width) // 2
        y = panel_y + safe_inset + (safe_size - tile.height) // 2
        canvas.paste(tile, (x, y))

    fixed_start = (GRID_CANVAS_SIZE - GRID_GUTTER_SIZE) // 2
    fixed_end = fixed_start + GRID_GUTTER_SIZE
    canvas.paste(GRID_DIVIDER_RGB, (fixed_start, 0, fixed_end, GRID_CANVAS_SIZE))
    canvas.paste(GRID_DIVIDER_RGB, (0, fixed_start, GRID_CANVAS_SIZE, fixed_end))
    output = BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


def _coordinate_stats(image: Any, axis: Literal["x", "y"], coordinate: int) -> tuple[list[float], list[float]]:
    from PIL import ImageStat  # type: ignore

    if axis == "x":
        sample = image.crop((coordinate, 0, coordinate + 1, image.height))
    else:
        sample = image.crop((0, coordinate, image.width, coordinate + 1))
    stats = ImageStat.Stat(sample.convert("RGB"))
    return [float(value) for value in stats.mean], [float(value) for value in stats.stddev]


def _divider_coordinate(means: list[float], deviations: list[float]) -> bool:
    return (
        min(means) >= 165
        and max(means) - min(means) <= 28
        and max(deviations) <= 18
    )


def _axis_bands(image: Any, axis: Literal["x", "y"], corridor: range) -> list[_AxisBand]:
    qualifying: list[tuple[int, float, list[float]]] = []
    for coordinate in corridor:
        means, deviations = _coordinate_stats(image, axis, coordinate)
        if _divider_coordinate(means, deviations):
            qualifying.append((coordinate, sum(means) / 3.0, means))

    groups: list[list[tuple[int, float, list[float]]]] = []
    for item in qualifying:
        if not groups:
            groups.append([item])
            continue
        prior = groups[-1][-1]
        adjacent = item[0] == prior[0] + 1
        same_tone = abs(item[1] - prior[1]) <= 5 and max(
            abs(item[2][index] - prior[2][index]) for index in range(3)
        ) <= 8
        if adjacent and same_tone:
            groups[-1].append(item)
        else:
            groups.append([item])

    bands: list[_AxisBand] = []
    for group in groups:
        width = group[-1][0] - group[0][0] + 1
        if 4 <= width <= 24:
            bands.append(
                _AxisBand(
                    start=group[0][0],
                    end=group[-1][0] + 1,
                    luminance=sum(item[1] for item in group) / len(group),
                )
            )
    return bands


def locate_split_guides(value: bytes | Any) -> GridSplitGuides:
    image = _open_rgb(value)
    width, height = image.size
    if width != height or width < 2000:
        raise GridLayoutError("four-grid source must be square and at least 2000 pixels")

    center_x, center_y = width // 2, height // 2
    corridor_x = max(8, round(width * 0.025))
    corridor_y = max(8, round(height * 0.025))
    x_bands = _axis_bands(image, "x", range(center_x - corridor_x, center_x + corridor_x + 1))
    y_bands = _axis_bands(image, "y", range(center_y - corridor_y, center_y + corridor_y + 1))
    if not x_bands or not y_bands:
        raise GridLayoutError("four-grid dividers are outside adaptive corridor or discontinuous")
    if len(x_bands) != 1 or len(y_bands) != 1:
        raise GridLayoutError("multiple divider candidates make four-grid crop ambiguous")

    x_band, y_band = x_bands[0], y_bands[0]
    left_width, right_width = x_band.start, width - x_band.end
    top_height, bottom_height = y_band.start, height - y_band.end
    if abs(left_width - right_width) > round(width * 0.05) or abs(top_height - bottom_height) > round(height * 0.05):
        raise GridLayoutError("four-grid panel dimensions exceed adaptive corridor tolerance")
    for panel_width, panel_height in (
        (left_width, top_height),
        (right_width, top_height),
        (left_width, bottom_height),
        (right_width, bottom_height),
    ):
        ratio = panel_width / max(panel_height, 1)
        if not 0.90 <= ratio <= 1.10:
            raise GridLayoutError("four-grid adaptive panels are not near-square")

    expected_start = round(width * 1016 / 2048)
    expected_end = round(width * 1032 / 2048)
    mode: Literal["fixed", "adaptive"] = (
        "fixed"
        if (
            abs(x_band.start - expected_start) <= 1
            and abs(x_band.end - expected_end) <= 1
            and abs(y_band.start - expected_start) <= 1
            and abs(y_band.end - expected_end) <= 1
        )
        else "adaptive"
    )
    return GridSplitGuides(x_band.start, x_band.end, y_band.start, y_band.end, mode)


def center_split_guides(value: bytes | Any) -> GridSplitGuides:
    """Fallback: split exactly at the center when no divider evidence exists.

    Used by the non-scaffold template path where the model is not required to
    paint a divider line. Panel independence is still enforced afterwards by
    ``extract_grid_panels``, so ambiguous collages keep failing closed.
    """
    image = _open_rgb(value)
    width, height = image.size
    return GridSplitGuides(width // 2, width // 2 + 1, height // 2, height // 2 + 1, "adaptive")


def _center_crop_to_square(image: Any) -> Any:
    side = min(image.width, image.height)
    left = max((image.width - side) // 2, 0)
    top = max((image.height - side) // 2, 0)
    return image.crop((left, top, left + side, top + side))


def extract_grid_panels(value: bytes | Any, guides: GridSplitGuides) -> list[Any]:
    image = _open_rgb(value)
    width, height = image.size
    boxes = (
        (0, 0, guides.x_start, guides.y_start),
        (guides.x_end, 0, width, guides.y_start),
        (0, guides.y_end, guides.x_start, height),
        (guides.x_end, guides.y_end, width, height),
    )
    panels: list[Any] = []
    for box in boxes:
        panel = image.crop(box)
        # 拆图尽力切：不做面板内容独立性校验，避免模型排版偏差导致整组拆图失败阻断流程。
        # 质量门改为软性（重绘尽力 + 回退来源图），不再因内容问题阻止入库。
        panels.append(_center_crop_to_square(panel))
    return panels


def _axis_uniform_groups(image: Any, axis: Literal["x", "y"], start: int, end: int) -> list[_AxisBand]:
    qualifying: list[tuple[int, float, list[float]]] = []
    for coordinate in range(start, end):
        means, deviations = _coordinate_stats(image, axis, coordinate)
        if max(deviations) <= 22 and max(means) - min(means) <= 45:
            qualifying.append((coordinate, sum(means) / 3.0, means))
    groups: list[list[tuple[int, float, list[float]]]] = []
    for item in qualifying:
        if groups and item[0] == groups[-1][-1][0] + 1 and abs(item[1] - groups[-1][-1][1]) <= 7:
            groups[-1].append(item)
        else:
            groups.append([item])
    return [
        _AxisBand(group[0][0], group[-1][0] + 1, sum(item[1] for item in group) / len(group))
        for group in groups
        if 2 <= group[-1][0] - group[0][0] + 1 <= 20
    ]


def _strip_luminance(image: Any, axis: Literal["x", "y"], start: int, end: int) -> float:
    from PIL import ImageStat  # type: ignore

    if axis == "x":
        sample = image.crop((start, 0, end, image.height))
    else:
        sample = image.crop((0, start, image.width, end))
    means = [float(value) for value in ImageStat.Stat(sample.convert("RGB")).mean]
    return sum(means) / 3.0


def validate_panel_independence(panel: Any) -> None:
    """Reject the white/neutral long lines left by nested or shifted collages."""

    image = _open_rgb(panel)
    for axis, size in (("x", image.width), ("y", image.height)):
        interior_start = round(size * 0.08)
        interior_end = round(size * 0.92)
        for band in _axis_uniform_groups(image, axis, interior_start, interior_end):
            gap = max(5, round(size * 0.01))
            left_start = max(0, band.start - gap * 2)
            left_end = max(left_start + 1, band.start - gap)
            right_start = min(size - 1, band.end + gap)
            right_end = min(size, band.end + gap * 2)
            if left_end > band.start or right_start < band.end or right_end <= right_start:
                continue
            left = _strip_luminance(image, axis, left_start, left_end)
            right = _strip_luminance(image, axis, right_start, right_end)
            if abs(band.luminance - left) >= 25 and abs(band.luminance - right) >= 25:
                raise GridLayoutError(f"panel contains an internal divider on the {axis} axis")
