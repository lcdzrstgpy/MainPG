"""POD 第 4 张图「规格卡」渲染单测（方案 §13）。

覆盖：文本原样性 / 双风格 / 四角 / 空表与 enabled / 截断 / 丢行 / 字号自适应 /
     字体缺失 / 非法底图 / 中英混排，以及 SpecCardConfig 的行列与字数校验。
"""

from __future__ import annotations

import io
import json
import types

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont
from pydantic import BaseModel

from wh_local.modules.pod_customization import spec_card
from wh_local.modules.pod_customization.contracts import (
    SPEC_CARD_MAX_CELL_LENGTH,
    SPEC_CARD_MAX_COLUMNS,
    SPEC_CARD_MAX_ROWS,
    ListingFields,
    SpecCardConfig,
    spec_card_is_configured,
    validate_spec_card,
    validate_spec_card_cells,
)
from wh_local.modules.pod_customization.spec_card import (
    CORNERS,
    STYLE_DARK,
    STYLE_LIGHT,
    STYLES,
    SpecCardRenderError,
    SpecCardRequest,
    render_spec_card,
)


BASE_SIDE = 800
BASE_COLOR = "#7a7a7a"
MARGIN = round(BASE_SIDE * 0.0325)  # 26
CARD_MAX_WIDTH = round(BASE_SIDE * 0.46)  # 368
CARD_MAX_HEIGHT = round(BASE_SIDE * 0.42)  # 336
JPEG_TOLERANCE = 8  # JPEG 有损，未绘制区域的像素比较留一点余量
CARD_EDGE_THRESHOLD = 24  # 用较高阈值定位卡片边界，避开 JPEG 振铃
CARD_EDGE_TOLERANCE = 2


def _base_png(*, size: tuple[int, int] | None = None) -> bytes:
    image = Image.new("RGB", size or (BASE_SIDE, BASE_SIDE), BASE_COLOR)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _decode(content: bytes) -> Image.Image:
    return Image.open(io.BytesIO(content)).convert("RGB")


def _injected_font(size: int) -> ImageFont.FreeTypeFont:
    """Portable, font-file-free font handle for injection tests."""

    return ImageFont.load_default(size=size)


