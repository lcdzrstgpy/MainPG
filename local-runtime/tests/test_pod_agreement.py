# -*- coding: utf-8 -*-
"""POD 使用协议确认：本地只做转发，不落库。

上游契约（产品约定）：POST /api/customer/pod-agreement
    {"username": "<登录用户名>", "agreed": true}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.customer.contracts import CustomerAuthResult, CustomerAuthUnavailable
from wh_local.customer.local_session import LocalSessionService, MemoryCustomerSessionStore
from wh_local.customer.remote_client import normalize_login_response
from wh_local.customer.routes import create_customer_router


class _RemoteStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = False

    def submit_pod_agreement(
        self, remote_token: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if self.fail:
            raise CustomerAuthUnavailable("network down")
        self.calls.append((remote_token, payload))
        return {"ok": True}


def _build(*, remote_token: str = "remote-secret-token"):
    sessions = LocalSessionService(MemoryCustomerSessionStore())
    token = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="operator",
            role="operator",
            workspace_code="workspace-1",
            remote_token=remote_token,
        )
    ).token
    remote = _RemoteStub()
    app = FastAPI()
    app.include_router(create_customer_router(remote, sessions))
    return TestClient(app), token, remote


def _confirm(client: TestClient, token: str, agreed: Any = True):
    return client.post(
        "/api/customer/pod-agreement",
        json={"agreed": agreed},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_confirm_is_forwarded_with_username_and_agreed() -> None:
    client, token, remote = _build()

    response = _confirm(client, token)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "reported": True}
    assert remote.calls == [
        ("remote-secret-token", {"username": "operator", "agreed": True})
    ]


def test_remote_failure_is_reported_as_not_uploaded() -> None:
    client, token, remote = _build()
    remote.fail = True

    response = _confirm(client, token)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "reported": False}


def test_missing_remote_token_does_not_require_remote_call() -> None:
    client, token, remote = _build(remote_token="")

    response = _confirm(client, token)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "reported": False}
    assert remote.calls == []


def test_requires_login_session() -> None:
    client, _token, remote = _build()

    response = client.post("/api/customer/pod-agreement", json={"agreed": True})

    assert response.status_code == 401
    assert remote.calls == []


def test_agreed_must_be_boolean() -> None:
    client, token, remote = _build()

    response = _confirm(client, token, agreed="yes")

    assert response.status_code == 400
    assert remote.calls == []


# ---- 登录响应里的确认状态：true 时前端不再弹窗 ----


def test_login_response_reads_confirmed_flag_top_level() -> None:
    result = normalize_login_response(
        {"token": "t", "username": "operator", "pod_agreement_confirmed": True}
    )

    assert result.pod_agreement_confirmed is True


def test_login_response_reads_confirmed_flag_inside_account() -> None:
    result = normalize_login_response(
        {
            "token": "t",
            "account": {"username": "operator", "pod_agreement_confirmed": True},
        }
    )

    assert result.pod_agreement_confirmed is True


def test_login_response_accepts_confirmation_token() -> None:
    """服务端若以「确认 token」形式下发，非空即视为已确认。"""
    result = normalize_login_response(
        {"token": "t", "username": "operator", "pod_agreement_token": "pod-ok-123"}
    )

    assert result.pod_agreement_confirmed is True


def test_login_response_defaults_to_not_confirmed() -> None:
    result = normalize_login_response({"token": "t", "username": "operator"})

    assert result.pod_agreement_confirmed is False


def test_login_route_exposes_confirmation_to_frontend() -> None:
    class _ConfirmedAuth:
        def login(self, payload: dict[str, Any]) -> CustomerAuthResult:
            return CustomerAuthResult(
                customer_id="customer-1",
                username="operator",
                remote_token="remote-token",
                pod_agreement_confirmed=True,
            )

    app = FastAPI()
    app.include_router(
        create_customer_router(
            _ConfirmedAuth(), LocalSessionService(MemoryCustomerSessionStore())
        )
    )

    response = TestClient(app).post(
        "/api/customer/login", json={"username": "operator", "password": "p"}
    )

    assert response.status_code == 200
    assert response.json()["account"]["pod_agreement_confirmed"] is True
