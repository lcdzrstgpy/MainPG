"""图片清晰化（media_enhance）单测：能力边界 + 拆图接线 + 回归保护。"""

from __future__ import annotations

import inspect
from io import BytesIO
from typing import Any

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat, JpegImagePlugin

from wh_local.media_enhance import (
    DEFAULT_PERCENT,
    DEFAULT_RADIUS,
    DEFAULT_THRESHOLD,
    EnhanceOptions,
    options_from_env,
    sharpen,
    sharpness_score,
)
from wh_local.modules.pod_customization.ai_runtime import PodCustomizationAiRuntime
from wh_local.modules.product_processing.infrastructure.media import (
    DXM_IMAGE_TARGET_SIZE,
    GeneratedMedia,
    ProductImageProcessor,
)


def _grid_bytes(size: int = 2048, *, lines: bool = False) -> bytes:
    """四格各自不同底色；``lines=True`` 时叠加细线棋盘（模拟雕刻/版画细节）。

    四格底色必须不同：拆图有"面板独立性"校验，四格完全相同会被判为无效传输网格。

    线条用 6px 宽 / 48px 间隔，刻意避开"1px 线贴满整块"的近奈奎斯特图案——那种
    极端内容会触发 Pillow `optimize=True` 的既有编码缺陷（OSError: broken data
    stream）。该缺陷另有专门的回归用例 ``_pathological_grid_bytes`` 覆盖。
    """

    image = Image.new("RGB", (size, size), "white")
    half = size // 2
    colors = [(210, 30, 30), (30, 180, 50), (30, 70, 210), (220, 180, 30)]
    for index, color in enumerate(colors):
        left = (index % 2) * half
        top = (index // 2) * half
        image.paste(color, (left + 8, top + 8, left + half - 8, top + half - 8))
        if not lines:
            continue
        drawing = ImageDraw.Draw(image)
        for offset in range(24, half - 24, 48):
            drawing.line((left + offset, top + 14, left + offset, top + half - 14), fill="white", width=6)
            drawing.line((left + 14, top + offset, left + half - 14, top + offset), fill="white", width=6)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _pathological_grid_bytes(size: int = 2048) -> bytes:
    """近奈奎斯特图案（1px 线 / 6px 间隔）：稳定触发 Pillow ``optimize=True`` 缺陷。"""

    image = Image.new("RGB", (size, size), "white")
    half = size // 2
    colors = [(210, 30, 30), (30, 180, 50), (30, 70, 210), (220, 180, 30)]
    for index, color in enumerate(colors):
        left = (index % 2) * half
        top = (index // 2) * half
        image.paste(color, (left + 8, top + 8, left + half - 8, top + half - 8))
        drawing = ImageDraw.Draw(image)
        for offset in range(16, half - 16, 6):
            drawing.line((left + offset, top + 14, left + offset, top + half - 14), fill="white", width=1)
            drawing.line((left + 14, top + offset, left + half - 14, top + offset), fill="white", width=1)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _media(content: bytes) -> GeneratedMedia:
    return GeneratedMedia(
        stage="grid_image",
        content=content,
        content_type="image/png",
        suffix=".png",
        provider="fake",
        model="fake",
        reference_count=1,
    )


def _open(content: bytes) -> Image.Image:
    return Image.open(BytesIO(content)).convert("RGB")


# --- 算法本身：能做什么 / 不能做什么 -------------------------------------------------


def test_sharpen_raises_edge_contrast_on_soft_fine_line_content() -> None:
    """插值放大后的"发软"能被锐化补回一点：细线内容锐度严格上升。"""

    soft = _open(_grid_bytes(lines=True)).filter(ImageFilter.GaussianBlur(1.2))

    before = sharpness_score(soft)
    after = sharpness_score(sharpen(soft, EnhanceOptions()))

    assert after > before


def test_sharpen_is_disabled_is_a_pure_no_op() -> None:
    image = _open(_grid_bytes(lines=True))

    result = sharpen(image, EnhanceOptions(enabled=False))

    assert result is image
    assert result.tobytes() == image.tobytes()


def test_sharpen_keeps_flat_areas_flat_thanks_to_threshold() -> None:
    """阈值保护：平坦区域不该被锐化啃出噪点/白边（这是"光晕"的可量化护栏）。"""

    flat = Image.new("RGB", (DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE), (120, 60, 180))

    sharpened = sharpen(flat, EnhanceOptions())

    difference = ImageChops.difference(flat, sharpened)
    worst = max(high for _low, high in ImageStat.Stat(difference).extrema)
    assert worst <= 2


# --- 配置读取：任何非法值都不许阻断生图 ---------------------------------------------


def test_options_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "WH_POD_IMAGE_ENHANCE",
        "WH_POD_IMAGE_ENHANCE_RADIUS",
        "WH_POD_IMAGE_ENHANCE_PERCENT",
        "WH_POD_IMAGE_ENHANCE_THRESHOLD",
    ):
        monkeypatch.delenv(name, raising=False)

    options = options_from_env()

    assert options.enabled is True
    assert options.radius == DEFAULT_RADIUS
    assert options.percent == DEFAULT_PERCENT
    assert options.threshold == DEFAULT_THRESHOLD


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_options_from_env_can_be_switched_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("WH_POD_IMAGE_ENHANCE", value)

    assert options_from_env().enabled is False


def test_options_from_env_empty_value_keeps_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WH_POD_IMAGE_ENHANCE", "  ")

    assert options_from_env().enabled is True


def test_options_from_env_falls_back_on_invalid_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WH_POD_IMAGE_ENHANCE_RADIUS", "abc")
    monkeypatch.setenv("WH_POD_IMAGE_ENHANCE_PERCENT", "-5")
    monkeypatch.setenv("WH_POD_IMAGE_ENHANCE_THRESHOLD", "not-a-number")

    options = options_from_env()

    assert options.radius == DEFAULT_RADIUS
    assert options.percent == DEFAULT_PERCENT
    assert options.threshold == DEFAULT_THRESHOLD


def test_options_from_env_accepts_explicit_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WH_POD_IMAGE_ENHANCE_RADIUS", "1.8")
    monkeypatch.setenv("WH_POD_IMAGE_ENHANCE_PERCENT", "120")
    monkeypatch.setenv("WH_POD_IMAGE_ENHANCE_THRESHOLD", "5")

    options = options_from_env()

    assert (options.radius, options.percent, options.threshold) == (1.8, 120, 5)


# --- 拆图接线：默认绝不自作主张 -----------------------------------------------------


def test_split_four_grid_enhance_is_opt_in_by_signature() -> None:
    """默认值必须是 None：product_processing 与 POD 共用本方法，不许默默改变成品。"""

    parameter = inspect.signature(ProductImageProcessor.split_four_grid).parameters["enhance"]

    assert parameter.default is None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_split_four_grid_default_output_equals_explicitly_disabled_output() -> None:
    """`enhance=None` 与 `enabled=False` 必须逐字节一致（回归保护）。"""

    processor = ProductImageProcessor(lambda: {})
    grid = _media(_grid_bytes(lines=True))

    default_parts = processor.split_four_grid(grid)
    disabled_parts = processor.split_four_grid(grid, enhance=EnhanceOptions(enabled=False))

    assert [part.content for part in default_parts] == [part.content for part in disabled_parts]


def test_split_four_grid_with_enhance_keeps_marketplace_shape_and_sampling() -> None:
    processor = ProductImageProcessor(lambda: {})

    parts = processor.split_four_grid(_media(_grid_bytes(lines=True)), enhance=EnhanceOptions())

    assert len(parts) == 5
    for part in parts[:4]:
        with Image.open(BytesIO(part.content)) as opened:
            assert opened.size == (DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE)
            assert opened.format == "JPEG"
            # 4:4:4，不带色度抽样（与既有交付口径一致）
            assert JpegImagePlugin.get_sampling(opened) == 0


def test_split_four_grid_with_enhance_is_actually_sharper_than_without() -> None:
    processor = ProductImageProcessor(lambda: {})
    grid = _media(_grid_bytes(lines=True))

    enhanced = processor.split_four_grid(grid, enhance=EnhanceOptions())
    plain = processor.split_four_grid(grid, enhance=EnhanceOptions(enabled=False))

    assert enhanced[0].content != plain[0].content
    assert sharpness_score(_open(enhanced[0].content)) > sharpness_score(_open(plain[0].content))


def test_split_four_grid_survives_encoder_defect_on_pathological_content() -> None:
    """回归保护：极高频内容曾让 `optimize=True` 的 JPEG 编码整批失败（OSError），
    现在必须由 `optimize=False` 兜底救回——真实母图里确有 2 张会命中，锐化后更多。
    """

    processor = ProductImageProcessor(lambda: {})

    parts = processor.split_four_grid(_media(_pathological_grid_bytes()), enhance=EnhanceOptions())

    assert len(parts) == 5
    for part in parts:
        with Image.open(BytesIO(part.content)) as opened:
            assert opened.format == "JPEG"
            assert opened.size == (DXM_IMAGE_TARGET_SIZE, DXM_IMAGE_TARGET_SIZE)


# --- POD 接线：默认开启，且可回退 ---------------------------------------------------


def test_pod_split_listing_grid_applies_enhancement_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0)
    try:
        monkeypatch.delenv("WH_POD_IMAGE_ENHANCE", raising=False)
        grid = _media(_grid_bytes(lines=True))

        enhanced = runtime.split_listing_grid(grid)
        assert len(enhanced) == 4
        assert all(part.content_type == "image/jpeg" for part in enhanced)

        monkeypatch.setenv("WH_POD_IMAGE_ENHANCE", "0")
        plain = runtime.split_listing_grid(grid)

        assert [part.content for part in enhanced] != [part.content for part in plain]
        assert sharpness_score(_open(enhanced[0].content)) > sharpness_score(_open(plain[0].content))
    finally:
        runtime.close()


def test_pod_split_listing_grid_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = PodCustomizationAiRuntime(image_workers=1, requests_per_minute=0)
    try:
        monkeypatch.setenv("WH_POD_IMAGE_ENHANCE", "0")
        grid = _media(_grid_bytes(lines=True))

        off = runtime.split_listing_grid(grid)
        expected = ProductImageProcessor(lambda: {}).split_four_grid(grid, enhance=EnhanceOptions(enabled=False))

        assert [part.content for part in off] == [part.content for part in expected[:4]]
    finally:
        runtime.close()


def test_sharpness_score_is_finite_for_extreme_inputs() -> None:
    black = Image.new("RGB", (64, 64), (0, 0, 0))
    white = Image.new("RGB", (64, 64), (255, 255, 255))

    for image in (black, white):
        value: Any = sharpness_score(image)
        assert isinstance(value, float)
        assert value == value  # 非 NaN
