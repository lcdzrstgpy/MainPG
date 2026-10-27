"""尺寸角标卡：把本体长宽高以英寸表格印到第 4 张轮播图的角落。

与尺寸画布的箭头标注不同，角标卡不需要知道商品轮廓，适合批量自动出图；
排版与单位完全确定，不调用任何模型，数字不会被改写。
"""

from __future__ import annotations

from .physical_dimensions import PhysicalDimensions

INCHES_PER_CM = 1 / 2.54
CARD_UNITS = {"in", "cm"}


class DimensionCardError(ValueError):
    pass


def format_dimension(value_cm: float, unit: str = "in") -> str:
    """把厘米值转成展示字符串；英寸保留 1 位小数，厘米保留 1 位小数。"""

    if unit == "in":
        return f"{value_cm * INCHES_PER_CM:.1f}"
    return f"{value_cm:.1f}"


def build_card_cells(
    dimensions: PhysicalDimensions,
    *,
    unit: str = "in",
) -> tuple[tuple[str, str], ...]:
    """生成两列卡片：左列轴名，右列数值（单位写在表头）。"""

    if unit not in CARD_UNITS:
        raise DimensionCardError("dimension card unit must be in or cm")
    rows: list[tuple[str, str]] = [("Size", "inch" if unit == "in" else "cm")]
    for label, item in (
        ("Length", dimensions.length),
        ("Width", dimensions.width),
        ("Height", dimensions.height),
    ):
        if item.value_cm is None or item.value_cm <= 0:
            continue
        rows.append((label, format_dimension(float(item.value_cm), unit)))
    if len(rows) < 2:
        raise DimensionCardError("no drawable dimensions for the card")
    return tuple(rows)


def render_dimension_card(
    source_bytes: bytes,
    dimensions: PhysicalDimensions,
    *,
    unit: str = "in",
    corner: str = "bottom-right",
    style: str = "light",
) -> bytes:
    """在正方形底图上印尺寸卡，返回 JPEG 字节（与底图同尺寸）。"""

    # 延迟导入：product_processing 只在真正渲染时依赖 POD 的纯本地卡片渲染器。
    from ...pod_customization.spec_card import (  # noqa: PLC0415
        SpecCardRenderError,
        SpecCardRequest,
        render_spec_card,
    )

    cells = build_card_cells(dimensions, unit=unit)
    try:
        result = render_spec_card(
            source_bytes,
            SpecCardRequest(cells=cells, style=style, corner=corner),
        )
    except SpecCardRenderError as exc:
        raise DimensionCardError(str(exc)) from exc
    return result.jpeg_bytes
