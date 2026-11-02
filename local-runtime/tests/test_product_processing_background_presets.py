from __future__ import annotations

from wh_local.modules.product_processing.domain.background_presets import (
    BACKDROP_BY_CATEGORY,
    BACKDROP_FRAGMENTS,
    SYSTEM_BACKGROUNDS,
    background_asset_url,
    detect_category_id,
    pick_system_background,
    select_backdrop_fragment,
)


def test_eight_backdrop_fragments_are_short() -> None:
    assert len(BACKDROP_FRAGMENTS) == 8
    for text in BACKDROP_FRAGMENTS.values():
        assert 4 <= len(text.split()) <= 10, text


def test_every_category_maps_to_a_known_fragment() -> None:
    assert len(BACKDROP_BY_CATEGORY) == 12
    for category_id, fragment_id in BACKDROP_BY_CATEGORY.items():
        assert fragment_id in BACKDROP_FRAGMENTS, category_id


def test_select_backdrop_fragment_is_stable_and_text_aware() -> None:
    first = select_backdrop_fragment("家居 / 厨房", "木质收纳盒")
    second = select_backdrop_fragment("家居 / 厨房", "木质收纳盒")
    assert first == second
    assert first == BACKDROP_FRAGMENTS[BACKDROP_BY_CATEGORY["home_kitchen"]]
    # 认不出的品类也要落到某个片段，而不是空串
    fallback = select_backdrop_fragment("未知类目", "")
    assert fallback in BACKDROP_FRAGMENTS.values()


def test_detect_category_id_matches_keywords() -> None:
    assert detect_category_id("厨房用品", "硅胶铲") == "home_kitchen"
    assert detect_category_id("Pet Supplies", "dog leash") == "pet_supplies"
    assert detect_category_id("unknown", "") == "general"


def test_twenty_system_backgrounds_with_asset_paths() -> None:
    assert len(SYSTEM_BACKGROUNDS) == 20
    ids = [item["id"] for item in SYSTEM_BACKGROUNDS]
    assert len(set(ids)) == len(ids)
    for item in SYSTEM_BACKGROUNDS:
        assert item["usable_for_main"] in (True, False)
        assert background_asset_url(item["id"]).startswith("/assets/backgrounds/")


def test_pick_system_background_is_stable_and_filters() -> None:
    first = pick_system_background(shop_id="shop-1", batch_id="batch-1", panel=1)
    assert first is not None
    assert first == pick_system_background(shop_id="shop-1", batch_id="batch-1", panel=1)
    assert first["usable_for_main"] is True

    festive = pick_system_background(
        shop_id="shop-1",
        batch_id="batch-1",
        panel=3,
        categories=("adult_games",),
        style_family="festive",
    )
    assert festive is not None
    assert festive["style_family"] == "festive"


def test_every_template_has_prompt_fragment() -> None:
    for item in SYSTEM_BACKGROUNDS:
        assert item.get("prompt"), item["id"]
        assert 3 <= len(item["prompt"].split()) <= 12, item["id"]


def test_explicit_template_prompt_wins() -> None:
    from wh_local.modules.product_processing.domain.background_presets import background_template

    item = background_template("bg_walnut")
    assert item is not None
    assert item["prompt"] == "dark walnut tabletop against deep gray wall"
    assert (
        select_backdrop_fragment("家居", "收纳盒", template_id="bg_walnut")
        == item["prompt"]
    )


def test_unknown_template_falls_back_to_category_fragment() -> None:
    assert (
        select_backdrop_fragment("厨房", "", template_id="bg_not_exist")
        == BACKDROP_FRAGMENTS[BACKDROP_BY_CATEGORY["home_kitchen"]]
    )


def test_main_panel_never_picks_non_main_templates() -> None:
    for index in range(40):
        picked = pick_system_background(shop_id=f"shop-{index}", batch_id="b", panel=1)
        assert picked is not None and picked["usable_for_main"] is True
