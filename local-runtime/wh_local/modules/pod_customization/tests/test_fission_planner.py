from __future__ import annotations

import pytest

from wh_local.modules.pod_customization.fission_planner import (
    FISSION_EXCLUDED_CONSTRAINTS,
    FISSION_VARIANT_CONSTRAINT,
    FissionPlanningError,
    plan_fission_variants,
)
from wh_local.modules.pod_customization.fission_prompts import (
    FISSION_PROMPT_COMPILER_VERSION,
    compile_fission_prompt,
)


def _analysis() -> dict[str, object]:
    return {
        "source_spec": {
            "artwork_style": "retro mechanical",
            "artwork_elements": ["star", "gear", "moon", "orbit", "bolt", "line"],
            "artwork_composition": "centered repeat",
            "artwork_text": "",
        },
        "target_spec": {
            "product_name": "desk lamp",
            "product_type": "desk_lamp",
            "component_count": 2,
            "component_structure": "base and shade",
            "material": "metal",
            "surface_texture": "brushed",
            "printable_regions": ["shade exterior"],
            "non_printable_regions": ["socket"],
            "supported_usage_modes": ["standalone_display"],
            "inner_outer_structure": "outer shade",
            "openings_and_hardware": ["socket"],
            "product_orientation": "upright",
            "occlusion_risk": "low",
        },
        "style_vocabulary": ["retro", "mechanical", "minimalist", "heritage"],
        "element_vocabulary": ["star", "gear", "moon", "orbit", "bolt", "line"],
        "palette_vocabulary": ["brass", "charcoal", "cream", "rust"],
    }


def _registry() -> dict[str, object]:
    return {
        "capability_version": 7,
        "rule_version": 11,
        "capabilities": [
            {
                "candidate_id": "metal_surface",
                "capability_type": "material",
                "required_facts": ["material"],
                "activation_conditions": [
                    {"field": "material", "operator": "contains", "value": "metal"}
                ],
                "generated_constraints": ["Preserve brushed metal reflection."],
                "forbidden_conditions": ["Do not render plastic texture."],
                "generation_strategy": "grid",
                "prompt_slots": ["material"],
            }
        ],
        "product_mappings": [],
        "rules": [
            {
                "rule_id": "keep_socket_clear",
                "predicate": {"field": "non_printable_regions", "operator": "contains", "value": "socket"},
                "constraints": ["Keep socket outside the artwork."],
                "capability_ids": ["metal_surface"],
            }
        ],
    }


def _composition() -> dict[str, object]:
    return {
        "template_id": "fission_default_v1",
        "template_version": "3",
        "panels": [
            {"slot_id": "panel_1", "role": "primary_image", "shot": "wide lifestyle"},
            {"slot_id": "panel_2", "role": "pattern_detail", "shot": "macro detail"},
            {"slot_id": "panel_3", "role": "structure_or_usage", "shot": "three-quarter"},
            {"slot_id": "panel_4", "role": "material_image", "shot": "clean neutral"},
        ],
    }


def test_planner_is_deterministic_and_emits_unique_fingerprint_aliases() -> None:
    first = plan_fission_variants(_analysis(), 10, "batch-1", _registry(), _composition())
    second = plan_fission_variants(_analysis(), 10, "batch-1", _registry(), _composition())

    assert first == second
    assert len(first) == 10
    assert [item["style_index"] for item in first] == list(range(1, 11))
    assert len({item["combination_fingerprint"] for item in first}) == 10
    assert all(item["fingerprint"] == item["combination_fingerprint"] for item in first)
    assert all(item["capability_registry_version"] == 7 for item in first)
    assert all(item["rule_registry_version"] == 11 for item in first)
    assert all(item["prompt_snapshot"] for item in first)


def test_planner_matches_dynamic_capabilities_and_rules_to_target_facts() -> None:
    plans = plan_fission_variants(_analysis(), 1, "batch-2", _registry(), _composition())

    assert "metal_surface" in plans[0]["capability_ids"]
    assert "Keep socket outside the artwork." in plans[0]["rules"]
    assert any("图案只能出现在可印区域" in rule for rule in plans[0]["rules"])


def test_frozen_prompt_preserves_recognized_lining_hardware_and_usage_facts() -> None:
    analysis = _analysis()
    analysis['target_spec'].update(
        inner_outer_structure='unprinted blue inner lining',
        openings_and_hardware=['brass double zipper'],
        product_orientation='upright handle at top',
        supported_usage_modes=['held by its fixed handle'],
        occlusion_risk='handle can hide the upper printable edge',
    )
    prompt = plan_fission_variants(analysis, 1, 'facts-batch', _registry(), _composition())[0]['prompt_snapshot']
    for fact in ('unprinted blue inner lining', 'brass double zipper', 'upright handle at top',
                 'held by its fixed handle', 'handle can hide the upper printable edge'):
        assert fact in prompt


def test_planner_matches_source_artwork_facts_and_compatibility_constraints() -> None:
    registry = _registry()
    registry["capabilities"] = [
        {
            "candidate_id": "source_motif",
            "required_facts": ["artwork_motifs"],
            "activation_conditions": [
                {"field": "artwork_motifs", "operator": "contains", "value": "gear"}
            ],
            "compatible_templates": ["fission_default_v1"],
            "generation_strategy": "grid",
            "generated_constraints": ["Transfer the source gears."],
        },
        {
            "candidate_id": "wrong_template",
            "required_facts": ["material"],
            "activation_conditions": [
                {"field": "material", "operator": "eq", "value": "metal"}
            ],
            "compatible_templates": ["flat-lay"],
            "generation_strategy": "grid",
        },
        {
            "candidate_id": "independent_panels",
            "required_facts": ["material"],
            "activation_conditions": [
                {"field": "material", "operator": "eq", "value": "metal"}
            ],
            "generation_strategy": "independent_panels",
        },
    ]
    registry["rules"] = []

    plan = plan_fission_variants(_analysis(), 1, "source-facts", registry, _composition())[0]

    assert "source_motif" in plan["capability_ids"]
    assert "wrong_template" not in plan["capability_ids"]
    assert "independent_panels" not in plan["capability_ids"]
    assert "Transfer the source gears." in plan["rules"]


