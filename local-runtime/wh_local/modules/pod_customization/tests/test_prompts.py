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


def _signature_line(prompt: str) -> str:
    for line in prompt.splitlines():
        if line.startswith("Style creative signature:"):
            return line
    raise AssertionError("missing style creative signature line")


def test_themed_brief_never_injects_unrelated_generic_motifs() -> None:
    for style_index in range(1, 41):
        prompt = build_style_listing_prompt(
            BASE, style_index=style_index, attempt=1, business_fields=OCEAN_BRIEF
        )
        assert "architectural arches" not in prompt
        assert "bold tropical foliage" not in prompt
        assert "tropical" not in prompt


def test_themed_brief_motif_stays_inside_theme_pool() -> None:
    motifs = _THEME_MOTIFS["ocean"]
    for style_index in range(1, 9):
        prompt = build_style_listing_prompt(
            BASE, style_index=style_index, attempt=1, business_fields=OCEAN_BRIEF
        )
        expected = f"motif={motifs[(style_index - 1) % len(motifs)]}"
        assert expected in _signature_line(prompt)


def test_contract_declares_theme_precedence_and_drops_binding_direction() -> None:
    prompt = build_style_listing_prompt(
        BASE, style_index=8, attempt=1, business_fields=OCEAN_BRIEF
    )
    assert "binding art direction" not in prompt
    assert "highest priority" in prompt
    assert "must never be contradicted" in prompt


def test_empty_brief_falls_back_to_generic_pool() -> None:
    prompt = build_style_listing_prompt(BASE, style_index=8, attempt=1, business_fields={})
    assert "architectural arches and steps" in prompt


def test_unknown_theme_uses_literal_theme_text() -> None:
    prompt = build_style_listing_prompt(
        BASE,
        style_index=3,
        attempt=1,
        business_fields={"design_theme": "赛博朋克", "style_keywords": []},
    )
    assert "the literal '赛博朋克' theme" in prompt


def test_keywords_only_derive_motif_from_keywords() -> None:
    prompt = build_style_listing_prompt(
        BASE,
        style_index=2,
        attempt=1,
        business_fields={"design_theme": "", "style_keywords": ["几何", "复古"]},
    )
    assert "hexagons and honeycomb" in prompt


def test_recipe_is_deterministic_for_retry_safety() -> None:
    first = build_style_listing_prompt(BASE, style_index=7, attempt=1, business_fields=OCEAN_BRIEF)
    second = build_style_listing_prompt(BASE, style_index=7, attempt=1, business_fields=OCEAN_BRIEF)
    assert first == second


def test_free_text_theme_is_detected_when_structured_fields_are_empty() -> None:
    free_text_base = "Creative direction: 海洋主题，绗缝油画风格，颜色鲜艳."
    prompt = build_style_listing_prompt(free_text_base, style_index=1, attempt=1, business_fields={})
    assert f"motif={_THEME_MOTIFS['ocean'][0]}" in _signature_line(prompt)


def test_structured_theme_takes_priority_over_free_text() -> None:
    tropical_base = "Creative direction: tropical palm jungle leaves."
    prompt = build_style_listing_prompt(
        tropical_base, style_index=1, attempt=1, business_fields=OCEAN_BRIEF
    )
    assert f"motif={_THEME_MOTIFS['ocean'][0]}" in _signature_line(prompt)
    assert "monstera" not in prompt


def test_ascii_needles_match_on_word_boundaries() -> None:
    assert not _theme_contains("product category: laundry basket", "cat")
    assert _theme_contains("cat silhouette", "cat")
    assert not _theme_contains("seasonal coast", "sea")
    assert _theme_contains("ocean and sea waves", "sea")
