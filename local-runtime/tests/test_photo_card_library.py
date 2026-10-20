from __future__ import annotations

import pytest

from wh_local.modules.product_processing.domain.photo_card_library import (
    AUX_CARDS,
    CARD_LIBRARIES,
    CATEGORY_AUX_DEFAULTS,
    CATEGORY_DEFAULT_PACKS,
    LAYER_KEYS,
    PRESET_INTENTS,
    assemble_image_prompt,
    assemble_panel_image_prompt,
    render_panel_plan_override,
)
from wh_local.modules.product_processing.service import ProductProcessingService

SIMPLE_PRESET_IDS = tuple(preset_id for preset_id in PRESET_INTENTS if preset_id != "auto")


def _white(frame: str = "frame_hero") -> dict:
    return {
        "frame": frame,
        "light": "light_studio_hero",
        "scene": "scene_white_sweep",
        "props": [],
        "white_hero": True,
    }


# 与前端 SIMPLE_PRESETS 保持一致：四套模板不只换全局光影，四格构图/场景/视角也不同。
SIMPLE_PRESET_LAYERS = {
    "hero_proof": {
        "frame": "frame_hero",
        "light": "light_clean_bright",
        "tone": "tone_bright_airy",
        "detail": "detail_matte_plastic",
        "style": "bright_minimal",
    },
    "lifestyle_use": {
        "frame": "frame_environment",
        "light": "light_soft_home",
        "tone": "tone_natural",
        "detail": "detail_ceramic_fabric",
        "style": "calm_domestic",
    },
    "macro_craft": {
        "frame": "frame_macro",
        "light": "light_workshop_hard",
        "tone": "tone_editorial_muted",
        "detail": "detail_metal_wood",
        "style": "editorial_minimal",
    },
    "knolling_set": {
        "frame": "frame_flatlay",
        "light": "light_clean_bright",
        "tone": "tone_bright_airy",
        "detail": "detail_paper_plastic",
        "style": "bright_minimal",
    },
}

SIMPLE_PRESET_PANELS = {
    "hero_proof": [
        _white(),
        {"frame": "frame_portrait", "light": "light_clean_bright", "tone": "tone_natural", "detail": "detail_matte_plastic", "style": "bright_minimal", "scene": "scene_stone", "props": []},
        {"frame": "frame_low_angle", "light": "light_clean_bright", "tone": "tone_bright_airy", "detail": "detail_matte_plastic", "style": "bright_minimal", "scene": "scene_plaster", "props": []},
        {"frame": "frame_macro", "light": "light_clean_bright", "tone": "tone_bright_airy", "detail": "detail_matte_plastic", "style": "bright_minimal", "scene": "scene_plaster", "props": []},
    ],
    "lifestyle_use": [
        _white(),
        {"frame": "frame_hero", "light": "light_soft_home", "tone": "tone_natural", "detail": "detail_ceramic_fabric", "style": "calm_domestic"},
        {"frame": "frame_environment", "light": "light_editorial_sun", "tone": "tone_warm_moody", "detail": "detail_ceramic_fabric", "style": "calm_domestic"},
        {"frame": "frame_macro", "light": "light_window_editorial", "tone": "tone_natural", "detail": "detail_ceramic_fabric", "style": "calm_domestic", "props": []},
    ],
    "macro_craft": [
        _white("frame_macro"),
        {"frame": "frame_macro", "light": "light_workshop_hard", "tone": "tone_industrial", "detail": "detail_metal_wood", "style": "industrial", "scene": "scene_concrete", "props": []},
        {"frame": "frame_low_angle", "light": "light_workshop_hard", "tone": "tone_warm_moody", "detail": "detail_metal_wood", "style": "editorial_minimal", "scene": "scene_dark_marble", "props": []},
        {"frame": "frame_macro", "light": "light_editorial_sun", "tone": "tone_warm_moody", "detail": "detail_metal_wood", "style": "editorial_minimal", "scene": "scene_plaster", "props": []},
    ],
    "knolling_set": [
        _white("frame_flatlay"),
        {"frame": "frame_flatlay", "light": "light_clean_bright", "tone": "tone_bright_airy", "detail": "detail_paper_plastic", "style": "bright_minimal", "scene": "scene_felt"},
        {"frame": "frame_flatlay", "light": "light_clean_bright", "tone": "tone_natural", "detail": "detail_paper_plastic", "style": "bright_minimal", "scene": "scene_marble"},
        {"frame": "frame_flatlay_detail", "light": "light_clean_bright", "tone": "tone_bright_airy", "detail": "detail_paper_plastic", "style": "bright_minimal", "scene": "scene_felt", "props": []},
    ],
}


def test_all_cards_are_single_line_and_short() -> None:
    for layer in LAYER_KEYS:
        assert CARD_LIBRARIES[layer], layer
        for card_id, card in CARD_LIBRARIES[layer].items():
            assert card.id == card_id
            assert "\n" not in card.prompt_en, card_id
            assert "{" not in card.prompt_en and "}" not in card.prompt_en, card_id
            assert len(card.prompt_en.split()) <= 25, (card_id, len(card.prompt_en.split()))


