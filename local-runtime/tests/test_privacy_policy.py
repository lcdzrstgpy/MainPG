# -*- coding: utf-8 -*-
"""隐私政策文档转发：本地只做缓存中转，不落库。

上游契约（服务端设计文档 4.2）：GET /privacy.json
    {"schema": 1, "version": "v1.2", "public_url": "...", "body_markdown": "..."}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.customer import routes as routes_module
from wh_local.customer.local_session import LocalSessionService, MemoryCustomerSessionStore
from wh_local.customer.routes import create_customer_router


class _RemoteStub:
    """本路由不需要上游账号服务，占位即可。"""


def _build() -> TestClient:
    sessions = LocalSessionService(MemoryCustomerSessionStore())
    app = FastAPI()
    app.include_router(create_customer_router(_RemoteStub(), sessions))
    return TestClient(app)


def _reset_cache(document: dict[str, Any] | None = None, at: float = 0.0) -> None:
    routes_module._privacy_policy_cache["at"] = at
    routes_module._privacy_policy_cache["document"] = document


def test_returns_document_and_caches_it(monkeypatch) -> None:
    _reset_cache()
    calls = {"count": 0}

    def fake_fetch() -> dict[str, Any]:
        calls["count"] += 1
        return {"schema": 1, "version": "v1.3", "public_url": "https://example.test/privacy"}

    monkeypatch.setattr(routes_module, "_fetch_privacy_policy_document", fake_fetch)
    client = _build()

    first = client.get("/api/customer/privacy-policy")
    second = client.get("/api/customer/privacy-policy")

    assert first.status_code == 200
    assert first.json()["ok"] is True
    assert first.json()["document"]["version"] == "v1.3"
    assert first.json()["cached"] is False
    # 5 分钟缓存内不再打远端
    assert second.json()["cached"] is True
    assert calls["count"] == 1


def test_falls_back_to_stale_document_when_upstream_fails(monkeypatch) -> None:
    _reset_cache(document={"version": "v1.2"}, at=0.0)  # 过期缓存
    monkeypatch.setattr(routes_module, "_fetch_privacy_policy_document", lambda: None)
    client = _build()

    response = client.get("/api/customer/privacy-policy")

    assert response.status_code == 200
    payload = response.json()
    # 远端失败仍把上一版给前端：宁可展示旧政策，也不能把登录页卡住
    assert payload["ok"] is True
    assert payload["document"]["version"] == "v1.2"
    assert payload["cached"] is True


def test_returns_not_ok_when_no_document_at_all(monkeypatch) -> None:
    _reset_cache()
    monkeypatch.setattr(routes_module, "_fetch_privacy_policy_document", lambda: None)
    client = _build()

    payload = client.get("/api/customer/privacy-policy").json()

    assert payload["ok"] is False
    assert payload["document"] is None
