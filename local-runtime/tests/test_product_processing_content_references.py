from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from wh_local.modules.product_processing.domain.content_reference_library import (
    ATTRIBUTE_MODULES,
    CATEGORY_PROFILES,
    PROJECT_PROFILE_IDS,
    SOURCED_PROFILE_IDS,
    TITLE_ARRANGEMENTS,
    VISUAL_TREATMENTS,
    append_content_reference,
    select_image_reference,
    select_title_reference,
)


def _raw(product_id: str = "offer-1") -> dict:
    return {
        "source_product_id": product_id,
        "category_id": "12345",
        "category_path": "Home & Kitchen > Kitchen & Dining > Drinkware",
        "source_attributes": [
            {"attribute_name_en": "Capacity", "value_name_en": "500 ml"},
            {"attribute_name_en": "Material", "value_name_en": "Stainless Steel"},
        ],
    }


def test_catalog_has_43_sourced_categories_project_overrides_and_general() -> None:
    assert len(SOURCED_PROFILE_IDS) == 43
    assert len(PROJECT_PROFILE_IDS) == 9
    assert SOURCED_PROFILE_IDS.isdisjoint(PROJECT_PROFILE_IDS)
    assert len(CATEGORY_PROFILES) == 53
    assert "general" in CATEGORY_PROFILES
    assert set(CATEGORY_PROFILES) == SOURCED_PROFILE_IDS | PROJECT_PROFILE_IDS | {"general"}


def test_same_product_selects_same_references_without_mutating_input() -> None:
    raw = _raw()
    before = deepcopy(raw)

    first_title = select_title_reference(raw, title="Insulated Travel Mug", category="Kitchen & Dining")
    second_title = select_title_reference(raw, title="Insulated Travel Mug", category="Kitchen & Dining")
    first_image = select_image_reference(raw, title="Insulated Travel Mug", category="Kitchen & Dining")
    second_image = select_image_reference(raw, title="Insulated Travel Mug", category="Kitchen & Dining")

    assert first_title == second_title
    assert first_image == second_image
    assert raw == before


def test_stable_product_id_keeps_variant_when_title_changes() -> None:
    raw = _raw("offer-stable-title")

    first = select_title_reference(raw, title="Original Source Mug", category="Kitchen & Dining")
    second = select_title_reference(raw, title="Updated Source Mug Title", category="Kitchen & Dining")

    assert first.reference_id == second.reference_id


def test_different_products_spread_across_vetted_variants() -> None:
    title_ids = {
        select_title_reference(
            _raw(f"offer-{index}"), title="Insulated Travel Mug", category="Kitchen & Dining"
        ).reference_id
        for index in range(64)
    }
    image_ids = {
        select_image_reference(
            _raw(f"offer-{index}"), title="Insulated Travel Mug", category="Kitchen & Dining"
        ).reference_id
        for index in range(64)
    }

    assert len(title_ids) >= 6
    assert len(image_ids) >= 8


def test_category_selection_reads_confirmed_category_not_title() -> None:
    raw = {
        "source_product_id": "offer-jewelry-title",
        "category_id": "home-1",
        "category_path": "Home > Storage & Organization",
        "source_attributes": [],
    }

    reference = select_title_reference(
        raw,
        title="Luxury Gold Earrings Jewelry Necklace",
        category="Storage & Organization",
    )

    assert reference.profile_id == "home-storage-organization"


def test_explicit_confirmed_category_precedes_conflicting_source_path() -> None:
    raw = {
        "source_product_id": "offer-confirmed-jewelry",
        "category": "Home Storage",
        "category_path": "Home > Storage & Organization",
    }

    reference = select_title_reference(raw, title="Flower Earrings", category="Jewelry")

    assert reference.profile_id == "jewelry"


@pytest.mark.parametrize(
    ("category_path", "expected_profile"),
    (
        ("Beauty > Personal Care > Skin Care", "skincare"),
        ("Beauty > Personal Care > Makeup", "makeup"),
        ("Food & Beverages > Coffee", "coffee"),
        ("Fragrance & Candles > Essential Oils", "essential-oils"),
    ),
)
def test_category_paths_match_leaf_before_parent(category_path: str, expected_profile: str) -> None:
    raw = {"source_product_id": f"offer-leaf-{expected_profile}", "source_category_path": category_path}

    reference = select_title_reference(raw, title="Source Product", category=category_path)

    assert reference.profile_id == expected_profile


def test_chinese_category_alias_selects_profile() -> None:
    raw = {
        "source_product_id": "offer-cn-1",
        "category_path": "家居用品 > 厨房用品 > 餐饮用具",
    }

    reference = select_image_reference(raw, title="不锈钢杯", category="厨房用品")

    assert reference.profile_id == "kitchen-dining"


