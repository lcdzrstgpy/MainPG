"""精选处理（一期）：排版层 + 提示词层的行为测试。"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from wh_local.modules.product_processing.domain.premium_composer import (
    MarketingCopy,
    compose_marketing_image,
    filter_copy,
)
from wh_local.modules.product_processing.domain.marketing_refs import pick_clean_references
from wh_local.modules.product_processing.domain.premium_prompts import (
    build_brief_messages,
    build_photo_prompt,
    build_qa_messages,
    build_scene_messages,
    clean_brief,
    fallback_copy,
    parse_copy_response,
    parse_qa_response,
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
    assert "Any text" in prompt
    assert "FORBIDDEN" in prompt
    assert "FINAL SELF-CHECK" in prompt


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
    assert plan is not None
    assert plan["surface"] == "dark walnut workbench"
    assert plan["action"] == "wire clamped in jaws"
    assert plan["props"] == ["coil"]
    assert parse_scene_plan("no json") is None
    assert parse_scene_plan('{"action": "x"}') is None  # 缺 surface 不可用


def test_photo_prompt_without_scene_is_conservative() -> None:
    prompt = build_photo_prompt("action_proof", MarketingCopy(headline="Cup Holder"))
    assert "ACTION: none" in prompt
    assert "PROPS: none" in prompt


def test_photo_prompt_with_scene_uses_surface_and_forces_no_action() -> None:
    """台面来自方案；动作/道具永久禁用（2026-11-23 两轮实测：画动作必穿模）。"""
    plan = {"surface": "brushed steel table", "action": "wire clamped in jaws", "props": ["coil"]}
    prompt = build_photo_prompt("action_proof", MarketingCopy(headline="Pour Over"), plan)
    assert "brushed steel table" in prompt
    assert "ACTION: none" in prompt
    assert "PROPS: none" in prompt
    assert "coil" not in prompt.split("SCENE PLAN")[-1].split("Overall tone")[0]


def test_scene_messages_include_product_context() -> None:
    messages = build_scene_messages("Wire Stripper", "tools", "a 5-in-1 electrical stripping tool")
    text = " ".join(str(item.get("content")) for item in messages)
    assert "Wire Stripper" in text and "tools" in text and "5-in-1" in text


def _png(color, side=200, noise: bool = False) -> bytes:
    from io import BytesIO as _B
    import random

    image = Image.new("RGB", (side, side), color)
    if noise:
        draw = ImageDraw.Draw(image)
        rng = random.Random(7)
        for _ in range(400):
            x = rng.randint(0, side - 20)
            y = rng.randint(0, side - 20)
            draw.rectangle([x, y, x + rng.randint(6, 26), y + rng.randint(6, 26)], fill=tuple(rng.randint(0, 255) for _ in range(3)))
    buffer = _B()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_pick_clean_references_prefers_white_background() -> None:
    """白底干净图应排在嘈杂拼贴图前面。"""
    urls = ["busy", "clean"]
    payloads = {"busy": _png((240, 40, 40), noise=True), "clean": _png((252, 252, 252))}

    picked = pick_clean_references(urls, lambda url: payloads[url], limit=1)
    assert picked == ["clean"]


def test_pick_clean_references_falls_back_on_fetch_error() -> None:
    def _boom(_: str) -> bytes:
        raise RuntimeError("net down")

    assert pick_clean_references(["a", "b", "c"], _boom) == ["a", "b"]


def test_qa_parse_ok_and_bad() -> None:
    good = parse_qa_response(
        '{"same_product": true, "fidelity_ok": true, "extra_objects": false, "has_text": false, "issues": ""}'
    )
    assert good is not None and good["ok"] is True
    bad = parse_qa_response(
        '{"same_product": true, "fidelity_ok": false, "extra_objects": true, "has_text": false, "issues": "melted jaw"}'
    )
    assert bad is not None and bad["ok"] is False and bad["issues"] == "melted jaw"
    assert parse_qa_response("not json") is None


def test_qa_messages_contain_two_images() -> None:
    messages = build_qa_messages("data:image/jpeg;base64,AAA", "data:image/jpeg;base64,BBB")
    content = messages[-1]["content"]
    assert sum(1 for part in content if part.get("type") == "image_url") == 2


def test_scene_plan_carries_parts_and_materials() -> None:
    """部件清单由场景方案现场产出（不做品类穷举）。"""
    plan = parse_scene_plan(
        '{"surface": "dark walnut workbench", "action": "", "props": [],'
        ' "type_label": "leather tote bag", "parts": "body panels, straps, zipper", "materials": "pebbled leather"}'
    )
    assert plan is not None
    assert plan["type_label"] == "leather tote bag"
    assert "zipper" in plan["parts"]
    assert plan["materials"] == "pebbled leather"


def test_photo_prompt_uses_dynamic_parts() -> None:
    scene = {"surface": "dark walnut workbench", "parts": "body panels, straps, zipper", "type_label": "leather tote bag"}
    prompt = build_photo_prompt("headline_hero", MarketingCopy(headline="Everyday Tote"), scene)
    assert "leather tote bag" in prompt
    assert "COUNT THESE PARTS WHEN YOU CHECK: body panels, straps, zipper" in prompt


def test_photo_prompt_falls_back_to_generic_counting() -> None:
    prompt = build_photo_prompt("headline_hero", MarketingCopy(headline="Any Product"), {"surface": "matte stone"})
    assert "every distinct visible part" in prompt


def test_qa_includes_parts_hint() -> None:
    messages = build_qa_messages("data:image/jpeg;base64,AAA", "data:image/jpeg;base64,BBB", "straps, zipper, buckles")
    text = " ".join(str(part.get("text") or "") for part in messages[-1]["content"])
    assert "straps, zipper, buckles" in text


def test_brief_messages_include_operator_caution() -> None:
    messages = build_brief_messages(
        "Tin Snips", "tools", "green handle tin snips", "handles x2, jaws x1", "dark stone",
        caution="把手必须是两只、钳头别融化",
    )
    text = " ".join(str(item.get("content")) for item in messages)
    assert "Tin Snips" in text
    assert "handles x2" in text
    assert "把手必须是两只、钳头别融化" in text
    assert "PRODUCT:" in text and "LOCK:" in text and "AVOID:" in text and "SHOT:" in text


def test_clean_brief_rejects_chinese_and_fences() -> None:
    assert clean_brief("PRODUCT: good tool") == "PRODUCT: good tool"
    assert clean_brief("PRODUCT: 好用的工具") == ""
    assert clean_brief("") == ""


def test_photo_prompt_embeds_brief() -> None:
    prompt = build_photo_prompt(
        "headline_hero", MarketingCopy(headline="Snips"), {"surface": "stone"}, "",
        "PRODUCT: industrial tin snips. LOCK: exactly 2 handles. AVOID: extra pivots.",
    )
    assert "PRODUCT-SPECIFIC BRIEF" in prompt
    assert "exactly 2 handles" in prompt
