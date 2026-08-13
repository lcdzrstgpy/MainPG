from __future__ import annotations

import io
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from wh_local.modules.product_processing import ai_client


def _provider() -> dict:
    return {
        "base_url": "https://station-88.aicoming.top/v1",
        "api_key": "test-key",
        "text_model": "gpt-5.6-terra",
        "text_model_fallback_order": [],
        "image_model": "gpt-image-2-1k",
        "image_size": "1024x1024",
        "image_quality": "medium",
        "timeout_seconds": 60,
        "text_timeout_seconds": 25,
        "text_total_timeout_seconds": 100,
        "image_timeout_seconds": 90,
    }


class _Response:
    def __init__(self, payload: dict | str, *, status_code: int = 200):
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
    def __init__(self, responses: list[_Response | BaseException]):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def request(self, method, url, *, headers, json, timeout):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json": json,
                "timeout": timeout,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def test_station_requests_use_non_default_user_agent(monkeypatch):
    responses = [
        _Response({"choices": [{"message": {"content": "OK"}}]}),
        _Response({"data": [{"id": "gpt-5.6-terra"}]}),
    ]
    session = _Session(responses)

    monkeypatch.setattr(ai_client, "resolve_ai_provider", _provider)
    monkeypatch.setattr(ai_client, "_HTTP_SESSION", session)

    client = ai_client.AiClient()
    assert client.chat([{"role": "user", "content": "Reply exactly OK"}]) == "OK"
    assert client.ping()["ok"] is True
    assert len(session.requests) == 2
    assert all(request["headers"]["User-Agent"] == ai_client.AI_USER_AGENT for request in session.requests)
    assert all(request["headers"]["Authorization"] == "Bearer test-key" for request in session.requests)
    assert all(response.closed for response in responses)


def test_text_fallback_uses_only_the_remaining_total_timeout_budget(monkeypatch):
    clock = [0.0]
    request_timeouts: list[float] = []

    def provider() -> dict:
        return {
            **_provider(),
            "text_timeout_seconds": 300,
            "text_total_timeout_seconds": 360,
            "text_model_fallback_order": ["gpt-5.6-luna"],
        }

    class TimedSession(_Session):
        def request(self, method, url, *, headers, json, timeout):
            request_timeouts.append(float(timeout))
            if len(request_timeouts) == 1:
                clock[0] = 300.0
                raise requests.ConnectionError("slow model timed out")
            return super().request(method, url, headers=headers, json=json, timeout=timeout)

    session = TimedSession([_Response({"choices": [{"message": {"content": "OK"}}]})])

    monkeypatch.setattr(ai_client, "resolve_ai_provider", provider)
    monkeypatch.setattr(ai_client.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ai_client, "_HTTP_SESSION", session)

    client = ai_client.AiClient()
    assert client.chat([{"role": "user", "content": "Reply exactly OK"}]) == "OK"
    assert request_timeouts == [300.0, 60.0]


def test_structured_no_route_code_skips_each_unroutable_model_until_one_succeeds(monkeypatch):
    def provider() -> dict:
        return {
            **_provider(),
            "text_model_fallback_order": ["gpt-5.6-mini", "gpt-5.6-luna"],
        }

    no_route = {
        "error": {
            "code": "key_has_no_route_providers",
            "message": "No route is configured for this key",
        }
    }
    responses = [
        _Response(no_route, status_code=400),
        _Response(no_route, status_code=400),
        _Response({"choices": [{"message": {"content": "LUNA OK"}}]}),
    ]
    session = _Session(responses)
    monkeypatch.setattr(ai_client, "resolve_ai_provider", provider)
    monkeypatch.setattr(ai_client, "_HTTP_SESSION", session)

    result = ai_client.AiClient().chat([{"role": "user", "content": "Reply exactly OK"}])

    assert result == "LUNA OK"
    assert [request["json"]["model"] for request in session.requests] == [
        "gpt-5.6-terra",
        "gpt-5.6-mini",
        "gpt-5.6-luna",
    ]
    assert all(response.closed for response in responses)


def test_all_models_without_routes_expose_provider_code_to_batch_layer(monkeypatch):
    def provider() -> dict:
        return {**_provider(), "text_model_fallback_order": ["gpt-5.6-luna"]}

    no_route = {"error": {"code": ai_client.KEY_HAS_NO_ROUTE_PROVIDERS}}
    session = _Session(
        [_Response(no_route, status_code=400), _Response(no_route, status_code=400)]
    )
    monkeypatch.setattr(ai_client, "resolve_ai_provider", provider)
    monkeypatch.setattr(ai_client, "_HTTP_SESSION", session)

    with pytest.raises(ai_client.AiProviderError) as captured:
        ai_client.AiClient().chat([{"role": "user", "content": "Reply exactly OK"}])

    assert captured.value.status_code == 400
    assert captured.value.provider_code == ai_client.KEY_HAS_NO_ROUTE_PROVIDERS
    assert ai_client.is_key_no_route_provider_error(captured.value) is True
    assert len(session.requests) == 2


