"""多结构骨架的回归测试：结构选择、构图预留、四结构渲染、信息带安全高度。"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from wh_local.modules.product_processing.domain.html_composer import build_skeleton_html
from wh_local.modules.product_processing.domain.layout_variants import (
    SKELETONS,
    pick_variant,
    variant_order,
)
from wh_local.modules.product_processing.domain.marketing_gen import build_prompt
from wh_local.modules.product_processing.domain.premium_composer import (
    content_bottom_ratio,
    resolve_band_ratio,
)


def _photo(product_top: float = 0.2, product_bottom: float = 0.72) -> bytes:
    image = Image.new("RGB", (800, 800), (238, 232, 220))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        [200, int(800 * product_top), 600, int(800 * product_bottom)],
        fill=(30, 30, 34),
    )
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def test_variant_order_is_stable_and_distinct() -> None:
    first = variant_order(12345)
    assert first == variant_order(12345)
    assert sorted(first) == sorted(SKELETONS)
    picks = [pick_variant(template, first, index) for index, template in enumerate(
        ["headline_hero", "icon_grid", "feature_cards", "side_rail"]
    )]
    assert len(set(picks)) == 4, "同一商品的前 4 个模板必须拿到 4 种不同结构"
    assert pick_variant("white_main", first, 2) == "S1"


def test_prompt_reserves_space_by_skeleton() -> None:
    s1 = build_prompt("icon_grid", skeleton="S1")
    assert "upper 64%" in s1 and "bottom 36%" in s1
    # 有结构时不再叠加模板自带的「居中 50%」，避免两段构图互相矛盾
    assert "middle 50%" not in s1
    s2 = build_prompt("icon_grid", skeleton="S2")
    assert "left 58%" in s2 and "right 42%" in s2
    # 白底主图不拼结构预留（会顶掉纯白背景）
    assert "upper 64%" not in build_prompt("white_main", skeleton="S1")


def test_copy_bullet_count_follows_layout() -> None:
    from wh_local.modules.product_processing.domain.premium_prompts import (
        build_copy_messages,
    )

    four = build_copy_messages("Pliers", "tools", "", bullet_count=4)[-1]["content"]
    six = build_copy_messages("Pliers", "tools", "", bullet_count=6)[-1]["content"]
    assert "4 SHORT feature labels" in four
    assert "6 SHORT feature labels" in six


def test_judge_uses_thumbnails_and_structure_hint() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import pick_best

    captured: dict[str, object] = {}

    def fake_complete(messages):
        captured["messages"] = messages
        return '{"best": 1, "scores": [5, 8]}'

    candidates = [_photo(), _photo(0.3, 0.8)]
    assert pick_best(candidates, complete=fake_complete, structure_hint="bottom 35% clean") == 1
    payload = captured["messages"][1]["content"]
    text = payload[-1]["text"]
    assert "bottom 35% clean" in text
    # 打分图必须是缩略图，不能把 2048 原图塞进视觉请求
    import base64
    from io import BytesIO

    from PIL import Image

    encoded = payload[0]["image_url"]["url"].split(",", 1)[1]
    with Image.open(BytesIO(base64.b64decode(encoded))) as thumb:
        assert max(thumb.size) <= 768


def test_band_ratio_never_cuts_a_low_product() -> None:
    low = _photo(0.15, 0.95)
    assert content_bottom_ratio(low) > 0.9
    assert resolve_band_ratio(low, 0.34) == pytest.approx(0.26, abs=0.01)

    normal = _photo(0.15, 0.66)
    assert resolve_band_ratio(normal, 0.32) == pytest.approx(0.32, abs=0.01)


@pytest.mark.parametrize("template", [
    "headline_hero", "action_proof", "icon_grid", "material_strip", "use_cases",
    "side_rail", "feature_cards", "feature_strip", "callout_ring",
])
def test_pil_fallback_renders_every_template(template: str) -> None:
    from wh_local.modules.product_processing.domain.premium_composer import (
        MarketingCopy,
        compose_band_skeleton,
    )

    out = compose_band_skeleton(
        _photo(),
        template,
        MarketingCopy(headline="BUILT FOR HARD JOBS", subline="One compact body", bullets=["Steel Grip", "Wide Jaw"]),
        details=[(_photo(0.1, 0.5), "Jaw"), (_photo(0.2, 0.5), "Grip")],
        callout_parts=[{"label": "Jaw"}, {"label": "Pivot"}],
    )
    assert out[:2] == b"\xff\xd8"
    assert Image.open(BytesIO(out)).size == (1600, 1600)


def test_compose_routes_skeleton_and_notes_fallback(monkeypatch) -> None:
    from wh_local.modules.product_processing.domain import marketing_pipeline as pipeline
    from wh_local.modules.product_processing.domain.premium_composer import MarketingCopy

    monkeypatch.setattr(pipeline, "_compose_skeleton_html", lambda **_kwargs: b"")
    notes: list[str] = []
    out = pipeline.compose(
        plan=pipeline.plan_for("icon_grid"),
        copy=MarketingCopy(headline="TOUGH", bullets=["Steel Grip"]),
        blocks={"hero": _photo(), "details": [], "category_id": ""},
        engine_notes=notes,
        skeleton="S2",
    )
    assert out and out[:2] == b"\xff\xd8"
    assert notes[-1] == "icon_grid:S2:pil-fallback"


@pytest.mark.parametrize("template", [
    "headline_hero", "action_proof", "icon_grid", "material_strip", "use_cases",
    "side_rail", "feature_cards", "feature_strip", "callout_ring",
])
@pytest.mark.parametrize("skeleton", list(SKELETONS))
def test_every_template_renders_every_skeleton(template: str, skeleton: str) -> None:
    hero = _photo()
    detail = _photo(0.1, 0.5)
    html = build_skeleton_html(
        template=template,
        skeleton=skeleton,
        hero=hero,
        headline="BUILT FOR HARD JOBS",
        subline="One compact body, every day duty",
        bullets=["Steel Grip", "Wide Jaw", "Soft Handle", "Heavy Duty", "Rust Proof", "Easy Carry"],
        details=[(detail, "Jaw"), (detail, "Grip")],
        callout_parts=[
            {"label": "Jaw", "x": 400, "y": 400},
            {"label": "Pivot", "x": 600, "y": 300},
        ],
        category_id="hand_tools",
    )
    assert '<div class="stage">' in html
    assert "BUILT FOR" in html


def test_supersampling_downscales_to_target_size() -> None:
    from io import BytesIO

    from PIL import Image

    from wh_local.modules.product_processing.domain.html_composer import _downscale_jpeg

    buffer = BytesIO()
    Image.new("RGB", (3200, 3200), (250, 250, 250)).save(buffer, format="JPEG")
    out = _downscale_jpeg(buffer.getvalue(), 1600)
    with Image.open(BytesIO(out)) as image:
        assert image.size == (1600, 1600)


def test_content_bbox_detects_product_region() -> None:
    from wh_local.modules.product_processing.domain.premium_composer import (
        content_bbox_ratios,
    )

    bbox = content_bbox_ratios(_photo(0.2, 0.7))
    assert bbox is not None
    left, top, right, bottom = bbox
    assert 0.2 <= left <= 0.3 and 0.7 <= right <= 0.8
    assert 0.15 <= top <= 0.25 and 0.65 <= bottom <= 0.75


def test_judge_order_uses_reference_and_scores() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import judge_order

    captured: dict[str, object] = {}

    def fake_complete(messages):
        captured["messages"] = messages
        return '{"best": 1, "scores": [4, 9]}'

    order = judge_order(
        [_photo(), _photo(0.3, 0.8)],
        complete=fake_complete,
        structure_hint="bottom 35% clean",
        reference_data_urls=["data:image/jpeg;base64,AAAA"],
    )
    assert order == [1, 0]
    payload = captured["messages"][1]["content"]
    # 参考图排在最前面，候选图随后
    assert payload[0]["image_url"]["url"].endswith("AAAA")
    assert "bottom 35% clean" in payload[-1]["text"]


def test_generate_best_skips_candidate_failing_verification() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import generate_best

    first, second = _photo(0.2, 0.6), _photo(0.3, 0.8)
    verified: list[bytes] = []
    calls = {"n": 0}

    def fake_verify(candidate: bytes) -> bool:
        verified.append(candidate)
        return candidate is first

    def fake_render(_prompt: str, _refs: list[str]) -> bytes:
        calls["n"] += 1
        return first if calls["n"] == 1 else second

    out = generate_best(
        prompt="prompt",
        refs=["ref"],
        render=fake_render,
        complete=lambda _messages: '{"best": 1, "scores": [4, 9]}',
        count=2,
        verify=fake_verify,
    )
    # 评分最高的是 second，但终检拒绝；应回退到 first
    assert out == first
    assert verified == [second, first]


def test_parse_qc_response_defaults_to_pass() -> None:
    from wh_local.modules.product_processing.domain.marketing_gen import parse_qc_response

    assert parse_qc_response('{"pass": false, "issues": ["text over product"]}') == (
        False,
        ["text over product"],
    )
    assert parse_qc_response("not json") == (True, [])

