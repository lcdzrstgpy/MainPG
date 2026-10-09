"""collect-key 必须按会话 token 认身份（P0：零鉴权下发上游采集凭据）。

回归覆盖两件事：
1. 匿名请求即使带上「已注册用户名」也必须被拒 —— 这正是原来的漏洞路径；
2. 身份**只能**来自 token，body 里自填的 account_id 一律忽略。

判别力说明：旧实现下「带有效 token + 假 account_id」会命中
`401 user is not registered on the server`；新实现会走到解密步骤并返回
`400 cannot decrypt session key`。用这两个错误码即可区分新旧。
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wh_local.customer.auth_server import create_auth_app
from wh_local.db import transaction

_EMAIL_CODE_SECRET = "collect-key-test-secret-that-is-at-least-32-chars"
_INVITE_CODE = "MAINPG-COLLECT-KEY-TEST"


def _register_and_login(client: TestClient, db_path: Path, *, username: str) -> str:
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
                    _EMAIL_CODE_SECRET, verification_id, email, "register", email_code
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
            "workspace_code": "collect-ws",
        },
    ).status_code == 200
    login = client.post(
        "/api/customer/login",
        json={"username": username, "password": "StrongPassword123!"},
    )
    assert login.status_code == 200
    return login.json()["token"]


def _setup(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    db_path = tmp_path / "auth.sqlite3"
    client = TestClient(create_auth_app(db_path))
    return client, db_path


def test_collect_key_rejects_anonymous_request_even_with_valid_username(
    tmp_path: Path, monkeypatch
) -> None:
    """原漏洞：只要知道一个有效用户名就能拿到 OneBound api_key/api_secret。"""
    client, db_path = _setup(tmp_path, monkeypatch)
    _register_and_login(client, db_path, username="victim")

    response = client.post(
        "/api/customer/collect-key",
        json={
            "account_id": "",
            "username": "victim",
            "workspace_code": "",
            "encrypted_session_key": "AAAA",
        },
    )
    assert response.status_code in (401, 403), response.text
    body = response.json()
    assert "payload" not in body, f"匿名请求竟然返回了加密凭据载荷: {body}"


def test_collect_key_identity_comes_from_token_not_body(
    tmp_path: Path, monkeypatch
) -> None:
    """body 里自填的 account_id 必须被忽略，身份以 token 为准。"""
    client, db_path = _setup(tmp_path, monkeypatch)
    token = _register_and_login(client, db_path, username="alice")

    # 假 account_id：旧实现会走「按 account_id 查不到用户」→ 401 user is not registered；
    # 新实现忽略它、按 token 认到 alice，继续走到解密步骤（测试里没配密钥 → 400）。
    response = client.post(
        "/api/customer/collect-key",
        json={
            "account_id": "cust_does_not_exist",
            "username": "nobody",
            "workspace_code": "",
            "encrypted_session_key": "AAAA",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400, response.text
    detail = str(response.json().get("detail") or "")
    assert "cannot decrypt session key" in detail, detail
    assert "not registered" not in detail