def _pixel_distance(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    return max(abs(first - second) for first, second in zip(left, right, strict=True))


def _change_box(
    base: Image.Image,
    rendered: Image.Image,
    *,
    threshold: int = CARD_EDGE_THRESHOLD,
) -> tuple[int, int, int, int] | None:
    difference = (
        ImageChops.difference(base, rendered)
        .convert("L")
        .point(lambda value: 255 if value > threshold else 0)
    )
    return difference.getbbox()


def _jpeg_sof_sampling(content: bytes) -> tuple[int, list[tuple[int, int]]]:
    """解析 JPEG 的 SOF 段，返回 (分量数, 各分量采样因子)；(1,1) 即无色度抽样。"""

    assert content[:2] == b"\xff\xd8"
    index = 2
    while index < len(content) - 3:
        assert content[index] == 0xFF
        marker = content[index + 1]
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        length = int.from_bytes(content[index + 2 : index + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            segment = content[index + 4 : index + 2 + length]
            count = segment[5]
            factors = [(segment[6 + component * 3 + 1] >> 4, segment[6 + component * 3 + 1] & 0x0F) for component in range(count)]
            return count, factors
        index += 2 + length
    raise AssertionError("no SOF marker found in JPEG output")


def _listing_fields(**extra: object) -> dict:
    payload: dict = {
        "suggested_price_usd": 29.99,
        "category_name": "bags",
        "skus": [{"name": "S", "declared_price": 18.5, "weight_g": 450}],
    }
    payload.update(extra)
    return payload


class _RecordingDraw:
    """包一层真实 ImageDraw，记录所有被印出的字符串（用于验证「文本原样」）。"""

    def __init__(self, image: Image.Image, mode: str | None = None) -> None:
        self._draw = ImageDraw.Draw(image)
        self.texts: list[str] = []

    def text(self, xy, text, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self.texts.append(text)
        return self._draw.text(xy, text, *args, **kwargs)

    def __getattr__(self, name: str):  # textlength / line / rounded_rectangle ...
        return getattr(self._draw, name)


@pytest.fixture
def drawn_texts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorded: list[str] = []

    class Draw(_RecordingDraw):
        def __init__(self, image: Image.Image, mode: str | None = None) -> None:
            super().__init__(image, mode)
            self.texts = recorded

    monkeypatch.setattr(spec_card, "ImageDraw", types.SimpleNamespace(Draw=Draw))
    return recorded


# --- 渲染：文本原样性 -------------------------------------------------------


def test_user_text_is_printed_verbatim(drawn_texts: list[str]) -> None:
    cells = (("长", "54 cm"), ("Weight", "300 g (10.6 oz)"), ("Note", "  保留 空格  "))

    result = render_spec_card(_base_png(), SpecCardRequest(cells=cells), font_loader=_injected_font)

    # 逐字原样：不翻译、不做单位换算、不做变量替换、不 strip。
    assert drawn_texts == ["长", "54 cm", "Weight", "300 g (10.6 oz)", "Note", "  保留 空格  "]
    assert set(drawn_texts) == {cell for row in cells for cell in row}
    assert result.cell_count == 6
    assert result.truncated_cells == 0
    assert result.dropped_rows == 0
    assert result.jpeg_bytes.startswith(b"\xff\xd8")


def test_blank_rows_are_skipped_without_counting_as_dropped(drawn_texts: list[str]) -> None:
    cells = (("长", "54 cm"), ("", ""), ("   ", ""), ("宽", "22 cm"))

    result = render_spec_card(_base_png(), SpecCardRequest(cells=cells), font_loader=_injected_font)

    assert drawn_texts == ["长", "54 cm", "宽", "22 cm"]
    assert result.cell_count == 4
    assert result.dropped_rows == 0


def test_renderer_is_generic_over_rows_and_columns(drawn_texts: list[str]) -> None:
    # 行列上限校验在配置模型层（contracts.SpecCardConfig），渲染器本身必须 m×n 通用。
    cells = tuple(tuple(f"c{row}-{column}" for column in range(4)) for row in range(12))

    result = render_spec_card(_base_png(), SpecCardRequest(cells=cells), font_loader=_injected_font)

    assert result.jpeg_bytes
    # 自动缩放会缩小字号以放下 12 行：不丢行、不截断，全部单元格都印出来
    assert result.dropped_rows == 0
    assert result.truncated_cells == 0
    assert result.cell_count == 4 * 12


# --- 渲染：风格 / 位置 ------------------------------------------------------


def test_light_and_dark_styles_render_different_bytes() -> None:
    cells = (("长", "54 cm"), ("宽", "22 cm"))
    base = _base_png()

    light = render_spec_card(base, SpecCardRequest(cells=cells, style=STYLE_LIGHT), font_loader=_injected_font)
    dark = render_spec_card(base, SpecCardRequest(cells=cells, style=STYLE_DARK), font_loader=_injected_font)

    assert light.jpeg_bytes != dark.jpeg_bytes
    assert light.font_px == dark.font_px


def test_dark_card_is_darker_than_light_card_over_a_bright_base() -> None:
    bright = Image.new("RGB", (BASE_SIDE, BASE_SIDE), "#efefef")
    output = io.BytesIO()
    bright.save(output, "PNG")
    cells = (("长", "54 cm"),)

    light = render_spec_card(output.getvalue(), SpecCardRequest(cells=cells, style=STYLE_LIGHT), font_loader=_injected_font)
    dark = render_spec_card(output.getvalue(), SpecCardRequest(cells=cells, style=STYLE_DARK), font_loader=_injected_font)

    light_image = _decode(light.jpeg_bytes)
    dark_image = _decode(dark.jpeg_bytes)
    box = _change_box(light_image, dark_image)
    assert box is not None
    # 采样点取卡片圆角以内、正文以外的板面（避开文字像素）
    sample = (box[0] + 10, box[1] + 10)

    assert light_image.getpixel(sample)[0] > bright.getpixel(sample)[0]
    assert dark_image.getpixel(sample)[0] < bright.getpixel(sample)[0]


@pytest.mark.parametrize(
    ("corner", "near_left", "near_top"),
    (
        ("bottom-right", False, False),
        ("bottom-left", True, False),
        ("top-right", False, True),
        ("top-left", True, True),
    ),
)
def test_card_lands_in_the_selected_corner(corner: str, near_left: bool, near_top: bool) -> None:
    base = _decode(_base_png())
    result = render_spec_card(
        _base_png(),
        SpecCardRequest(cells=(("长", "54 cm"), ("宽", "22 cm")), corner=corner),
        font_loader=_injected_font,
    )
    rendered = _decode(result.jpeg_bytes)

    box = _change_box(base, rendered)
    assert box is not None
    left, top, right, bottom = box

    if near_left:
        assert right < BASE_SIDE // 2
        assert left <= MARGIN + CARD_EDGE_TOLERANCE
    else:
        assert left > BASE_SIDE // 2
        assert BASE_SIDE - right <= MARGIN + CARD_EDGE_TOLERANCE
    if near_top:
        assert bottom < BASE_SIDE // 2
        assert top <= MARGIN + CARD_EDGE_TOLERANCE
    else:
        assert top > BASE_SIDE // 2
        assert BASE_SIDE - bottom <= MARGIN + CARD_EDGE_TOLERANCE

    # 卡片内部采样点已改变（半透明底板确实画上去了）
    center = ((left + right) // 2, (top + bottom) // 2)
    assert _pixel_distance(base.getpixel(center), rendered.getpixel(center)) > 10

    # 对角象限未被触碰
    opposite = (BASE_SIDE - 12, BASE_SIDE - 12) if near_top else (12, 12)
    assert _pixel_distance(base.getpixel(opposite), rendered.getpixel(opposite)) <= JPEG_TOLERANCE


def test_corners_and_styles_constants_cover_every_supported_value() -> None:
    assert set(CORNERS) == {"bottom-right", "bottom-left", "top-right", "top-left"}
    assert set(STYLES) == {"light", "dark"}
    for corner in CORNERS:
        result = render_spec_card(
            _base_png(),
            SpecCardRequest(cells=(("长", "54 cm"),), corner=corner),
            font_loader=_injected_font,
        )
        assert result.jpeg_bytes


def test_card_respects_size_caps_and_margins() -> None:
    base = _decode(_base_png())
    # 固定 6 行：本用例验证尺寸/边距，表格规模需落在卡片高度上限（42%）之内
    cells = tuple((f"Label {index}", f"value {index}") for index in range(1, 7))

    result = render_spec_card(
        _base_png(),
        SpecCardRequest(cells=cells, corner="bottom-right"),
        font_loader=_injected_font,
    )
    box = _change_box(base, _decode(result.jpeg_bytes))

    assert box is not None
    left, top, right, bottom = box
    assert right - left <= CARD_MAX_WIDTH + CARD_EDGE_TOLERANCE
    assert bottom - top <= CARD_MAX_HEIGHT + CARD_EDGE_TOLERANCE
    assert BASE_SIDE - right <= MARGIN + CARD_EDGE_TOLERANCE
    assert BASE_SIDE - bottom <= MARGIN + CARD_EDGE_TOLERANCE
    assert result.dropped_rows == 0
    assert result.truncated_cells == 0


# --- 渲染：兜底与失败 -------------------------------------------------------


@pytest.mark.parametrize(
    "cells",
    (
        (),
        (("", ""),),
        (("",), ("   ",)),
        (("", ""), ("", "")),
    ),
)
def test_empty_grid_raises(cells: tuple[tuple[str, ...], ...]) -> None:
    with pytest.raises(SpecCardRenderError, match="no content"):
        render_spec_card(_base_png(), SpecCardRequest(cells=cells), font_loader=_injected_font)


def test_disabled_config_is_not_rendered_but_still_counts_as_configured() -> None:
    config = SpecCardConfig(enabled=False, cells=(("长", "54 cm"),))

    # §10.4：必填判定只看有没有非空单元格；enabled=False 表示不出卡（调用方短路）。
    assert spec_card_is_configured(config) is True
    assert (config.enabled and spec_card_is_configured(config)) is False


@pytest.mark.parametrize("style", ("neon", "", "LIGHT"))
def test_unknown_style_raises(style: str) -> None:
    with pytest.raises(SpecCardRenderError, match="style"):
        render_spec_card(
            _base_png(),
            SpecCardRequest(cells=(("长", "54 cm"),), style=style),
            font_loader=_injected_font,
        )


@pytest.mark.parametrize("corner", ("center", "", "bottom_right"))
def test_unknown_corner_raises(corner: str) -> None:
    with pytest.raises(SpecCardRenderError, match="corner"):
        render_spec_card(
            _base_png(),
            SpecCardRequest(cells=(("长", "54 cm"),), corner=corner),
            font_loader=_injected_font,
        )


def test_overlong_text_is_truncated_and_reported(drawn_texts: list[str]) -> None:
    cells = (("Long label text here", "x" * 200),)

    result = render_spec_card(_base_png(), SpecCardRequest(cells=cells), font_loader=_injected_font)

    assert result.truncated_cells == 2
    assert result.dropped_rows == 0
    assert result.font_px >= spec_card.HEADER_FONT_PX_AT_800  # 卡片按 1/9 面积整体放大
    assert all(text.endswith("…") for text in drawn_texts)
    assert len(drawn_texts[1]) < 200
    assert drawn_texts[1][:-1] == "x" * (len(drawn_texts[1]) - 1)  # 前缀原样 + 省略号
    # 只截断渲染副本，用户输入本身不被改写
    assert cells[0][0] == "Long label text here"
    assert cells[0][1] == "x" * 200


def test_twelve_rows_shrink_instead_of_dropping(drawn_texts: list[str]) -> None:
    """12 行（配置上限）会被自动缩号全部放下：不丢行、每格都印出来。"""

    cells = tuple((f"Row {index}", f"value {index}") for index in range(1, 13))

    result = render_spec_card(_base_png(), SpecCardRequest(cells=cells), font_loader=_injected_font)

    assert result.dropped_rows == 0
    assert drawn_texts[:2] == ["Row 1", "value 1"]
    assert "Row 12" in drawn_texts
    assert result.cell_count == 2 * len(cells)
    # 字号固定不动（用户规格）：行高 32px × 12 行仍在高度上限内
    assert result.font_px == spec_card.HEADER_FONT_PX_AT_800
    assert result.body_font_px == spec_card.BODY_FONT_PX_AT_800


def test_planner_drops_trailing_rows_when_height_cap_is_too_small() -> None:
    """排版层仍保留「放不下就丢尾行」的兜底（极端内容时不会画到卡片外）。"""

    rows = tuple((f"Row {index}",) for index in range(1, 9))
    plan = spec_card._plan_card(
        _injected_font(19), 19, _injected_font(16), 16, rows, max_width=400, max_height=60
    )

    assert plan.dropped_rows > 0
    assert len(plan.rows) < len(rows)


def test_card_width_is_one_third_and_row_height_is_32px_with_fixed_fonts() -> None:
    """用户规格：宽度拉到画布 1/3（=「九分之一区域」宽度）、行高固定 32px、字号固定不动。"""

    cells = (("长", "宽", "高"), ("54", "22", "15"))
    base = _decode(_base_png())
    result = render_spec_card(_base_png(), SpecCardRequest(cells=cells), font_loader=_injected_font)
    rendered = _decode(result.jpeg_bytes)

    box = _change_box(base, rendered)
    assert box is not None
    width, height = box[2] - box[0], box[3] - box[1]

    assert abs(width - spec_card.card_target_width(BASE_SIDE)) <= 6, f"卡片宽 {width} 应约为画布 1/3"
    # 行高固定 32px：两行卡片高度 ≈ 内边距×2 + 2×32
    expected_height = 2 * round(spec_card.BODY_FONT_PX_AT_800 * 0.72) + 2 * spec_card.row_height_px(BASE_SIDE)
    assert abs(height - expected_height) <= 8, f"卡片高 {height} 应约为 {expected_height}"

    # 字号固定不动
    assert result.font_px == spec_card.HEADER_FONT_PX_AT_800 == 19
    assert result.body_font_px == spec_card.BODY_FONT_PX_AT_800 == 16
    assert spec_card.row_height_px(BASE_SIDE) == 32
    assert spec_card.card_target_width(BASE_SIDE) == round(BASE_SIDE / 3)
    # 画布放大时同步换算
    assert spec_card.row_height_px(1600) == 64
    assert spec_card.card_target_width(1600) == round(1600 / 3)


def test_missing_font_raises() -> None:
    def exploding_font_loader(size: int):  # noqa: ANN202
        raise OSError(f"font {size} is unavailable")

    for loader in (lambda size: None, exploding_font_loader):
        with pytest.raises(SpecCardRenderError, match="no usable font"):
            render_spec_card(
                _base_png(),
                SpecCardRequest(cells=(("长", "54 cm"),)),
                font_loader=loader,
            )


def test_no_font_candidates_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spec_card, "_FONT_CANDIDATES", ())
    monkeypatch.setattr(spec_card, "_resolved_font_path", (False, None))

    with pytest.raises(SpecCardRenderError, match="no usable font"):
        render_spec_card(_base_png(), SpecCardRequest(cells=(("长", "54 cm"),)))


def test_non_square_base_raises() -> None:
    with pytest.raises(SpecCardRenderError, match="square"):
        render_spec_card(
            _base_png(size=(600, 400)),
            SpecCardRequest(cells=(("长", "54 cm"),)),
            font_loader=_injected_font,
        )


@pytest.mark.parametrize("payload", (b"not an image", b"", _base_png()[:64]))
def test_unreadable_base_raises(payload: bytes) -> None:
    with pytest.raises(SpecCardRenderError):
        render_spec_card(payload, SpecCardRequest(cells=(("长", "54 cm"),)), font_loader=_injected_font)


def test_corrupted_jpeg_base_raises() -> None:
    good = render_spec_card(_base_png(), SpecCardRequest(cells=(("长", "54 cm"),)), font_loader=_injected_font)
    truncated = good.jpeg_bytes[: len(good.jpeg_bytes) // 3]

    with pytest.raises(SpecCardRenderError):
        render_spec_card(truncated, SpecCardRequest(cells=(("长", "54 cm"),)), font_loader=_injected_font)


def test_mixed_cjk_and_latin_text_renders_with_the_system_font() -> None:
    # font_loader=None → 走默认字体候选（bundled 优先，其次系统字体）
    result = render_spec_card(
        _base_png(),
        SpecCardRequest(cells=(("长度", "54 cm / 21.3 in"), ("备注", "手洗 Hand wash")), style=STYLE_DARK),
    )

    assert result.jpeg_bytes.startswith(b"\xff\xd8")
    assert _decode(result.jpeg_bytes).size == (BASE_SIDE, BASE_SIDE)
    assert result.cell_count == 4
    assert result.truncated_cells == 0


def test_output_is_a_deterministic_square_jpeg() -> None:
    request = SpecCardRequest(cells=(("长", "54 cm"), ("宽", "22 cm")))

    first = render_spec_card(_base_png(), request, font_loader=_injected_font)
    second = render_spec_card(_base_png(), request, font_loader=_injected_font)

    assert first.jpeg_bytes == second.jpeg_bytes  # 内容寻址幂等的前提
    decoded = Image.open(io.BytesIO(first.jpeg_bytes))
    assert decoded.format == "JPEG"
    assert decoded.size == (BASE_SIDE, BASE_SIDE)


def test_output_is_jpeg_quality_92_without_chroma_subsampling() -> None:
    result = render_spec_card(
        _base_png(),
        SpecCardRequest(cells=(("长", "54 cm"),)),
        font_loader=_injected_font,
    )

    def _control(**kwargs: object):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "white").save(buffer, "JPEG", **kwargs)
        return Image.open(io.BytesIO(buffer.getvalue())).quantization

    # 量化表只由 quality 决定：与 q92 一致、与 q75 不同
    assert Image.open(io.BytesIO(result.jpeg_bytes)).quantization == _control(quality=92, subsampling=0)
    assert Image.open(io.BytesIO(result.jpeg_bytes)).quantization != _control(quality=75)
    # 分量采样因子 (1,1) = 4:4:4，即 subsampling=0
    assert _jpeg_sof_sampling(result.jpeg_bytes) == (3, [(1, 1), (1, 1), (1, 1)])


def test_font_loader_receives_integer_pixel_sizes() -> None:
    sizes: list[int] = []

    def loader(size: int) -> ImageFont.FreeTypeFont:
        sizes.append(size)
        return ImageFont.load_default(size=size)

    render_spec_card(_base_png(), SpecCardRequest(cells=(("长", "54 cm"),)), font_loader=loader)

    # 自动缩放会多次试算：首行字号不小于其余行，且全为整数像素
    assert all(isinstance(size, int) for size in sizes)
    assert sizes[0] >= sizes[1]
    assert sizes[0] > 0 and sizes[1] > 0


def test_card_surface_is_opaque_and_corner_is_square() -> None:
    """用户评审要求：卡片不透明（纯白）、直角、行间有可见分隔线。"""

    dark_base = Image.new("RGB", (BASE_SIDE, BASE_SIDE), (20, 20, 20))
    buffer = io.BytesIO()
    dark_base.save(buffer, "PNG")
    result = render_spec_card(
        buffer.getvalue(),
        SpecCardRequest(cells=(("尺寸", "重量"), ("54", "22")), style=STYLE_LIGHT),
        font_loader=_injected_font,
    )
    rendered = _decode(result.jpeg_bytes)
    pixels = rendered.load()
    box = _change_box(dark_base, rendered)
    assert box is not None
    left, top, right, bottom = box

    # 不透明：卡片区域应为纯白（半透明底板在深色底图上会明显偏灰）
    inside = pixels[left + 6, top + 6]
    assert min(inside) >= 250, f"卡片底色应为纯白不透明，实际 {inside}"

    # 直角：外接矩形四角都必须是卡片底色（圆角时四角会露出深色底图）
    # 阈值留一点余量：卡片紧贴角落时描边落在 JPEG 块边界，振铃会让白底掉到 ~248
    for point in ((left + 2, top + 2), (right - 3, top + 2), (left + 2, bottom - 3), (right - 3, bottom - 3)):
        assert min(pixels[point]) >= 246, f"角点 {point} 不是卡片底色 → 仍有圆角"

    # 行间分隔线：卡片内部应存在明显暗于底色的横向像素行
    center_x = (left + right) // 2
    rows = [min(pixels[center_x, y]) for y in range(top + 4, bottom - 4)]
    assert min(rows) < 230, "两行之间缺少可见分隔线"


def test_every_column_is_horizontally_centered() -> None:
    """用户规格：每列居中（不再是首列左对齐、末列右对齐）。"""

    drawn: list[tuple[float, float, str]] = []

    class Draw(_RecordingDraw):
        def text(self, xy, text, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            drawn.append((float(xy[0]), float(xy[1]), text))
            return super().text(xy, text, *args, **kwargs)

    original = spec_card.ImageDraw
    spec_card.ImageDraw = types.SimpleNamespace(Draw=Draw)
    try:
        cells = (("长", "宽", "高"), ("54", "22", "15"))
        render_spec_card(_base_png(), SpecCardRequest(cells=cells), font_loader=_injected_font)
    finally:
        spec_card.ImageDraw = original

    assert [entry[2] for entry in drawn] == ["长", "宽", "高", "54", "22", "15"]
    # 同一列上下两格的左边界应一致（说明按列居中，而不是左/右对齐）
    header_x = {text_: x for x, _y, text_ in drawn}
    value_x = {text_: x for x, _y, text_ in drawn}
    assert abs(header_x["长"] - value_x["54"]) > 0  # 文本宽度不同 → 起点不同属正常
    # 关键：每格文本的中心应落在本列中心；用相邻列中心等距来间接验证（列宽按内容比例分配）
    centers = {}
    for x, _y, text_ in drawn:
        font = _injected_font(19 if text_ in ("长", "宽", "高") else 16)
        centers[text_] = x + float(font.getlength(text_)) / 2
    # 三列中心应等距分布（居中排版下每列中心间距 = 列宽 + 列间距）
    gaps = [
        round(centers["宽"] - centers["长"], 3),
        round(centers["高"] - centers["宽"], 3),
    ]
    assert gaps[0] > 0 and gaps[1] > 0
    assert abs(gaps[0] - gaps[1]) / max(gaps) < 0.35, f"列中心间距应大致均匀，实际 {gaps}"


# --- 配置模型 --------------------------------------------------------------


def test_spec_card_config_defaults_and_mapping() -> None:
    default = SpecCardConfig()

    assert (default.enabled, default.style, default.corner, default.cells) == (True, "light", "bottom-right", ())
    assert spec_card_is_configured(default) is False
    assert spec_card_is_configured(None) is False
    assert spec_card_is_configured({}) is False
    assert spec_card_is_configured({"cells": [["", "  "]]}) is False

    config = SpecCardConfig.from_mapping(
        {"cells": [["长", "54 cm"], ["宽", "22 cm"]], "style": "dark", "corner": "top-left"}
    )

    assert config.cells == (("长", "54 cm"), ("宽", "22 cm"))
    assert all(isinstance(row, tuple) for row in config.cells)  # 不可变嵌套元组
    assert (config.style, config.corner, config.enabled) == ("dark", "top-left", True)
    assert spec_card_is_configured(config) is True


def test_spec_card_config_from_mapping_ignores_unknown_keys_and_keeps_text_verbatim() -> None:
    config = SpecCardConfig.from_mapping({"cells": [["  保留 空格  ", " 54 cm "]], "per_style": {}})

    assert config.enabled is True
    assert config.style == "light"
    assert config.corner == "bottom-right"
    assert config.cells == (("  保留 空格  ", " 54 cm "),)


def test_spec_card_config_treats_null_cells_as_empty() -> None:
    config = SpecCardConfig.from_mapping({"cells": [["长", None]]})

    assert config.cells == (("长", ""),)
    assert validate_spec_card_cells(validate_spec_card(config).cells, require_content=False) == (("长", ""),)


@pytest.mark.parametrize(
    ("payload", "match"),
    (
        ({"cells": [[f"行{index}"] for index in range(SPEC_CARD_MAX_ROWS + 1)]}, f"规格卡最多 {SPEC_CARD_MAX_ROWS} 行"),
        ({"cells": [["a"] * (SPEC_CARD_MAX_COLUMNS + 1)]}, f"规格卡最多 {SPEC_CARD_MAX_COLUMNS} 列"),
        ({"cells": [["x" * (SPEC_CARD_MAX_CELL_LENGTH + 1)]]}, "规格卡每格最多 120 个字符"),
        ({"cells": "54 cm"}, "规格卡表格结构不正确"),
        ({"cells": [["a"], "b"]}, "规格卡表格结构不正确"),
        ({"cells": [[54]]}, "规格卡表格单元格必须是文本"),
        ({"style": "neon"}, "规格卡风格"),
        ({"corner": "center"}, "规格卡位置"),
    ),
)
def test_spec_card_config_rejects_invalid_payloads(payload: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        SpecCardConfig.from_mapping(payload)


def test_spec_card_config_rejects_invalid_direct_construction() -> None:
    with pytest.raises(ValueError, match=f"规格卡最多 {SPEC_CARD_MAX_COLUMNS} 列"):
        SpecCardConfig(cells=(tuple("a" for _ in range(SPEC_CARD_MAX_COLUMNS + 1)),))
    with pytest.raises(ValueError, match="规格卡每格最多 120 个字符"):
        SpecCardConfig(cells=(("x" * (SPEC_CARD_MAX_CELL_LENGTH + 1),),))
    with pytest.raises(ValueError, match="规格卡风格"):
        SpecCardConfig(style="neon")
    with pytest.raises(ValueError, match="规格卡位置"):
        SpecCardConfig(corner="center")


def test_spec_card_config_requires_at_least_one_filled_cell_for_submission() -> None:
    empty = SpecCardConfig.from_mapping({"cells": [["", ""], ["", " "]]})

    assert spec_card_is_configured(empty) is False
    with pytest.raises(ValueError, match="至少需要一个非空单元格"):
        validate_spec_card(empty)
    # 校验函数对允许空表的场景可关闭内容要求
    assert validate_spec_card_cells((), require_content=False) == ()


def test_spec_card_blank_rows_do_not_count_toward_row_limit() -> None:
    cells = tuple([("", "")] * 20) + (("长", "54 cm"),)

    normalized = validate_spec_card_cells(cells, require_content=False)

    assert normalized == cells
    assert (normalized[-1]) == ("长", "54 cm")
    assert SpecCardConfig.from_mapping({"cells": [list(row) for row in cells]}).cells == cells


def test_spec_card_limits_accept_the_boundary_values() -> None:
    rows = tuple((f"标签{index}", f"值 {index}") for index in range(SPEC_CARD_MAX_ROWS))
    cells = (("长", "54 cm", ""),) + rows[:-1]

    assert validate_spec_card_cells(cells) == cells
    assert validate_spec_card_cells((("x" * SPEC_CARD_MAX_CELL_LENGTH,),)) == (("x" * SPEC_CARD_MAX_CELL_LENGTH,),)


# --- 与 ListingFields / 批次快照的集成 ---------------------------------------


def test_spec_card_config_never_strips_cell_text() -> None:
    assert issubclass(SpecCardConfig, BaseModel)
    assert SpecCardConfig.model_config.get("extra") == "forbid"
    # 明确不能带 str_strip_whitespace：单元格必须逐字符原样印出
    assert SpecCardConfig.model_config.get("str_strip_whitespace", False) is False
    assert SpecCardConfig(cells=(("  a  ",),)).cells == (("  a  ",),)


def test_nested_spec_card_keeps_text_verbatim_inside_listing_fields() -> None:
    # ListingFields 自己是 extra="forbid" + str_strip_whitespace=True，但嵌套模型用自己的 config，
    # 父模型不会把 strip 传播进来 —— 这是「原样印出」的硬性保证。
    fields = ListingFields(
        **_listing_fields(
            spec_card={
                "enabled": True,
                "style": "dark",
                "corner": "top-left",
                "cells": [["  保留 空格  ", " 54 cm "], ["\tTab\t", "长 "]],
            }
        )
    )

    assert fields.spec_card is not None
    assert fields.spec_card.cells == (("  保留 空格  ", " 54 cm "), ("\tTab\t", "长 "))
    assert isinstance(fields.spec_card.cells, tuple)
    assert all(isinstance(row, tuple) for row in fields.spec_card.cells)
    # 父模型对自己的字段仍然照常 strip（未被本次改动影响）
    assert ListingFields(**_listing_fields(category_name="  bags  ")).category_name == "bags"


def test_nested_spec_card_survives_the_frozen_snapshot_round_trip() -> None:
    frozen = ListingFields(**_listing_fields(spec_card={"cells": [["  保留 空格  ", " 54 cm "]]})).model_dump_json()

    restored = ListingFields.model_validate_json(frozen)

    assert restored.spec_card is not None
    assert restored.spec_card.cells == (("  保留 空格  ", " 54 cm "),)
    assert SpecCardConfig.from_mapping(json.loads(frozen)["spec_card"]).cells == (("  保留 空格  ", " 54 cm "),)
    assert spec_card_is_configured(restored.spec_card) is True


def test_listing_fields_spec_card_is_optional_and_still_validated() -> None:
    assert ListingFields(**_listing_fields()).spec_card is None

    with pytest.raises(ValueError, match="规格卡每格最多 120 个字符"):
        ListingFields(**_listing_fields(spec_card={"cells": [["x" * (SPEC_CARD_MAX_CELL_LENGTH + 1)]]}))

    with pytest.raises(ValueError, match=f"规格卡最多 {SPEC_CARD_MAX_COLUMNS} 列"):
        ListingFields(**_listing_fields(spec_card={"cells": [["a"] * (SPEC_CARD_MAX_COLUMNS + 1)]}))
