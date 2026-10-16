from __future__ import annotations

import json

import pytest

from wh_local.modules.pod_customization.billing_contract import (
    PodBillingAuthorizationRequired,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.fission_runtime import (
    FISSION_ANALYSIS_MAX_ATTEMPTS,
    FISSION_ANALYSIS_SCHEMA_VERSION,
    FissionAnalysisRequest,
    FissionAssetImage,
    PodFissionAnalysisRuntime,
)
from wh_local.modules.product_processing.doubao_ark import DoubaoArkError


def _payload() -> dict[str, object]:
    return {
        "source_spec": {
            "artwork_style": "retro mechanical",
            "artwork_elements": ["star", "gear"],
            "artwork_composition": "centered repeat",
            "artwork_text": "",
            "source_selling_points": ["bold contrast"],
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
            "inner_outer_structure": "hollow shade over solid base",
            "openings_and_hardware": ["socket opening"],
            "product_orientation": "upright",
            "occlusion_risk": "shade rim may occlude print",
            "manufacturing_process": "brushed metal finish",
            "selling_points": ["brushed metal", "compact footprint"],
        },
        "user_summary": {
            "source_summary": "Retro mechanical star artwork.",
            "target_summary": "A two piece brushed metal desk lamp.",
        },
        "style_vocabulary": ["retro", "mechanical"],
        "element_vocabulary": ["star", "gear"],
        "palette_vocabulary": ["brass", "charcoal"],
        "capability_candidates": [{
            "candidate_id": "metal_surface",
            "capability_type": "material",
            "required_facts": ["material"],
            "optional_facts": [],
            "activation_conditions": [{"field": "material", "operator": "contains", "value": "metal"}],
            "generated_constraints": ["Preserve metal reflections."],
            "forbidden_conditions": [],
            "compatible_templates": [],
            "incompatible_capabilities": [],
            "generation_strategy": "grid",
            "prompt_slots": ["material"],
        }],
        "product_mapping_candidates": [{"product_type": "desk_lamp", "capability_ids": ["metal_surface"]}],
        "rule_candidates": [{
            "rule_id": "preserve_socket",
            "predicate": {"field": "non_printable_regions", "operator": "contains", "value": "socket"},
            "constraints": ["Keep the socket clear."],
            "capability_ids": ["metal_surface"],
        }],
    }


class _Response:
    def __init__(self, payload: object) -> None:
        content = payload if isinstance(payload, str) else json.dumps(payload)
        self.content = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.status_code = 200

    def close(self) -> None:
        pass


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def post(self, url, *, headers, json, timeout, allow_redirects):  # noqa: A002
        self.requests.append({"url": url, "headers": headers, "json": json})
        return self.responses.pop(0)


def _grant() -> PodExecutionGrant:
    return PodExecutionGrant("freeze-1", 1, "2099-01-01T00:00:00Z", {"ark": "ark-key"})


def _request() -> FissionAnalysisRequest:
    return FissionAnalysisRequest(source_asset_id="source-a", target_asset_id="target-b")


def _image(asset_id: str, marker: str) -> FissionAssetImage:
    return FissionAssetImage(asset_id, f"data:image/png;base64,{marker}")


def _runtime(responses: list[_Response]) -> tuple[PodFissionAnalysisRuntime, _Session]:
    session = _Session(responses)
    return PodFissionAnalysisRuntime(session=session, sleeper=lambda _seconds: None), session


def test_analysis_posts_once_with_a_then_b_and_returns_normalized_candidates() -> None:
    runtime, session = _runtime([_Response(_payload())])
    outcomes: list[tuple[str, str]] = []

    result = runtime.analyze_assets(
        _request(),
        _image("source-a", "AAAA"),
        _image("target-b", "BBBB"),
        grant=_grant(),
        call_ids=("analysis-1:analysis:1", "analysis-1:analysis:2"),
        on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
    )

    assert result.source_spec["artwork_style"] == "retro mechanical"
    assert result.target_spec["product_type"] == "desk_lamp"
    assert result.style_vocabulary == ("retro", "mechanical")
    assert result.capability_candidates[0]["candidate_id"] == "metal_surface"
    assert result.product_mapping_candidates[0]["product_type"] == "desk_lamp"
    assert result.raw_response["target_spec"]["material"] == "metal"
    assert result.model_version
    assert result.schema_version == FISSION_ANALYSIS_SCHEMA_VERSION
    assert len(session.requests) == 1
    assert outcomes == [
        ("analysis-1:analysis:1", "success"),
        ("analysis-1:analysis:2", "no_return"),
    ]
    content = session.requests[0]["json"]["messages"][1]["content"]
    assert content[0]["image_url"]["url"].endswith("AAAA")
    assert content[1]["image_url"]["url"].endswith("BBBB")


def test_contract_failure_gets_one_repair_post_then_returns_failure() -> None:
    invalid = _payload()
    del invalid["target_spec"]["material"]
    runtime, session = _runtime([_Response(invalid), _Response(invalid)])
    outcomes: list[tuple[str, str]] = []

    with pytest.raises(DoubaoArkError):
        runtime.analyze_assets(
            _request(),
            _image("source-a", "AAAA"),
            _image("target-b", "BBBB"),
            grant=_grant(),
            call_ids=("analysis-2:analysis:1", "analysis-2:analysis:2"),
            on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
        )

    assert len(session.requests) == FISSION_ANALYSIS_MAX_ATTEMPTS == 2
    assert outcomes == [
        ("analysis-2:analysis:1", "success"),
        ("analysis-2:analysis:2", "success"),
    ]
    assert "rejection_feedback" in str(session.requests[1]["json"]["messages"][-1])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(extra="unexpected"),
        lambda payload: payload["target_spec"].update(artwork_style="crossed role"),
        lambda payload: payload["target_spec"].update(component_count=True),
    ],
)
def test_analysis_rejects_extra_fields_role_overlap_and_invalid_types(mutate) -> None:
    payload = _payload()
    mutate(payload)
    runtime, _session = _runtime([_Response(payload), _Response(payload)])

    with pytest.raises(DoubaoArkError):
        runtime.analyze_assets(
            _request(),
            _image("source-a", "AAAA"),
            _image("target-b", "BBBB"),
            grant=_grant(),
            call_ids=("analysis-3:analysis:1", "analysis-3:analysis:2"),
        )


