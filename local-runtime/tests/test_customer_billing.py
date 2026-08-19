from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.customer import contracts as customer_contracts
from wh_local.customer.auth_server import create_auth_app
from wh_local.customer.auth_service import _email_code_digest
from wh_local.customer.contracts import (
    CustomerAuthRejected,
    CustomerAuthResult,
    CustomerBillingPermissionError,
    CustomerBillingProtocolError,
)
from wh_local.customer.local_session import LocalSessionService
from wh_local.customer.remote_client import CustomerAuthClient
from wh_local.customer.routes import create_customer_router
import wh_local.billing as billing_module
from wh_local.db import transaction

_EMAIL_CODE_SECRET = "billing-test-secret-that-is-at-least-32-chars"


def test_remote_billing_protocol_has_a_dedicated_error_contract() -> None:
    assert hasattr(customer_contracts, "CustomerBillingProtocolError")


def test_product_processing_reserve_points_have_one_authoritative_accessor() -> None:
    assert billing_module.feature_reserve_points("product_processing.text") == 5
    assert billing_module.feature_reserve_points("product_processing.image_grid_2k") == 40


def test_remote_billing_permission_has_a_dedicated_error_contract() -> None:
    assert hasattr(customer_contracts, "CustomerBillingPermissionError")


def _register_and_login(client: TestClient, db_path: Path, *, username: str = "billing_user") -> str:
    verification_id = f"ver_{username}"
    email = f"{username}@example.test"
    email_code = "654321"
    with transaction(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO invitation_codes (code, max_uses, used_count, expires_at, created_by, created_at)
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
            "username": username,
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
        json={"username": username, "password": "StrongPassword123!"},
    )
    assert login.status_code == 200
    return login.json()["token"]


def _grant_points(db_path: Path, points: int = 1000, *, username: str = "billing_user") -> str:
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


