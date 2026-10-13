"""复刻识图与构图参数 runtime 的契约与运行测试（假 session，无真实付费调用）。"""

from __future__ import annotations

import json

import pytest

from wh_local.modules.pod_customization.billing_contract import (
    PodBillingAuthorizationRequired,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.replica_analysis_runtime import (
    ANALYSIS_MAX_ATTEMPTS,
    ANALYSIS_PROMPT_VERSION,
    CompositionTemplateOptions,
    PodReplicaAnalysisRuntime,
    ReplicaAssetImage,
    ReplicaCompositionRequest,
    _SOURCE_FIELDS,
    _TARGET_FIELDS,
)
from wh_local.modules.pod_customization.replica_capabilities import match_capabilities
from wh_local.modules.pod_customization.replica_planner import derive_rules
from wh_local.modules.pod_customization.replica_specs import (
    PresentationPreferences,
    normalize_source_analysis,
    normalize_target_analysis,
)
from wh_local.modules.product_processing.doubao_ark import DoubaoArkError

_SOURCE_VALUES: dict[str, object] = {
    "source_product_name": "原素材帆布袋",
    "artwork_motifs": ["玫瑰", "藤蔓"],
    "artwork_colors": ["酒红"],
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
_TARGET_VALUES: dict[str, object] = {
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


def _section(values: dict[str, object], fields: tuple[str, ...], confidence: float = 0.9) -> dict[str, object]:
    section: dict[str, object] = {name: values[name] for name in fields}
    section["confidence"] = {name: confidence for name in fields}
    return section


def _analysis_payload() -> dict[str, object]:
    return {
        "source": _section(_SOURCE_VALUES, _SOURCE_FIELDS),
        "target": _section(_TARGET_VALUES, _TARGET_FIELDS),
    }


class _Response:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        body = json.dumps({"choices": [{"message": {"content": content}}]})
        self.content = body.encode("utf-8")
        self.status_code = status_code

    def close(self) -> None:
        return None


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def post(self, url: str, *, headers, json, timeout, allow_redirects):  # noqa: A002 - 与 requests 签名一致
        self.requests.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)


def _grant() -> PodExecutionGrant:
    return PodExecutionGrant("freeze-replica", 1, "2099-01-01T00:00:00Z", {"ark": "ark-key"})


def _analysis_call_ids(analysis_id: str) -> tuple[str, ...]:
    return tuple(f"{analysis_id}:analysis:{attempt}" for attempt in range(1, ANALYSIS_MAX_ATTEMPTS + 1))


def _asset(asset_id: str, marker: str) -> ReplicaAssetImage:
    return ReplicaAssetImage(asset_id=asset_id, data_url=f"data:image/png;base64,{marker}")


def _runtime(responses: list[_Response]) -> tuple[PodReplicaAnalysisRuntime, _Session]:
    session = _Session(responses)
    runtime = PodReplicaAnalysisRuntime(session=session, sleeper=lambda _seconds: None)
    return runtime, session


def test_analyze_assets_sends_source_then_target_and_normalizes() -> None:
    runtime, session = _runtime([_Response(_analysis_payload())])
    outcomes: list[tuple[str, str]] = []

    result = runtime.analyze_assets(
        _asset("asset-a", "AAAA"),
        _asset("asset-b", "BBBB"),
        grant=_grant(),
        call_id="a1:analysis:1",
        call_ids=_analysis_call_ids("a1"),
        on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
    )

    assert result.source_artwork_spec.source_asset_id == "asset-a"
    assert result.target_product_spec.target_asset_id == "asset-b"
    assert result.prompt_version == ANALYSIS_PROMPT_VERSION
    assert result.attempt_count == 1
    assert outcomes == [("a1:analysis:1", "success")]

    request = session.requests[0]
    assert request["json"]["thinking"] == {"type": "disabled"}
    assert request["json"]["response_format"]["json_schema"]["name"] == "pod_replica_analysis"
    content = request["json"]["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[0]["image_url"]["url"].endswith("AAAA")
    assert content[1]["image_url"]["url"].endswith("BBBB")
    assert "target_contract" in str(content[2]["text"])


def test_analyze_assets_repairs_missing_field_then_succeeds() -> None:
    broken = _analysis_payload()
    broken["target"] = _section(_TARGET_VALUES, _TARGET_FIELDS)
    del broken["target"]["material"]
    runtime, session = _runtime([_Response(broken), _Response(_analysis_payload())])
    outcomes: list[tuple[str, str]] = []

    result = runtime.analyze_assets(
        _asset("asset-a", "AAAA"),
        _asset("asset-b", "BBBB"),
        grant=_grant(),
        call_id="a2:analysis:1",
        call_ids=_analysis_call_ids("a2"),
        on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
    )

    assert result.attempt_count == 2
    assert len(session.requests) == 2
    assert outcomes == [("a2:analysis:1", "success"), ("a2:analysis:2", "success")]


def test_analyze_assets_rejects_out_of_range_confidence_after_budget() -> None:
    payload = _analysis_payload()
    payload["target"] = _section(_TARGET_VALUES, _TARGET_FIELDS, confidence=2.0)
    runtime, session = _runtime([_Response(payload) for _ in range(ANALYSIS_MAX_ATTEMPTS)])

    with pytest.raises(DoubaoArkError):
        runtime.analyze_assets(
            _asset("asset-a", "AAAA"),
            _asset("asset-b", "BBBB"),
            grant=_grant(),
            call_id="a3:analysis:1",
            call_ids=_analysis_call_ids("a3"),
        )

    assert len(session.requests) == ANALYSIS_MAX_ATTEMPTS


def test_analyze_assets_bounds_overlong_scalars_and_lists() -> None:
    payload = _analysis_payload()
    source = _section(_SOURCE_VALUES, _SOURCE_FIELDS)
    source["artwork_layout"] = "x" * 900
    source["artwork_motifs"] = [f"motif-{index}" for index in range(60)]
    payload["source"] = source
    runtime, _session = _runtime([_Response(payload)])

    result = runtime.analyze_assets(
        _asset("asset-a", "AAAA"),
        _asset("asset-b", "BBBB"),
        grant=_grant(),
        call_id="a4:analysis:1",
        call_ids=_analysis_call_ids("a4"),
    )

    assert len(result.source_artwork_spec.artwork_layout.value) == 500
    assert len(result.source_artwork_spec.artwork_motifs.value) == 50


def test_analyze_assets_requires_ark_grant_and_image_data_urls() -> None:
    runtime, _session = _runtime([])
    with pytest.raises(PodBillingAuthorizationRequired):
        runtime.analyze_assets(
            _asset("asset-a", "AAAA"),
            _asset("asset-b", "BBBB"),
            grant=PodExecutionGrant("freeze", 1, "2099-01-01T00:00:00Z", {}),
            call_id="a5:analysis:1",
        )

    runtime2, _session2 = _runtime([])
    with pytest.raises(DoubaoArkError):
        runtime2.analyze_assets(
            ReplicaAssetImage(asset_id="asset-a", data_url="https://example/a.png"),
            _asset("asset-b", "BBBB"),
            grant=_grant(),
            call_id="a6:analysis:1",
        )


# --- 构图参数 -------------------------------------------------------------------


def _template() -> CompositionTemplateOptions:
    return CompositionTemplateOptions(
        template_id="ecommerce_standard_v1",
        template_version="1",
        panel_slots=("panel_1", "panel_2", "panel_3", "panel_4"),
        required_slots=("panel_1", "panel_4"),
        camera_options=("eye_level", "top_down"),
        lighting_options=("soft_diffused",),
        background_options=("clean_neutral",),
        composition_options=("centered",),
    )


def _composition_request(preferences: PresentationPreferences | None = None) -> ReplicaCompositionRequest:
    prefs = preferences or PresentationPreferences()
    source = normalize_source_analysis(_SOURCE_VALUES, "asset-a")
    target = normalize_target_analysis(_TARGET_VALUES, "asset-b")
    capabilities = match_capabilities(target, prefs)
    rules = derive_rules(source, target, capabilities, prefs)
    return ReplicaCompositionRequest(
        template=_template(),
        source_artwork_spec=source,
        target_product_spec=target,
        capability_set=capabilities,
        rule_set=rules,
        preferences=prefs,
    )


def _composition_payload(
    template: CompositionTemplateOptions,
    *,
    slots: tuple[str, ...] | None = None,
    camera: str = "eye_level",
    lighting: str = "soft_diffused",
    background: str = "clean_neutral",
) -> dict[str, object]:
    chosen = slots if slots is not None else template.panel_slots
    panels = [
        {
            "slot_id": slot_id,
            "role": f"role_{slot_id}",
            "camera": camera,
            "lighting": lighting,
            "background": background,
            "composition_notes": "notes",
        }
        for slot_id in chosen
    ]
    return {
        "template_id": template.template_id,
        "template_version": template.template_version,
        "panels": panels,
        "camera": camera,
        "lighting": lighting,
        "background": background,
        "composition_notes": "overall",
    }


def test_generate_composition_plan_merges_template_params_into_slots() -> None:
    request = _composition_request()
    runtime, session = _runtime([_Response(_composition_payload(request.template))])

    plan = runtime.generate_composition_plan(
        request,
        grant=_grant(),
        call_id="c1:composition:1",
    )

    assert plan.template_id == request.template.template_id
    assert plan.template_version == request.template.template_version
    assert tuple(slot.slot_id for slot in plan.slots) == request.template.panel_slots
    assert plan.slots[0].camera == "eye_level"
    assert plan.slots[0].shot == "notes"
    assert session.requests[0]["json"]["response_format"]["json_schema"]["name"] == "pod_replica_composition"


def test_generate_composition_plan_has_no_hand_slot_when_preference_is_off() -> None:
    request = _composition_request(PresentationPreferences(use_hand_model=False))
    runtime, _session = _runtime([_Response(_composition_payload(request.template))])

    plan = runtime.generate_composition_plan(request, grant=_grant(), call_id="c2:composition:1")

    assert all(not slot.hand_worn for slot in plan.slots)
    assert "hand_worn" not in request.capability_set.capabilities


def test_generate_composition_plan_adds_hand_slot_only_when_selected() -> None:
    request = _composition_request(PresentationPreferences(use_hand_model=True))
    assert "hand_worn" in request.capability_set.capabilities
    runtime, _session = _runtime([_Response(_composition_payload(request.template))])

    plan = runtime.generate_composition_plan(request, grant=_grant(), call_id="c3:composition:1")

    hand_slots = [slot.slot_id for slot in plan.slots if slot.hand_worn]
    assert hand_slots == ["panel_3"]


def test_generate_composition_plan_rejects_unknown_slot_and_out_of_range_camera() -> None:
    template = _template()
    request = _composition_request()

    bad_slot = _composition_payload(template, slots=("panel_9", "panel_2", "panel_3", "panel_4"))
    runtime, session = _runtime([_Response(bad_slot) for _ in range(3)])
    with pytest.raises(DoubaoArkError):
        runtime.generate_composition_plan(request, grant=_grant(), call_id="c4:composition:1")
    assert len(session.requests) == 3

    bad_camera = _composition_payload(template, camera="underwater_macro")
    runtime2, _session2 = _runtime([_Response(bad_camera) for _ in range(3)])
    with pytest.raises(DoubaoArkError):
        runtime2.generate_composition_plan(request, grant=_grant(), call_id="c5:composition:1")


def test_generate_composition_plan_rejects_missing_required_slot_and_template_mismatch() -> None:
    template = _template()
    request = _composition_request()

    missing = _composition_payload(template, slots=("panel_2", "panel_3"))
    runtime, _session = _runtime([_Response(missing) for _ in range(3)])
    with pytest.raises(DoubaoArkError):
        runtime.generate_composition_plan(request, grant=_grant(), call_id="c6:composition:1")

    mismatch = _composition_payload(template)
    mismatch["template_id"] = "some_other_template"
    runtime2, _session2 = _runtime([_Response(mismatch) for _ in range(3)])
    with pytest.raises(DoubaoArkError):
        runtime2.generate_composition_plan(request, grant=_grant(), call_id="c7:composition:1")
