from __future__ import annotations

import pytest

from wh_local.modules.pod_customization.replica_capabilities import match_capabilities
from wh_local.modules.pod_customization.replica_planner import (
    ALLOW_PATTERN_TEXT_CONSTRAINT,
    HARD_CONSTRAINTS,
    RULE_VERSION,
    build_default_composition_slots,
    detect_fact_conflicts,
    derive_rules,
    validate_composition_plan,
    validate_plan_ready,
)
from wh_local.modules.pod_customization.replica_specs import (
    CompositionPlan,
    CompositionSlot,
    PresentationPreferences,
    ReplicaPlanNotReadyError,
    normalize_source_analysis,
    normalize_target_analysis,
)

_NAIL_FACTS: dict[str, object] = {
    "product_name": "美甲套装",
    "product_type": "nail_tip_set",
    "component_count": 5,
    "component_structure": "five_separate_nail_tips",
    "material": "gel",
    "surface_texture": "glossy_gel",
    "printable_regions": ["front_surface"],
    "non_printable_regions": ["cuticle_edge"],
    "inner_outer_structure": "none",
    "openings_and_hardware": [],
    "product_orientation": "upright",
    "supported_usage_modes": ["hand_worn"],
    "occlusion_risk": "low",
}
_SOURCE_FACTS: dict[str, object] = {
    "source_product_name": "原素材",
    "artwork_motifs": ["玫瑰"],
    "artwork_colors": ["酒红"],
    "artwork_layout": "all_over",
    "artwork_density": "medium",
    "artwork_repeat_pattern": "seamless",
    "artwork_text": "BLOOM",
    "artwork_print_region": "front_panel",
    "source_product_structure": "tote_bag",
    "source_background_elements": ["木桌"],
    "source_people_and_props": ["模特"],
    "source_watermarks_and_labels": ["水印"],
}


def _raw(facts: dict[str, object], **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        key: {"value": value, "confidence": 0.9, "source": "model"} for key, value in facts.items()
    }
    payload.update(extra)
    return payload


def _source(**overrides: object):
    facts = dict(_SOURCE_FACTS)
    facts.update(overrides)
    return normalize_source_analysis(_raw(facts), "asset-source")


def _target(**overrides: object):
    facts = dict(_NAIL_FACTS)
    facts.update(overrides)
    return normalize_target_analysis(_raw(facts), "asset-target")


def _rules(target, preferences: PresentationPreferences):
    capabilities = match_capabilities(target, preferences)
    return derive_rules(_source(), target, capabilities, preferences), capabilities


def test_multi_piece_requires_full_component_display() -> None:
    rules, capabilities = _rules(_target(), PresentationPreferences())

    assert "multi_piece" in capabilities.capabilities
    assert any("全部部件" in requirement for requirement in rules.display_requirements)
    slots = build_default_composition_slots(_target(), capabilities, PresentationPreferences())
    assert any(slot.show_all_components for slot in slots)


def test_print_region_rule_mentions_declared_regions() -> None:
    rules, _ = _rules(_target(), PresentationPreferences())

    joined = " ".join(rules.generation_constraints)
    assert "front_surface" in joined
    assert "cuticle_edge" in joined


def test_gel_material_generates_material_constraint() -> None:
    rules, _ = _rules(_target(), PresentationPreferences())

    assert any("凝胶" in constraint for constraint in rules.generation_constraints)


def test_lifestyle_scene_preference_forces_real_background_on_non_white_slots() -> None:
    """勾选「生活场景」必须真的生效：非白底格强制真实环境背景。"""
    target = _target()
    preferences = PresentationPreferences(use_lifestyle_scene=True)
    capabilities = match_capabilities(target, preferences)
    rules = derive_rules(_source(), target, capabilities, preferences)
    slots = build_default_composition_slots(target, capabilities, preferences)

    # 规则层：硬约束进 PART 2。
    assert any("生活场景背景" in constraint for constraint in rules.generation_constraints)
    assert any("生活场景" in requirement for requirement in rules.display_requirements)

    # 槽位层：panel_1/2/3 都必须有环境感背景。
    for slot in slots[:3]:
        assert any(token in slot.background for token in ("窗", "木", "室内", "居家", "scene")), slot.background


