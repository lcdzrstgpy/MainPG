"""HTML/CSS 排版层：数据绑定、降级、真实浏览器渲染。

重点守住三条：
1. 文案/标签必须转义，不能因为商品标题里的 & 或 < 破坏版式；
2. 浏览器不可用时 compose 必须回退 PIL，不能返回空图；
3. HTML 路径能真的渲染出 1600×1600（用真实浏览器验证，环境缺失则跳过）。
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from wh_local.modules.product_processing.domain.html_composer import (
    LAYOUT_TEMPLATES,
    build_feature_cards_html,
    build_marketing_html,
    shutdown_renderer,
)
from wh_local.modules.product_processing.domain.marketing_pipeline import (
    MarketingCopy,
    compose,
    plan_for,
)


def _jpeg(width: int = 200, color=(90, 90, 90)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, width), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_feature_cards_html_binds_data_and_escapes() -> None:
    html = build_feature_cards_html(
        hero=_jpeg(),
        cards=[(_jpeg(), "Precision Cut"), (_jpeg(), "A & B <Grip>")],
        headline="4-IN-1\nMulti-Function",
        subline="Cut, strip & crimp",
        size=800,
    )

    assert "width:800px" in html
    assert "4-IN-1<br><span>Multi-Function</span>" in html
    assert "A &amp; B &lt;Grip&gt;" in html
    assert "Cut, strip &amp; crimp" in html
    assert "repeat(2,1fr)" in html
    assert html.count('class="card"') == 2
    assert "data:image/jpeg;base64," in html


def test_feature_cards_html_accepts_single_card_but_rejects_empty() -> None:
    # 单细节不硬凑：1 张卡正常出图；0 张才拒（不足不编造）。
    html = build_feature_cards_html(hero=_jpeg(), cards=[(_jpeg(), "Only")], headline="X")
    assert html
    with pytest.raises(ValueError):
        build_feature_cards_html(hero=_jpeg(), cards=[], headline="X")


def test_compose_falls_back_to_pil_when_html_engine_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    import wh_local.modules.product_processing.domain.html_composer as html_composer

    def boom(**_kwargs):
        raise RuntimeError("browser missing")

    monkeypatch.setattr(html_composer, "render_feature_cards", boom)

    copy = MarketingCopy(headline="4-IN-1", subline="", bullets=["A", "B"])
    blocks = {"hero": _jpeg(), "details": [(_jpeg(), "A"), (_jpeg(), "B")]}
    notes: list[str] = []

    composed = compose(
        plan=plan_for("feature_cards"), copy=copy, blocks=blocks, engine_notes=notes
    )

    assert composed, "HTML 失败时必须回退 PIL，不能返回空图"
    assert notes == ["feature_cards:pil"]
    assert Image.open(BytesIO(composed)).size == (1600, 1600)


def test_feature_cards_render_in_real_browser() -> None:
    """真实渲染（400×400 提速）。没有可用浏览器时跳过，不把测试环境问题当失败。"""
    from wh_local.modules.product_processing.domain import html_composer

    try:
        content = html_composer.render_feature_cards(
            hero=_jpeg(),
            cards=[(_jpeg(), "Precision Cut"), (_jpeg(), "Comfort Grip")],
            headline="4-IN-1\nMulti-Function",
            subline="Cut, strip and crimp with one tool",
            size=400,
        )
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no headless browser available: {exc}")
    finally:
        shutdown_renderer()

    assert content.startswith(b"\xff\xd8"), "默认输出 JPEG，与预览图 image/jpeg 契约一致"
    with Image.open(BytesIO(content)) as image:
        assert image.size == (400, 400)
        assert image.format == "JPEG"


# ── 照片+文案家族 ────────────────────────────────────────────────


def test_marketing_html_covers_all_photo_layouts() -> None:
    assert set(LAYOUT_TEMPLATES) == {
        "headline_hero", "action_proof", "icon_grid", "material_strip", "use_cases",
        "side_rail",
    }


def test_marketing_html_centers_headline_and_binds_bullets() -> None:
    html = build_marketing_html(
        template="icon_grid",
        hero=_jpeg(),
        headline="Built For\nThe Hard Jobs",
        subline="Drop-forged steel",
        bullets=["Steel Grip", "Wide Jaw <XL>"],
        size=800,
    )

    assert 'class="band"' in html and 'class="photo"' in html
    assert "Built For<br><span>The Hard Jobs</span>" in html
    # icon_grid 不画副文案；短词给几个画几个（不凭空造卖点）
    assert 'class="sub"' not in html
    assert html.count('class="chip"') == 2
    assert "Steel Grip" in html and "Wide Jaw &lt;XL&gt;" in html
    assert "<svg" in html


def test_marketing_html_never_fabricates_placeholder_bullets() -> None:
    # 不编造卖点：没有真实 bullets 时一个 chip 都不画（不再兜底占位词）。
    html = build_marketing_html(
        template="icon_grid", hero=_jpeg(), headline="Tough", subline="", bullets=[]
    )
    assert html.count('class="chip"') == 0


def test_marketing_html_material_strip_renders_pills() -> None:
    html = build_marketing_html(
        template="material_strip",
        hero=_jpeg(),
        headline="Rust Proof",
        subline="Marine grade steel",
        bullets=["Steel", "Aluminum", "Wood", "PVC"],
    )

    assert 'class="top "' in html
    assert html.count('class="pill"') == 4
    assert "<span>" not in html  # 单行标题不产生强调 span


def test_marketing_html_rejects_unknown_template_and_empty_hero() -> None:
    with pytest.raises(ValueError):
        build_marketing_html(template="nope", hero=_jpeg(), headline="X")
    with pytest.raises(ValueError):
        build_marketing_html(template="headline_hero", hero=b"", headline="X")


def test_marketing_compose_falls_back_to_pil_when_engine_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wh_local.modules.product_processing.domain.html_composer as html_composer

    def boom(**_kwargs):
        raise RuntimeError("browser missing")

    monkeypatch.setattr(html_composer, "render_marketing", boom)

    copy = MarketingCopy(headline="4-IN-1", subline="One tool", bullets=["A", "B"])
    notes: list[str] = []
    composed = compose(
        plan=plan_for("headline_hero"),
        copy=copy,
        blocks={"hero": _jpeg()},
        engine_notes=notes,
    )

    assert composed
    assert notes == ["headline_hero:pil"]
    assert Image.open(BytesIO(composed)).size == (1600, 1600)


def test_marketing_layout_renders_in_real_browser() -> None:
    from wh_local.modules.product_processing.domain import html_composer
    from wh_local.modules.product_processing.infrastructure.preview_image_files import (
        validate_preview_image,
    )

    try:
        for template in LAYOUT_TEMPLATES:
            content = html_composer.render_marketing(
                template=template,
                hero=_jpeg(),
                headline="Built For\nThe Hard Jobs",
                subline="Drop-forged steel with a grip that never slips",
                bullets=["Steel Grip", "Wide Jaw", "Hardened", "Lifetime"],
                size=400,
            )
            assert content.startswith(b"\xff\xd8"), template
            with Image.open(BytesIO(content)) as image:
                assert image.size == (400, 400), template
                assert image.format == "JPEG", template
            # 生产上按 image/jpeg + .jpg 落盘；PNG 会被预览校验直接拒绝（任务 55 的故障）。
            decoded = validate_preview_image(content, "image/jpeg")
            assert decoded.content_type == "image/jpeg"
            assert decoded.suffix == ".jpg"
    except AssertionError:
        raise
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no headless browser available: {exc}")
    finally:
        shutdown_renderer()