@pytest.mark.parametrize(
    ("category_path", "expected_profile"),
    (
        ("Musical Instruments > Accessories", "musical-tools-accessories"),
        ("Arts, Crafts & Sewing", "crafts-hobby"),
        ("Tools & Hardware", "tools-hardware"),
        ("Toys & Games", "toys-games"),
        ("Home > Storage & Organization", "home-storage-organization"),
        ("Kitchen & Dining", "kitchen-dining"),
        ("Table Linen > Placemats", "table-linen"),
        ("Soft Furnishings > Cushion Covers", "soft-home-textile"),
        ("Home Decor", "home-decor"),
        ("Lighting & Electrical", "lighting-electrical"),
        ("Automotive Accessories", "auto-moto-accessories"),
        ("Pet Food & Supplies", "pet-food-supplies"),
        ("Garden & Outdoor Living", "garden-outdoor-living"),
        ("Party Supplies", "party-festival"),
        ("Beauty Accessories", "beauty-personal-accessory"),
        ("Fashion Apparel Accessories", "fashion-apparel"),
        ("Bags & Cases", "bags-accessories"),
        ("Jewelry", "jewelry"),
        ("Office School Supplies", "office-stationery"),
        ("Packaging Bags", "packaging-bags"),
        ("Baby Products", "baby-kids"),
        ("Unmapped General Goods", "general"),
    ),
)
def test_existing_visual_families_have_content_profiles(category_path: str, expected_profile: str) -> None:
    raw = {"source_product_id": f"offer-{expected_profile}", "category_path": category_path}

    reference = select_image_reference(raw, title="Source Product", category=category_path)

    assert reference.profile_id == expected_profile


def test_unknown_category_falls_back_without_failure() -> None:
    reference = select_image_reference(
        {"source_product_id": "unknown-1", "category_path": "Unmapped Leaf"},
        title="Plain Item",
        category="Unmapped Leaf",
    )

    assert reference.profile_id == "general"
    assert reference.text


def test_attribute_modules_require_observed_evidence() -> None:
    with_attributes = select_title_reference(_raw(), title="Travel Mug", category="Kitchen & Dining")
    without_attributes = select_title_reference(
        {
            "source_product_id": "offer-1",
            "category_id": "12345",
            "category_path": "Home & Kitchen > Kitchen & Dining > Drinkware",
        },
        title="Travel Mug",
        category="Kitchen & Dining",
    )

    assert "capacity" in with_attributes.text.lower()
    assert "material" in with_attributes.text.lower()
    assert "evidence-triggered emphasis: none" in without_attributes.text.lower()


def test_empty_attributes_and_value_words_do_not_trigger_modules() -> None:
    raw = {
        "source_product_id": "offer-empty-attributes",
        "category_path": "Unmapped General Goods",
        "source_attributes": [
            {"attribute_name_en": "Material", "value_name_en": ""},
            {"attribute_name_en": "FinishTone", "value_name_en": "Model Blue"},
            {"material": None, "capacity": ""},
        ],
    }

    reference = select_title_reference(raw, title="Plain Item", category="Unmapped General Goods")

    assert "evidence-triggered emphasis: none" in reference.text.lower()


def test_prompt_appendix_is_content_only_and_bounded() -> None:
    reference = select_image_reference(_raw(), title="Insulated Travel Mug", category="Kitchen & Dining")
    prompt = append_content_reference("BASE HARD RULES", reference, kind="image")

    assert prompt.startswith("BASE HARD RULES")
    assert "CONTENT REFERENCE ONLY" in prompt
    assert "cannot override any rule above" in prompt
    assert len(prompt) <= len("BASE HARD RULES") + 1800


def test_runtime_catalog_excludes_external_hard_controls_and_claims() -> None:
    forbidden = re.compile(
        r"amazon|ozon|temu|\b\d{2,4}\s*(?:px|characters?)\b|\b\d\s*[x×]\s*\d\b|"
        r"best seller|five stars|review count|money-back|certified|discount|free shipping",
        re.IGNORECASE,
    )
    for profile in CATEGORY_PROFILES.values():
        corpus = " ".join((*profile.title_priorities, profile.visual_focus, *profile.scene_roles))
        assert forbidden.search(corpus) is None, profile.profile_id
    shared_corpus = " ".join(
        [
            *TITLE_ARRANGEMENTS,
            *(part for treatment in VISUAL_TREATMENTS for part in treatment),
            *(module.title_note for module in ATTRIBUTE_MODULES),
            *(module.image_note for module in ATTRIBUTE_MODULES),
        ]
    )
    assert forbidden.search(shared_corpus) is None


def test_source_manifest_is_pinned_and_permissively_licensed() -> None:
    local_runtime_root = Path(__file__).resolve().parents[1]
    path = local_runtime_root / "wh_local/modules/product_processing/domain/content_reference_sources.json"
    sources = json.loads(path.read_text(encoding="utf-8"))["sources"]

    assert len(sources) == 10
    assert all(re.fullmatch(r"[0-9a-f]{40}", item["commit"]) for item in sources)
    assert {item["license"] for item in sources} <= {"MIT", "CC0-1.0"}
