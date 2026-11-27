"""细节块结构化描述（DetailSpec）与局部参考图裁剪的单测。

覆盖三类回归点：
1. 提示词必须带上「部位 + 动作 + 构图 + 必须/禁止」，不能退化成营销短词；
2. 视觉效果标出的部位坐标要能转成裁剪框，坐标非法时降级为无裁剪；
3. 裁剪失败绝不阻断出图（返回空串 → 调用方回退整图参考）。
"""

from __future__ import annotations

import base64
from io import BytesIO

from PIL import Image

from wh_local.modules.product_processing.domain.detail_specs import (
    DetailSpec,
    build_detail_prompt_from_spec,
    crop_reference_data_url,
    detail_crop_usable,
    fallback_detail_specs,
    lift_detail_exposure,
    spec_from_label,
    specs_from_callout_parts,
)
from wh_local.modules.product_processing.domain.marketing_pipeline import (
    MarketingCopy,
    plan_for,
    render_blocks,
)


def _photo_bytes(width: int = 600, height: int = 600, color=(80, 80, 80)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_prompt_carries_part_action_framing_and_guards() -> None:
    spec = DetailSpec(
        feature_id="ratchet_lock",
        part="the ratchet teeth and locking pawl",
        action="the pawl seated in a tooth groove while the handle is partly squeezed",
        must_show=("serrated teeth", "steel pawl"),
        forbidden=("extra jaws", "melted metal"),
    )

    prompt = build_detail_prompt_from_spec(spec)

    assert "the ratchet teeth and locking pawl" in prompt
    assert "seated in a tooth groove" in prompt
    assert "Framing:" in prompt
    assert "Must show: serrated teeth, steel pawl" in prompt
    assert "Do not show: extra jaws, melted metal" in prompt
    # 不能把「必须出现」误写成「禁止出现」
    assert "Do not show: serrated teeth" not in prompt


def test_specs_from_callout_parts_dedupes_limits_and_builds_crops() -> None:
    parts = [
        {"label": "ratchet", "x": 300, "y": 400},
        {"label": "Ratchet", "x": 320, "y": 410},  # 大小写重复
        {"label": "jaws", "x": 600, "y": 500},
        {"label": "grip", "x": 800, "y": 700},
    ]

    specs = specs_from_callout_parts(parts, limit=2)

    assert [spec.part for spec in specs] == ["the ratchet", "the jaws"]
    for spec in specs:
        assert spec.crop is not None
        x0, y0, x1, y1 = spec.crop
        assert 0.0 <= x0 < x1 <= 1.0
        assert 0.0 <= y0 < y1 <= 1.0
        assert spec.must_show == (spec.part.removeprefix("the "),)
    assert specs[0].display_label == "Ratchet"


def test_specs_keep_label_but_drop_crop_when_coordinates_invalid() -> None:
    specs = specs_from_callout_parts(
        [
            {"label": "pivot", "x": -5, "y": 400},
            {"label": "lever", "x": "n/a", "y": 400},
        ],
        limit=2,
    )

    assert [spec.part for spec in specs] == ["the pivot", "the lever"]
    assert all(spec.crop is None for spec in specs)


def test_crop_returns_png_data_url_and_matches_region_aspect() -> None:
    hero = _photo_bytes(600, 600)

    data_url = crop_reference_data_url(hero, (0.2, 0.2, 0.5, 0.5))

    assert data_url.startswith("data:image/png;base64,")
    payload = base64.b64decode(data_url.split(",", 1)[1])
    with Image.open(BytesIO(payload)) as image:
        assert image.format == "PNG"
        assert 150 <= image.width <= 260
        assert 150 <= image.height <= 260


def test_crop_is_blank_on_unusable_input() -> None:
    assert crop_reference_data_url(b"", (0.1, 0.1, 0.2, 0.2)) == ""
    assert crop_reference_data_url(_photo_bytes(600, 600), None) == ""
    assert crop_reference_data_url(_photo_bytes(600, 600), (0.5, 0.5, 0.5, 0.5)) == ""
    assert crop_reference_data_url(b"not-an-image", (0.1, 0.1, 0.2, 0.2)) == ""


def test_render_blocks_crop_mode_uses_cropped_bytes_without_regeneration() -> None:
    """默认 crop 模式：细节块直接裁切，不再调用生图——这是零畸形的关键。"""
    hero = _photo_bytes(600, 600)
    seen: list[tuple[str, list[str], int]] = []

    def fake_render(prompt: str, refs: list[str], count: int = 1) -> bytes:
        seen.append((prompt, list(refs), count))
        return _photo_bytes(200, 200)

    blocks = render_blocks(
        plan=plan_for("feature_strip"),
        copy=MarketingCopy(headline="4-IN-1", subline="", bullets=["Stripping"]),
        refs=["https://example.test/ref.jpg"],
        render=fake_render,
        specs_factory=lambda _hero: specs_from_callout_parts(
            [{"label": "jaws", "x": 400, "y": 500}, {"label": "ratchet", "x": 600, "y": 500}],
            limit=4,
        ),
    )

    assert blocks["detail_source"] == "crop"
    assert [label for _content, label in blocks["details"]] == ["Jaws", "Ratchet"]
    # 只生成了 hero，细节块是本地裁切
    assert len(seen) == 1
    for content, _label in blocks["details"]:
        assert content.startswith(b"\x89PNG")


def test_render_blocks_generate_mode_passes_crop_as_first_reference() -> None:
    """显式要求重画时：物理提示词 + 裁剪图作为第一张参考。"""
    seen: list[tuple[str, list[str], int]] = []

    def fake_render(prompt: str, refs: list[str], count: int = 1) -> bytes:
        seen.append((prompt, list(refs), count))
        return _photo_bytes(200, 200)

    blocks = render_blocks(
        plan=plan_for("feature_strip"),
        copy=MarketingCopy(headline="4-IN-1", subline="", bullets=["Stripping"]),
        refs=["https://example.test/ref.jpg"],
        render=fake_render,
        specs_factory=lambda _hero: specs_from_callout_parts(
            [{"label": "jaws", "x": 400, "y": 500}, {"label": "ratchet", "x": 600, "y": 500}],
            limit=4,
        ),
        detail_mode="generate",
    )

    assert blocks["detail_source"] == "spec"
    assert len(seen) == 3  # hero + 2 细节
    for prompt, refs, count in seen[1:]:
        assert "Extreme macro photograph of the" in prompt
        assert refs[0].startswith("data:image/png;base64,")
        assert refs[1] == "https://example.test/ref.jpg"
        assert count == 1


def test_crop_mode_falls_back_to_generation_when_no_crop_available() -> None:
    """视觉坐标不可用时没有裁剪，crop 模式必须退回重画而不是留空。"""
    seen: list[str] = []

    def fake_render(prompt: str, refs: list[str], count: int = 1) -> bytes:
        seen.append(prompt)
        return _photo_bytes(200, 200)

    blocks = render_blocks(
        plan=plan_for("feature_strip"),
        copy=MarketingCopy(headline="4-IN-1", subline="", bullets=["Stripping"]),
        refs=["https://example.test/ref.jpg"],
        render=fake_render,
        specs_factory=lambda _hero: specs_from_callout_parts(
            [{"label": "pivot", "x": "n/a", "y": 400}], limit=4
        ),
    )

    assert blocks["detail_source"] == "spec"
    assert len(blocks["details"]) == 1
    assert any("the pivot" in prompt for prompt in seen)


def test_render_blocks_skips_detail_generation_without_specs_factory() -> None:
    """没有部位坐标就不生细节块：既不出歪图，也不浪费生图调用。"""
    seen: list[str] = []

    def fake_render(prompt: str, refs: list[str], count: int = 1) -> bytes:
        seen.append(prompt)
        return _photo_bytes(200, 200)

    blocks = render_blocks(
        plan=plan_for("feature_strip"),
        copy=MarketingCopy(headline="4-IN-1", subline="", bullets=["Stripping"]),
        refs=["https://example.test/ref.jpg"],
        render=fake_render,
    )

    assert blocks["detail_source"] == "label"
    assert blocks["details"] == []
    assert len(seen) == 1  # 只生成了 hero
    assert "Stripping" not in seen[0]


def test_render_blocks_uses_object_aware_crops_when_vision_returns_no_parts() -> None:
    """视觉调用失败/返回空坐标时：按商品包围盒切段兜底，版式不再整张不出。"""
    seen: list[str] = []

    def fake_render(prompt: str, refs: list[str], count: int = 1) -> bytes:
        seen.append(prompt)
        return _photo_bytes(200, 200)

    blocks = render_blocks(
        plan=plan_for("feature_cards"),
        copy=MarketingCopy(
            headline="4-IN-1",
            subline="",
            bullets=["Stripping", "Cutting", "Crimping", "Twisting"],
        ),
        refs=["https://example.test/ref.jpg"],
        render=fake_render,
        specs_factory=lambda _hero: [],
    )

    assert blocks["detail_source"] == "fallback-crop"
    assert [label for _content, label in blocks["details"]] == [
        "Stripping", "Cutting", "Crimping", "Twisting",
    ]
    assert len(blocks["callout_parts"]) == 4
    assert len(seen) == 1  # 只有 hero 生图，细节全部本地裁切


def test_render_blocks_prefers_real_detail_images_over_hero_crops() -> None:
    """有真实原图局部时直接用，不再从 AI 主图裁、也不再调视觉坐标。"""
    seen: list[str] = []

    def fake_render(prompt: str, refs: list[str], count: int = 1) -> bytes:
        seen.append(prompt)
        return _photo_bytes(200, 200)

    blocks = render_blocks(
        plan=plan_for("feature_cards"),
        copy=MarketingCopy(headline="CUTTER", bullets=["A", "B", "C", "D"]),
        refs=["https://example.test/ref.jpg"],
        render=fake_render,
        specs_factory=lambda _hero: [],
        detail_images=[
            (b"real-jaws", "CUTTING JAWS"),
            (b"real-ratchet", "RATCHET GEAR"),
        ],
    )

    assert blocks["detail_source"] == "source"
    assert [label for _content, label in blocks["details"]] == [
        "CUTTING JAWS",
        "RATCHET GEAR",
    ]
    assert len(seen) == 1  # 只有 hero 生图，细节零调用
    assert not blocks.get("callout_parts")


def test_fallback_detail_specs_follow_product_bbox() -> None:
    """竖长商品：裁切只落在商品 x 范围内，沿 y 递增，不切整帧背景。"""
    specs = fallback_detail_specs(
        ["A", "B", "C", "D"], limit=4, bbox=(0.4, 0.1, 0.6, 0.9)
    )

    crops = [spec.crop for spec in specs]
    assert len(crops) == 4
    assert all(crop is not None for crop in crops)
    assert all(0.38 <= crop[0] <= 0.42 and 0.58 <= crop[2] <= 0.62 for crop in crops)
    assert all(crops[index][1] < crops[index + 1][1] for index in range(3))
    assert crops[0][1] <= 0.1 and crops[-1][3] >= 0.9


def test_detail_crop_usable_rejects_dark_empty_card() -> None:
    dark = _photo_bytes(200, 200, (8, 8, 8))
    normal = _photo_bytes(200, 200, (80, 80, 80))
    assert not detail_crop_usable(dark)
    assert detail_crop_usable(normal)


def test_lift_detail_exposure_brightens_dark_crop() -> None:
    from PIL import ImageStat

    dark = _photo_bytes(200, 200, (20, 20, 20))
    lifted = lift_detail_exposure(dark)
    with Image.open(BytesIO(dark)) as before, Image.open(BytesIO(lifted)) as after:
        assert ImageStat.Stat(after.convert("L")).mean[0] > ImageStat.Stat(
            before.convert("L")
        ).mean[0]


def test_label_fallback_keeps_marketing_word() -> None:
    spec = spec_from_label("Hardened Steel")
    assert spec.feature_id == "hardened_steel"
    assert spec.part == "Hardened Steel"
    assert spec.display_label == "Hardened Steel"
    assert spec.crop is None