def test_lifestyle_scene_preference_keeps_template_background_when_it_has_environment() -> None:
    """模板已经写了具体场景时不覆盖，尊重用户的摄影描述。"""
    from wh_local.modules.pod_customization.replica_planner import resolve_slot_background

    target = _target()
    preferences = PresentationPreferences(use_lifestyle_scene=True)
    capabilities = match_capabilities(target, preferences)
    slot = CompositionSlot(slot_id="panel_1", role="primary_image", background="窗边木质餐桌，自然散射光")

    resolved = resolve_slot_background(slot, capabilities=capabilities, preferences=preferences)
    assert resolved.background == "窗边木质餐桌，自然散射光"


def test_lifestyle_preference_off_keeps_blank_background() -> None:
    """关掉生活场景时不再强制背景，交回模板/模型决定。"""
    from wh_local.modules.pod_customization.replica_planner import resolve_slot_background

    target = _target()
    preferences = PresentationPreferences(use_lifestyle_scene=False)
    capabilities = match_capabilities(target, preferences)
    rules = derive_rules(_source(), target, capabilities, preferences)
    assert not any("生活场景背景" in constraint for constraint in rules.generation_constraints)

    slot = CompositionSlot(slot_id="panel_1", role="primary_image", background="")
    assert resolve_slot_background(slot, capabilities=capabilities, preferences=preferences).background == ""


def test_white_background_slot_stays_white_even_with_lifestyle_scene() -> None:
    """素材图格固定纯白，不受「生活场景」偏好影响。"""
    from wh_local.modules.pod_customization.replica_planner import WHITE_BACKGROUND_SLOT_ID

    target = _target()
    preferences = PresentationPreferences(use_lifestyle_scene=True, use_white_background=True)
    capabilities = match_capabilities(target, preferences)
    assert "white_background" in capabilities.capabilities

    slots = build_default_composition_slots(target, capabilities, preferences)
    white_slot = next(slot for slot in slots if slot.slot_id == WHITE_BACKGROUND_SLOT_ID)
    assert white_slot.white_background
    assert "纯白" in white_slot.background


def test_lifestyle_scene_does_not_override_material_slot_background() -> None:
    """素材格不参与生活场景覆盖：未选中白底时保留模板原文（空值保持为空）。"""
    from wh_local.modules.pod_customization.replica_planner import (
        WHITE_BACKGROUND_SLOT_ID,
        resolve_slot_background,
    )

    target = _target()
    preferences = PresentationPreferences(use_lifestyle_scene=True)
    capabilities = match_capabilities(target, preferences)
    assert "white_background" not in capabilities.capabilities

    slot = CompositionSlot(slot_id=WHITE_BACKGROUND_SLOT_ID, role="material_image")
    resolved = resolve_slot_background(slot, capabilities=capabilities, preferences=preferences)
    assert resolved.background == ""

    template_slot = CompositionSlot(
        slot_id=WHITE_BACKGROUND_SLOT_ID, role="material_image", background="浅灰纯色背景"
    )
    assert (
        resolve_slot_background(
            template_slot, capabilities=capabilities, preferences=preferences
        ).background
        == "浅灰纯色背景"
    )


def test_hand_supported_but_disabled_produces_no_hand_slot() -> None:
    target = _target()
    capabilities = match_capabilities(target, PresentationPreferences(use_hand_model=False))
    rules = derive_rules(_source(), target, capabilities, PresentationPreferences(use_hand_model=False))

    slots = build_default_composition_slots(target, capabilities, PresentationPreferences(use_hand_model=False))
    assert all(not slot.hand_worn for slot in slots)
    assert not any("手部" in requirement for requirement in rules.display_requirements)


