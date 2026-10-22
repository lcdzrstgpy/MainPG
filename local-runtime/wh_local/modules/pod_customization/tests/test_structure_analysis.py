from __future__ import annotations

import json

import pytest

from wh_local.modules.pod_customization.structure_analysis import (
    load_structure_facts,
    needs_confirmation,
    parse_structure_response,
    render_part_count_lock,
    render_reference_views_block,
    render_structure_facts_block,
    render_transparent_material_lock,
)


def _payload(**overrides):
    base = {
        "product_type": "acrylic tiered display stand",
        "part_count": 4,
        "tiers": 4,
        "units_per_tier": 14,
        "height_mm": 31,
        "thickness_mm": 10.2,
        "material": "clear acrylic",
        "transparency": "high",
        "surface_finish": "glossy",
        "symmetry": "mirror",
        "connections": "stepped slots",
        "printable_regions": ["front plate surface"],
        "confidence": {
            "product_type": 0.9,
            "part_count": 0.9,
            "tiers": 0.95,
            "units_per_tier": 0.85,
            "height_mm": 0.9,
            "thickness_mm": 0.8,
            "material": 0.9,
            "transparency": 0.95,
            "surface_finish": 0.9,
            "symmetry": 0.9,
            "connections": 0.85,
        },
    }
    base.update(overrides)
    return base


def test_parse_keeps_confident_facts_and_drops_guesses() -> None:
    payload = _payload(
        material="clear acrylic",
        confidence={
            **_payload()["confidence"],
            "material": 0.2,
            "units_per_tier": 0.3,
        },
    )
    facts = parse_structure_response(json.dumps(payload))

    assert facts["tiers"] == 4
    assert facts["thickness_mm"] == 10.2
    # 低置信度字段直接丢弃：宁可不写，也不把猜的数字写成硬约束。
    assert "material" not in facts
    assert "units_per_tier" not in facts


def test_parse_rejects_unknown_transparency_and_empty_results() -> None:
    with pytest.raises(ValueError):
        parse_structure_response("not json")
    with pytest.raises(ValueError):
        parse_structure_response(
            json.dumps(
                _payload(
                    transparency="unknown",
                    confidence={key: 0.1 for key in _payload()["confidence"]},
                    product_type="",
                    printable_regions=[],
                )
            )
        )


def test_needs_confirmation_flags_medium_confidence() -> None:
    assert needs_confirmation(_payload()) is False
    medium = _payload()
    medium["confidence"] = {**medium["confidence"], "tiers": 0.6}
    assert needs_confirmation(medium) is True
    assert needs_confirmation(None) is False


def test_renderers_only_emit_blocks_when_facts_exist() -> None:
    facts = _payload()
    facts_block = render_structure_facts_block(facts)
    lock_block = render_part_count_lock(facts)
    transparent_block = render_transparent_material_lock(facts)

    assert "STRUCTURE FACTS" in facts_block
    assert "exactly 4" in lock_block and "14 repeated units" in lock_block
    assert "TRANSPARENT MATERIAL LOCK" in transparent_block
    # 普通布艺产品没有事实表 / 不是透明材质 → 不渲染任何新增块。
    assert render_structure_facts_block(None) == ""
    assert render_part_count_lock(None) == ""
    assert render_transparent_material_lock({"material": "canvas", "transparency": "none"}) == ""


def test_reference_views_block_requires_multiple_views() -> None:
    assert render_reference_views_block(("primary",)) == ""
    block = render_reference_views_block(("primary", "side", "top"))
    assert "REFERENCE VIEWS CONTRACT" in block
    assert "Image 1 = PRIMARY" in block
    assert "Image 3 = TOP" in block


def test_load_structure_facts_degrades_safely() -> None:
    raw = json.dumps({"tiers": 4})
    assert load_structure_facts(raw) == {"tiers": 4}
    assert load_structure_facts("") is None
    assert load_structure_facts("{bad") is None
    assert load_structure_facts(None) is None
