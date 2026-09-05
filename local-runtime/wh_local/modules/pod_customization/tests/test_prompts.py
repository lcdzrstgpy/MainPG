from __future__ import annotations

from wh_local.modules.pod_customization.prompts import (
    _THEME_MOTIFS,
    _theme_contains,
    build_style_listing_prompt,
)

# Deliberately theme-neutral: no motif keyword, so structured vs free-text
# detection is unambiguous.
BASE = "Create one square 2x2 contact sheet. Product name: laundry basket."
OCEAN_BRIEF = {"design_theme": "海洋", "style_keywords": ["绗缝", "油画风格", "颜色鲜艳", "全覆盖"]}
OCEAN_POOL = _THEME_MOTIFS["ocean"]


def _motif_of(prompt: str) -> str:
    line = next(l for l in prompt.splitlines() if l.startswith("Style creative signature:"))
    return line.split("| motif=", 1)[1].split("|", 1)[0].strip()


def test_themed_brief_never_injects_unrelated_generic_motifs() -> None:
    for style_index in range(1, 41):
        prompt = build_style_listing_prompt(
            BASE, style_index=style_index, attempt=1, business_fields=OCEAN_BRIEF
        )
        assert "architectural arches" not in prompt
        assert "bold tropical foliage" not in prompt
        assert "tropical" not in prompt


def test_themed_subject_is_anchored_to_user_theme_and_cycles_pool_variations() -> None:
    for style_index in range(1, 25):
        prompt = build_style_listing_prompt(
            BASE, style_index=style_index, attempt=1, business_fields=OCEAN_BRIEF
        )
        variation = OCEAN_POOL[(style_index - 1) % len(OCEAN_POOL)]
        assert _motif_of(prompt) == f"a variation inspired by '{variation}', within the brief's theme (海洋)"


def test_subject_reuses_across_styles_within_batch() -> None:
    pool_size = len(OCEAN_POOL)
    beyond = build_style_listing_prompt(BASE, style_index=pool_size + 1, attempt=1, business_fields=OCEAN_BRIEF)
    assert f"within the brief's theme (海洋)" in _motif_of(beyond)


def test_style_bias_theme_does_not_use_fixed_style_pool() -> None:
    # "复古/轻复古" is a style/era descriptor; the fixed retro pool (atomic shapes,
    # starbursts, ...) must NOT supply the motif for a compound user theme.
    brief = {"design_theme": "美式轻复古手绘蝴蝶结", "style_keywords": ["手绘水彩小蝴蝶结", "Coquette"]}
    prompt = build_style_listing_prompt(BASE, style_index=1, attempt=1, business_fields=brief)
    assert "a NEW specific subject within the brief's theme (美式轻复古手绘蝴蝶结)" in _motif_of(prompt)
    assert "atomic shapes" not in _motif_of(prompt)
    assert "starbursts" not in _motif_of(prompt)


def test_compound_theme_subject_is_anchored_to_user_theme_not_detected_style() -> None:
    # Detector finds 'retro' (via 复古), but the subject must be the user's own
    # wildflower/cottagecore theme, never the retro pool's subject.
    brief = {"design_theme": "美式复古野花田园风", "style_keywords": ["复古水彩野花", "植物花草"]}
    prompt = build_style_listing_prompt(BASE, style_index=1, attempt=1, business_fields=brief)
    assert "within the brief's theme (美式复古野花田园风)" in _motif_of(prompt)
    assert "a NEW specific subject" in _motif_of(prompt)
    assert "atomic shapes" not in _motif_of(prompt)


def test_subject_theme_still_uses_its_builtin_pool() -> None:
    # Non-style themes keep their deterministic pool.
    prompt = build_style_listing_prompt(BASE, style_index=1, attempt=1, business_fields=OCEAN_BRIEF)
    assert "a variation inspired by" in _motif_of(prompt)
    assert "rolling ocean waves" in _motif_of(prompt)


def test_contract_declares_theme_precedence_and_drops_binding_direction() -> None:
    prompt = build_style_listing_prompt(BASE, style_index=8, attempt=1, business_fields=OCEAN_BRIEF)
    assert "binding art direction" not in prompt
    assert "highest priority" in prompt
    assert "must never be contradicted" in prompt
    assert "only a variation idea" in prompt


def test_empty_brief_falls_back_to_generic_pool() -> None:
    prompt = build_style_listing_prompt(BASE, style_index=8, attempt=1, business_fields={})
    assert "architectural arches and steps" in prompt


def test_unknown_theme_instructs_invent_subject() -> None:
    prompt = build_style_listing_prompt(
        BASE, style_index=3, attempt=1, business_fields={"design_theme": "赛博朋克", "style_keywords": []}
    )
    assert "the brief's theme (赛博朋克)" in prompt
    assert "a NEW specific subject" in prompt


def test_keywords_only_drive_curated_theme_detection() -> None:
    prompt = build_style_listing_prompt(
        BASE, style_index=1, attempt=1, business_fields={"design_theme": "", "style_keywords": ["几何", "复古"]}
    )
    # "几何" resolves the geometric theme; subject anchors to it.
    assert f"within the brief's theme (geometric)" in _motif_of(prompt)


