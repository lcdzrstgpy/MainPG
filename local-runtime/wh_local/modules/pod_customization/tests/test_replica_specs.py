from __future__ import annotations

import pytest
from pydantic import ValidationError

from wh_local.modules.pod_customization.replica_specs import (
    REPLICA_ANALYSIS_SCHEMA_VERSION,
    FactOverrides,
    FactValue,
    normalize_source_analysis,
    normalize_target_analysis,
)

_TARGET_FACTS: dict[str, object] = {
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
_SOURCE_FACTS: dict[str, object] = {
    "source_product_name": "原素材帆布袋",
    "artwork_motifs": ["玫瑰", "藤蔓"],
    "artwork_colors": ["酒红", "墨绿"],
    "artwork_layout": "all_over_repeat",
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


def test_fact_value_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValidationError):
        FactValue(value="gel", confidence=1.5)


def test_fact_value_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        FactValue(value="gel", confidence=0.9, source="guess")  # type: ignore[arg-type]


def test_fact_value_confidence_is_confident_threshold() -> None:
    assert FactValue(value="x", confidence=0.9).is_confident()
    assert not FactValue(value="x", confidence=0.2).is_confident()


def test_missing_target_fields_default_to_system_and_need_confirmation() -> None:
    spec = normalize_target_analysis({}, "asset-target")

    assert spec.target_asset_id == "asset-target"
    assert spec.product_type.value == ""
    assert spec.product_type.source == "system"
    assert spec.product_type.confidence == 0.0
    assert spec.needs_confirmation is True
    assert "product_type" in spec.low_confidence_fields()
    assert spec.analysis_schema_version == REPLICA_ANALYSIS_SCHEMA_VERSION


def test_complete_target_analysis_does_not_need_confirmation() -> None:
    spec = normalize_target_analysis(_raw(_TARGET_FACTS), "asset-target")

    assert spec.needs_confirmation is False
    assert spec.component_count.value == 5
    assert spec.supported_usage_modes.value == ["hand_worn"]
    assert spec.material.source == "model"


def test_source_analysis_keeps_pattern_text_and_excluded_environment() -> None:
    spec = normalize_source_analysis(_raw(_SOURCE_FACTS), "asset-source")

    assert spec.source_asset_id == "asset-source"
    assert spec.artwork_text.value == "BLOOM"
    assert spec.artwork_motifs.value == ["玫瑰", "藤蔓"]
    # 背景、人物、道具、水印分字段保存，供硬约束引用。
    assert spec.source_background_elements.value == ["木桌"]
    assert spec.source_people_and_props.value == ["模特"]
    assert spec.source_watermarks_and_labels.value == ["水印"]
    assert spec.needs_confirmation is False


def test_out_of_range_confidence_in_raw_payload_raises() -> None:
    raw = _raw(_TARGET_FACTS)
    raw["material"] = {"value": "gel", "confidence": 2.0, "source": "model"}
    with pytest.raises(ValidationError):
        normalize_target_analysis(raw, "asset-target")


def test_fact_overrides_mark_source_user_with_full_confidence() -> None:
    spec = normalize_target_analysis(
        _raw(_TARGET_FACTS), "asset-target", FactOverrides(material="paper", component_count=1)
    )

    assert spec.material.value == "paper"
    assert spec.material.source == "user"
    assert spec.material.confidence == 1.0
    assert spec.component_count.value == 1
    assert spec.product_name.source == "model"


def test_overrides_can_rescue_missing_fact_from_needs_confirmation() -> None:
    spec = normalize_target_analysis({}, "asset-target", FactOverrides(product_type="nail_tip_set"))

    assert spec.product_type.value == "nail_tip_set"
    assert spec.product_type.source == "user"