def test_concurrent_products_single_flight_unavailable_model_routes(monkeypatch):
    def provider() -> dict:
        return {**_provider(), "text_model_fallback_order": ["gpt-5.6-luna"]}

    no_route = {"error": {"code": ai_client.KEY_HAS_NO_ROUTE_PROVIDERS}}

    class SlowSession(_Session):
        def request(self, *args, **kwargs):
            time.sleep(0.03)
            return super().request(*args, **kwargs)

    session = SlowSession(
        [_Response(no_route, status_code=400), _Response(no_route, status_code=400)]
    )
    monkeypatch.setattr(ai_client, "resolve_ai_provider", provider)
    monkeypatch.setattr(ai_client, "_HTTP_SESSION", session)
    client = ai_client.AiClient()

    def call() -> str:
        with pytest.raises(ai_client.AiProviderError) as captured:
            client.chat([{"role": "user", "content": "Reply exactly OK"}])
        return captured.value.provider_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _index: call(), range(2)))

    assert codes == [ai_client.KEY_HAS_NO_ROUTE_PROVIDERS] * 2
    assert [request["json"]["model"] for request in session.requests] == [
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ]


def test_mixed_retryable_failures_do_not_claim_every_model_has_no_route(monkeypatch):
    def provider() -> dict:
        return {**_provider(), "text_model_fallback_order": ["gpt-5.6-luna"]}

    session = _Session(
        [
            _Response({"error": {"code": "upstream_unavailable"}}, status_code=500),
            _Response(
                {"error": {"code": ai_client.KEY_HAS_NO_ROUTE_PROVIDERS}},
                status_code=400,
            ),
        ]
    )
    monkeypatch.setattr(ai_client, "resolve_ai_provider", provider)
    monkeypatch.setattr(ai_client, "_HTTP_SESSION", session)

    with pytest.raises(ai_client.AiProviderError) as captured:
        ai_client.AiClient().chat([{"role": "user", "content": "Reply exactly OK"}])

    assert captured.value.status_code == 500
    assert captured.value.provider_code == "upstream_unavailable"
    assert ai_client.is_key_no_route_provider_error(captured.value) is False
    assert len(session.requests) == 2


def test_no_route_words_in_plain_error_text_are_not_misclassified(monkeypatch):
    def provider() -> dict:
        return {**_provider(), "text_model_fallback_order": ["gpt-5.6-luna"]}

    response = _Response(
        "key_has_no_route_providers appeared only in unstructured text",
        status_code=400,
    )
    session = _Session([response])
    monkeypatch.setattr(ai_client, "resolve_ai_provider", provider)
    monkeypatch.setattr(ai_client, "_HTTP_SESSION", session)

    with pytest.raises(ai_client.AiProviderError) as captured:
        ai_client.AiClient().chat([{"role": "user", "content": "Reply exactly OK"}])

    assert captured.value.provider_code == ""
    assert ai_client.is_key_no_route_provider_error(captured.value) is False
    assert len(session.requests) == 1


def test_process_http_pool_is_bounded_and_blocking() -> None:
    assert ai_client._HTTP_ADAPTER._pool_connections == ai_client.HTTP_POOL_CONNECTIONS
    assert ai_client._HTTP_ADAPTER._pool_maxsize == ai_client.HTTP_POOL_MAXSIZE
    assert ai_client._HTTP_ADAPTER._pool_block is True
    assert "Authorization" not in ai_client._HTTP_SESSION.headers


def test_connection_capacity_wait_uses_request_timeout_without_calling_provider(monkeypatch):
    class NoCapacity:
        def acquire(self, *, timeout):
            assert timeout == 25.0
            return False

        def release(self):
            raise AssertionError("unacquired capacity must not be released")

    session = _Session([])
    monkeypatch.setattr(ai_client, "resolve_ai_provider", _provider)
    monkeypatch.setattr(ai_client, "_HTTP_CAPACITY", NoCapacity())
    monkeypatch.setattr(ai_client, "_HTTP_SESSION", session)

    with pytest.raises(ai_client.AiProviderError, match="connection pool timed out"):
        ai_client.AiClient().chat([{"role": "user", "content": "Reply exactly OK"}])

    assert session.requests == []
