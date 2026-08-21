"""阶段4：服务端↔客户端全链路 mock 联调（密钥发放审计/脱敏/TTL + 直连消费 + 结算）。

全程使用假 provider 密钥与 mock HTTP，绝不触发任何真实付费 AI 调用：
- 服务端：freeze 下发 6h 时效密钥，billing_key_grants 只存掩码 label
- 客户端：granted key 注入 DoubaoArk 直连 / provider_config 无印直连
- 结算：按子项明细扣费，明细条数与 link_count 不一致 → 400
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from requests import Response

import wh_local.billing as billing_module
from wh_local.customer.auth_server import create_auth_app
from wh_local.db import transaction
from wh_local.modules.product_processing import doubao_ark
from wh_local.modules.product_processing import provider_config as provider_config_module
from wh_local.modules.product_processing import server_ai_proxy

_JSON = json

_EMAIL_CODE_SECRET = "full-chain-test-secret-that-is-at-least-32-chars"
_INVITE_CODE = "MAINPG-FULL-CHAIN"
_ARK_SECRET = "ark-mock-secret-7890"
_WUYIN_SECRET = "wuyin-mock-secret-1234"


def _register_and_login(client: TestClient, db_path: Path, *, username: str = "chain_user") -> str:
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
                _email_code_digest(_EMAIL_CODE_SECRET, verification_id, email, "register", email_code),
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
            "workspace_code": "chain-ws",
        },
    ).status_code == 200
    login = client.post(
        "/api/customer/login",
        json={"username": username, "password": "StrongPassword123!"},
    )
    assert login.status_code == 200
    return login.json()["token"]


def _grant_points(db_path: Path, points: int = 10000, *, username: str = "chain_user") -> str:
    with transaction(db_path) as conn:
        account = conn.execute(
            "SELECT account_id, workspace_id FROM auth_accounts WHERE username = ?",
            (username,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO billing_wallets (account_id, workspace_id, points_balance)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET points_balance = excluded.points_balance
            """,
            (account["account_id"], account["workspace_id"], points),
        )
    return str(account["account_id"])


def _full_success_items(link_count: int) -> list[dict[str, Any]]:
    return [
        {
            "subitems": [
                {"feature": "title", "status": "success"},
                {"feature": "description", "status": "success"},
                {"feature": "product_dimensions", "status": "success"},
                {"feature": "four_grid", "status": "success"},
                {"feature": "detail_images", "status": "success"},
            ]
        }
        for _ in range(link_count)
    ]


def _fake_ark_post(recorded: dict[str, Any]):
    def fake_post(url: str, *, headers: dict[str, str], json: dict[str, Any], **_: Any):
        recorded["url"] = url
        recorded["auth"] = headers.get("Authorization")
        recorded["model"] = json.get("model")
        response = Response()
        response.status_code = 200
        response._content = _JSON.dumps(
            {"choices": [{"message": {"content": '{"optimized_title":"T","description":"D","variant_translations":[],"product_dimensions":{}}'}}]}
        ).encode("utf-8")
        return response

    return fake_post


def test_full_chain_freeze_grants_direct_consume_and_settle(tmp_path: Path, monkeypatch) -> None:
    """服务端 freeze 下发双 provider 密钥 → 审计行掩码 → 客户端直连消费 → settle 扣费。"""
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setenv("WH_TEXT_API_KEY", _ARK_SECRET)
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", _WUYIN_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    db_path = tmp_path / "auth.sqlite3"
    client = TestClient(create_auth_app(db_path))
    token = _register_and_login(client, db_path)
    account_id = _grant_points(db_path, username="chain_user")
    headers = {"Authorization": f"Bearer {token}"}

    # ---- 服务端冻结：返回密钥 + 服务端 expires_at（6h 时效）----
    freeze = client.post(
        "/api/customer/billing/batch/freeze",
        json={"link_count": 2, "idempotency_key": "full-chain-freeze-0001"},
        headers=headers,
    )
    assert freeze.status_code == 200
    payload = freeze.json()["freeze"]
    freeze_id = payload["freeze_id"]
    assert payload["link_count"] == 2
    assert payload["frozen_points"] == 90

    keys = {key["provider"]: key for key in payload["keys"]}
    assert set(keys) == {"ark", "wuyin"}
    assert keys["ark"]["api_key"] == _ARK_SECRET
    assert keys["wuyin"]["api_key"] == _WUYIN_SECRET
    expires_at = datetime.fromisoformat(keys["ark"]["expires_at"])
    assert (expires_at - datetime.now(timezone.utc)).total_seconds() > 5 * 3600  # 约 6h
    assert keys["ark"]["kind"] == "text"
    assert keys["wuyin"]["kind"] == "image"

    # ---- 审计：billing_key_grants 只存掩码 label，不含明文 ----
    with transaction(db_path) as conn:
        audit = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM billing_key_grants WHERE freeze_id = ?",
                (freeze_id,),
            ).fetchall()
        ]
    assert len(audit) == 2
    audit_by_provider = {row["provider"]: row for row in audit}
    assert audit_by_provider["ark"]["key_label"] == f"ark:text::{_ARK_SECRET[-4:]}"
    assert audit_by_provider["wuyin"]["key_label"] == f"wuyin:image::{_WUYIN_SECRET[-4:]}"
    for row in audit:
        assert str(row["account_id"]) == account_id
        serialized = json.dumps(dict(row))
        assert _ARK_SECRET not in serialized and _WUYIN_SECRET not in serialized

    # admin grants 接口同样只暴露掩码 label
    grants = client.get("/api/admin/billing/keys/grants", headers=headers)
    assert grants.status_code == 200
    grant_items = grants.json()["items"]
    assert len(grant_items) == 2
    for item in grant_items:
        assert "api_key" not in item
        assert item["key_label"].endswith("::" + (item["provider"] == "ark" and _ARK_SECRET or _WUYIN_SECRET)[-4:])

    # ---- 客户端：granted key 注入直连消费（mock 上游，不触发真实调用）----
    granted = {provider: entry["api_key"] for provider, entry in keys.items()}
    recorded: dict[str, Any] = {}
    monkeypatch.setattr(doubao_ark._HTTP_SESSION, "post", _fake_ark_post(recorded))
    with server_ai_proxy.server_ai_context(token, {}, granted_keys=granted, freeze_id=freeze_id):
        content = doubao_ark.DoubaoArkClient().complete([{"role": "user", "content": "hi"}])
        assert content.startswith('{"optimized_title"')
        assert recorded["url"].startswith("https://ark.cn-beijing.volces.com/api/v3/chat/completions")
        assert recorded["auth"] == f"Bearer {_ARK_SECRET}"
        assert recorded["model"] == doubao_ark.MODEL_ID

        provider = provider_config_module.resolve_ai_provider()
        assert provider["direct_mode"] is True
        sys_image = provider["_sys_image_ai"]
        assert sys_image["base_url"] == "https://api.wuyinkeji.com"
        assert sys_image["api_key"] == _WUYIN_SECRET

    # ---- 结算：2 条全成功 → 扣 90 全价，无退款 ----
    settle = client.post(
        "/api/customer/billing/batch/settle",
        json={"freeze_id": freeze_id, "items": _full_success_items(2)},
        headers=headers,
    )
    assert settle.status_code == 200
    result = settle.json()["settle"]
    assert result["status"] == "settled"
    assert result["charged_points"] == 90
    assert result["refunded_points"] == 0

    # 幂等重放
    repeated = client.post(
        "/api/customer/billing/batch/settle",
        json={"freeze_id": freeze_id, "items": _full_success_items(2)},
        headers=headers,
    )
    assert repeated.json()["settle"]["already_settled"] is True


