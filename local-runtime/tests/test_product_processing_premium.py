"""精选处理（第一版形态）：排版层 + 提示词层的行为测试。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from wh_local.modules.product_processing.domain.premium_composer import (
    MarketingCopy,
    compose_feature_cards,
    compose_marketing_image,
    filter_copy,
)
from wh_local.modules.product_processing.domain.premium_prompts import (
    build_card_prompt,
    build_photo_prompt,
    fallback_copy,
    parse_copy_response,
)


def _photo_bytes(side: int = 800, color=(200, 200, 200)) -> bytes:
    image = Image.new("RGB", (side, side), color)
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


def test_filter_copy_removes_banned_words() -> None:
    assert filter_copy("The Best No.1 perfect cutter") == "The cutter"
    assert "guaranteed" not in filter_copy("100% guaranteed sharp").lower()


def test_parse_copy_response_tolerates_markdown_fence() -> None:
    text = '```json\n{"headline": "Sharp And Durable", "subline": "Cuts steel wire with ease"}\n```'
    copy = parse_copy_response(text)
    assert copy is not None
    assert copy.headline == "Sharp And Durable"
    assert copy.subline == "Cuts steel wire with ease"


def test_parse_copy_response_rejects_garbage() -> None:
    assert parse_copy_response("sorry I cannot help") is None
    assert parse_copy_response("") is None
    assert parse_copy_response('{"subline": "no headline"}') is None


def test_fallback_copy_uses_title_without_inventing() -> None:
    copy = fallback_copy("Industrial Steel Wire Cutter Pliers Heavy Duty Tool For Home")
    assert copy.headline.startswith("Industrial")
    assert len(copy.headline.split()) <= 8
    assert copy.subline == ""


def test_compose_marketing_image_outputs_square_jpeg() -> None:
    for template in ("headline_hero", "action_proof", "icon_grid", "material_strip", "use_cases"):
        data = compose_marketing_image(
            _photo_bytes(),
            template,
            MarketingCopy(headline="Sharp And Smooth\nStrong Shearing", subline="Cuts steel wire with ease"),
        )
        image = Image.open(BytesIO(data))
        assert image.size == (1600, 1600)
        assert data[:2] == b"\xff\xd8"  # JPEG magic


def test_compose_rejects_unknown_template() -> None:
    try:
        compose_marketing_image(_photo_bytes(), "nope", MarketingCopy(headline="x"))
    except ValueError as exc:
        assert "未知版式" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("未知版式没有报错")


def test_photo_prompt_is_first_version_short_contract() -> None:
    """第一版形态：短合同 + 版式构图（无场景方案/brief/锚点等扩展）。"""
    prompt = build_photo_prompt("headline_hero", MarketingCopy(headline="Sharp And Smooth"))
    assert "HARD RULES" in prompt
    assert "ZERO text" in prompt
    assert "COMPOSITION" in prompt
    assert "FUSION MODE" not in prompt
    assert "EDIT MODE" not in prompt
    assert "FINAL SELF-CHECK" not in prompt


def test_card_prompt_is_macro() -> None:
    prompt = build_card_prompt("Cutting")
    assert "extreme close-up" in prompt
    assert "Cutting" in prompt


def test_compose_feature_cards_layout() -> None:
    hero = _photo_bytes()
    card = _photo_bytes(400, (60, 60, 60))
    out = compose_feature_cards(
        hero,
        [(card, "Wire Stripping"), (card, "Crimping"), (card, "Cutting"), (card, "Twisting")],
        MarketingCopy(headline="Precision Working Head", subline="Designed for control"),
    )
    image = Image.open(BytesIO(out))
    assert image.size == (1600, 1600)
    assert out[:2] == b"\xff\xd8"


def test_batch_hook_passes_real_task_id() -> None:
    """批次里调营销图生成必须传真实 task_id（曾把 draft_id 当 task_id，三张全丢）。"""
    import re
    from pathlib import Path as _Path

    import wh_local.modules.product_processing.service as service_module

    source = _Path(service_module.__file__).read_text(encoding="utf-8")
    assert re.search(
        r"_generate_marketing_images\(\s*\n\s*task_id,\s*\n\s*draft\[\"id\"\]",
        source,
    ), "批次挂载处必须用 task_id 调 _generate_marketing_images"


def test_compose_callout_ring_with_and_without_coords() -> None:
    from wh_local.modules.product_processing.domain.premium_composer import compose_callout_ring

    copy = MarketingCopy(headline="RATCHET SELF-LOCKING", subline="Powerful grip")
    hero = _photo_bytes(1200, (40, 40, 44))
    out = compose_callout_ring(
        hero,
        [
            {"label": "RATCHET LOCK", "x": 420, "y": 300},
            {"label": "FORGED STEEL", "x": 620, "y": 260},
            {"label": "GRIP", "x": 500, "y": 700},
        ],
        copy,
    )
    assert Image.open(BytesIO(out)).size == (1600, 1600)
    # 无坐标也不炸：退化为编号列表
    fallback = compose_callout_ring(hero, [{"label": "No Coords", "x": -1, "y": -1}], copy)
    assert Image.open(BytesIO(fallback)).size == (1600, 1600)


def test_compose_feature_strip() -> None:
    from wh_local.modules.product_processing.domain.premium_composer import compose_feature_strip

    hero = _photo_bytes(1200, (30, 30, 36))
    card = _photo_bytes(400, (90, 90, 96))
    out = compose_feature_strip(
        hero,
        [(card, "Wire Stripping"), (card, "Cutting"), (card, "Crimping"), (card, "Twisting")],
        MarketingCopy(headline="4-IN-1 MULTI-FUNCTION"),
    )
    assert Image.open(BytesIO(out)).size == (1600, 1600)


def test_parse_callout_response() -> None:
    from wh_local.modules.product_processing.domain.premium_prompts import parse_callout_response

    parts = parse_callout_response(
        '{"parts": [{"label": "Jaws", "x": 400, "y": 300}, {"label": "Pivot", "x": 1200, "y": 5}]}'
    )
    assert len(parts) == 1 and parts[0]["label"] == "Jaws"
    assert parse_callout_response("nope") == []
