# -*- coding: utf-8 -*-
"""并发治理：全局 AI 请求速率限制器 + 任务级串行闸门接入点测试。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from wh_local.modules.product_processing import ai_client as ai_client_module
from wh_local.modules.product_processing.infrastructure import media as media_module
from wh_local.modules.product_processing.infrastructure import rate_limit
from wh_local.modules.product_processing.infrastructure.media import ProductImageProcessor


def test_rate_zero_does_not_block(monkeypatch) -> None:
    monkeypatch.setenv("WH_PRODUCT_AI_RATE_PER_MINUTE", "0")
    rate_limit.reset_limiter()
    limiter = rate_limit.global_ai_request_limiter()
    start = time.monotonic()
    for _ in range(5):
        limiter.acquire()
    assert time.monotonic() - start < 0.5


def test_rate_limits_requests_per_minute(monkeypatch) -> None:
    """60 次/分钟 = 每秒 1 个令牌，连续两次 acquire 需等待约 1 秒。"""
    monkeypatch.setenv("WH_PRODUCT_AI_RATE_PER_MINUTE", "60")
    rate_limit.reset_limiter()
    limiter = rate_limit.global_ai_request_limiter()
    start = time.monotonic()
    limiter.acquire()  # 桶满，立即通过
    limiter.acquire()  # 令牌耗尽，需等补一个
    assert time.monotonic() - start >= 0.8


def test_ai_client_send_waits_on_global_limiter(monkeypatch) -> None:
    calls: list[str] = []
    fake = rate_limit._TokenBucket(0.0)  # 不限速，仅记录调用
    monkeypatch.setattr(fake, "acquire", lambda: calls.append("limiter"))
    monkeypatch.setattr(ai_client_module, "global_ai_request_limiter", lambda: fake)
    monkeypatch.setattr(ai_client_module, "_HTTP_CAPACITY", threading.Semaphore(4))

    def _fake_request(method, url, *, headers, json, timeout):  # noqa: ARG001
        body = b'{"choices":[{"message":{"content":"OK"}}]}'
        return SimpleNamespace(content=body, status_code=200, close=lambda: None)

    fake_session = SimpleNamespace(request=_fake_request)
    monkeypatch.setattr(ai_client_module, "_HTTP_SESSION", fake_session)
    client = ai_client_module.AiClient.__new__(ai_client_module.AiClient)
    client.base_url = "https://example.test/v1"
    client.api_key = "test-key"
    client.timeout_seconds = 10.0
    result = client._send("POST", "/chat/completions", payload={"messages": []})
    assert result["choices"][0]["message"]["content"] == "OK"
    assert calls == ["limiter"]


def test_media_request_edit_waits_on_global_limiter(monkeypatch) -> None:
    calls: list[str] = []
    fake = rate_limit._TokenBucket(0.0)
    monkeypatch.setattr(fake, "acquire", lambda: calls.append("limiter"))
    monkeypatch.setattr(media_module, "global_ai_request_limiter", lambda: fake)

    class _FakeResponse:
        ok = True

        def json(self) -> dict:
            return {"data": [{"b64_json": "aGVsbG8="}]}

        def close(self) -> None:
            return

    monkeypatch.setattr(media_module._SESSION, "post", lambda *a, **k: _FakeResponse())
    result = ProductImageProcessor._request_edit(
        {"base_url": "https://example.test", "api_key": "k", "model": "m", "reference_model": ""},
        "prompt",
        [("x".encode(), "ref.png", "image/png")],
    )
    assert result[1] == "image/png"
    assert calls == ["limiter"]