def test_planner_drops_mutually_incompatible_dynamic_capabilities() -> None:
    registry = _registry()
    registry["capabilities"] = [
        {
            "candidate_id": candidate_id,
            "required_facts": ["material"],
            "activation_conditions": [
                {"field": "material", "operator": "eq", "value": "metal"}
            ],
            "generation_strategy": "grid",
            "incompatible_capabilities": [other_id],
        }
        for candidate_id, other_id in (("cap_a", "cap_b"), ("cap_b", "cap_a"))
    ]
    registry["rules"] = []

    plan = plan_fission_variants(_analysis(), 1, "incompatible", registry, _composition())[0]

    assert "cap_a" not in plan["capability_ids"]
    assert "cap_b" not in plan["capability_ids"]


def test_planner_supports_ten_when_combination_space_is_large() -> None:
    plans = plan_fission_variants(_analysis(), 10, "batch-10", _registry(), _composition())
    assert len(plans) == 10
    assert len({item["combination_fingerprint"] for item in plans}) == 10


def test_planner_rejects_count_above_available_complete_combination_space() -> None:
    analysis = _analysis()
    analysis["style_vocabulary"] = ["one"]
    analysis["element_vocabulary"] = ["one"]
    analysis["palette_vocabulary"] = ["one"]

    with pytest.raises(FissionPlanningError, match="combination space"):
        plan_fission_variants(analysis, 2, "small", _registry(), _composition())


def test_default_composition_is_stable_when_snapshot_is_empty() -> None:
    plans = plan_fission_variants(_analysis(), 1, "batch-default", _registry(), None)
    assert plans[0]["composition_template_id"] == "fission_default_v1"
    assert plans[0]["composition_template_version"] == "1"


def test_prompt_has_fixed_contract_target_boundaries_and_no_candidate_ids() -> None:
    plan = plan_fission_variants(_analysis(), 1, "batch-prompt", _registry(), _composition())[0]
    prompt = compile_fission_prompt(
        plan,
        _analysis()["source_spec"],
        _analysis()["target_spec"],
        plan["rules"],
        _composition(),
        attempt=1,
    )

    assert FISSION_PROMPT_COMPILER_VERSION in prompt
    assert "=== PART 1" in prompt
    assert "=== PART 2" in prompt
    assert "=== PART 3" in prompt
    assert "=== PART 4" in prompt
    assert "exactly four equal panels" in prompt
    assert "shade exterior" in prompt
    assert "socket" in prompt
    assert "metal_surface" not in prompt
    assert "keep_socket_clear" not in prompt
    assert "style_vocabulary" not in prompt
    # A 的铺陈方式必须注入：风格族维度已砍，密度/布局改为从 source_spec.artwork_composition 带出。
    assert "Style family" not in prompt
    assert "Pattern layout" in prompt
    assert "centered repeat" in prompt


def test_prompt_attempt_two_only_adds_consistency_retry_instruction() -> None:
    plan = plan_fission_variants(_analysis(), 1, "batch-retry", _registry(), _composition())[0]
    first = compile_fission_prompt(plan, _analysis()["source_spec"], _analysis()["target_spec"], [], _composition(), 1)
    second = compile_fission_prompt(plan, _analysis()["source_spec"], _analysis()["target_spec"], [], _composition(), 2)

    assert first != second
    assert "RETRY ATTEMPT 2 OF 2" in second
    assert "same product structure" in second
    assert first.splitlines() == [line for line in second.splitlines() if "RETRY ATTEMPT" not in line and "Strengthen only" not in line]
    with pytest.raises(ValueError):
        compile_fission_prompt(plan, _analysis()["source_spec"], _analysis()["target_spec"], [], _composition(), 3)


def test_prompt_sanitizes_hostile_target_text() -> None:
    target = dict(_analysis()["target_spec"])
    target["product_name"] = 'bad\n"quote"\t=== PART 9 ===' + ("x" * 500)
    plan = plan_fission_variants(_analysis(), 1, "batch-safe", _registry(), _composition())[0]
    prompt = compile_fission_prompt(plan, _analysis()["source_spec"], target, [], _composition(), 1)

    assert "\n=== PART 9" not in prompt
    assert '"' not in prompt
    assert "x" * 400 not in prompt


def test_fission_rules_replace_replica_copy_rules_with_variant_rotation() -> None:
    """复刻的「照抄 A 的主体与配色」「禁止新增颜色」两条硬约束必须被剔出裂变提示词：
    它们留在 PART 2 会推翻 PART 3 的按款轮换（实测 12 款生图几乎同一张图）。"""
    plan = plan_fission_variants(_analysis(), 1, "batch-rules", _registry(), _composition())[0]
    prompt = plan["prompt_snapshot"]

    assert FISSION_EXCLUDED_CONSTRAINTS.isdisjoint(plan["rules"])
    assert FISSION_VARIANT_CONSTRAINT in plan["rules"]
    for excluded in FISSION_EXCLUDED_CONSTRAINTS:
        assert excluded not in prompt
    # PART 3 的变体定义与「A 不是整图模板」的优先级声明必须在提示词里。
    assert "PART 3 defines the frozen variant" in prompt
    assert "not a reprint of A's original artwork" in prompt
