from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

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


class _Response:
    def __init__(self, payload: dict | str, *, status_code: int = 200) -> None:
        self.content = (
            payload.encode("utf-8")
            if isinstance(payload, str)
            else json.dumps(payload).encode("utf-8")
        )
        self.status_code = status_code
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self, responses: list[_Response | BaseException]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def post(self, url, *, headers, json, timeout, allow_redirects):
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "json": json,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _success(payload: dict = VALID_TEXT) -> _Response:
    return _Response({"choices": [{"message": {"content": json.dumps(payload)}}]})


@pytest.fixture(autouse=True)
def _reserved_server_text_usage():
    with server_ai_context("platform-token", {"text": "usage-text"}):
        yield


def test_text_request_uses_platform_gateway_and_contains_no_image(monkeypatch) -> None:
    session = _Session([_success()])
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)

    result = doubao_text.DoubaoTextClient().generate_listing_text(
        "STRICT LISTING PROMPT"
    )

    assert result.as_dict() == VALID_TEXT
    request = session.requests[0]
    assert request["url"].endswith("/api/customer/ai/chat")
    assert request["json"]["model"] == "gpt-5.6-terra"
    assert request["json"]["usage_id"] == "usage-text"
    assert request["json"]["messages"] == [
        {"role": "user", "content": "STRICT LISTING PROMPT"}
    ]
    assert "image_url" not in json.dumps(request["json"])
    assert request["allow_redirects"] is False
    assert request["headers"]["Authorization"] == "Bearer platform-token"


def test_server_ai_gateway_requests_share_two_slot_gate(monkeypatch) -> None:
    lock = threading.Lock()
    in_flight = 0
    peak = 0

    class SlowSession:
        def post(self, *_args, **_kwargs):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.03)
            with lock:
                in_flight -= 1
            return _success()

    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", SlowSession())
    clients = [doubao_ark.DoubaoArkClient() for _index in range(6)]

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda client: client.complete(
                    [{"role": "user", "content": "same prompt"}]
                ),
                clients,
            )
        )

    assert len(results) == 6
    assert peak == 2


def test_invalid_contract_retries_whole_text_stage_until_third_success(monkeypatch) -> None:
    invalid = {**VALID_TEXT, "description": "not five points"}
    session = _Session([_success(invalid), _success(invalid), _success()])
    monkeypatch.setenv("ARK_API_KEY", "ark-secret")
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
    monkeypatch.setenv("ARK_API_KEY", "ark-secret")
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
    monkeypatch.setenv("ARK_API_KEY", "ark-secret")
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
    monkeypatch.setenv("ARK_API_KEY", "ark-secret")
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    with pytest.raises(doubao_text.DoubaoTextError) as captured:
        doubao_text.DoubaoTextClient().generate_listing_text(invalid_body)

    assert captured.value.error_kind == "invalid_response"
    assert captured.value.retryable is True
    assert captured.value.attempt_count == 3
    assert invalid_body not in str(captured.value)
    assert "ark-secret" not in str(captured.value)
    assert len(session.requests) == 3


def test_old_gateway_retry_limit_preserves_contract_failure(monkeypatch) -> None:
    session = _Session(
        [
            _success({}),
            _success({}),
            _Response({"detail": "gateway request limit reached"}, status_code=409),
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


def test_configuration_error_does_not_retry_or_expose_provider_body(monkeypatch) -> None:
    session = _Session([_Response("permission-secret", status_code=403)])
    monkeypatch.setenv("ARK_API_KEY", "ark-secret")
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)

    with pytest.raises(doubao_text.DoubaoTextError) as captured:
        doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    assert captured.value.error_kind == "configuration"
    assert captured.value.retryable is False
    assert captured.value.attempt_count == 1
    assert "permission-secret" not in str(captured.value)
    assert len(session.requests) == 1


def test_missing_reserved_usage_is_configuration_error_without_provider_attempt() -> None:
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
    monkeypatch.setenv("ARK_API_KEY", "ark-secret")
    monkeypatch.setattr(doubao_ark, "_HTTP_SESSION", session)
    monkeypatch.setattr(doubao_text.time, "sleep", lambda _seconds: None)

    with pytest.raises(doubao_text.DoubaoTextError) as captured:
        doubao_text.DoubaoTextClient().generate_listing_text("prompt")

    assert captured.value.error_kind == "invalid_response"
    assert captured.value.attempt_count == 3
