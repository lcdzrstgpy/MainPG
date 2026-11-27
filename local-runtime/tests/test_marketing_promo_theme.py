"""促销排版：所有品类统一暗色排版，强调色跟随商品主色/品类色。"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from wh_local.modules.product_processing.domain.html_composer import (
    _promo_palette,
    build_skeleton_html,
    promo_theme,
)
from wh_local.modules.product_processing.domain.marketing_gen import build_prompt


def _jpeg(width: int = 320, color=(40, 42, 46)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, width), color).save(buffer, format="JPEG")
    return buffer.getvalue()


BULLETS = ["Ratchet Lock", "Forged Steel", "1.4in Jaw Opening", "Comfort Grip"]
PARTS = [
    {"label": "RATCHET LOCK", "x": 500, "y": 320},
    {"label": "1.4IN JAW OPENING", "x": 420, "y": 500},
    {"label": "FORGED STEEL", "x": 620, "y": 820},
    {"label": "COMFORT GRIP", "x": 560, "y": 700},
]


def test_promo_theme_follows_category_brightness() -> None:
    """auto 默认全亮（不出暗黑环境图）；暗色版式只在显式选 dark 时用。"""
    for category in (
        "hand_tools",
        "auto_accessories",
        "sports_outdoor",
        "passive_tech",
        "general",
        "",
        "apparel_accessories",
        "beauty_tools",
        "home_kitchen",
        "pet_supplies",
        "office_stationery",
    ):
        assert not promo_theme(category), f"{category} auto 不应再用暗色版式"
    assert promo_theme("hand_tools", style_template="dark")
    assert promo_theme("apparel_accessories", style_template="dark")


def test_promo_palette_follows_product_then_category() -> None:
    neutral = _jpeg(color=(120, 120, 120))
    kitchen = _promo_palette("home_kitchen", neutral)
    tools = _promo_palette("hand_tools", neutral)
    assert kitchen["accent"] != tools["accent"], "中性商品回落到各自的品类色"

    red = _jpeg(color=(200, 30, 40))
    red_palette = _promo_palette("hand_tools", red)
    assert red_palette["accent"].lower() != tools["accent"].lower(), (
        "有彩色商品时强调色跟随商品主色，而不是固定橙色"
    )


def test_promo_skeleton_uses_dark_theme_with_palette_vars() -> None:
    html = build_skeleton_html(
        template="callout_ring",
        skeleton="S3",
        hero=_jpeg(),
        headline='9" Ratchet Self-Locking Pliers',
        subline="Powerful Grip for Everyday Repairs",
        bullets=BULLETS,
        callout_parts=PARTS,
        category_id="hand_tools",
        style_template="dark",
    )
    assert "PREMIUM DETAILS" in html
    assert "var(--acc)" in html
    assert "--acc:" in html
    assert "font-style:italic" in html
    assert "-webkit-text-stroke" in html
    # 暗色舞台，不再出现浅色编辑风的信息带底色
    assert "background:#08090b" in html
    assert "#fbf9f5" not in html
    # 四角圆形细节卡：每个部位一条标签
    assert html.count("RATCHET LOCK") >= 1
    assert html.count("1.4IN JAW OPENING") >= 1


def test_promo_band_layout_keeps_feature_cards() -> None:
    html = build_skeleton_html(
        template="feature_cards",
        skeleton="S1",
        hero=_jpeg(),
        headline="4-IN-1 Multi-Function Pliers",
        bullets=BULLETS,
        details=[(_jpeg(120), "Wire Stripping"), (_jpeg(120), "Cutting")],
        category_id="hand_tools",
        style_template="dark",
    )
    assert "PRODUCT DETAILS" in html
    assert "// BUILT TO PERFORM" in html
    assert html.count("Wire Stripping") >= 1
    assert "height:36%" in html


def test_promo_prompt_varies_rim_light_by_category() -> None:
    tools = build_prompt("headline_hero", "hand_tools", 0, skeleton="S3", style_template="dark")
    assert "warm amber rim light" in tools
    assert "dark low-key background" in tools
    # 暗色也要把产品拍亮，不能出「物品很暗」的图
    assert "never muddy or underexposed" in tools
    # 暗色结构预留区：标题与底部条位置留空
    assert "top 30%" in tools and "bottom 12%" in tools

    # auto 默认明亮影棚，不再出暗黑环境图
    auto_tools = build_prompt("headline_hero", "hand_tools", 0, skeleton="S3")
    assert "dark low-key background" not in auto_tools
    assert "bright airy warm light-grey background" in auto_tools

    kitchen = build_prompt("headline_hero", "home_kitchen", 0, skeleton="S3")
    assert "warm window-light rim" in kitchen
    assert "warm amber rim light" not in kitchen
    assert "top 30%" in kitchen
