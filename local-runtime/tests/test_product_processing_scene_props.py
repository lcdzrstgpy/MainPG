from __future__ import annotations

from wh_local.modules.product_processing.domain.scene_props import (
    STYLE_PROP_RANGES,
    panel_props,
    prop_limit,
    resolve_props,
    scene_text,
    split_prop_shares,
)


def test_style_prop_ranges_match_frontend_limits() -> None:
    assert STYLE_PROP_RANGES == {"minimal": (1, 1), "lifestyle": (3, 3), "ornate": (4, 6)}
    assert prop_limit("ornate") == 6
    assert prop_limit("unknown") == 6
    assert prop_limit("minimal") == 1


def test_split_prop_shares_puts_single_prop_on_panel_two() -> None:
    assert split_prop_shares("minimal") == [1, 0, 0]
    assert split_prop_shares("lifestyle") == [1, 1, 1]
    assert split_prop_shares("ornate") == [2, 2, 2]


def test_scene_text_falls_back_to_general_for_unknown_category() -> None:
    assert scene_text("home_kitchen")
    assert scene_text("no_such_category") == scene_text("general")


def test_resolve_props_drops_props_that_match_the_subject() -> None:
    props = resolve_props("home_kitchen", title="wooden cutting board", style="ornate")
    assert props
    assert all("cutting board" not in name for name in props)


def test_panel_props_never_repeats_one_prop_across_panels() -> None:
    panels = panel_props("home_kitchen", title="ceramic mug", style="ornate")
    assert len(panels) == 3
    flattened = [name for panel in panels for name in panel]
    assert len(flattened) == len(set(flattened))
