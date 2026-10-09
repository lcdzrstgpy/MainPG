from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.customer.admin_proxy import create_admin_proxy_router
from wh_local.customer.auth_server import create_auth_app
from wh_local.customer.contracts import LocalSession
from wh_local.db import transaction


_EMAIL_CODE_SECRET = "admin-pricing-test-secret-that-is-at-least-32-chars"
_INVITE_CODE = "MAINPG-ADMIN-TEST"
_EXPECTED_SUBITEMS = {"title": 8, "description": 8, "product_dimensions": 7, "four_grid": 12, "detail_images": 10}


def _register_and_login(client: TestClient, db_path: Path, *, username: str = "admin_user") -> str:
    from wh_local.customer.auth_service import _email_code_digest

    email = f"{username}@example.test"
    verification_id = f"ver_{username}"
    email_code = "654321"
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO invitation_codes (code, max_uses, used_count, expires_at, created_by, created_at)
            VALUES (?, 10, 0, '', 'test', datetime('now'))
            """,
            (_INVITE_CODE,),
        )
        conn.execute(
            """
            INSERT INTO auth_email_verifications (
                verification_id, email, token_hash, purpose, expires_at
            ) VALUES (?, ?, ?, 'register', '9999-12-31T00:00:00+00:00')
            """,
            (
                verification_id,
                email,
                _email_code_digest(
                    _EMAIL_CODE_SECRET,
                    verification_id,
                    email,
                    "register",
                    email_code,
                ),
            ),
        )
    assert client.post(
        "/api/customer/register",
        json={
            "username": username,
            "email": email,
            "email_code": email_code,
            "password": "StrongPassword123!",
            "invitation_code": _INVITE_CODE,
            "workspace_code": "admin-ws",
        },
    ).status_code == 200
    login = client.post(
        "/api/customer/login",
        json={"username": username, "password": "StrongPassword123!"},
    )
    assert login.status_code == 200
    return login.json()["token"]


def test_admin_pricing_items_routes_on_auth_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr("wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env", lambda: object())
    client = TestClient(create_auth_app(tmp_path / "auth.sqlite3"))
    token = _register_and_login(client, tmp_path / "auth.sqlite3")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/api/admin/billing/pricing/items", headers=headers)
    assert response.status_code == 200
    pricing = response.json()["pricing"]
    initial_version = pricing["rule_version"]
    assert set(pricing["items"].keys()) == set(_EXPECTED_SUBITEMS.keys())
    for key, points in _EXPECTED_SUBITEMS.items():
        assert pricing["items"][key]["charge_points"] == points
    assert pricing["max_charge_per_link"] == 45
    assert pricing["freeze_per_link"] == 45

    # 缺 change_reason → 400
    rejected = client.put(
        "/api/admin/billing/pricing/items",
        json={"items": {"title": {"charge_points": 8}}},
        headers=headers,
    )
    assert rejected.status_code == 400

    # 修改 title 8 -> 10（合计 47 超过上限 → 400）
    over = dict(_EXPECTED_SUBITEMS)
    over["title"] = 10
    over_limit = client.put(
        "/api/admin/billing/pricing/items",
        json={"items": {k: {"charge_points": v} for k, v in over.items()}, "change_reason": "over limit"},
        headers=headers,
    )
    assert over_limit.status_code == 400

    # 合法修改：description 8 -> 6（合计 43）
    changed = dict(_EXPECTED_SUBITEMS)
    changed["description"] = 6
    updated = client.put(
        "/api/admin/billing/pricing/items",
        json={"items": {k: {"charge_points": v} for k, v in changed.items()}, "change_reason": "desc cheaper"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["pricing"]["rule_version"] == initial_version + 1
    assert updated.json()["pricing"]["items"]["description"]["charge_points"] == 6

    # 变更日志追加且含 before/after（拒绝的请求不产生日志）
    log = client.get("/api/admin/billing/pricing/changelog", headers=headers)
    assert log.status_code == 200
    entries = log.json()["items"]
    assert len(entries) == 1
    assert entries[0]["change_reason"] == "desc cheaper"
    assert entries[0]["before"]["items"]["description"]["charge_points"] == 8
    assert entries[0]["after"]["items"]["description"]["charge_points"] == 6

    # 密钥发放记录路由可用（空表）
    grants = client.get("/api/admin/billing/keys/grants", headers=headers)
    assert grants.status_code == 200
    assert grants.json()["items"] == []


def test_admin_pricing_routes_require_billing_admin(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr("wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env", lambda: object())
    db_path = tmp_path / "auth.sqlite3"
    client = TestClient(create_auth_app(db_path))

    # 未知 token → 401
    headers = {"Authorization": "Bearer unknown-token"}
    assert client.get("/api/admin/billing/pricing/items", headers=headers).status_code == 401

    # operator 角色即使持有有效 token 也 → 403（直接验证鉴权函数）
    from fastapi import HTTPException
    from wh_local.customer import auth_server

    monkeypatch.setattr(
        auth_server,
        "_required_account",
        lambda database_path, authorization: {"account_id": "op-1", "role": "operator", "username": "operator_user"},
    )
    with pytest.raises(HTTPException) as exc_info:
        auth_server._require_billing_admin(db_path, "Bearer operator-token")
    assert exc_info.value.status_code == 403


def test_workbench_admin_proxy_forwards_only_billing_paths() -> None:
    class RecordingAuth:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str, object | None]] = []

        def admin_request(self, remote_token: str, method: str, path: str, payload: dict | None = None):
            self.calls.append((remote_token, method, path, payload))
            return {"ok": True, "path": path, "method": method, "payload": payload}

    class StubStore:
        def __init__(self, session: LocalSession | None) -> None:
            self._session = session

        def get_session(self, token: str):
            return self._session if token == "local-token" else None

    class StubSessions:
        def __init__(self, session: LocalSession | None) -> None:
            self.store = StubStore(session)

    session = LocalSession(
        user_id="u1",
        token="local-token",
        expires_at="9999-12-31T00:00:00+00:00",
        username="admin_user",
        remote_token="remote-token-abc",
    )
    auth = RecordingAuth()
    app = FastAPI()
    app.include_router(create_admin_proxy_router(auth, StubSessions(session)))
    client = TestClient(app)

    # GET 转发并携带远端 token
    response = client.get("/api/admin/billing/pricing/items", headers={"Authorization": "Bearer local-token"})
    assert response.status_code == 200
    assert auth.calls[-1][0] == "remote-token-abc"
    assert auth.calls[-1][1] == "GET"
    assert auth.calls[-1][2] == "/api/admin/billing/pricing/items"

    # PUT 携带 payload 转发
    response = client.put(
        "/api/admin/billing/pricing/items",
        headers={"Authorization": "Bearer local-token"},
        json={"items": {}, "change_reason": "test"},
    )
    assert response.status_code == 200
    assert auth.calls[-1][3] == {"items": {}, "change_reason": "test"}

    # 非 billing 前缀不转发
    assert client.get("/api/admin/other", headers={"Authorization": "Bearer local-token"}).status_code == 404
    assert len(auth.calls) == 2

    # 缺 token / 错误 token 拒绝
    assert client.get("/api/admin/billing/pricing/items").status_code == 401
    assert client.get("/api/admin/billing/pricing/items", headers={"Authorization": "Bearer wrong"}).status_code == 403
