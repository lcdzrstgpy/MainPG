from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wh_local.customer.auth_server import create_auth_app
from wh_local.customer.auth_service import _email_code_digest
from wh_local.db import transaction

_EMAIL_CODE_SECRET = "billing-test-secret-that-is-at-least-32-chars"


def _register_and_login(client: TestClient, db_path: Path) -> str:
    verification_id = "ver_billing_test"
    email = "billing@example.test"
    email_code = "654321"
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO invitation_codes (code, max_uses, used_count, expires_at, created_by, created_at)
            VALUES ('MAINPG-BILL-TEST', 10, 0, '', 'test', datetime('now'))
            """
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
    response = client.post(
        "/api/customer/register",
        json={
            "username": "billing_user",
            "email": email,
            "email_code": email_code,
            "password": "StrongPassword123!",
            "invitation_code": "MAINPG-BILL-TEST",
            "workspace_code": "billing-ws",
        },
    )
    assert response.status_code == 200
    login = client.post(
        "/api/customer/login",
        json={"username": "billing_user", "password": "StrongPassword123!"},
    )
    assert login.status_code == 200
    return login.json()["token"]


def test_billing_summary_requires_server_session(tmp_path: Path) -> None:
    client = TestClient(create_auth_app(tmp_path / "auth.sqlite3"))

    response = client.get("/api/customer/billing/summary")

    assert response.status_code == 401


def test_billing_topup_order_is_pending_and_idempotent(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    client = TestClient(create_auth_app(db_path))
    token = _register_and_login(client, db_path)
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "wechat",
            "package_id": "points_10",
            "idempotency_key": "idem_billing_test_0001",
        },
        headers=headers,
    )
    second = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "wechat",
            "package_id": "points_10",
            "idempotency_key": "idem_billing_test_0001",
        },
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["order"]["status"] == "pending"
    assert second.json()["reused"] is True
    assert second.json()["order"]["order_id"] == first.json()["order"]["order_id"]

    summary = client.get("/api/customer/billing/summary", headers=headers).json()
    assert summary["wallet"]["available_points"] == 0
    assert summary["recent_orders"][0]["status"] == "pending"


def test_unverified_payment_callback_fails_closed(tmp_path: Path) -> None:
    client = TestClient(create_auth_app(tmp_path / "auth.sqlite3"))

    response = client.post(
        "/api/customer/billing/payment-callback/wechat",
        json={"out_trade_no": "fake", "status": "paid"},
    )

    assert response.status_code == 503