def test_ai_usage_api_reserves_settles_and_reuses_idempotency(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    client = TestClient(create_auth_app(db_path))
    token = _register_and_login(client, db_path)
    account_id = _grant_points(db_path)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "feature_key": "product_processing.text",
        "idempotency_key": "product-processing-api-test-0001",
        "source_ref": "test:item",
        "metadata": {
            "task_id": 1,
            "item": {
                "item_id": "item-1",
                "pricing": {"points": 50, "currency": "points"},
                "accessToken": "nested-access-secret",
                "children": [
                    {"label": "safe", "provider_secret": "nested-provider-secret"},
                    {"refresh_token": "nested-refresh-secret"},
                ],
            },
            "api_key": "must-not-persist",
            "authorization": "Bearer must-not-persist",
            "sessionCookie": "nested-cookie-secret",
            "customerCredential": "nested-credential-secret",
        },
    }

    first = client.post("/api/customer/billing/usage/reserve", json=payload, headers=headers)
    repeated = client.post("/api/customer/billing/usage/reserve", json=payload, headers=headers)

    assert first.status_code == 200
    assert repeated.json()["usage"]["usage_id"] == first.json()["usage"]["usage_id"]

    usage_id = first.json()["usage"]["usage_id"]
    settled = client.post(
        f"/api/customer/billing/usage/{usage_id}/succeed",
        json={"metadata": {"task_id": 1}},
        headers=headers,
    )
    assert settled.status_code == 200
    assert settled.json()["usage"]["status"] == "succeeded"

    with transaction(db_path) as conn:
        usage = conn.execute(
            "SELECT account_id, metadata_json FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
    assert usage["account_id"] == account_id
    stored_metadata = json.loads(usage["metadata_json"])
    serialized_metadata = json.dumps(stored_metadata)
    assert stored_metadata["task_id"] == 1
    assert stored_metadata["item"]["item_id"] == "item-1"
    assert stored_metadata["item"]["pricing"] == {"points": 50, "currency": "points"}
    assert stored_metadata["item"]["children"] == [{"label": "safe"}, {}]
    for secret in (
        "must-not-persist",
        "nested-access-secret",
        "nested-provider-secret",
        "nested-refresh-secret",
        "nested-cookie-secret",
        "nested-credential-secret",
    ):
        assert secret not in serialized_metadata


def test_ai_usage_api_rejects_invalid_reservation_payload(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr("wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env", lambda: object())
    client = TestClient(create_auth_app(db_path))
    token = _register_and_login(client, db_path)
    _grant_points(db_path)
    headers = {"Authorization": f"Bearer {token}"}

    unsupported = client.post(
        "/api/customer/billing/usage/reserve",
        json={"feature_key": "unsupported", "idempotency_key": "valid-idempotency-key"},
        headers=headers,
    )
    short_key = client.post(
        "/api/customer/billing/usage/reserve",
        json={"feature_key": "product_processing.text", "idempotency_key": "too-short"},
        headers=headers,
    )

    assert unsupported.status_code == 400
    assert short_key.status_code == 400


def test_ai_usage_api_hides_other_accounts_usage_and_releases_failed_hold(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr("wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env", lambda: object())
    client = TestClient(create_auth_app(db_path))
    owner_token = _register_and_login(client, db_path)
    owner_id = _grant_points(db_path, points=1000)
    other_token = _register_and_login(client, db_path, username="other_billing_user")
    headers = {"Authorization": f"Bearer {owner_token}"}
    reservation = client.post(
        "/api/customer/billing/usage/reserve",
        json={"feature_key": "product_processing.text", "idempotency_key": "other-account-usage-0001"},
        headers=headers,
    )
    assert reservation.status_code == 200
    usage_id = reservation.json()["usage"]["usage_id"]

    other_settlement = client.post(
        f"/api/customer/billing/usage/{usage_id}/succeed",
        json={},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    failed = client.post(
        f"/api/customer/billing/usage/{usage_id}/fail",
        json={"error_message": "provider rejected request"},
        headers=headers,
    )

    assert other_settlement.status_code == 404
    assert failed.status_code == 200
    with transaction(db_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (owner_id,),
        ).fetchone()
    assert dict(wallet) == {"points_balance": 1000, "locked_points": 0}


def test_remote_client_posts_authoritative_usage_with_remote_session(monkeypatch) -> None:
    client = CustomerAuthClient()
    posted: list[tuple[str, dict[str, object], dict[str, str] | None]] = []

    def fake_post(path: str, payload: dict[str, object], headers: dict[str, str] | None = None) -> dict[str, object]:
        posted.append((path, payload, headers))
        return {"ok": True}

    monkeypatch.setattr(client, "_post", fake_post)

    assert client.reserve_ai_usage("remote-token", {"feature_key": "product_processing.text"}) == {"ok": True}
    assert client.settle_ai_usage_success("remote-token", "use_123", {}) == {"ok": True}
    assert client.settle_ai_usage_failure("remote-token", "use_123", {}) == {"ok": True}
    with pytest.raises(
        CustomerBillingPermissionError,
        match="remote billing session was rejected",
    ):
        client.reserve_ai_usage("", {})

    assert [item[0] for item in posted] == [
        "/api/customer/billing/usage/reserve",
        "/api/customer/billing/usage/use_123/succeed",
        "/api/customer/billing/usage/use_123/fail",
    ]
    assert all(item[2] == {"Authorization": "Bearer remote-token"} for item in posted)


@pytest.mark.parametrize(
    "invalid_remote_result",
    [
        [],
        json.JSONDecodeError("remote-token api-key payload", "not-json", 0),
    ],
)
def test_remote_billing_client_normalizes_invalid_json_and_envelopes(
    monkeypatch,
    invalid_remote_result: object,
) -> None:
    client = CustomerAuthClient("https://customer.example.test")

    def invalid_post(*_args, **_kwargs):
        if isinstance(invalid_remote_result, BaseException):
            raise invalid_remote_result
        return invalid_remote_result

    monkeypatch.setattr(client, "_post", invalid_post)

    with pytest.raises(CustomerBillingProtocolError) as caught:
        client.reserve_ai_usage("remote-token", {"feature_key": "product_processing.text"})

    assert str(caught.value) == "remote billing service returned an invalid response"
    assert "remote-token" not in str(caught.value)


def test_remote_billing_client_rejects_malformed_remote_error_status(monkeypatch) -> None:
    client = CustomerAuthClient("https://customer.example.test")
    malformed = CustomerAuthRejected.__new__(CustomerAuthRejected)
    RuntimeError.__init__(malformed, "remote-token api-key payload")
    malformed.status_code = 500
    malformed.message = "remote-token api-key payload"
    monkeypatch.setattr(
        client,
        "_post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(malformed),
    )

    with pytest.raises(CustomerBillingProtocolError) as caught:
        client.reserve_ai_usage("remote-token", {"feature_key": "product_processing.text"})

    assert str(caught.value) == "remote billing service returned an invalid response"


def test_remote_billing_client_normalizes_non_utf8_http_response(monkeypatch) -> None:
    client = CustomerAuthClient("https://customer.example.test")

    class NonUtf8Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        @staticmethod
        def read() -> bytes:
            return b"\xff\xfe\xfd"

    monkeypatch.setattr(
        "wh_local.customer.remote_client.urlopen",
        lambda *_args, **_kwargs: NonUtf8Response(),
    )

    with pytest.raises(CustomerBillingProtocolError) as caught:
        client.reserve_ai_usage("remote-token", {"feature_key": "product_processing.text"})

    assert str(caught.value) == "remote billing service returned an invalid response"


def test_remote_billing_client_normalizes_remote_permission_error(monkeypatch) -> None:
    client = CustomerAuthClient("https://customer.example.test")
    monkeypatch.setattr(
        client,
        "_post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("remote secret")),
    )

    with pytest.raises(CustomerBillingPermissionError) as caught:
        client.reserve_ai_usage("remote-token", {"feature_key": "product_processing.text"})

    assert str(caught.value) == "remote billing session was rejected"
    assert "remote secret" not in str(caught.value)


def _customer_router_client(remote_auth: object) -> tuple[TestClient, str]:
    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="billing-user",
            remote_token="remote-session-token",
        )
    )
    app = FastAPI()
    app.include_router(create_customer_router(remote_auth, sessions))
    return TestClient(app), session.token


@pytest.mark.parametrize(
    ("status_code", "detail"),
    [(402, "insufficient points"), (404, "usage event not found")],
)
def test_customer_router_preserves_remote_billing_rejection_status(
    monkeypatch,
    status_code: int,
    detail: str,
) -> None:
    remote_auth = CustomerAuthClient("https://customer.example.test")

    def reject_remote_request(*_args, **_kwargs):
        raise HTTPError(
            "https://customer.example.test/api/customer/billing/topup-orders",
            status_code,
            "rejected",
            None,
            BytesIO(f'{{"detail": "{detail}"}}'.encode("utf-8")),
        )

    monkeypatch.setattr("wh_local.customer.remote_client.urlopen", reject_remote_request)
    client, local_token = _customer_router_client(remote_auth)

    response = client.post(
        "/api/customer/billing/topup-orders",
        json={"package_id": "points_10"},
        headers={"Authorization": f"Bearer {local_token}"},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


def test_customer_router_returns_503_when_development_fallback_has_no_billing_summary() -> None:
    client, local_token = _customer_router_client(object())

    response = client.get(
        "/api/customer/billing/summary",
        headers={"Authorization": f"Bearer {local_token}"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "remote billing service is not configured"}