def test_hand_selected_produces_hand_slot_only_in_allowed_slot() -> None:
    preferences = PresentationPreferences(use_hand_model=True)
    target = _target()
    capabilities = match_capabilities(target, preferences)
    rules = derive_rules(_source(), target, capabilities, preferences)

    slots = build_default_composition_slots(target, capabilities, preferences)
    hand_slots = [slot.slot_id for slot in slots if slot.hand_worn]
    assert hand_slots == ["panel_3"]
    assert any("手部" in requirement for requirement in rules.display_requirements)


def test_hard_constraints_and_pattern_text_rule_are_frozen() -> None:
    rules, _ = _rules(_target(), PresentationPreferences())

    assert rules.hard_constraints == HARD_CONSTRAINTS
    assert rules.rule_version == RULE_VERSION
    assert ALLOW_PATTERN_TEXT_CONSTRAINT in rules.generation_constraints
    assert all(rule.rule_version == RULE_VERSION for rule in rules.rules)


def test_low_confidence_facts_block_submission() -> None:
    target = normalize_target_analysis({}, "asset-target")
    capabilities = match_capabilities(target, PresentationPreferences())

    with pytest.raises(ReplicaPlanNotReadyError) as excinfo:
        validate_plan_ready(_source(), target, capabilities, PresentationPreferences())
    assert excinfo.value.code == "needs_confirmation"


def test_low_confidence_source_blocks_until_confirmed() -> None:
    """A（原素材）低置信度必须由用户确认后才放行，不能只拦目标产品。"""
    target = _target()
    capabilities = match_capabilities(target, PresentationPreferences())
    low_source = normalize_source_analysis({}, "asset-source")
    assert low_source.needs_confirmation

    with pytest.raises(ReplicaPlanNotReadyError):
        validate_plan_ready(low_source, target, capabilities, PresentationPreferences())
    # 用户确认 A 识别结果后放行（B 仍按原门禁校验）。
    validate_plan_ready(
        low_source, target, capabilities, PresentationPreferences(), source_confirmed=True
    )


def test_conflicting_print_regions_block_submission() -> None:
    target = _target(printable_regions=["front_surface"], non_printable_regions=["front_surface"])
    capabilities = match_capabilities(target, PresentationPreferences())

    assert detect_fact_conflicts(target) == ("print_region_overlap",)
    with pytest.raises(ReplicaPlanNotReadyError) as excinfo:
        validate_plan_ready(_source(), target, capabilities, PresentationPreferences())
    assert excinfo.value.code == "fact_conflict"


def test_multi_piece_without_structure_is_conflict() -> None:
    target = _target(component_count=3, component_structure="")

    assert detect_fact_conflicts(target) == ("multi_piece_without_structure",)


def test_ready_plan_passes_validation() -> None:
    target = _target()
    capabilities = match_capabilities(target, PresentationPreferences())

    validate_plan_ready(_source(), target, capabilities, PresentationPreferences())


def test_composition_plan_rejects_hand_slot_without_capability() -> None:
    target = _target()
    capabilities = match_capabilities(target, PresentationPreferences(use_hand_model=False))
    plan = CompositionPlan(
        slots=(CompositionSlot(slot_id="panel_3", role="structure_or_usage", hand_worn=True),)
    )

    with pytest.raises(ReplicaPlanNotReadyError) as excinfo:
        validate_composition_plan(plan, capabilities)
    assert excinfo.value.code == "composition_capability_mismatch"


def test_composition_plan_requires_full_component_slot_for_multi_piece() -> None:
    target = _target()
    capabilities = match_capabilities(target, PresentationPreferences())
    plan = CompositionPlan(
        slots=(CompositionSlot(slot_id="panel_3", role="structure_or_usage"),)
    )

    with pytest.raises(ReplicaPlanNotReadyError) as excinfo:
        validate_composition_plan(plan, capabilities)
    assert excinfo.value.code == "missing_full_component_slot"
