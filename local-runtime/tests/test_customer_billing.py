from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wh_local.customer.auth_server import create_auth_app
from wh_local.db import transaction


def _register_and_login(client: TestClient, db_path: Path) -> str:
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO invitation_codes (code, max_uses, used_count, expires_at, created_by, created_at)
            VALUES ('MAINPG-BILL-TEST', 10, 0, '', 'test', datetime('now'))
            """
        )
    response = client.post(
        "/api/customer/register",
        json={
            "username": "billing_user",
            "email": "billing@example.test",
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


def test_billing_topup_order_is_pending_and_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "auth.sqlite3"
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
