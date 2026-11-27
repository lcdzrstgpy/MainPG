"""原图版式蓝图的回归测试：解析护栏、聚类选优、风格片段、相似度护栏。"""

from __future__ import annotations

import json
from io import BytesIO

from PIL import Image, ImageDraw

from wh_local.modules.product_processing.domain.reference_layouts import (
    LayoutBlueprint,
    image_hash_distance,
    is_near_duplicate,
    parse_layout_response,
    plan_from_blueprints,
    style_fragment,
    usable_blueprints,
)

FALLBACK = ["S1", "S3", "S4", "S2"]


def test_parse_layout_response_clamps_and_normalizes() -> None:
    payload = {
        "image_type": "LIFESTYLE",
        "layout": "hero_split_side",
        "photo_zone": "left",
        "text_zones": ["right", "bogus", "top"],
        "item_count": 99,
        "card_shape": "rounded",
        "background": "Warm beige wooden desk, soft daylight",
        "accent_color": "#C9A227",
        "quality_score": 14,
        "reusable": "true",
        "covers_product": "false",
        "brand_elements": ["ACME logo", "CE badge"],
        "notes": "clean split with strong product hero",
        "skeleton": "S2",  # 模型多返回的字段必须被忽略
    }
    blueprint = parse_layout_response(f"```json\n{json.dumps(payload)}\n```")
    assert blueprint is not None
    assert blueprint.layout == "hero_split_side"
    assert blueprint.skeleton == "S2"
    assert blueprint.text_zones == ("right", "top")
    assert blueprint.item_count == 12
    assert blueprint.quality_score == 10.0
    assert blueprint.reusable is True
    assert blueprint.covers_product is False
    assert blueprint.background == "Warm beige wooden desk, soft daylight"


def test_parse_layout_response_rejects_garbage() -> None:
    assert parse_layout_response("") is None
    assert parse_layout_response("not json at all") is None
    assert parse_layout_response('{"layout": "hero_split_side"') is None


def test_plan_prefers_high_quality_blueprints() -> None:
    blueprints = [
        LayoutBlueprint(
            layout="hero_split_side",
            quality_score=9.0,
            reusable=True,
            background="warm beige seamless studio backdrop",
            card_shape="rounded",
        ),
        LayoutBlueprint(
            layout="hero_split_side",
            quality_score=8.0,
            reusable=True,
            background="soft grey gradient",
        ),
        LayoutBlueprint(
            layout="hero_band_bottom",
            quality_score=4.0,
            reusable=True,
            background="white",
        ),
    ]
    plan = plan_from_blueprints(blueprints, fallback_order=FALLBACK, template_count=4)
    assert plan["skeleton_order"][0] == "S2"
    assert sorted(plan["skeleton_order"]) == ["S1", "S2", "S3", "S4"]
    assert plan["usable_count"] == 2
    assert plan["signature"].startswith("hero_split_side|")
    fragment = plan["prompt_fragment"]
    assert "warm beige seamless studio backdrop" in fragment
    assert "rounded information cards" in fragment


def test_plan_falls_back_when_nothing_usable() -> None:
    blueprints = [
        LayoutBlueprint(layout="full_bleed_overlay", quality_score=9.0, reusable=False)
    ]
    assert usable_blueprints(blueprints) == []
    plan = plan_from_blueprints(blueprints, fallback_order=FALLBACK, template_count=4)
    assert plan["skeleton_order"] == FALLBACK
    assert plan["prompt_fragment"] == ""


def test_style_fragment_never_carries_brand_text() -> None:
    blueprints = [
        LayoutBlueprint(
            layout="card_centered",
            quality_score=9.0,
            reusable=True,
            background="ACME red poster with 50% OFF",  # 品牌词只可能出现在 brand 字段
            brand_elements=("ACME logo",),
        )
    ]
    fragment = style_fragment(blueprints)
    assert "ACME" not in fragment
    assert "ACME logo" not in fragment
    assert "OFF" not in fragment and "50%" not in fragment
    assert fragment.isascii()


def test_image_hash_distance_flags_near_duplicates() -> None:
    def _photo(offset: int) -> bytes:
        image = Image.new("RGB", (240, 240), (240, 236, 228))
        draw = ImageDraw.Draw(image)
        draw.rectangle([60 + offset, 40, 180 + offset, 200], fill=(25, 25, 30))
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()

    first = _photo(0)
    assert image_hash_distance(first, first) == 0
    assert is_near_duplicate(first, first)
    assert not is_near_duplicate(first, _photo(60))
