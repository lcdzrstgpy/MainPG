from __future__ import annotations

from wh_local.modules.pod_customization.replica_capabilities import (
    CAPABILITY_IDS,
    CAPABILITY_REGISTRY,
    CAPABILITY_REGISTRY_VERSION,
    capability_definition,
    match_capabilities,
)
from wh_local.modules.pod_customization.replica_specs import (
    PresentationPreferences,
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
    "non_printable_regions": [],
    "inner_outer_structure": "none",
    "openings_and_hardware": [],
    "product_orientation": "upright",
    "supported_usage_modes": ["hand_worn"],
    "occlusion_risk": "low",
}


_TOP_LEVEL_KEYS = frozenset(
    {"suggested_capabilities", "alternative_hypotheses", "analysis_model_version", "product_confidence"}
)


def _raw(facts: dict[str, object], **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        key: {"value": value, "confidence": 0.9, "source": "model"}
        for key, value in facts.items()
        if key not in _TOP_LEVEL_KEYS
    }
    for key, value in facts.items():
        if key in _TOP_LEVEL_KEYS:
            payload[key] = value
    payload.update(extra)
    return payload


def _spec(**overrides: object):
    facts = dict(_NAIL_FACTS)
    facts.update(overrides)
    return normalize_target_analysis(_raw(facts), "asset-target")


def test_registry_covers_all_initial_capabilities() -> None:
    assert set(CAPABILITY_REGISTRY) == set(CAPABILITY_IDS)
    for capability_id, definition in CAPABILITY_REGISTRY.items():
        assert definition.capability_id == capability_id
        assert definition.version == CAPABILITY_REGISTRY_VERSION
        assert definition.capability_type
        assert set(definition.incompatible_capabilities) <= set(CAPABILITY_IDS)
        assert capability_definition(capability_id) is definition


def test_nail_defaults_to_multi_piece_gel_and_macro_without_hand_worn() -> None:
    capabilities = match_capabilities(_spec(), PresentationPreferences())

    assert "multi_piece" in capabilities.capabilities
    assert "gel_material" in capabilities.capabilities
    assert "macro_detail" in capabilities.capabilities
    # 能力标签 ≠ 必拍画面：未选择手部展示就不进入能力集。
    assert "hand_worn" not in capabilities.capabilities
    assert "standalone_display" not in capabilities.capabilities
    assert capabilities.registry_version == CAPABILITY_REGISTRY_VERSION


def test_hand_worn_requires_both_support_and_user_selection() -> None:
    supported = _spec()
    unsupported = _spec(supported_usage_modes=[])

    with_pref = match_capabilities(supported, PresentationPreferences(use_hand_model=True))
    assert "hand_worn" in with_pref.capabilities

    without_pref = match_capabilities(supported, PresentationPreferences(use_hand_model=False))
    assert "hand_worn" not in without_pref.capabilities

    # 产品不支持手部使用时，即使用户开启也不给手部能力。
    unsupported_with_pref = match_capabilities(unsupported, PresentationPreferences(use_hand_model=True))
    assert "hand_worn" not in unsupported_with_pref.capabilities


def test_unknown_suggestion_labels_go_to_unknown_capability() -> None:
    spec = _spec(suggested_capabilities=["hand_painted"])
    capabilities = match_capabilities(spec, PresentationPreferences())

    assert capabilities.unknown_capability == ("hand_painted",)
    assert "hand_painted" not in capabilities.capabilities


def test_mappable_suggestion_not_backed_by_facts_is_dropped() -> None:
    # component_count=1 时，模型建议 multi_part 也不能凭空产生 multi_piece。
    spec = _spec(component_count=1, component_structure="", suggested_capabilities=["multi_part"])
    capabilities = match_capabilities(spec, PresentationPreferences())

    assert "multi_piece" not in capabilities.capabilities
    assert capabilities.unknown_capability == ()


def test_material_and_print_capabilities_follow_facts() -> None:
    spec = _spec(
        material="canvas",
        printable_regions=["front_panel"],
        component_count=1,
        component_structure="",
        supported_usage_modes=["standalone"],
    )
    capabilities = match_capabilities(spec, PresentationPreferences())

    assert "fabric_material" in capabilities.capabilities
    assert "local_print" in capabilities.capabilities
    assert "gel_material" not in capabilities.capabilities
    assert "standalone_display" in capabilities.capabilities


def test_incompatible_capabilities_resolved_by_priority() -> None:
    # 墙面展示与人体穿着互斥：优先级更高者保留。
    spec = _spec(supported_usage_modes=["body_worn", "wall_mounted"])
    capabilities = match_capabilities(
        spec, PresentationPreferences(use_body_model=True, use_wall_display=True)
    )

    assert "body_worn" in capabilities.capabilities
    assert "wall_mounted" not in capabilities.capabilities


def test_white_background_requires_preference() -> None:
    spec = _spec()
    assert "white_background" not in match_capabilities(spec, PresentationPreferences()).capabilities
    enabled = match_capabilities(spec, PresentationPreferences(use_white_background=True))
    assert "white_background" in enabled.capabilities
