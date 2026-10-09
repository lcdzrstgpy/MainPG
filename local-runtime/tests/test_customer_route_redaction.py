import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.customer.contracts import (
    CustomerAuthActionResult,
    CustomerAuthRejected,
    CustomerAuthResult,
)
from wh_local.customer.local_session import LocalSessionService
from wh_local.customer.routes import create_customer_router


class SecretBearingCustomerAuth:
    def login(self, _payload):
        return CustomerAuthResult(
            customer_id="customer-1",
            username="operator",
            email="operator@example.test",
            account_status="active",
            login_status="online",
            remote_token="remote-login-secret",
            remote_expires_at="2099-01-01T00:00:00Z",
            role="operator",
            workspace_code="workspace-1",
            workspace_name="Workspace One",
            raw={
                "access_token": "upstream-access-secret",
                "Authorization": "Bearer upstream-bearer-secret",
                "unexpected": "must-not-cross-the-public-contract",
            },
        )

    @staticmethod
    def _action(_payload):
        return CustomerAuthActionResult(
            ok=True,
            message="accepted",
            raw={
                "session_token": "upstream-action-secret",
                "unexpected": "must-not-cross-the-public-contract",
            },
        )

    register = _action
    activate = _action
    email_code = _action
    password_reset = _action
    change_password = _action
    forgot_password = _action
    reset_password = _action


def _client(remote_auth) -> TestClient:
    app = FastAPI()
    app.include_router(create_customer_router(remote_auth, LocalSessionService()))
    return TestClient(app)


def test_login_returns_local_token_and_only_public_account_fields() -> None:
    response = _client(SecretBearingCustomerAuth()).post(
        "/api/customer/login",
        json={"username": "operator", "password": "password"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token"].startswith("wh_local_")
    assert body["token"] != "remote-login-secret"
    assert body["account"] == {
        "customer_id": "customer-1",
        "username": "operator",
        "email": "operator@example.test",
        "account_status": "active",
        "login_status": "online",
        "role": "operator",
        "workspace_code": "workspace-1",
        "workspace_name": "Workspace One",
    }
    encoded_account = json.dumps(body["account"], sort_keys=True)
    for forbidden in (
        "remote_token",
        "raw",
        "access_token",
        "session_token",
        "Authorization",
        "upstream-access-secret",
        "upstream-bearer-secret",
        "must-not-cross-the-public-contract",
    ):
        assert forbidden not in encoded_account


@pytest.mark.parametrize(
    "path",
    [
        "/api/customer/register",
        "/api/customer/activate",
        "/api/customer/email-code",
        "/api/customer/password-reset",
        "/api/customer/change-password",
        "/api/customer/forgot-password",
        "/api/customer/reset-password",
    ],
)
def test_customer_actions_do_not_return_upstream_raw_payload(path: str) -> None:
    response = _client(SecretBearingCustomerAuth()).post(path, json={})

    assert response.status_code == 200
    assert response.json() == {"ok": True, "message": "accepted"}


class RejectingCustomerAuth:
    def login(self, _payload):
        raise CustomerAuthRejected(
            401,
            "Authorization: Bearer upstream-bearer-secret remote_token=remote-secret token=plain-secret",
        )


def test_customer_route_redacts_credentials_from_error_details() -> None:
    response = _client(RejectingCustomerAuth()).post(
        "/api/customer/login",
        json={"username": "operator", "password": "password"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": "Authorization: Bearer [redacted] remote_token=[redacted] token=[redacted]"
    }
