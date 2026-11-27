"""信息图版式（A+ 风格两张 1:1）：结构、编号引线与 compose 路由。"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

from wh_local.modules.product_processing.domain.detail_specs import DetailSpec
from wh_local.modules.product_processing.domain.infographic_composer import (
    build_infographic_html,
)
from wh_local.modules.product_processing.domain.marketing_pipeline import (
    compose,
    plan_for,
    render_blocks,
)
from wh_local.modules.product_processing.domain.premium_composer import MarketingCopy


def _photo(product_top: float = 0.2, product_bottom: float = 0.72) -> bytes:
    image = Image.new("RGB", (800, 800), (226, 220, 208))
    draw = ImageDraw.Draw(image)
    draw.rectangle([260, int(800 * product_top), 540, int(800 * product_bottom)], fill=(32, 34, 38))
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _copy() -> MarketingCopy:
    return MarketingCopy(
        headline="Industrial Carbon Steel Pliers",
        subline="One tool for work and daily DIY",
        bullets=["Durable Performance", "Comfortable Grip", "Built For Everyday Use", "Multi Size"],
    )


def _details(count: int = 5) -> list[tuple[bytes, str]]:
    labels = ["Jaws", "Pivot", "Grip", "Handle", "Lever"]
    return [(_photo(0.1 + index * 0.1, 0.25 + index * 0.1), labels[index]) for index in range(count)]


def _parts(count: int = 5) -> list[dict]:
    return [
        {"label": f"Part {index + 1}", "x": 400 + index * 30, "y": 150 + index * 150}
        for index in range(count)
    ]


def test_infographic_hero_html_has_benefits_icons_and_cards() -> None:
    html = build_infographic_html(
        "infographic_hero",
        hero=_photo(),
        copy=_copy(),
        details=_details(4),
        category_id="hand_tools",
    )
    assert html.count('class="case"') == 4
    assert html.count('class="benefit"') == 3
    assert "<svg" in html and "Product Highlights" in html
    assert "<script" not in html.lower()
    # 标题最后一行用强调色
    assert "<span>" in html


def test_layout_never_fabricates_bullets_or_drops_numbers() -> None:
    """不编造卖点词；编号标注必须显示编号；单张细节也出图；长标题自适应。"""
    from wh_local.modules.product_processing.domain.html_composer import (
        _bullets_for,
        _promo_card,
        _promo_row,
        build_feature_cards_html,
    )

    # 无卖点时留空由版式降级，不画 DURABLE/PRECISE 之类编造词
    assert _bullets_for([], "", 4) == []

    # 编号标注：徽章画编号圆而不是图标
    assert ">2</span>" in _promo_card({"label": "JAW", "number": 2}, 0)
    assert ">3</span>" in _promo_row({"label": "JAW", "number": 3}, 0)

    # 1 张真细节也出（单卡自适应），不再「不足 2 张不出」
    html = build_feature_cards_html(
        hero=_photo(),
        cards=[(_photo(), "JAW TIP")],
        headline="Cut Clean",
        category_id="hand_tools",
    )
    assert "JAW TIP" in html

    # 长标题/长副标挂 data-max-height 自适应
    hero_html = build_infographic_html(
        "infographic_hero",
        hero=_photo(),
        copy=MarketingCopy(
            headline="Heavy Duty Compound Action Wire Cutting Pliers For Industrial Cable Work",
            subline="Precision forged jaws deliver effortless clean cuts through thick wires and cables every single day",
        ),
        details=[],
        category_id="hand_tools",
    )
    assert "data-max-height" in hero_html


def test_infographic_cards_never_fabricate_from_hero() -> None:
    """细节卡只用真实近景：不足 4 张不从 hero 裁背景凑数（「一坨」的来源）。"""
    from wh_local.modules.product_processing.domain.infographic_composer import _cards

    details = [(b"img1", "Wire Cutter"), (b"img2", "Comfort Grip")]
    cards = _cards(
        details,
        b"hero-bytes",
        MarketingCopy(headline="X", bullets=["A", "B", "C", "D"]),
    )
    assert len(cards) == 2
    assert [label for _content, label, _caption in cards] == [
        "Wire Cutter",
        "Comfort Grip",
    ]

    # 2 张细节 → 单行 2 列卡带；0 张 → 不画卡区
    two = build_infographic_html(
        "infographic_hero",
        hero=_photo(),
        copy=_copy(),
        details=_details(2),
        category_id="hand_tools",
    )
    assert 'class="cases is-band"' in two
    assert "repeat(2,1fr)" in two
    assert two.count('class="case"') == 2

    none = build_infographic_html(
        "infographic_hero",
        hero=_photo(),
        copy=_copy(),
        details=[],
        category_id="hand_tools",
    )
    assert 'class="cases' not in none


def test_infographic_theme_follows_category_brightness() -> None:
    """auto 默认浅色信息图（含工具类，不出暗黑环境图）；显式 dark 才走暗色。"""
    bright = build_infographic_html(
        "infographic_details",
        hero=_photo(),
        copy=_copy(),
        details=_details(2),
        category_id="apparel_accessories",
    )
    assert "#f4efe6" in bright
    assert "#08090b" not in bright

    tool_auto = build_infographic_html(
        "infographic_details",
        hero=_photo(),
        copy=_copy(),
        details=_details(2),
        category_id="hand_tools",
    )
    assert "#08090b" not in tool_auto

    dark = build_infographic_html(
        "infographic_details",
        hero=_photo(),
        copy=_copy(),
        details=_details(2),
        category_id="hand_tools",
        style_template="dark",
    )
    assert "#08090b" in dark


def test_infographic_style_template_overrides_category() -> None:
    """工具选亮色、包袋选暗色时，信息图主题要跟用户选择走。"""
    tool_bright = build_infographic_html(
        "infographic_details",
        hero=_photo(),
        copy=_copy(),
        details=_details(2),
        category_id="hand_tools",
        style_template="bright",
    )
    assert "#f4efe6" in tool_bright

    bag_dark = build_infographic_html(
        "infographic_details",
        hero=_photo(),
        copy=_copy(),
        details=_details(2),
        category_id="apparel_accessories",
        style_template="dark",
    )
    assert "#08090b" in bag_dark


def test_hero_cards_stay_left_or_fall_back_to_bottom_band(monkeypatch) -> None:
    """卡片只放左下；商品碰到左下卡片区就改底部卡带，绝不把卡片放右侧。"""

    from wh_local.modules.product_processing.domain import dimension_source_reuse

    for bbox, expected in (
        ((0.55, 0.20, 0.92, 0.90), "is-left"),
        ((0.08, 0.20, 0.45, 0.90), "is-band"),
        ((0.03, 0.18, 0.97, 0.92), "is-band"),
    ):
        monkeypatch.setattr(
            dimension_source_reuse,
            "subject_bbox_ratios",
            lambda _hero, _bbox=bbox: _bbox,
        )
        html = build_infographic_html(
            "infographic_hero",
            hero=_photo(),
            copy=_copy(),
            details=_details(4),
            category_id="hand_tools",
        )
        assert f'class="cases {expected}"' in html
        assert 'class="cases is-right"' not in html


def test_infographic_details_html_renders_list_and_leader_lines() -> None:
    html = build_infographic_html(
        "infographic_details",
        hero=_photo(),
        copy=_copy(),
        details=_details(5),
        callout_parts=_parts(5),
        category_id="hand_tools",
    )
    assert html.count('class="drow"') == 5
    # 每个部位 = 圆点 + 光晕 + 编号圆，共 3 个 circle
    assert html.count("<circle") == 15
    assert 'class="pane"' in html
    assert "object-fit:contain" in html


def test_render_blocks_exposes_part_centres_for_callouts() -> None:
    spec = DetailSpec(feature_id="jaws", part="Jaws", crop=(0.30, 0.10, 0.50, 0.30))
    blocks = render_blocks(
        plan=plan_for("infographic_details"),
        copy=_copy(),
        refs=["https://img.example/a.jpg"],
        render=lambda *_args, **_kwargs: _photo(),
        specs_factory=lambda _hero: [spec],
    )
    parts = blocks.get("callout_parts") or []
    assert len(parts) == 1
    assert parts[0]["x"] == 400.0 and parts[0]["y"] == 200.0


def test_compose_routes_infographic_templates(monkeypatch) -> None:
    from wh_local.modules.product_processing.domain import infographic_composer

    monkeypatch.setattr(
        infographic_composer, "render_infographic", lambda *_args, **_kwargs: b"jpeg"
    )
    for template in ("infographic_hero", "infographic_details"):
        out = compose(
            plan=plan_for(template),
            copy=_copy(),
            blocks={"hero": _photo(), "details": [], "category_id": "hand_tools"},
            skeleton="S1",
        )
        assert out == b"jpeg"
