"""第 4 张图「规格卡」：把用户填的表格原样印到生成图的指定角落。

设计要点（方案 docs/superpowers/specs/2026-09-10-pod-spec-card-plan.md）：
* 纯本地 Pillow 渲染，不调用任何 provider，不计费；刻意不 import 产品处理链路
  （``product_processing``）的任何模块。
* 单元格文本**原样印出**：不翻译、不做单位换算、不做变量替换、不换行（超宽只截断）。
* 卡片背景**不透明**（纯白 / 深色实底），不做半透明（2026-09-10 用户评审）。
* 卡片**直角**，行与行之间画清晰的分隔线。
* 字体为**宋体族**，字号严格固定：首行「四号」(14pt)，其余行「小四」(12pt)；
  800×800 母版按 96dpi 折算即 19px / 16px，画布更大时等比放大。
* 渲染失败一律抛 :class:`SpecCardRenderError`，调用方回退干净图，不阻断批次。
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


STYLE_LIGHT = "light"
STYLE_DARK = "dark"
STYLES = (STYLE_LIGHT, STYLE_DARK)

CORNER_BOTTOM_RIGHT = "bottom-right"
CORNER_BOTTOM_LEFT = "bottom-left"
CORNER_TOP_RIGHT = "top-right"
CORNER_TOP_LEFT = "top-left"
CORNERS = (CORNER_BOTTOM_RIGHT, CORNER_BOTTOM_LEFT, CORNER_TOP_RIGHT, CORNER_TOP_LEFT)

ELLIPSIS = "…"

# 字号规格（用户指定，严格固定，不做自适应缩号）：
#   首行 = 四号 = 14pt，其余行 = 小四 = 12pt；按 96dpi 折算成像素（1pt = 4/3 px）。
REFERENCE_SIDE = 800
HEADER_FONT_PT = 14
BODY_FONT_PT = 12
_PT_TO_PX = 4 / 3
HEADER_FONT_PX_AT_800 = round(HEADER_FONT_PT * _PT_TO_PX)  # 19（scale = 1 时的基准）
BODY_FONT_PX_AT_800 = round(BODY_FONT_PT * _PT_TO_PX)  # 16（scale = 1 时的基准）

# 用户规格（2026-09-10 最终）：
#   * 字号固定不动：首行四号、其余行小四（14:12 比例，scale 恒为 1）
#   * 行高固定 32px（@800 画布，随画布等比换算），不随字号变化
#   * 卡片宽度拉到「九分之一区域」的宽度，即画布宽的 1/3
ROW_HEIGHT_PX_AT_800 = 32
CARD_TARGET_WIDTH_RATIO = 1 / 3

# 几何比例（除内边距/行高/列间距外，均「相对画布边长」）。
_MARGIN_RATIO = 0.0  # 外边距：0 = 卡片紧贴所选角落（2026-09-11 用户规格）
_STROKE_RATIO = 0.0025  # 描边
_CARD_WIDTH_RATIO = 0.46  # 卡片宽上限
_CARD_HEIGHT_RATIO = 0.62  # 卡片高上限（12 行 × 32px ≈ 430px，需放宽）
_PADDING_RATIO = 0.72  # 内边距 = 0.72 × 正文字号
_LINE_HEIGHT_RATIO = 1.72  # 行高下限 = 1.72 × 该行字号（行高实际取 32px 与它的大者）
_COLUMN_GAP_RATIO = 1.1  # 列间距 = 1.1 × 内边距
_RULE_OFFSET_RATIO = 0.36  # 行分隔线相对行顶上移（× 该行字号）
_RULE_WIDTH_RATIO = 0.09  # 行分隔线粗细（× 正文字号）
_JPEG_QUALITY = 92  # 与其它三格一致

# 卡片底色不透明：浅色 = 纯白，深色 = 实底深蓝灰（用户要求「不要半透明」）。
_PALETTES: dict[str, dict[str, tuple[int, int, int, int]]] = {
    STYLE_LIGHT: {
        "surface": (255, 255, 255, 255),
        "outline": (226, 226, 226, 255),
        "text": (34, 34, 34, 255),
        "label": (86, 86, 86, 255),
        "rule": (176, 176, 176, 255),
    },
    STYLE_DARK: {
        "surface": (26, 34, 51, 255),
        "outline": (255, 255, 255, 60),
        "text": (255, 255, 255, 255),
        "label": (206, 216, 232, 255),
        "rule": (255, 255, 255, 110),
    },
}

# 字体候选：宋体族优先（用户指定「宋体」）→ 其它 CJK 字体 → 纯拉丁兜底。
# 元素为 ``(字体文件, face index)``；bundled 目录（assets/fonts）优先级最高，
# 上线时把 OFL 授权的中文字体放进去即可摆脱对系统字体的依赖。
_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
_FONT_CANDIDATES: tuple[tuple[Path, int], ...] = (
    (_FONT_DIR / "NotoSerifSC-Regular.otf", 0),
    (_FONT_DIR / "NotoSerifSC-Bold.otf", 0),
    (_FONT_DIR / "NotoSansSC-Regular.otf", 0),
    (_FONT_DIR / "NotoSansSC-Bold.otf", 0),
    (_FONT_DIR / "NotoSans-Regular.ttf", 0),
    (_FONT_DIR / "DejaVuSans.ttf", 0),
    # macOS：Songti.ttc 的 face 4 = STSong（标准宋体）
    (Path("/System/Library/Fonts/Supplemental/Songti.ttc"), 4),
    (Path("/System/Library/Fonts/Supplemental/Songti.ttc"), 3),
    # Windows：宋体
    (Path("C:/Windows/Fonts/simsun.ttc"), 0),
    (Path("C:/Windows/Fonts/simsunb.ttf"), 0),
    # Linux：Noto Serif CJK / 文鼎明体
    (Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"), 0),
    (Path("/usr/share/fonts/truetype/arphic/uming.ttc"), 0),
    # CJK 黑体兜底（无宋体时的最后选择）
    (Path("/System/Library/Fonts/PingFang.ttc"), 0),
    (Path("C:/Windows/Fonts/msyh.ttc"), 0),
    (Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), 0),
    # 纯拉丁兜底
    (Path("/System/Library/Fonts/Supplemental/Arial.ttf"), 0),
    (Path("C:/Windows/Fonts/arial.ttf"), 0),
    (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), 0),
)

_resolved_font_path: tuple[bool, Path | None] = (False, None)
_resolved_font_index: int = 0


class SpecCardRenderError(RuntimeError):
    """规格卡渲染失败。调用方按方案 §9 回退干净图，不阻断单款/整批。"""


@dataclass(frozen=True, slots=True)
class SpecCardRequest:
    """一次规格卡渲染的全部输入（``cells`` 为 m×n 自由表格，文本原样印出）。"""

    cells: tuple[tuple[str, ...], ...]
    style: str = STYLE_LIGHT
    corner: str = CORNER_BOTTOM_RIGHT


@dataclass(frozen=True, slots=True)
class SpecCardResult:
    """渲染结果与兜底统计（``cell_count`` = 实际印在卡上的非空单元格数）。"""

    jpeg_bytes: bytes
    font_px: int
    cell_count: int
    dropped_rows: int = 0
    truncated_cells: int = 0
    body_font_px: int = 0


@dataclass(frozen=True, slots=True)
class _CardPlan:
    header_font: Any
    body_font: Any
    font_px: int
    body_font_px: int
    column_widths: tuple[float, ...]
    rows: tuple[tuple[str, ...], ...]
    truncated_cells: int
    dropped_rows: int
    cell_count: int
    width: int
    height: int
    padding: float
    column_gap: float

    def row_font(self, index: int) -> Any:
        return self.header_font if index == 0 else self.body_font

    def row_font_px(self, index: int) -> int:
        return self.font_px if index == 0 else self.body_font_px

    def row_height(self, index: int) -> float:
        return self.row_font_px(index) * _LINE_HEIGHT_RATIO


def row_height_px(side: int) -> int:
    """固定行高：32px @800 画布，随画布等比换算（用户指定）。"""

    return max(12, round(ROW_HEIGHT_PX_AT_800 * side / REFERENCE_SIDE))


def card_target_width(side: int) -> int:
    """卡片目标宽度：画布宽的 1/3（即「九分之一区域」的宽度）。"""

    return max(1, round(side * CARD_TARGET_WIDTH_RATIO))


def header_font_px(side: int, scale: float = 1.0) -> int:
    """首行字号：四号（14pt）为基准，随画布边长与整体 scale 等比放大。"""

    return max(9, round(HEADER_FONT_PX_AT_800 * side / REFERENCE_SIDE * float(scale)))


def body_font_px(side: int, scale: float = 1.0) -> int:
    """其余行字号：小四（12pt）为基准，保持与首行 14:12 的比例。"""

    return max(8, round(BODY_FONT_PX_AT_800 * side / REFERENCE_SIDE * float(scale)))


def render_spec_card(
    base_content: bytes,
    request: SpecCardRequest,
    *,
    font_loader: Callable[[int], Any] | None = None,
) -> SpecCardResult:
    """把 ``request.cells`` 原样合成到底图 ``base_content`` 的指定角落。

    ``base_content`` 必须是正方形图片（800×800 母版），输出为同尺寸 JPEG（q92, 4:4:4）。
    字号严格按规格固定，不随内容缩放；放不下时只做列宽压缩、单元格截断与丢尾行。
    ``font_loader`` 仅供测试注入：``Callable[[font_px], font | None]``，返回 None 视为该字号不可用。
    """

    if request.style not in STYLES:
        raise SpecCardRenderError(f"unknown spec card style: {request.style!r}")
    if request.corner not in CORNERS:
        raise SpecCardRenderError(f"unknown spec card corner: {request.corner!r}")

    rows = _content_rows(request.cells)
    if not rows:
        raise SpecCardRenderError("spec card has no content")

    base = _open_square_base(base_content)
    side = base.width

    margin = max(0, round(side * _MARGIN_RATIO))
    stroke = max(1, round(side * _STROKE_RATIO))
    max_width = max(1, round(side * _CARD_WIDTH_RATIO))
    max_height = max(1, round(side * _CARD_HEIGHT_RATIO))
    loader: Callable[[int], Any] = font_loader if font_loader is not None else _default_font_loader
    head_px = header_font_px(side)
    text_px = body_font_px(side)
    header_font = _load_font(loader, head_px)
    body_font = _load_font(loader, text_px) or header_font
    if header_font is None or body_font is None:
        raise SpecCardRenderError("no usable font for the spec card renderer")
    plan = _plan_card(
        header_font,
        head_px,
        body_font,
        text_px,
        rows,
        max_width=max_width,
        max_height=max_height,
        target_width=card_target_width(side),
        row_height=row_height_px(side),
    )

    composed = _draw_card(
        base,
        plan,
        _PALETTES[request.style],
        side=side,
        margin=margin,
        stroke=stroke,
        corner=request.corner,
    ).convert("RGB")

    output = io.BytesIO()
    composed.save(output, "JPEG", quality=_JPEG_QUALITY, subsampling=0)
    return SpecCardResult(
        jpeg_bytes=output.getvalue(),
        font_px=plan.font_px,
        cell_count=plan.cell_count,
        dropped_rows=plan.dropped_rows,
        truncated_cells=plan.truncated_cells,
        body_font_px=plan.body_font_px,
    )


def _content_rows(cells: Iterable[Iterable[str]]) -> tuple[tuple[str, ...], ...]:
    """去掉整行皆空的空行（§4「空行自动跳过」）；单元格文本本身一律不改动。"""

    if isinstance(cells, (str, bytes, bytearray)):
        raise SpecCardRenderError("spec card cells must be an m×n grid of strings")
    try:
        source = tuple(tuple(row) for row in cells)
    except TypeError as exc:
        raise SpecCardRenderError("spec card cells must be an m×n grid of strings") from exc
    rows: list[tuple[str, ...]] = []
    for row in source:
        normalized = tuple("" if cell is None else str(cell) for cell in row)
        if any(cell.strip() for cell in normalized):
            rows.append(normalized)
    return tuple(rows)


def _open_square_base(base_content: bytes) -> Image.Image:
    if not isinstance(base_content, (bytes, bytearray, memoryview)):
        raise SpecCardRenderError("spec card base image must be bytes")
    if not base_content:
        raise SpecCardRenderError("spec card base image is empty")
    try:
        with Image.open(io.BytesIO(bytes(base_content))) as source:
            source.load()
            image = source.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise SpecCardRenderError("spec card base image is not readable") from exc
    if image.width != image.height:
        raise SpecCardRenderError("spec card base image must be square")
    if image.width < 16:
        raise SpecCardRenderError("spec card base image is too small")
    return image


def _plan_card(
    header_font: Any,
    header_px: int,
    body_font: Any,
    body_px: int,
    rows: tuple[tuple[str, ...], ...],
    *,
    max_width: int,
    max_height: int,
    target_width: int | None = None,
    row_height: int | None = None,
) -> _CardPlan:
    """按固定字号排版：行高固定（默认 32px@800），列宽拉伸到目标宽度，
    超宽按比例压缩 → 仍超则截断加省略号；超高则丢尾行。"""

    padding = body_px * _PADDING_RATIO
    column_gap = padding * _COLUMN_GAP_RATIO
    column_count = max(len(row) for row in rows)
    row_fonts = [header_font if index == 0 else body_font for index in range(len(rows))]
    row_px = [header_px if index == 0 else body_px for index in range(len(rows))]
    # 行高固定：不低于字体所需的 1.72 × 字号
    fixed_row_height = float(row_height) if row_height else 0.0
    row_heights = [max(fixed_row_height, px * _LINE_HEIGHT_RATIO) for px in row_px]

    lengths = [_row_lengths(font, row) for font, row in zip(row_fonts, rows, strict=True)]
    column_widths = [0.0] * column_count
    for row_lengths in lengths:
        for index, length in enumerate(row_lengths):
            if length > column_widths[index]:
                column_widths[index] = length

    natural_width = 2 * padding + sum(column_widths) + (column_count - 1) * column_gap

    # 用户规格：宽度拉到「九分之一区域」的宽度（画布 1/3），列宽按内容比例分摊剩余空间。
    if target_width and natural_width < target_width and target_width <= max_width:
        extra = target_width - natural_width
        total = sum(column_widths)
        if total > 0:
            column_widths = [width + extra * (width / total) for width in column_widths]
        else:
            column_widths = [width + extra / column_count for width in column_widths]
        natural_width = 2 * padding + sum(column_widths) + (column_count - 1) * column_gap

    truncated_cells = 0
    if natural_width > max_width:
        budget = max(0.0, max_width - 2 * padding - (column_count - 1) * column_gap)
        total = sum(column_widths)
        if total > 0:
            # 先按比例压缩，并给每列留出至少一个省略号的宽度。
            minimum = max(1.0, _text_length(header_font, ELLIPSIS))
            column_widths = [max(minimum, width * (budget / total)) for width in column_widths]
            # 极端情况（列数多 + 文本极长）再压一次，硬保证卡片不超宽。
            if 2 * padding + sum(column_widths) + (column_count - 1) * column_gap > max_width:
                total = sum(column_widths)
                if total > 0:
                    column_widths = [width * (budget / total) for width in column_widths]
        rows = tuple(
            tuple(
                _truncate_text(cell, font, column_widths[index])
                if length > column_widths[index]
                else cell
                for index, (cell, length) in enumerate(zip(row, row_lengths, strict=True))
            )
            for font, row, row_lengths in zip(row_fonts, rows, lengths, strict=True)
        )
        truncated_cells = sum(
            1
            for row, row_lengths in zip(rows, lengths, strict=True)
            for index, cell in enumerate(row)
            if cell and row_lengths[index] > column_widths[index]
        )

    # 高度上限：按每行实际行高贪心填充，放不下的尾行丢弃（字号不变）。
    available = max_height - 2 * padding
    kept = 0
    used = 0.0
    for height in row_heights:
        if used + height > available:
            break
        used += height
        kept += 1
    kept = max(1, kept)
    kept_rows = rows[:kept]
    dropped_rows = max(0, len(rows) - len(kept_rows))
    if dropped_rows:
        row_fonts = row_fonts[:kept]
        row_px = row_px[:kept]
        row_heights = row_heights[:kept]
        row_fonts = [header_font] + [body_font] * (kept - 1)  # 首行始终是首行规格

    width = min(max_width, math.ceil(2 * padding + sum(column_widths) + (column_count - 1) * column_gap))
    height = math.ceil(2 * padding + sum(row_heights))
    cell_count = sum(1 for row in kept_rows for cell in row if cell.strip())
    return _CardPlan(
        header_font=header_font,
        body_font=body_font,
        font_px=header_px,
        body_font_px=body_px,
        column_widths=tuple(column_widths),
        rows=kept_rows,
        truncated_cells=truncated_cells,
        dropped_rows=dropped_rows,
        cell_count=cell_count,
        width=width,
        height=height,
        padding=padding,
        column_gap=column_gap,
    )


def _draw_card(
    base: Image.Image,
    plan: _CardPlan,
    palette: dict[str, tuple[int, int, int, int]],
    *,
    side: int,
    margin: int,
    stroke: int,
    corner: str,
) -> Image.Image:
    """在透明 plate 上画卡片，再与底图 alpha_composite（卡片本身不透明）。"""

    left = margin if corner in (CORNER_TOP_LEFT, CORNER_BOTTOM_LEFT) else side - margin - plan.width
    top = margin if corner in (CORNER_TOP_LEFT, CORNER_TOP_RIGHT) else side - margin - plan.height
    left = max(0, min(left, max(0, side - plan.width)))
    top = max(0, min(top, max(0, side - plan.height)))

    plate = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    # 直角卡片：用户明确要求不要圆角（2026-09-10 评审）。
    draw.rectangle(
        (left, top, left + plan.width - 1, top + plan.height - 1),
        fill=palette["surface"],
        outline=palette["outline"],
        width=stroke,
    )

    rule_width = max(2, round(plan.body_font_px * _RULE_WIDTH_RATIO))
    row_top = top + plan.padding
    for index, row in enumerate(plan.rows):
        font = plan.row_font(index)
        if index:  # 首行上方不画分隔线；其余行之间的横线要清晰可见（用户要求）
            rule_y = round(row_top - plan.row_font_px(index) * _RULE_OFFSET_RATIO)
            draw.line(
                (left + plan.padding, rule_y, left + plan.width - plan.padding, rule_y),
                fill=palette["rule"],
                width=rule_width,
            )
        last_filled = max((position for position, cell in enumerate(row) if cell.strip()), default=-1)
        cursor = left + plan.padding
        row_height = plan.row_height(index)
        for column, cell in enumerate(row):
            if cell:
                # 用户规格（2026-09-10）：每列水平居中；数值列用正文色，标签列用次级色。
                is_value = last_filled > 0 and column == last_filled
                color = palette["text"] if is_value or last_filled <= 0 else palette["label"]
                text_width = _text_length(font, cell)
                text_x = cursor + max(0.0, (plan.column_widths[column] - text_width) / 2)
                # 行高固定 32px：文字在行内垂直居中，避免贴顶
                text_y = row_top + max(0.0, (row_height - _text_height(font, cell)) / 2)
                draw.text((text_x, text_y), cell, font=font, fill=color)
            cursor += plan.column_widths[column] + plan.column_gap
        row_top += row_height

    return Image.alpha_composite(base, plate)


def _row_lengths(font: Any, row: Sequence[str]) -> list[float]:
    return [_text_length(font, cell) for cell in row]


def _text_length(font: Any, text: str) -> float:
    if not text:
        return 0.0
    try:
        return float(font.getlength(text))
    except AttributeError:  # 极老/自定义 font 对象兜底
        return float(len(text))


def _text_height(font: Any, text: str) -> float:
    """文字行高（用于固定行高内的垂直居中）。"""

    try:
        left, top, right, bottom = font.getbbox(text)
        return float(bottom - top)
    except Exception:  # noqa: BLE001 - 自定义 font 对象兜底
        try:
            return float(font.size)
        except AttributeError:
            return 0.0


def _truncate_text(text: str, font: Any, limit: float) -> str:
    """只截断，绝不换行/改写：取能容下的最长前缀 + '…'。"""

    ellipsis_width = _text_length(font, ELLIPSIS)
    if limit <= ellipsis_width:
        return ELLIPSIS
    budget = limit - ellipsis_width
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if _text_length(font, text[:middle]) <= budget:
            low = middle
        else:
            high = middle - 1
    if low <= 0:
        return ELLIPSIS
    return f"{text[:low]}{ELLIPSIS}"


def _resolve_font_entry() -> tuple[Path, int] | None:
    """bundled 宋体优先，其次系统宋体/CJK/拉丁候选；结果缓存（同进程内视为不变）。"""

    global _resolved_font_path, _resolved_font_index
    resolved, path = _resolved_font_path
    if resolved:
        return None if path is None else (path, _resolved_font_index)
    found: tuple[Path, int] | None = None
    for candidate, index in _FONT_CANDIDATES:
        try:
            if candidate.is_file():
                found = (candidate, int(index))
                break
        except OSError:
            continue
    _resolved_font_path = (True, None if found is None else found[0])
    _resolved_font_index = 0 if found is None else found[1]
    return found


def _default_font_loader(size: int) -> Any | None:
    entry = _resolve_font_entry()
    if entry is None:
        return None
    path, index = entry
    try:
        return ImageFont.truetype(str(path), size, index=index)
    except (OSError, ValueError):
        return None


def _load_font(loader: Callable[[int], Any], size: int) -> Any | None:
    try:
        font = loader(size)
    except Exception:  # noqa: BLE001 - 任何字体加载失败都按「该字号不可用」处理
        return None
    return font if font is not None else None