def test_batch_route_enables_random_prices_only_for_pod_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    picks = iter((0, 5))
    monkeypatch.setattr(billing_module.secrets, "randbelow", lambda upper: next(picks))
    db_path = tmp_path / "auth.sqlite3"
    client = TestClient(create_auth_app(db_path))
    token = _register_and_login(client, db_path)
    _grant_points(db_path, username="chain_user")
    headers = {"Authorization": f"Bearer {token}"}

    product = client.post(
        "/api/customer/billing/batch/freeze",
        json={"link_count": 1, "idempotency_key": "product-random-isolation-0001"},
        headers=headers,
    )
    assert product.status_code == 200
    assert product.json()["freeze"]["billing_profile"] == "product_processing"
    assert product.json()["freeze"]["frozen_points"] == 45
    assert product.json()["freeze"]["link_prices"] == []

    pod = client.post(
        "/api/customer/billing/batch/freeze",
        json={
            "link_count": 2,
            "scope": ["title", "four_grid"],
            "idempotency_key": "pod:batch:route-random-0001",
            "billing_profile": "pod_random_v1",
        },
        headers=headers,
    )
    assert pod.status_code == 200, pod.text
    assert pod.json()["freeze"]["billing_profile"] == "pod_random_v1"
    assert pod.json()["freeze"]["link_prices"] == [40, 45]
    assert pod.json()["freeze"]["frozen_points"] == 85

    invalid = client.post(
        "/api/customer/billing/batch/freeze",
        json={
            "link_count": 1,
            "idempotency_key": "invalid-billing-profile-0001",
            "billing_profile": "client_selected_price",
        },
        headers=headers,
    )
    assert invalid.status_code == 400


def test_settle_rejects_item_count_mismatch(tmp_path: Path, monkeypatch) -> None:
    """风险#20：结算明细条数与冻结 link_count 不一致 → 400，防止漏报/多报逃费。"""
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    db_path = tmp_path / "auth.sqlite3"
    client = TestClient(create_auth_app(db_path))
    token = _register_and_login(client, db_path)
    _grant_points(db_path, username="chain_user")
    headers = {"Authorization": f"Bearer {token}"}

    freeze = client.post(
        "/api/customer/billing/batch/freeze",
        json={"link_count": 2},
        headers=headers,
    ).json()["freeze"]

    # 只报 1 条（漏报一半）→ 400
    short = client.post(
        "/api/customer/billing/batch/settle",
        json={"freeze_id": freeze["freeze_id"], "items": _full_success_items(1)},
        headers=headers,
    )
    assert short.status_code == 400
    assert "link_count" in short.json()["detail"]

    # 多报（3 条 > 2）→ 400
    over = client.post(
        "/api/customer/billing/batch/settle",
        json={"freeze_id": freeze["freeze_id"], "items": _full_success_items(3)},
        headers=headers,
    )
    assert over.status_code == 400

    # 冻结仍处于 frozen，锁未被错误释放
    status = client.get(f"/api/customer/billing/batch/{freeze['freeze_id']}", headers=headers)
    assert status.status_code == 200
    assert status.json()["freeze"]["status"] == "frozen"