@pytest.mark.parametrize("category_id", tuple(CATEGORY_DEFAULT_PACKS))
def test_default_assembly_for_every_category(category_id: str) -> None:
    assembly = assemble_image_prompt(category_id)
    assert assembly.category_id == category_id
    assert assembly.word_count <= 110, (category_id, assembly.word_count)
    assert assembly.over_budget is False
    assert "\n" not in assembly.prompt
    assert "{" not in assembly.prompt and "}" not in assembly.prompt
    for label in ("Compose:", "Light:", "Color:", "Detail:", "Style:"):
        assert label in assembly.prompt, (category_id, label)


def test_explicit_layer_overrides_are_used() -> None:
    assembly = assemble_image_prompt(
        "home_kitchen",
        {
            "frame": "frame_macro",
            "light": "light_workshop_hard",
            "tone": "tone_industrial",
            "detail": "detail_metal_wood",
            "style": "industrial",
        },
    )
    assert assembly.selections == {
        "frame": "frame_macro",
        "light": "light_workshop_hard",
        "tone": "tone_industrial",
        "detail": "detail_metal_wood",
        "style": "industrial",
    }
    assert "Compose: 90mm macro" in assembly.prompt
    assert "Light: hard raking work light" in assembly.prompt
    assert "Color: muted color" in assembly.prompt
    assert "Detail: anisotropic metal highlights" in assembly.prompt
    assert "Style: industrial workshop mood" in assembly.prompt


def test_auto_and_invalid_values_fall_back_to_category_defaults() -> None:
    assembly = assemble_image_prompt(
        "hand_tools",
        {
            "frame": "auto",
            "light": "not-a-light",
            "tone": None,
            "detail": "",
            "style": "auto",
        },
    )
    defaults = CATEGORY_DEFAULT_PACKS["hand_tools"]
    assert assembly.selections == defaults
    assert set(assembly.used_defaults) == set(LAYER_KEYS)


def test_unknown_category_falls_back_to_general() -> None:
    assembly = assemble_image_prompt("not-a-category")
    assert assembly.category_id == "general"
    assert assembly.selections == CATEGORY_DEFAULT_PACKS["general"]


def test_preview_service_returns_prompt_and_word_count() -> None:
    service = ProductProcessingService.__new__(ProductProcessingService)
    result = service.preview_image_prompt(
        {
            "category_id": "beauty_tools",
            "frame": "frame_macro",
            "light": "light_vanity",
            "tone": "tone_clinical",
            "detail": "detail_metal_ceramic",
            "style": "clinical_clean",
        }
    )
    assert result["category_id"] == "beauty_tools"
    assert result["word_count"] == len(result["prompt"].split())
    assert result["word_count"] <= 110
    assert result["selections"]["style"] == "clinical_clean"


@pytest.mark.parametrize("preset_id", SIMPLE_PRESET_IDS)
def test_simple_preset_injects_buyer_proof_intent(preset_id: str) -> None:
    assembly = assemble_panel_image_prompt(
        "home_kitchen",
        SIMPLE_PRESET_PANELS[preset_id],
        SIMPLE_PRESET_LAYERS[preset_id],
        preset_id=preset_id,
    )
    assert assembly.preset_id == preset_id
    assert PRESET_INTENTS[preset_id] in assembly.prompt
    assert assembly.prompt.count("BUYER PROOF") == 1
    assert assembly.word_count <= 340, (preset_id, assembly.word_count)
    assert "\n" not in assembly.prompt
    assert "{" not in assembly.prompt and "}" not in assembly.prompt


def test_auto_preset_adds_no_buyer_proof_intent() -> None:
    assert assemble_image_prompt("home_kitchen").preset_id == "auto"
    assert "BUYER PROOF" not in assemble_image_prompt("home_kitchen").prompt
    assert (
        assemble_panel_image_prompt("home_kitchen", SIMPLE_PRESET_PANELS["hero_proof"])
        .prompt.count("BUYER PROOF")
        == 0
    )


def test_render_panel_plan_override_keeps_operator_props() -> None:
    """导演模式逐格卡片要以高优先级逐个复述，道具必须原样出现。"""

    assembly = assemble_panel_image_prompt(
        "home_kitchen",
        [
            _white(),
            {"frame": "auto", "scene": "scene_linen", "props": ["prop_linen_towel"]},
            {"frame": "auto", "scene": "scene_light_oak", "props": ["prop_woven_basket"]},
            {"frame": "auto", "scene": "auto", "props": []},
        ],
        SIMPLE_PRESET_LAYERS["hero_proof"],
    )
    override = render_panel_plan_override(assembly)

    assert override.startswith("USER PANEL PLAN OVERRIDE")
    assert "one folded linen towel" in override
    assert "one woven basket" in override
    assert "THIS BLOCK WINS" in override
    assert override.count("PANEL ") >= 4


