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
    _messages_for_generation,
    validate_generated_panels,
    validate_localized_panels,
)
from wh_local.modules.product_processing.doubao_ark import DoubaoArkError


def _grant() -> PodExecutionGrant:
    return PodExecutionGrant("freeze-composition", 1, "2099-01-01T00:00:00Z", {"ark": "ark-key"})


def _generated() -> dict[str, str]:
    """合规样例：只讲拍摄手法，不含任何具体事物（含 role 前缀便于断言）。"""

    return {
        "panel_1_zh": "主图：略低机位中全景，主体落在三分点、另一侧大面积留白，暖金色方向光，背景大幅虚化",
        "panel_1_en": "flat-lay hero at a slightly low angle, subject on the rule-of-thirds point with wide negative space, warm golden directional light, heavily blurred background",
        "panel_2_zh": "细节图 A：垂直俯视微距近景，主体表面居中、四周等距留白，均匀漫射光",
        "panel_2_en": "macro close-up from directly overhead, subject surface centred with even margins, soft diffused light",
        "panel_3_zh": "细节图 B：低机位平视近景，对准主体边缘与结构转折，浅景深",
        "panel_3_en": "low eye-level close shot aimed at the subject edge and structural joint, shallow depth of field",
        "panel_4_zh": "素材图：平视正面居中，中性纯净背景，四边等距留白，影棚均匀布光",
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


def test_generation_prompt_states_the_required_output_shape() -> None:
    """生成提示词必须给出"只讲拍摄手法"的句式模板，让输出形状稳定。"""

    messages = _messages_for_generation(
        PodCompositionRequest(composition_id="cmp1", brief="随便拍")
    )
    payload = json.loads(messages[1]["content"])
    assert "句式固定为" in payload["output_shape"]
    assert "不得出现任何东西的名字" in payload["output_shape"]


def test_generate_composition_disables_thinking_and_returns_zh_and_en() -> None:
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
    # 用户要求关掉深度思考：构图生成也是纯改写，开着只会让用户干等。
    assert request["json"]["thinking"] == {"type": "disabled"}
    schema = request["json"]["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {
        "panel_1_zh", "panel_1_en", "panel_2_zh", "panel_2_en",
        "panel_3_zh", "panel_3_en", "panel_4_zh", "panel_4_en",
    }
    # 模糊输入必须被当作不可信数据包裹。
    assert "untrusted" in str(request["json"]["messages"][0]["content"]).lower()

    # 「只讲怎么拍、严禁点名具体东西」的硬约束必须随提示词下发，防止口径走样。
    user_prompt = json.loads(request["json"]["messages"][1]["content"])
    hard_rules = " ".join(user_prompt["hard_rules"])
    assert "严禁出现任何具体事物与元素的名字" in hard_rules
    assert "只允许描述拍摄手法" in hard_rules
    assert "主体与其部件一律用抽象、通用的说法" in hard_rules
    assert "自检" in hard_rules
    # 抽象说法（场景氛围 / 边角 / 提手结构）是允许的，别被误删成只剩机位景别。
    panel_contract = " ".join(user_prompt["panel_contract"].values())
    assert "场景氛围" in panel_contract
    assert "提手结构" in panel_contract


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
    # 转写环节不得自行新增具体事物（只忠实翻译用户已经写下的内容）。
    localize_prompt = json.loads(session.requests[0]["json"]["messages"][1]["content"])
    assert "不得自行新增任何具体事物" in localize_prompt["instructions"]
    # 翻译不该开深度思考：否则手动保存要干等几十秒（实测约 40s）。
    assert session.requests[0]["json"]["thinking"] == {"type": "disabled"}


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