def test_non_curated_keywords_instruct_invent() -> None:
    prompt = build_style_listing_prompt(
        BASE, style_index=1, attempt=1, business_fields={"design_theme": "", "style_keywords": ["咖啡豆", "猫咪"]}
    )
    assert "a NEW specific subject derived from the style keywords (咖啡豆, 猫咪)" in prompt


def test_recipe_is_deterministic_for_retry_safety() -> None:
    first = build_style_listing_prompt(BASE, style_index=7, attempt=1, business_fields=OCEAN_BRIEF)
    second = build_style_listing_prompt(BASE, style_index=7, attempt=1, business_fields=OCEAN_BRIEF)
    assert first == second


def test_free_text_theme_is_detected_when_structured_fields_are_empty() -> None:
    prompt = build_style_listing_prompt(
        BASE, style_index=1, attempt=1, business_fields={}, creative_prompt="海洋主题，绗缝油画风格，颜色鲜艳"
    )
    assert "within the brief's theme (ocean)" in _motif_of(prompt)


def test_structured_theme_takes_priority_over_free_text() -> None:
    prompt = build_style_listing_prompt(
        BASE, style_index=1, attempt=1, business_fields=OCEAN_BRIEF, creative_prompt="tropical palm jungle leaves"
    )
    assert "within the brief's theme (海洋)" in _motif_of(prompt)
    assert "monstera" not in prompt


def test_ascii_needles_match_on_word_boundaries() -> None:
    assert not _theme_contains("product category: laundry basket", "cat")
    assert _theme_contains("cat silhouette", "cat")
    assert not _theme_contains("seasonal coast", "sea")
    assert _theme_contains("ocean and sea waves", "sea")


def test_holiday_theme_beats_broad_subject_keyword() -> None:
    brief = {"design_theme": "美式复古圣诞节", "style_keywords": ["圣诞植物手绘", "美式复古红绿格纹"]}
    prompt = build_style_listing_prompt(BASE, style_index=1, attempt=1, business_fields=brief)
    assert "snowflakes and winter motifs" in prompt
    assert "eucalyptus" not in prompt
    assert "olive branches" not in prompt


def test_design_theme_alone_is_dominant_over_keywords() -> None:
    brief = {"design_theme": "海洋", "style_keywords": ["圣诞树", "植物"]}
    prompt = build_style_listing_prompt(BASE, style_index=1, attempt=1, business_fields=brief)
    assert "within the brief's theme (海洋)" in _motif_of(prompt)


def test_palette_defers_to_user_color_preferences() -> None:
    brief = {"design_theme": "美式复古圣诞节", "style_keywords": [], "color_preferences": ["圣诞正红", "松针墨绿"]}
    prompt = build_style_listing_prompt(BASE, style_index=1, attempt=1, business_fields=brief)
    assert "the brief's specified colors (圣诞正红, 松针墨绿)" in prompt


def test_palette_uses_pool_when_no_colors_specified() -> None:
    brief = {"design_theme": "美式复古圣诞节", "style_keywords": [], "color_preferences": []}
    prompt = build_style_listing_prompt(BASE, style_index=1, attempt=1, business_fields=brief)
    assert "the brief's specified colors" not in prompt
    assert "palette=two-color high contrast" in prompt


def test_novel_theme_with_declared_design_theme_never_degrades_to_generic() -> None:
    brief = {"design_theme": "哈利波特魔法学院", "style_keywords": []}
    prompt = build_style_listing_prompt(BASE, style_index=1, attempt=1, business_fields=brief)
    assert "the brief's theme (哈利波特魔法学院)" in prompt
    assert "a NEW specific subject" in prompt
    assert "architectural arches" not in prompt
    assert "bold tropical foliage" not in prompt


def test_novel_theme_only_in_creative_prompt_is_respected() -> None:
    prompt = build_style_listing_prompt(
        BASE, style_index=1, attempt=1, business_fields={}, creative_prompt="赛博朋克霓虹城市"
    )
    assert "a NEW specific subject from the creative direction" in prompt
    assert "architectural arches" not in prompt


def test_learned_pool_used_as_variation_not_subject() -> None:
    # A Doubao-learned pool supplies a within-theme variation; the subject stays
    # the user's own theme label.
    learned = {"喵星人咖啡": ["sleeping cat", "cat with coffee cup", "kitten paw prints"]}
    prompt = build_style_listing_prompt(
        BASE,
        style_index=2,
        attempt=1,
        business_fields={"design_theme": "喵星人咖啡", "style_keywords": []},
        theme_pools=learned,
    )
    assert _motif_of(prompt) == (
        "a variation inspired by 'cat with coffee cup', within the brief's theme (喵星人咖啡)"
    )


def test_learned_pool_falls_back_to_invent_when_theme_missing() -> None:
    prompt = build_style_listing_prompt(
        BASE,
        style_index=1,
        attempt=1,
        business_fields={"design_theme": "软萌兔兔", "style_keywords": []},
        theme_pools={"喵星人咖啡": ["sleeping cat"]},
    )
    assert "a NEW specific subject within the brief's theme (软萌兔兔)" in prompt
