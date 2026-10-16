from __future__ import annotations

import json

import pytest
import requests

from wh_local.modules.product_processing import doubao_ark, doubao_text
from wh_local.modules.product_processing.server_ai_proxy import server_ai_context


VALID_TEXT = {
    "optimized_title": "Insulated Stainless Steel Travel Mug with Lid for Daily Commuting",
    "description": (
        "PRODUCT FORM - The cylindrical travel mug has a fitted lid and a stable flat base.\n"
        "VISIBLE FINISH - A smooth blue exterior gives the mug a clean everyday appearance.\n"
        "LID DESIGN - The fitted top helps keep the drinking opening covered between uses.\n"
        "PORTABLE SHAPE - The compact upright profile is suitable for desks and commuting bags.\n"
        "DAILY USE - The reusable cup supports drinks at home, work, or while traveling."
    ),
    "variant_translations": [
        {"raw_value": "蓝色", "export_value": "Blue"},
    ],
    "product_dimensions": {
        "length_cm": 8,
        "width_cm": 8,
        "height_cm": 20,
        "weight_g": 300,
    },
}

# 直连模式下客户端只使用批次冻结下发的短期 Ark 密钥，不再经平台网关。
ARK_KEY = "ark-direct-key"


class _Response:
    def __init__(
        self, payload: dict | str, *, status_code: int = 200, headers: dict | None = None
    ) -> None:
        self.content = (
            payload.encode("utf-8")
            if isinstance(payload, str)
            else json.dumps(payload).encode("utf-8")
        )
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self, responses: list[_Response | BaseException]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def post(self, url, *, headers, json, timeout, allow_redirects, verify=None):
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "json": json,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
                "verify": verify,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _success(payload: dict = VALID_TEXT) -> _Response:
    return _Response({"choices": [{"message": {"content": json.dumps(payload)}}]})


@pytest.fixture(autouse=True)
def _direct_ark_granted_key():
    with server_ai_context(
        "platform-token",
        {"text": "usage-text"},
        granted_keys={"ark": ARK_KEY},
    ):
        yield


def test_text_request_uses_direct_ark_and_contains_no_image(monkeypatch) -> None:
    session = _Session([_success()])
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)

    result = doubao_text.DoubaoTextClient().generate_listing_text(
        "STRICT LISTING PROMPT"
    )

    assert result.as_dict() == VALID_TEXT
    request = session.requests[0]
    assert request["url"] == doubao_ark.API_URL
    assert request["json"]["model"] == doubao_ark.MODEL_ID
    assert "usage_id" not in request["json"]
    assert request["json"]["messages"] == [
        {"role": "user", "content": "STRICT LISTING PROMPT"}
    ]
    assert "image_url" not in json.dumps(request["json"])
    assert request["allow_redirects"] is False
    assert request["headers"]["Authorization"] == f"Bearer {ARK_KEY}"


def test_invalid_contract_retries_whole_text_stage_until_third_success(monkeypatch) -> None:
    invalid = {**VALID_TEXT, "description": "not five points"}
    session = _Session([_success(invalid), _success(invalid), _success()])
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    validations = 0

    def validate(result: doubao_text.DoubaoTextResult) -> None:
        nonlocal validations
        validations += 1
        if result.description == "not five points":
            raise ValueError("description contract failed")

    client = doubao_text.DoubaoTextClient()
    result = client.generate_listing_text("prompt", validator=validate)

    assert result.optimized_title == VALID_TEXT["optimized_title"]
    assert validations == 3
    assert client.last_attempt_count == 3
    assert len(session.requests) == 3
    prompts = [request["json"]["messages"][0]["content"] for request in session.requests]
    assert prompts[0] == "prompt"
    assert "description contract failed" in prompts[1]
    assert "repair attempt 2" in prompts[1]
    assert "repair attempt 3" in prompts[2]
    assert len(set(prompts)) == 3


def test_transient_errors_share_the_three_attempt_budget(monkeypatch) -> None:
    session = _Session(
        [requests.Timeout("secret socket detail"), _Response({}, status_code=429), _success()]
    )
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    client = doubao_text.DoubaoTextClient()
    result = client.generate_listing_text("prompt")

    assert result.description == VALID_TEXT["description"]
    assert client.last_attempt_count == 3
    assert len(session.requests) == 3
    assert {
        request["json"]["messages"][0]["content"] for request in session.requests
    } == {"prompt"}


def test_invalid_ark_envelope_json_uses_the_same_three_attempt_budget(monkeypatch) -> None:
    session = _Session([_Response("not-json"), _Response("still-not-json"), _success()])
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    client = doubao_text.DoubaoTextClient()
    result = client.generate_listing_text("prompt")

    assert result.optimized_title == VALID_TEXT["optimized_title"]
    assert client.last_attempt_count == 3
    assert len(session.requests) == 3


def test_three_invalid_responses_raise_sanitized_retryable_error(monkeypatch) -> None:
    invalid_body = "provider-secret-body"
    session = _Session([_success({}), _success({}), _success({})])
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    with pytest.raises(doubao_text.DoubaoTextError) as captured:
        doubao_text.DoubaoTextClient().generate_listing_text(invalid_body)

    assert captured.value.error_kind == "invalid_response"
    assert captured.value.retryable is True
    assert captured.value.attempt_count == 3
    assert invalid_body not in str(captured.value)
    assert ARK_KEY not in str(captured.value)
    assert len(session.requests) == 3


