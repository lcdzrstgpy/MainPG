from __future__ import annotations

import json

import pytest

from wh_local.modules.pod_customization.billing_contract import (
    PodBillingAuthorizationRequired,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.fission_runtime import (
    FISSION_ANALYSIS_MAX_ATTEMPTS,
    FISSION_PLAN_PROMPT_VERSION,
    FISSION_PLAN_SCHEMA_VERSION,
    FissionAssetImage,
    FissionPlanRequest,
    PodFissionAnalysisRuntime,
)
from wh_local.modules.product_processing.doubao_ark import DoubaoArkError

_TAIL = (
    " The single square frame shows reference image 2 (a cotton bag) printed edge to edge with "
    "reference image 1 motifs, continuing across the front and both side panels, on a soft studio "
    "backdrop with warm directional key light and gentle contact shadow."
)


def _variant(index: int) -> dict[str, object]:
    return {
        "style_family": f"family-{index}",
        "motifs": [f"motif-{index}a", f"motif-{index}b"],
        "palette": {"primary": f"colour-{index}", "secondary": "sand", "accent": "brass"},
        "composition_blueprint": (
            f"A scattered field of motif-{index}a across the lower half with motif-{index}b rising "
            "diagonally into the upper-right corner at reduced scale."
        ),
        "scene": (
            "A pale oak table top with a soft grey linen backdrop, one folded cloth in the corner and "
            "diffused daylight from a high window."
        ),
        "variation_note": f"differs by family-{index}",
        "main_prompt": f"Design {index}: family-{index} all-over print." + _TAIL,
    }


def _payload(count: int = 2) -> dict[str, object]:
    return {
        "source_summary": "retro star artwork",
        "target_summary": "a cotton tote bag",
        "product_name": "cotton tote bag",
        "variants": [_variant(index) for index in range(1, count + 1)],
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


def _request(count: int = 2) -> FissionPlanRequest:
    return FissionPlanRequest(source_asset_id="source-a", target_asset_id="target-b", requested_count=count)


def _image(asset_id: str, marker: str) -> FissionAssetImage:
    return FissionAssetImage(asset_id, f"data:image/png;base64,{marker}")


def _runtime(responses: list[_Response]) -> tuple[PodFissionAnalysisRuntime, _Session]:
    session = _Session(responses)
    return PodFissionAnalysisRuntime(session=session, sleeper=lambda _seconds: None), session


def test_plans_post_once_with_a_then_b_and_return_normalized_plans() -> None:
    runtime, session = _runtime([_Response(_payload(2))])
    outcomes: list[tuple[str, str]] = []

    result = runtime.generate_plans(
        _request(2),
        _image("source-a", "AAAA"),
        _image("target-b", "BBBB"),
        grant=_grant(),
        call_ids=("analysis-1:analysis:1", "analysis-1:analysis:2"),
        on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
    )

    assert result.product_name == "cotton tote bag"
    assert [plan.style_family for plan in result.plans] == ["family-1", "family-2"]
    assert result.plans[0].motifs == ("motif-1a", "motif-1b")
    assert result.plans[0].fingerprint
    assert result.schema_version == FISSION_PLAN_SCHEMA_VERSION
    assert result.raw_response["target_summary"] == "a cotton tote bag"
    assert len(session.requests) == 1
    assert outcomes == [
        ("analysis-1:analysis:1", "success"),
        ("analysis-1:analysis:2", "no_return"),
    ]
    content = session.requests[0]["json"]["messages"][1]["content"]
    assert content[0]["image_url"]["url"].endswith("AAAA")
    assert content[1]["image_url"]["url"].endswith("BBBB")
    instruction = json.loads(content[2]["text"])
    assert instruction["hard_rules"]
    assert instruction["diversity_rules"]
    assert any("reference library" in rule for rule in instruction["hard_rules"])
    assert any("single plan" in rule for rule in instruction["diversity_rules"])
    assert any("Never reuse A's layout" in rule for rule in instruction["hard_rules"])
    assert "composition_blueprint" in instruction["variant_contract"]
    assert "scene" in instruction["variant_contract"]
    assert "composition_blueprint" in instruction["variant_contract"]["main_prompt"]
    assert "2x2" not in instruction["variant_contract"]["main_prompt"]
    assert FISSION_PLAN_PROMPT_VERSION == "pod-fission-plan-prompt-v4"


def test_variant_count_mismatch_gets_one_repair_then_fails() -> None:
    runtime, session = _runtime([_Response(_payload(2)), _Response(_payload(2))])

    with pytest.raises(DoubaoArkError):
        runtime.generate_plans(
            _request(3),
            _image("source-a", "AAAA"),
            _image("target-b", "BBBB"),
            grant=_grant(),
            call_ids=("analysis-2:analysis:1", "analysis-2:analysis:2"),
        )

    assert len(session.requests) == FISSION_ANALYSIS_MAX_ATTEMPTS == 2
    assert "rejection_feedback" in str(session.requests[1]["json"]["messages"][-1])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["variants"][0].update(main_prompt="Short 2x2 grid layout." + " x" * 120),
        lambda payload: payload["variants"][0].update(palette={"primary": "", "secondary": "s", "accent": "a"}),
        lambda payload: payload["variants"][1].update(style_family="family-1", motifs=["motif-1a", "motif-1b"],
                                                      palette={"primary": "colour-1", "secondary": "sand", "accent": "brass"}),
        lambda payload: payload.update(extra="unexpected"),
    ],
)
def test_plan_contract_rejections_repair_then_fail(mutate) -> None:
    payload = _payload(2)
    mutate(payload)
    runtime, session = _runtime([_Response(payload), _Response(payload)])
    with pytest.raises(DoubaoArkError):
        runtime.generate_plans(
            _request(2),
            _image("source-a", "AAAA"),
            _image("target-b", "BBBB"),
            grant=_grant(),
            call_ids=("analysis-3:analysis:1", "analysis-3:analysis:2"),
        )
    assert len(session.requests) == 2


