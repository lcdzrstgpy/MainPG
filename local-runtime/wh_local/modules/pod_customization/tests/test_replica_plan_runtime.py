"""复刻主图方案 runtime：参考图顺序、严格 JSON 契约与契约修复重试（假 session）。"""

from __future__ import annotations

import json

import pytest

from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
from wh_local.modules.pod_customization.replica_analysis_runtime import (
    MAIN_PROMPT_MAX_ATTEMPTS,
    MAIN_PROMPT_MAX_LENGTH,
    MAIN_PROMPT_VERSION,
    PodReplicaAnalysisRuntime,
    ReplicaAssetImage,
    parse_main_prompt_response,
)
from wh_local.modules.product_processing.doubao_ark import DoubaoArkError

_PROMPT = (
    "Use reference image 1 as the exact product and reference image 2 only for the printed pattern. "
    "Render the full product on a warm wooden desk in an afternoon kitchen with a directional window light."
)


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


def _asset(asset_id: str, marker: str) -> ReplicaAssetImage:
    return ReplicaAssetImage(asset_id=asset_id, data_url=f"data:image/png;base64,{marker}")


def _runtime(responses: list[_Response]) -> tuple[PodReplicaAnalysisRuntime, _Session]:
    session = _Session(responses)
    runtime = PodReplicaAnalysisRuntime(session=session, sleeper=lambda _seconds: None)
    return runtime, session


def _call_ids(plan_id: str) -> tuple[str, ...]:
    return tuple(f"{plan_id}:analysis:{attempt}" for attempt in range(1, MAIN_PROMPT_MAX_ATTEMPTS + 1))


def _payload(main_prompt: str = _PROMPT) -> dict[str, object]:
    return {"main_prompt": main_prompt}


def _user_content(request: dict[str, object]) -> list[dict[str, object]]:
    messages = request["json"]["messages"]  # type: ignore[index]
    return next(item["content"] for item in messages if item["role"] == "user")


def test_generate_main_prompt_sends_target_first_then_source() -> None:
    runtime, session = _runtime([_Response(_payload())])
    outcomes: list[tuple[str, str]] = []

    result = runtime.generate_main_prompt(
        _asset("asset-a", "AAAA"),
        _asset("asset-b", "BBBB"),
        grant=_grant(),
        call_id="plan-1:analysis:1",
        call_ids=_call_ids("plan-1"),
        on_outcome=lambda call_id, status: outcomes.append((call_id, status)),
    )

    assert result.main_prompt == _PROMPT
    assert result.prompt_version == MAIN_PROMPT_VERSION
    assert result.attempt_count == 1
    assert outcomes == [("plan-1:analysis:1", "success")]

    content = _user_content(session.requests[0])
    images = [item for item in content if item.get("type") == "image_url"]
    # 顺序固定：第 1 张是目标产品 B，第 2 张是样图 A。
    assert images[0]["image_url"]["url"] == "data:image/png;base64,BBBB"
    assert images[1]["image_url"]["url"] == "data:image/png;base64,AAAA"

    instructions = json.loads(next(item["text"] for item in content if item["type"] == "text"))
    roles = " ".join(instructions["reference_roles"])
    assert "image 1 = target product (B)" in roles
    assert "image 2 = original artwork source (A)" in roles


def test_coverage_guidance_is_soft_and_mentions_printable_parts() -> None:
    """满印只做软引导：明确提到提手/肩带等可印部件，但不写死覆盖率。"""

    runtime, session = _runtime([_Response(_payload())])
    runtime.generate_main_prompt(
        _asset("asset-a", "AAAA"),
        _asset("asset-b", "BBBB"),
        grant=_grant(),
        call_id="plan-1:analysis:1",
        call_ids=_call_ids("plan-1"),
    )

    instructions = json.loads(
        next(item["text"] for item in _user_content(session.requests[0]) if item["type"] == "text")
    )
    coverage = instructions["coverage_preference"]
    assert "提手" in coverage
    assert "肩带" in coverage
    assert "大面积空白" in coverage
    # 软引导：不出现「必须 / 100%」这类硬性覆盖率指标。
    assert "100%" not in coverage
    assert "必须" not in coverage

    pattern = instructions["main_image_contract"]["pattern"]
    assert "尽量铺满" in pattern
    assert "优先" in pattern
    assert "算失败" not in pattern


def test_output_contract_only_asks_for_main_prompt() -> None:
    runtime, session = _runtime([_Response(_payload())])
    runtime.generate_main_prompt(
        _asset("asset-a", "AAAA"),
        _asset("asset-b", "BBBB"),
        grant=_grant(),
        call_id="plan-1:analysis:1",
        call_ids=_call_ids("plan-1"),
    )

    schema = session.requests[0]["json"]["response_format"]["json_schema"]["schema"]  # type: ignore[index]
    assert schema["required"] == ["main_prompt"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"main_prompt"}
    assert schema["properties"]["main_prompt"]["maxLength"] == MAIN_PROMPT_MAX_LENGTH


def test_invalid_json_triggers_one_contract_repair_retry() -> None:
    runtime, session = _runtime([_Response("not json"), _Response(_payload())])

    result = runtime.generate_main_prompt(
        _asset("asset-a", "AAAA"),
        _asset("asset-b", "BBBB"),
        grant=_grant(),
        call_id="plan-1:analysis:1",
        call_ids=_call_ids("plan-1"),
    )

    assert result.attempt_count == 2
    assert len(session.requests) == 2


def test_empty_prompt_exhausts_the_budget() -> None:
    runtime, _session = _runtime(
        [_Response(_payload("  ")) for _ in range(MAIN_PROMPT_MAX_ATTEMPTS)]
    )

    with pytest.raises(DoubaoArkError):
        runtime.generate_main_prompt(
            _asset("asset-a", "AAAA"),
            _asset("asset-b", "BBBB"),
            grant=_grant(),
            call_id="plan-1:analysis:1",
            call_ids=_call_ids("plan-1"),
        )


@pytest.mark.parametrize(
    "content",
    [
        "{}",
        json.dumps({"main_prompt": 42}),
        json.dumps({"main_prompt": _PROMPT, "summary": "extra"}),
        json.dumps({"main_prompt": "x" * (MAIN_PROMPT_MAX_LENGTH + 1)}),
    ],
)
def test_response_contract_rejects_non_conforming_payloads(content: str) -> None:
    with pytest.raises(ValueError):
        parse_main_prompt_response(content)
