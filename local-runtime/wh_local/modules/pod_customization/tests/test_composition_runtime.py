"""构图/视角定制：一段大白话 → 四格画面指令（composition_runtime）的契约与运行测试。

全程使用假 provider 密钥与 mock HTTP，不触发任何真实付费调用。
"""

from __future__ import annotations

import json

import pytest

from wh_local.modules.pod_customization.billing_contract import (
    COMPOSITION_ATTEMPTS,
    PodBillingAuthorizationRequired,
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.composition_runtime import (
    PROMPT_VERSION,
    PodCompositionLocalizeRequest,
    PodCompositionRequest,
    PodCompositionRuntime,
    validate_generated_panels,
    validate_localized_panels,
)
from wh_local.modules.product_processing.doubao_ark import DoubaoArkError


def _grant() -> PodExecutionGrant:
    return PodExecutionGrant("freeze-composition", 1, "2099-01-01T00:00:00Z", {"ark": "ark-key"})


def _generated() -> dict[str, str]:
    return {
        "panel_1_zh": "主图：木桌俯拍平铺，暖光",
        "panel_1_en": "flat-lay hero on a wooden table under warm window light",
        "panel_2_zh": "细节图 A：图案微距",
        "panel_2_en": "macro close-up of the surface artwork",
        "panel_3_zh": "细节图 B：侧面结构",
        "panel_3_en": "three-quarter structural detail",
        "panel_4_zh": "素材图：正面纯白底",
        "panel_4_en": "front view on a plain neutral white background",
    }


def _en_only() -> dict[str, str]:
    return {
        "panel_1": "edited flat-lay hero on a marble surface",
        "panel_2": "edited macro close-up",
        "panel_3": "edited structural detail",
        "panel_4": "edited front view on white",
    }


def _zh_panels() -> dict[str, str]:
    return {
        "panel_1": "主图：大理石台面平铺",
        "panel_2": "细节图 A：图案微距",
        "panel_3": "细节图 B：侧面结构",
        "panel_4": "素材图：正面纯白底",
    }


def _call_ids(composition_id: str) -> tuple[str, ...]:
    return tuple(
        f"{composition_id}:composition:{attempt}" for attempt in range(1, COMPOSITION_ATTEMPTS + 1)
    )


class _Response:
    def __init__(self, payload: dict[str, object], *, status_code: int = 200) -> None:
        body = json.dumps({"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})
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


def test_composition_plan_freezes_title_only_scope_so_the_action_is_free() -> None:
    """免费的关键：只冻结 pod.title，服务端对 POD 画像的纯 title scope 零计费。"""
    plan = PodCallPlan.for_composition("cmp1")

    assert plan.idempotency_key == "pod:composition:cmp1"
    assert len(plan.calls) == COMPOSITION_ATTEMPTS
    assert {call.feature for call in plan.calls} == {"pod.title"}

    payload = plan.product_batch_freeze_payload()
    assert payload["scope"] == ["title"]
    assert payload["billing_profile"] == "pod_random_v1"


def test_validate_generated_panels_requires_zh_and_en_for_all_four() -> None:
    panels = validate_generated_panels(_generated())
    assert panels.panel_1.zh.startswith("主图")
    assert panels.panel_1.en.startswith("flat-lay")

    with pytest.raises(ValueError, match="zh and en"):
        validate_generated_panels({"panel_1_zh": "只有一格"})

    with pytest.raises(ValueError, match="不能为空"):
        validate_generated_panels({**_generated(), "panel_3_zh": "   "})


def test_validate_localized_panels_merges_zh_with_model_english() -> None:
    panels = validate_localized_panels(_en_only(), _zh_panels())
    assert panels.panel_1.zh == "主图：大理石台面平铺"
    assert panels.panel_1.en.startswith("edited flat-lay")

    with pytest.raises(ValueError, match="four panel keys"):
        validate_localized_panels({"panel_1": "x"}, _zh_panels())


def test_validate_panels_rejects_prohibited_terms() -> None:
    with pytest.raises(ValueError, match="禁用词"):
        validate_generated_panels({**_generated(), "panel_2_en": "close-up with a nike logo watermark"})


def test_generate_composition_enables_thinking_and_returns_zh_and_en() -> None:
    session = _Session([_Response(_generated())])
    runtime = PodCompositionRuntime(session=session, sleeper=lambda _seconds: None)
    outcomes: list[tuple[str, str]] = []

    result = runtime.generate_composition(
        PodCompositionRequest(composition_id="cmp1", brief="四张图想这样拍"),
        grant=_grant(),
        call_id="cmp1:composition:1",
        call_ids=_call_ids("cmp1"),
        on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
    )

    assert result.prompt_version == PROMPT_VERSION
    assert result.panels.panel_4.zh.startswith("素材图")
    assert result.panels.panel_4.en.startswith("front view")
    assert outcomes == [("cmp1:composition:1", "success")]

    request = session.requests[0]
    # 用户要求「让豆包深度思考」：构图定制必须开启 thinking（brief/title 是 disabled）。
    assert request["json"]["thinking"] == {"type": "enabled"}
    schema = request["json"]["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {
        "panel_1_zh", "panel_1_en", "panel_2_zh", "panel_2_en",
        "panel_3_zh", "panel_3_en", "panel_4_zh", "panel_4_en",
    }
    # 模糊输入必须被当作不可信数据包裹。
    assert "untrusted" in str(request["json"]["messages"][0]["content"]).lower()


def test_localize_composition_translates_edited_zh_and_echoes_zh() -> None:
    session = _Session([_Response(_en_only())])
    runtime = PodCompositionRuntime(session=session, sleeper=lambda _seconds: None)

    result = runtime.localize_composition(
        PodCompositionLocalizeRequest(composition_id="cmp5", panels=_zh_panels()),
        grant=_grant(),
        call_id="cmp5:composition:1",
        call_ids=_call_ids("cmp5"),
    )

    assert result.panels.panel_2.zh == "细节图 A：图案微距"
    assert result.panels.panel_2.en == "edited macro close-up"

    schema = session.requests[0]["json"]["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"panel_1", "panel_2", "panel_3", "panel_4"}


def test_generate_composition_repairs_an_invalid_contract_then_succeeds() -> None:
    session = _Session([_Response({"panel_1_zh": "x"}), _Response(_generated())])
    runtime = PodCompositionRuntime(session=session, sleeper=lambda _seconds: None)
    outcomes: list[tuple[str, str]] = []

    result = runtime.generate_composition(
        PodCompositionRequest(composition_id="cmp2", brief="主题"),
        grant=_grant(),
        call_id="cmp2:composition:1",
        call_ids=_call_ids("cmp2"),
        on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
    )

    assert result.attempt_count == 2
    assert len(session.requests) == 2
    assert outcomes == [("cmp2:composition:1", "success"), ("cmp2:composition:2", "success")]


def test_generate_composition_fails_after_exhausting_frozen_calls() -> None:
    session = _Session([_Response({"panel_1_zh": "x"}) for _ in range(COMPOSITION_ATTEMPTS)])
    runtime = PodCompositionRuntime(session=session, sleeper=lambda _seconds: None)

    with pytest.raises(DoubaoArkError):
        runtime.generate_composition(
            PodCompositionRequest(composition_id="cmp3", brief="主题"),
            grant=_grant(),
            call_id="cmp3:composition:1",
            call_ids=_call_ids("cmp3"),
        )

    assert len(session.requests) == COMPOSITION_ATTEMPTS


def test_generate_composition_requires_an_ark_grant() -> None:
    runtime = PodCompositionRuntime(session=_Session([]), sleeper=lambda _seconds: None)

    with pytest.raises(PodBillingAuthorizationRequired):
        runtime.generate_composition(
            PodCompositionRequest(composition_id="cmp4", brief="主题"),
            grant=PodExecutionGrant("freeze", 1, "2099-01-01T00:00:00Z", {}),
            call_id="cmp4:composition:1",
        )
