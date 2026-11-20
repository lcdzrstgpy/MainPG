"""V2 快照/规则测试：6 个基线 × 5 张风格卡。"""

from __future__ import annotations

import pytest

from wh_local.modules.product_processing.prompting.brief import infer_brief
from wh_local.modules.product_processing.prompting.builder import build_grid_prompt
from wh_local.modules.product_processing.prompting.lint import lint_prompt
from wh_local.modules.product_processing.prompting.panel_plan import build_panel_plan
from wh_local.modules.product_processing.prompting.profiles import match_profile
from wh_local.modules.product_processing.prompting.scenes import build_scene_cards, detect_season

BASELINES = [
    ("Christmas Tree Shaped Brooch, Zinc Alloy Lapel Pin", "Jewelry", "halloween"),
    ("Retractable Pet Dog Leash, ABS Handle", "Pet Supplies", ""),
    ("Halloween Plush Spider Decoration", "Home Decor", "halloween"),
    ("Ceramic Coffee Mug", "Kitchen", ""),
    ("Canvas Tote Bag", "Apparel", ""),
    ("Linen Summer Dress", "Apparel", ""),
]


def test_profiles_match_expected_categories() -> None:
    assert match_profile("Christmas Tree Shaped Brooch").key == "small_jewelry"
    assert match_profile("Retractable Pet Dog Leash").key == "pet_walking"
    assert match_profile("Halloween Plush Spider Decoration").key in {"home_decor", "general"}
    assert match_profile("Ceramic Coffee Mug").key == "food"
    assert match_profile("Canvas Tote Bag").key == "bags"
    assert match_profile("Linen Summer Dress").key == "apparel"
    assert match_profile("Winter Beanie Hat").key == "headwear"


def test_season_detection() -> None:
    assert detect_season("Halloween Plush Spider") == "halloween"
    assert detect_season("Christmas Tree Brooch") == "christmas"
    assert detect_season("Ceramic Coffee Mug") == ""


def test_small_item_gets_scale_anchor_and_low_fill() -> None:
    brief = infer_brief("Christmas Tree Brooch", "Jewelry")
    assert brief.scale_anchor is True
    prompt = build_grid_prompt(brief, "E")
    assert "coin" in prompt.lower()
    assert "45%-65%" in prompt
    assert "78%-85%" not in prompt
    assert lint_prompt(prompt, brief, "E") == []


def test_soft_goods_never_stand_upright() -> None:
    brief = infer_brief("Canvas Tote Bag", "Apparel")
    prompt = build_grid_prompt(brief, "E")
    assert "drape" in prompt.lower() or "coil" in prompt.lower()
    assert "standing upright" not in prompt.lower()


def test_headwear_is_product_only() -> None:
    brief = infer_brief("Winter Beanie Hat", "Apparel")
    prompt = build_grid_prompt(brief, "E")
    assert "product only" in prompt.lower()
    assert "no person" in prompt.lower()


def test_human_policy_pet_walking_allows_hand_no_face() -> None:
    brief = infer_brief("Retractable Pet Dog Leash", "Pet Supplies")
    prompt = build_grid_prompt(brief, "E")
    assert "hand" in prompt.lower()
    assert "no face" in prompt.lower()


def test_non_white_style_never_mentions_pure_white() -> None:
    brief = infer_brief("Ceramic Coffee Mug", "Kitchen")
    for style in ("B", "C", "D", "E"):
        prompt = build_grid_prompt(brief, style)
        assert "pure white seamless" not in prompt.lower(), style


@pytest.mark.parametrize("title,category,season", BASELINES)
@pytest.mark.parametrize("style", ["A", "B", "C", "D", "E"])
def test_golden_baselines(title: str, category: str, season: str, style: str) -> None:
    brief = infer_brief(title, category, season_hint=season)
    prompt = build_grid_prompt(brief, style, seed=7)
    assert len(prompt) <= 8000
    assert lint_prompt(prompt, brief, style) == []
    assert "PANEL PLAN" in prompt and ("CONTRACT" in prompt or "硬契约" in prompt)


def test_pet_wearable_hero_shows_the_pet_not_flat() -> None:
    brief = infer_brief("Pet Christmas Sweater", "Pet Supplies")
    prompt = build_grid_prompt(brief, "E")
    panels = prompt.split("PANEL PLAN", 1)[1].splitlines()
    first = next(line for line in panels if line.startswith("1 |"))
    assert "wearing" in first
    assert "never a flat lay" in first.lower()


def test_small_decor_never_shows_hands() -> None:
    brief = infer_brief("Lion Statue Ornament", "Home Decor")
    prompt = build_grid_prompt(brief, "E")
    assert "one adult hand" not in prompt


def test_kitchen_basket_can_show_a_hand() -> None:
    brief = infer_brief("Double Layer Fruit Basket Colander", "Kitchen")
    prompt = build_grid_prompt(brief, "E")
    panels = prompt.split("PANEL PLAN", 1)[1].splitlines()
    panel2 = next(line for line in panels if line.startswith("2 |"))
    assert "hand" in panel2.lower()