def test_unknown_preset_falls_back_to_auto() -> None:
    assembly = assemble_image_prompt("home_kitchen", preset_id="not-a-preset")
    assert assembly.preset_id == "auto"
    assert "BUYER PROOF" not in assembly.prompt


def test_four_simple_presets_differ_in_composition_and_scene() -> None:
    frame_sequences = set()
    scene_sequences = set()
    prompts = set()
    for preset_id in SIMPLE_PRESET_IDS:
        assembly = assemble_panel_image_prompt(
            "office_stationery",
            SIMPLE_PRESET_PANELS[preset_id],
            SIMPLE_PRESET_LAYERS[preset_id],
            preset_id=preset_id,
        )
        panels = [assembly.selections[f"panel{index}"] for index in range(1, 5)]
        frame_sequences.add(tuple(panel["frame"] for panel in panels))
        scene_sequences.add(tuple(panel["scene"] for panel in panels))
        prompts.add(assembly.prompt)

    assert len(prompts) == len(SIMPLE_PRESET_IDS)
    assert len(frame_sequences) == len(SIMPLE_PRESET_IDS)
    assert len(scene_sequences) == len(SIMPLE_PRESET_IDS)


def test_preview_service_returns_preset_id_for_panels() -> None:
    service = ProductProcessingService.__new__(ProductProcessingService)
    result = service.preview_image_prompt(
        {
            "category_id": "office_stationery",
            "preset_id": "knolling_set",
            "panels": SIMPLE_PRESET_PANELS["knolling_set"],
            **SIMPLE_PRESET_LAYERS["knolling_set"],
        }
    )
    assert result["preset_id"] == "knolling_set"
    assert "BUYER PROOF" in result["prompt"]
    assert len(result["panels"]) == 4


FINE_PROP_IDS = (
    "prop_ruler",
    "prop_pencil",
    "prop_eraser",
    "prop_sticky_note",
    "prop_pen_cup",
    "prop_wooden_spoon",
    "prop_glass_jar",
    "prop_chew_bone",
    "prop_makeup_brush",
    "prop_comb",
    "prop_candle",
)


def test_prompts_suppress_hands_and_fine_props() -> None:
    """手指和尺子/笔这类精细道具是生图畸变高发项：提示词必须显式排除。"""
    for category_id in CATEGORY_DEFAULT_PACKS:
        prompt = assemble_image_prompt(category_id).prompt
        assert "hands/fingers" in prompt, category_id
        # 不允许再用“手”作为尺寸参照，否则模型会画手。
        assert "vs hand" not in prompt, category_id
        assert "hand or" not in prompt, category_id

    panel_prompt = assemble_panel_image_prompt(
        "office_stationery",
        SIMPLE_PRESET_PANELS["hero_proof"],
        SIMPLE_PRESET_LAYERS["hero_proof"],
        preset_id="hero_proof",
    ).prompt
    assert "hands/fingers" in panel_prompt
    assert "never rulers" in panel_prompt

    assert "no hands" in PRESET_INTENTS["lifestyle_use"].lower()
    assert "no hands" in PRESET_INTENTS["knolling_set"].lower()


def test_fine_props_are_not_offered_or_used_by_default() -> None:
    for prop_id in FINE_PROP_IDS:
        assert prop_id not in AUX_CARDS, prop_id
        for pack in CATEGORY_AUX_DEFAULTS.values():
            assert prop_id not in (pack.get("props") or ()), prop_id


def test_image_prompt_templates_do_not_request_hands_or_prop_clutter() -> None:
    """运行时的四宫格/单图模板不得再要求手部或 2-4 件道具。"""
    from wh_local.modules.product_processing.domain import prompts
    from wh_local.modules.product_processing.domain.visual_planner import (
        scene_plan_for_family,
    )

    template_names = (
        "ARK_GRID_IMAGE_PROMPT",
        "ARK_GRID_IMAGE_PROMPT_B",
        "ARK_GRID_IMAGE_PROMPT_C",
        "ARK_GRID_IMAGE_PROMPT_D",
        "IMAGE_SET_PROMPT",
        "IMAGE_SET_PROMPT_B",
        "IMAGE_SET_PROMPT_C",
        "IMAGE_SET_PROMPT_D",
        "GRID_IMAGE_PROMPT",
        "GRID_IMAGE_PROMPT_B",
        "GRID_IMAGE_PROMPT_C",
        "GRID_IMAGE_PROMPT_D",
        "PREMIUM_IMAGE_PROMPT",
    )
    forbidden = (
        "partial adult hand",
        "partial hand",
        "You may add realistic adult hands",
        "must actively hold",
        "may actually use or wear",
        "a real hand or a familiar",
        "MANDATORY IN-USE MOMENT",
        "2-4 companion",
        "2-4 件",
    )
    for name in template_names:
        text = str(getattr(prompts, name))
        for needle in forbidden:
            assert needle not in text, (name, needle)

    plan = scene_plan_for_family("general_small_goods", {})
    assert "no hands" in plan.lower()
    assert "0-2 large simple companion objects" in plan