def test_analysis_requires_grant_and_matching_a_b_assets() -> None:
    runtime, _session = _runtime([])
    with pytest.raises(PodBillingAuthorizationRequired):
        runtime.analyze_assets(
            _request(), _image("source-a", "AAAA"), _image("target-b", "BBBB"),
            grant=PodExecutionGrant("freeze", 1, "2099-01-01T00:00:00Z", {}),
            call_ids=("analysis-4:analysis:1", "analysis-4:analysis:2"),
        )


def test_empty_essential_fact_is_repaired_then_succeeds() -> None:
    invalid = _payload()
    invalid["target_spec"]["product_name"] = ""
    valid = _payload()
    runtime, session = _runtime([_Response(invalid), _Response(valid)])
    result = runtime.analyze_assets(_request(), _image("source-a", "AAAA"), _image("target-b", "BBBB"), grant=_grant(), call_ids=("analysis-5:analysis:1", "analysis-5:analysis:2"))
    assert result.target_spec["product_name"] == "desk lamp"
    assert len(session.requests) == 2


def test_empty_essential_fact_after_repair_still_fails() -> None:
    invalid = _payload()
    invalid["target_spec"]["product_name"] = ""
    runtime, session = _runtime([_Response(invalid), _Response(invalid)])
    with pytest.raises(DoubaoArkError):
        runtime.analyze_assets(_request(), _image("source-a", "AAAA"), _image("target-b", "BBBB"), grant=_grant(), call_ids=("analysis-6:analysis:1", "analysis-6:analysis:2"))
    assert len(session.requests) == 2

    with pytest.raises(DoubaoArkError):
        runtime.analyze_assets(
            _request(), _image("wrong", "AAAA"), _image("target-b", "BBBB"),
            grant=_grant(), call_ids=("analysis-4:analysis:1", "analysis-4:analysis:2"),
        )

@pytest.mark.parametrize('field', ['source_summary', 'target_summary'])
def test_empty_summary_gets_one_repair_then_fails(field):
    payload = _payload()
    payload['user_summary'][field] = '  '
    runtime, session = _runtime([_Response(payload), _Response(payload)])
    try:
        with pytest.raises(DoubaoArkError, match='contract'):
            runtime.analyze_assets(_request(), _image('source-a', 'AAAA'), _image('target-b', 'BBBB'),
                                  grant=_grant(), call_ids=('empty:analysis:1', 'empty:analysis:2'))
        assert len(session.requests) == 2
    finally:
        runtime.close()