def test_print_layout_grid_wording_is_not_rejected_as_multi_frame_output() -> None:
    """印花排布里的 loose grid 是设计描述，不该被当成「输出宫格」拦掉。"""
    payload = _payload(1)
    payload["variants"][0]["main_prompt"] = (
        "Design 1: motifs arranged in a gentle loose grid across the entire front panel and both side "
        "panels, printed edge to edge with a thin unprinted margin at the seams, on a plain studio "
        "backdrop with soft directional light."
    )
    runtime, _session = _runtime([_Response(payload)])

    result = runtime.generate_plans(
        _request(1),
        _image("source-a", "AAAA"),
        _image("target-b", "BBBB"),
        grant=_grant(),
        call_ids=("analysis-4:analysis:1", "analysis-4:analysis:2"),
    )

    assert "loose grid" in result.plans[0].main_prompt


def test_negated_multi_frame_wording_is_not_rejected() -> None:
    """「no … collage」「never a 2x2 contact sheet」是在复述禁止项，不该被判成多格要求。"""
    payload = _payload(1)
    payload["variants"][0]["main_prompt"] = (
        "A single square frame of the pouch printed edge to edge with A's gingerbread motifs, with no "
        "text, logos, borders or collage, and never a 2x2 contact sheet or a four-panel grid. Soft "
        "window light from the left, plain studio backdrop, quiet holiday mood."
    )
    runtime, _session = _runtime([_Response(payload)])

    result = runtime.generate_plans(
        _request(1),
        _image("source-a", "AAAA"),
        _image("target-b", "BBBB"),
        grant=_grant(),
        call_ids=("analysis-8:analysis:1", "analysis-8:analysis:2"),
    )

    assert "no text, logos, borders or collage" in result.plans[0].main_prompt


def test_plans_may_share_style_family_when_layout_differs() -> None:
    """同一爆款家族内允许共享 style_family，差异由构图、尺度、配色配比和场景承担。"""
    payload = _payload(5)
    for index, variant in enumerate(payload["variants"], start=1):
        variant["style_family"] = "gingerbread cottage family"
        variant["motifs"] = [f"motif-{index}a", f"motif-{index}b"]
        variant["main_prompt"] = f"Design {index}: gingerbread cottage all-over print." + _TAIL
        variant["palette"] = {"primary": f"c{index}", "secondary": "sand", "accent": "brass"}
    runtime, session = _runtime([_Response(payload)])

    result = runtime.generate_plans(
        _request(5),
        _image("source-a", "AAAA"),
        _image("target-b", "BBBB"),
        grant=_grant(),
        call_ids=("analysis-4:analysis:1", "analysis-4:analysis:2"),
    )

    assert len(result.plans) == 5
    assert {plan.style_family for plan in result.plans} == {"gingerbread cottage family"}
    assert len(session.requests) == 1


def test_duplicate_plans_are_rejected() -> None:
    payload = _payload(2)
    payload["variants"][1] = dict(payload["variants"][0])
    runtime, session = _runtime([_Response(payload), _Response(payload)])
    with pytest.raises(DoubaoArkError):
        runtime.generate_plans(
            _request(2),
            _image("source-a", "AAAA"),
            _image("target-b", "BBBB"),
            grant=_grant(),
            call_ids=("analysis-7:analysis:1", "analysis-7:analysis:2"),
        )
    assert len(session.requests) == 2


def test_requested_count_outside_range_is_rejected_before_call() -> None:
    runtime, session = _runtime([])
    with pytest.raises(DoubaoArkError):
        runtime.generate_plans(
            _request(6),
            _image("source-a", "AAAA"),
            _image("target-b", "BBBB"),
            grant=_grant(),
            call_ids=("analysis-5:analysis:1", "analysis-5:analysis:2"),
        )
    assert not session.requests


def test_plans_require_grant_and_matching_a_b_assets() -> None:
    runtime, _session = _runtime([])
    with pytest.raises(PodBillingAuthorizationRequired):
        runtime.generate_plans(
            _request(), _image("source-a", "AAAA"), _image("target-b", "BBBB"),
            grant=PodExecutionGrant("freeze", 1, "2099-01-01T00:00:00Z", {}),
            call_ids=("analysis-6:analysis:1", "analysis-6:analysis:2"),
        )
    with pytest.raises(DoubaoArkError):
        runtime.generate_plans(
            _request(), _image("wrong", "AAAA"), _image("target-b", "BBBB"),
            grant=_grant(), call_ids=("analysis-7:analysis:1", "analysis-7:analysis:2"),
        )
