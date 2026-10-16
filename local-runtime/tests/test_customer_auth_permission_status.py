"""会话失效的 HTTP 状态码契约：远端 401 必须原样透传到前端。

背景
----
远端 customer-auth 在会话被撤销（他端登录顶替 / 登出 / 改密）时回 401 +
detail ``"session revoked, account signed in on another device"``。
``remote_client.py`` 据此抛 ``CustomerAuthPermissionError(status, detail)``，
意图是"透传 detail，前端据此给出明确提示并回登录页"。

``routes.py::handle_auth_error`` 曾把它匹配到兜底的
``isinstance(exc, PermissionError) -> 403`` 分支（本类继承 ``PermissionError``），
上游的 401 被丢掉。而前端 ``transport/http/client.ts`` 的 ``isSessionExpired()``
是 ``response.status === 401 && isSessionExpiredDetail(detail)`` —— 状态码不对，
关键词再对也永远不触发 ``auth:session-expired``，用户只会看到"操作失败"。

修复：在 ``PermissionError`` 兜底分支**之前**单独匹配 ``CustomerAuthPermissionError``
并回它自带的 ``status_code``。

注意 detail 此前就是正确的，这里的用例把"状态码"和"detail"分开断言 ——
只看文案会漏掉这个 bug。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.customer.contracts import (
    CustomerAuthPermissionError,
    CustomerAuthResult,
    CustomerBillingPermissionError,
)
from wh_local.customer.db_store import SQLiteCustomerSessionStore
from wh_local.customer.local_session import LocalSessionService
from wh_local.customer.remote_client import CustomerAuthClient
from wh_local.customer.routes import create_customer_router
from wh_local.db import init_db

REVOKED_DETAIL = "session revoked, account signed in on another device"
# 前端 client.ts 的会话失效关键词表，必须能在 detail 里命中。
SESSION_EXPIRED_KEYWORDS = ("session revoked", "login session expired", "invalid bearer token", "missing bearer token")


class _RemoteAuthStub:
    """只覆写被测路由用到的远端方法；其余调用即失败。"""

    def __init__(self, *, status_code: int, message: str) -> None:
        self._status_code = status_code
        self._message = message

    def billing_summary(self, _remote_token: str) -> Any:
        raise CustomerAuthPermissionError(self._status_code, self._message)


def _build_client(tmp_path, *, status_code: int, message: str) -> tuple[TestClient, str]:
    database = tmp_path / "workbench.sqlite3"
    init_db(database)
    sessions = LocalSessionService(SQLiteCustomerSessionStore(database))
    token = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="operator",
            role="operator",
            workspace_code="workspace-1",
            remote_token="remote-secret-token",
        )
    ).token
    app = FastAPI()
    app.include_router(
        create_customer_router(
            _RemoteAuthStub(status_code=status_code, message=message), sessions
        )
    )
    return TestClient(app, raise_server_exceptions=False), token


def _call(client: TestClient, token: str):
    return client.get(
        "/api/customer/billing/summary",
        headers={"Authorization": f"Bearer {token}"},
    )


def test_revoked_remote_session_maps_to_401(tmp_path) -> None:
    """远端会话被顶替：本地必须回 401，否则前端不会自动回登录页。"""
    client, token = _build_client(tmp_path, status_code=401, message=REVOKED_DETAIL)
    response = _call(client, token)

    assert response.status_code == 401, (
        f"期望 401（前端只认 401 才判会话失效），实际 {response.status_code}，"
        f"body={response.text[:200]}"
    )
    assert any(kw in response.text for kw in SESSION_EXPIRED_KEYWORDS), (
        f"detail 应命中前端关键词表，实际 body={response.text[:200]}"
    )


def test_revoked_remote_session_detail_is_not_folded(tmp_path) -> None:
    """上游原文不能被折叠成固定话术，否则前端关键词匹配同样失效。"""
    client, token = _build_client(tmp_path, status_code=401, message=REVOKED_DETAIL)
    response = _call(client, token)
    assert REVOKED_DETAIL in response.text, (
        f"上游 detail 被改写，实际 body={response.text[:200]}"
    )


def test_upstream_403_stays_403(tmp_path) -> None:
    """上游真的是 403（账号停用等）时仍须保持 403，不能一律抬成 401。"""
    client, token = _build_client(
        tmp_path, status_code=403, message="customer account is not active"
    )
    response = _call(client, token)
    assert response.status_code == 403, (
        f"期望 403，实际 {response.status_code}，body={response.text[:200]}"
    )


def test_permission_error_keeps_returning_403(tmp_path) -> None:
    """普通 PermissionError 仍走兜底 403（本次改动不得放开这条）。"""
    database = tmp_path / "workbench.sqlite3"
    init_db(database)
    sessions = LocalSessionService(SQLiteCustomerSessionStore(database))
    token = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="operator",
            role="operator",
            workspace_code="workspace-1",
            remote_token="remote-secret-token",
        )
    ).token

    class _PlainPermissionRemote:
        def billing_summary(self, _remote_token: str) -> Any:
            raise PermissionError("plain permission failure")

    app = FastAPI()
    app.include_router(create_customer_router(_PlainPermissionRemote(), sessions))
    client = TestClient(app, raise_server_exceptions=False)
    response = _call(client, token)
    assert response.status_code == 403, (
        f"普通 PermissionError 应为 403，实际 {response.status_code}"
    )


# ---------------------------------------------------------------------------
# 客户端分类层：会话失效信号必须活着穿过 _get / _request / _billing_result
#
# 链路：远端 401 -> CustomerAuthClient._get（GET）或 _request（POST）分类
#       -> _billing_result 包装 -> routes.handle_auth_error -> 前端
# 过去两处都会把它折成 CustomerBillingPermissionError（403 + 固定话术），
# 前端硬要求 401 + 关键词，于是永不回登录页。
# ---------------------------------------------------------------------------


class _FakeHttpResponse:
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.text = body


def _stub_client(status_code: int, body: str) -> CustomerAuthClient:
    """真实 CustomerAuthClient + 打桩 HTTP 层（不打桩分类逻辑）。"""
    client = CustomerAuthClient("https://customer.example.test")
    client._session.request = lambda *a, **k: _FakeHttpResponse(status_code, body)  # type: ignore[assignment]
    return client


def test_get_path_session_401_survives_as_auth_permission_error() -> None:
    """GET 计费端点（走 _get）收到 401 + session revoked 必须原样上传。"""
    client = _stub_client(401, f'{{"detail":"{REVOKED_DETAIL}"}}')
    with pytest.raises(CustomerAuthPermissionError) as caught:
        client.billing_summary("remote-token")
    assert caught.value.status_code == 401
    assert "session revoked" in str(caught.value), (
        f"detail 被折叠成固定话术：{caught.value}"
    )


def test_post_path_session_401_survives_billing_result_wrapper() -> None:
    """POST 计费端点（走 _request + _billing_result）同样不能被折成 Billing 版。"""
    client = _stub_client(401, f'{{"detail":"{REVOKED_DETAIL}"}}')
    with pytest.raises(CustomerAuthPermissionError) as caught:
        client.claim_daily_extra("remote-token")
    assert caught.value.status_code == 401


@pytest.mark.parametrize(
    "detail",
    ["invalid bearer token", "missing bearer token"],
)
def test_all_whitelisted_session_details_pass_through(detail: str) -> None:
    """白名单里的会话短语都应透传（前端关键词表逐条对应）。"""
    client = _stub_client(401, f'{{"detail":"{detail}"}}')
    with pytest.raises(CustomerAuthPermissionError) as caught:
        client.billing_summary("remote-token")
    assert caught.value.status_code == 401
    assert detail in str(caught.value)


def test_get_path_non_session_401_still_folds_to_billing_error() -> None:
    """非白名单 401 不得透传 —— 计费侧不泄露上游细节，也不误触发登出。"""
    client = _stub_client(401, '{"detail":"authenticated workspace required"}')
    with pytest.raises(CustomerBillingPermissionError) as caught:
        client.billing_summary("remote-token")
    assert caught.value.status_code == 401
    assert "authenticated workspace required" not in str(caught.value), (
        "非会话类上游细节不应外泄到文案里"
    )


def test_get_path_upstream_403_still_folds_to_billing_error() -> None:
    """上游 403 仍按计费权限错误处理（403 + 固定话术）。"""
    client = _stub_client(403, '{"detail":"customer account is not active"}')
    with pytest.raises(CustomerBillingPermissionError) as caught:
        client.billing_summary("remote-token")
    assert caught.value.status_code == 403


def test_billing_route_end_to_end_returns_401_with_detail(tmp_path) -> None:
    """端到端：真实 client + 真实 router，GET 计费端点必须回 401 且保留关键词。"""
    database = tmp_path / "workbench.sqlite3"
    init_db(database)
    sessions = LocalSessionService(SQLiteCustomerSessionStore(database))
    token = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="operator",
            role="operator",
            workspace_code="workspace-1",
            remote_token="remote-secret-token",
        )
    ).token

    app = FastAPI()
    app.include_router(
        create_customer_router(
            _stub_client(401, f'{{"detail":"{REVOKED_DETAIL}"}}'), sessions
        )
    )
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/api/customer/billing/summary",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401, (
        f"期望 401，实际 {response.status_code}，body={response.text[:200]}"
    )
    assert "session revoked" in response.text
    assert "remote billing session was rejected" not in response.text, (
        "计费固定话术不得覆盖会话失效原文"
    )


# ---------------------------------------------------------------------------
# 兜底：POD / 商品处理 / combo_kit 的 _call 包装只捕 CustomerBillingPermissionError，
# 而 CustomerAuthPermissionError 继承 PermissionError，不在它们的兜底链里 —— 会逃逸出
# 路由。必须有 app 级 handler 接住并映回 401，否则会话失效会从 403 退化成 500。
# ---------------------------------------------------------------------------


def test_pod_call_lets_customer_auth_permission_error_escape() -> None:
    """固化"模块 _call 接不住"这一事实 —— 正因如此才需要 app 级兜底。"""
    from wh_local.modules.pod_customization import router as pod_router

    def _boom(*_args, **_kwargs):
        raise CustomerAuthPermissionError(401, REVOKED_DETAIL)

    with pytest.raises(CustomerAuthPermissionError):
        pod_router._call(_boom)


def test_product_processing_call_lets_customer_auth_permission_error_escape() -> None:
    """商品处理侧同理。"""
    from wh_local.modules.product_processing.api import router as pp_router

    def _boom(*_args, **_kwargs):
        raise CustomerAuthPermissionError(401, REVOKED_DETAIL)

    with pytest.raises(CustomerAuthPermissionError):
        pp_router._call(_boom)


def test_app_registers_global_handler_mapping_to_upstream_status(tmp_path) -> None:
    """app 必须注册全局 handler，把它映回上游状态码 + 原文（而不是 500）。"""
    from wh_local.app.main import create_app

    app = create_app(tmp_path / "workbench.sqlite3")
    assert CustomerAuthPermissionError in app.exception_handlers, (
        "缺少全局 handler：POD/商品处理路径的会话失效会变成 500"
    )

    @app.get("/__probe__/customer-auth-permission")
    def _probe() -> None:
        raise CustomerAuthPermissionError(401, REVOKED_DETAIL)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__probe__/customer-auth-permission")
    assert response.status_code == 401, (
        f"全局 handler 未生效，实际 {response.status_code}，body={response.text[:200]}"
    )
    assert "session revoked" in response.text


def test_app_global_handler_respects_upstream_403(tmp_path) -> None:
    """上游 403 不能被抬成 401（否则会把停用账号误判成会话失效）。"""
    from wh_local.app.main import create_app

    app = create_app(tmp_path / "workbench.sqlite3")

    @app.get("/__probe__/customer-auth-permission-403")
    def _probe() -> None:
        raise CustomerAuthPermissionError(403, "customer account is not active")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__probe__/customer-auth-permission-403")
    assert response.status_code == 403, (
        f"上游 403 应保持 403，实际 {response.status_code}"
    )


