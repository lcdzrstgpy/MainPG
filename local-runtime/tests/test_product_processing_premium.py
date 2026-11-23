"""精选处理（一期）：排版层 + 提示词层的行为测试。"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from wh_local.modules.product_processing.domain.premium_composer import (
    MarketingCopy,
    compose_marketing_image,
    filter_copy,
)
from wh_local.modules.product_processing.domain.premium_prompts import (
    build_photo_prompt,
    build_scene_messages,
    fallback_copy,
    parse_copy_response,
    parse_scene_plan,
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


def test_photo_prompt_forbids_rendered_text() -> None:
    prompt = build_photo_prompt("headline_hero", MarketingCopy(headline="Sharp And Smooth", subline="x"))
    assert "ZERO text" in prompt
    assert "reference images are the ONLY source of truth" in prompt.lower() or "ONLY source of truth" in prompt


def test_batch_hook_passes_real_task_id() -> None:
    """批次里调营销图生成必须传真实 task_id（曾把 draft_id 当 task_id，
    预览注册校验「preview image target does not belong to this task」三张全丢）。"""
    import re
    from pathlib import Path as _Path

    import wh_local.modules.product_processing.service as service_module

    source = _Path(service_module.__file__).read_text(encoding="utf-8")
    assert re.search(
        r"_generate_marketing_images\(\s*\n\s*task_id,\s*\n\s*draft\[\"id\"\]",
        source,
    ), "批次挂载处必须用 task_id 调 _generate_marketing_images"


def test_scene_plan_parse_and_reject() -> None:
    plan = parse_scene_plan('{"surface": "dark walnut workbench", "action": "wire clamped in jaws", "props": ["coil"]}')
    assert plan == {"surface": "dark walnut workbench", "action": "wire clamped in jaws", "props": ["coil"]}
    assert parse_scene_plan("no json") is None
    assert parse_scene_plan('{"action": "x"}') is None  # 缺 surface 不可用


def test_photo_prompt_without_scene_is_conservative() -> None:
    prompt = build_photo_prompt("action_proof", MarketingCopy(headline="Cup Holder"))
    assert "ACTION: none" in prompt
    assert "PROPS: none" in prompt


def test_photo_prompt_with_scene_uses_plan() -> None:
    plan = {"surface": "brushed steel table", "action": "a ceramic mug resting under the spout", "props": ["tea tin"]}
    prompt = build_photo_prompt("action_proof", MarketingCopy(headline="Pour Over"), plan)
    assert "brushed steel table" in prompt
    assert "ceramic mug" in prompt
    assert "tea tin" in prompt


def test_scene_messages_include_product_context() -> None:
    messages = build_scene_messages("Wire Stripper", "tools", "a 5-in-1 electrical stripping tool")
    text = " ".join(str(item.get("content")) for item in messages)
    assert "Wire Stripper" in text and "tools" in text and "5-in-1" in text