def _raw_content(text: str) -> _Response:
    """把任意字符串塞进 Ark 的 message.content，模拟模型的原始返回。"""
    return _Response({"choices": [{"message": {"content": text}}]})


def test_markdown_fenced_json_response_is_accepted(monkeypatch) -> None:
    """豆包常把契约 JSON 包进 ```json 代码块；这只是包装问题，不应整条判失败。"""
    fenced = f"```json\n{json.dumps(VALID_TEXT, ensure_ascii=False)}\n```"
    session = _Session([_raw_content(fenced)])
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)

    result = doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    assert result.as_dict() == VALID_TEXT
    assert len(session.requests) == 1


def test_prose_wrapped_json_response_is_accepted(monkeypatch) -> None:
    """前后夹带说明文字（含 BOM）同样只按包装处理，直接取配平的对象。"""
    wrapped = (
        "\ufeff好的，以下是结果：\n"
        f"{json.dumps(VALID_TEXT, ensure_ascii=False)}\n\n"
        "如需调整描述长度请告诉我。"
    )
    session = _Session([_raw_content(wrapped)])
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)

    result = doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    assert result.as_dict() == VALID_TEXT
    assert len(session.requests) == 1


def test_truncated_json_response_still_fails_with_shape_diagnostic(monkeypatch) -> None:
    """被截断的 JSON 绝不能猜测补全；失败详情要能指明「未配平」。"""
    truncated = json.dumps(VALID_TEXT, ensure_ascii=False)[:80]
    session = _Session([_raw_content(truncated)] * 3)
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    with pytest.raises(doubao_text.DoubaoTextError) as captured:
        doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    message = str(captured.value)
    assert captured.value.error_kind == "invalid_response"
    assert captured.value.attempt_count == 3
    assert "object_balanced=False" in message
    assert VALID_TEXT["optimized_title"] not in message


def test_fenced_json_still_fails_the_strict_structural_contract(monkeypatch) -> None:
    """取 JSON 变宽松、校验不变宽松：围栏里的结构错误照样拒绝。"""
    fenced_extra = "```json\n" + json.dumps(
        {**VALID_TEXT, "unexpected": "field"}, ensure_ascii=False
    ) + "\n```"
    session = _Session([_raw_content(fenced_extra)] * 3)
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    with pytest.raises(doubao_text.DoubaoTextError) as captured:
        doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    assert captured.value.error_kind == "invalid_response"
    assert captured.value.attempt_count == 3
    assert "output fields failed validation" in str(captured.value)


def test_409_after_contract_failures_preserves_contract_error(monkeypatch) -> None:
    session = _Session(
        [
            _success({}),
            _success({}),
            _Response({"detail": "upstream conflict"}, status_code=409),
        ]
    )
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    with pytest.raises(doubao_text.DoubaoTextError) as captured:
        doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    assert captured.value.error_kind == "invalid_response"
    assert captured.value.attempt_count == 3
    assert "output fields failed validation" in str(captured.value)
    assert len(session.requests) == 3


def test_unclassified_4xx_retries_once_then_succeeds(monkeypatch) -> None:
    """文本链路同样只追加一次：未归类 4xx 过去一次就判死。"""
    flaky = _Response({"error": {"message": "upstream refused"}}, status_code=400)
    session = _Session([flaky, _success()])
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    result = doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    assert result.as_dict() == VALID_TEXT
    assert len(session.requests) == 2


def test_configuration_error_does_not_retry_or_expose_provider_body(monkeypatch) -> None:
    session = _Session([_Response("permission-secret", status_code=403)])
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)

    with pytest.raises(doubao_text.DoubaoTextError) as captured:
        doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    assert captured.value.error_kind == "configuration"
    assert captured.value.retryable is False
    assert captured.value.attempt_count == 1
    assert "permission-secret" not in str(captured.value)
    assert len(session.requests) == 1


def test_missing_granted_key_is_configuration_error_without_provider_attempt() -> None:
    with server_ai_context("", {}):
        with pytest.raises(doubao_text.DoubaoTextError) as captured:
            doubao_text.DoubaoTextClient()

    assert captured.value.error_kind == "configuration"
    assert captured.value.attempt_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        {**VALID_TEXT, "optimized_title": 123},
        {**VALID_TEXT, "variant_translations": {"蓝色": "Blue"}},
        {**VALID_TEXT, "product_dimensions": []},
        {**VALID_TEXT, "unexpected": "field"},
    ],
)
def test_structural_contract_is_strict(monkeypatch, payload: dict) -> None:
    session = _Session([_success(payload)] * 3)
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    with pytest.raises(doubao_text.DoubaoTextError) as captured:
        doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    assert captured.value.error_kind == "invalid_response"
    assert captured.value.attempt_count == 3


def test_variant_rows_ignore_blanks_duplicates_and_normalize_numbers(monkeypatch) -> None:
    payload = {
        **VALID_TEXT,
        "variant_translations": [
            {"raw_value": "黑色", "export_value": "Black"},
            {"raw_value": " 黑色 ", "export_value": "Duplicate"},
            {"raw_value": "", "export_value": ""},
            {"raw_value": 73, "export_value": 73},
            {"raw_value": 160.22, "export_value": "160.22"},
            {"raw_value": True, "export_value": "True"},
            {"unexpected": "ignored"},
            "ignored",
        ],
    }
    session = _Session([_success(payload)])
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)

    result = doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    assert result.variant_translations == (
        ("黑色", "Black"),
        ("73", "73"),
        ("160.22", "160.22"),
    )
