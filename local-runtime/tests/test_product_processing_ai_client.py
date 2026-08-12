from __future__ import annotations

import json

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
        "image_timeout_seconds": 90,
    }


class _Response:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._body


def test_station_requests_use_non_default_user_agent(monkeypatch):
    requests = []

    def fake_urlopen(request, *, timeout):
        requests.append(request)
        if request.full_url.endswith("/models"):
            return _Response({"data": [{"id": "gpt-5.6-terra"}]})
        return _Response({"choices": [{"message": {"content": "OK"}}]})

    monkeypatch.setattr(ai_client, "resolve_ai_provider", _provider)
    monkeypatch.setattr(ai_client.urllib.request, "urlopen", fake_urlopen)

    client = ai_client.AiClient()
    assert client.chat([{"role": "user", "content": "Reply exactly OK"}]) == "OK"
    assert client.ping()["ok"] is True
    assert len(requests) == 2
    assert all(request.get_header("User-agent") == ai_client.AI_USER_AGENT for request in requests)