def test_kitchen_tool_gets_kitchen_scene_and_hand() -> None:
    brief = infer_brief("Manual Citrus Juicer", "Kitchen")
    assert brief.profile == "kitchen_tool"
    prompt = build_grid_prompt(brief, "E")
    assert "hand holding" in prompt
    assert "citrus" in prompt


def test_storage_rack_shows_function() -> None:
    brief = infer_brief("Under-Cabinet Hanging Storage Rack", "Kitchen")
    assert brief.profile == "home_storage"
    prompt = build_grid_prompt(brief, "E")
    assert "under a kitchen cabinet" in prompt


def test_pet_toy_has_pet_playing() -> None:
    brief = infer_brief("Squeaky Plush Dog Toy", "Pet Supplies")
    prompt = build_grid_prompt(brief, "E")
    assert "playing with it" in prompt


def test_pet_wearable_uses_pet_floor() -> None:
    brief = infer_brief("Winter Dog Coat", "Pet Supplies")
    prompt = build_grid_prompt(brief, "E")
    assert ("floor" in prompt) or ("rug" in prompt)


def test_pet_harness_with_charm_is_walking_with_animal() -> None:
    brief = infer_brief(
        "Pet Harness Leash and Matching Hat Set, Denim Dog Harness with Plush Bear Charm",
        "Pet Supplies",
    )
    assert brief.profile == "pet_walking"
    prompt = build_grid_prompt(brief, "E")
    assert "wearing the harness" in prompt


def test_car_dashboard_ornament_uses_car_scene() -> None:
    brief = infer_brief("Green Lizard Figurine, Desktop and Car Dashboard Decor", "Home Decor")
    assert brief.profile == "car_decor"
    assert brief.domain == "car"
    prompt = build_grid_prompt(brief, "E")
    assert "dashboard" in prompt or "console" in prompt or "air vent" in prompt


def test_no_caption_rule_is_present() -> None:
    brief = infer_brief("DIY Hair Clip Materials", "Craft")
    prompt = build_grid_prompt(brief, "E")
    assert ("no caption" in prompt) or ("字幕" in prompt)


def test_ornate_styles_add_atmosphere_props() -> None:
    brief = infer_brief("Ceramic Vase", "Home Decor")
    for style in ("E", "B"):
        prompt = build_grid_prompt(brief, style)
        assert ("dried florals" in prompt) or ("pillar candle" in prompt), style


def test_clean_styles_stay_clean() -> None:
    brief = infer_brief("Ceramic Vase", "Home Decor")
    for style in ("A", "C"):
        prompt = build_grid_prompt(brief, style)
        assert "dried florals" not in prompt, style
        assert "pillar candle" not in prompt, style


def test_props_are_supporting_only() -> None:
    brief = infer_brief("Ceramic Vase", "Home Decor")
    prompt = build_grid_prompt(brief, "E")
    assert ("轻微虚化" in prompt) or ("softly out of focus" in prompt)


def test_coaster_shows_mug_and_in_use() -> None:
    brief = infer_brief("Handmade Crocheted Tree Cup Coaster", "Home Decor")
    assert brief.profile == "coaster"
    prompt = build_grid_prompt(brief, "E")
    assert "ceramic mug" in prompt
    assert "hand setting" in prompt
    assert "never put the mug on top of a stack" in prompt


def test_multi_piece_set_uses_one_hero_plus_full_set() -> None:
    brief = infer_brief("Set of 3 Ceramic Vases", "Home Decor")
    assert brief.is_set is True
    prompt = build_grid_prompt(brief, "E")
    assert "one piece as the hero" in prompt
    assert "complete set arranged together" in prompt


def test_single_product_does_not_use_set_layout() -> None:
    brief = infer_brief("Ceramic Coffee Mug", "Kitchen")
    assert brief.is_set is False
    prompt = build_grid_prompt(brief, "E")
    assert "one piece as the hero" not in prompt


def test_campaign_style_lock_is_shared_across_styles() -> None:
    brief = infer_brief("Ceramic Vase", "Home Decor")
    scene = build_grid_prompt(brief, "E")
    white = build_grid_prompt(brief, "A")
    assert "CAMPAIGN STYLE LOCK" in scene
    assert "CAMPAIGN STYLE LOCK" in white
    assert "#F5F1E8" in scene
    assert "#FFFFFF" in white


def test_scene_cards_are_four_distinct_places() -> None:
    brief = infer_brief("Ceramic Coffee Mug", "Kitchen")
    cards = build_scene_cards(
        domain=brief.domain, season="", style_key="E", seed=3,
        scale_anchor=False, prop_policy="minimal",
    )
    assert len({card.place for card in cards}) == 4


def test_panel_plan_headwear_never_shows_head() -> None:
    brief = infer_brief("Winter Beanie Hat", "Apparel")
    cards = build_scene_cards(
        domain=brief.domain, season="", style_key="E", seed=0,
        scale_anchor=True, prop_policy="minimal",
    )
    plan = build_panel_plan(brief, __import__("wh_local.modules.product_processing.prompting.styles", fromlist=["get_style"]).get_style("E"), cards)
    assert all("no head" in line or "never worn" in line or "stand" in line for line in plan[:1])
