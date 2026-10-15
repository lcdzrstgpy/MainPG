from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from wh_local.customer import auth_server
from wh_local.customer.auth_server import create_auth_app
from wh_local.customer.auth_service import _email_code_digest
from wh_local.db import transaction


_EMAIL_CODE_SECRET = "pod-grant-hardening-test-secret-32-chars"
_INVITE_CODE = "POD-GRANT-HARDENING"


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path, rsa.RSAPrivateKey]:
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    monkeypatch.setattr(
        "wh_local.customer.auth_server._server_provider_secret",
        lambda kind, _environment: f"test-{kind}-provider-secret",
    )
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    monkeypatch.setenv("WH_AUTH_RSA_PRIVATE_KEY", private_pem)
    database_path = tmp_path / "pod-grant-hardening.sqlite3"
    return TestClient(create_auth_app(database_path)), database_path, private_key


def _register_login_and_fund(
    client: TestClient,
    database_path: Path,
) -> tuple[dict[str, str], str]:
    verification_id = "ver_pod_grant_hardening"
    email = "pod-grant-hardening@example.test"
    code = "654321"
    with transaction(database_path) as conn:
        conn.execute(
            """
            INSERT INTO invitation_codes
                (code, max_uses, used_count, expires_at, created_by, created_at)
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
                    code,
                ),
            ),
        )
    registered = client.post(
        "/api/customer/register",
        json={
            "username": "pod_grant_hardening",
            "email": email,
            "email_code": code,
            "password": "StrongPassword123!",
            "invitation_code": _INVITE_CODE,
            "workspace_code": "pod-grant-hardening-ws",
        },
    )
    assert registered.status_code == 200, registered.text
    login = client.post(
        "/api/customer/login",
        json={"username": "pod_grant_hardening", "password": "StrongPassword123!"},
    )
    assert login.status_code == 200, login.text
    account_id = str(login.json()["account"]["account_id"])
    headers = {"Authorization": f"Bearer {login.json()['token']}"}
    with transaction(database_path) as conn:
        workspace_id = str(
            conn.execute(
                "SELECT workspace_id FROM auth_accounts WHERE account_id = ?",
                (account_id,),
            ).fetchone()["workspace_id"]
        )
        conn.execute(
            """
            INSERT INTO billing_wallets (account_id, workspace_id, points_balance)
            VALUES (?, ?, 1000)
            ON CONFLICT(account_id) DO UPDATE SET points_balance = 1000
            """,
            (account_id, workspace_id),
        )
    pricing = client.put(
        "/api/admin/billing/pricing/pod",
        headers=headers,
        json={
            "items": {
                "pod.title": {"charge_points": 1.5},
                "pod.image": {"charge_points": 3.2},
            },
            "change_reason": "test POD pricing",
        },
    )
    assert pricing.status_code == 200, pricing.text
    return headers, account_id


def _encrypted_session_key(private_key: rsa.RSAPrivateKey) -> str:
    encrypted = private_key.public_key().encrypt(
        b"s" * 32,
        asymmetric_padding.OAEP(
            mgf=asymmetric_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(encrypted).decode("ascii")


def _freeze_payload(private_key: rsa.RSAPrivateKey) -> dict[str, object]:
    return {
        "idempotency_key": "pod-grant-hardening-idempotency-0001",
        "title_call_count": 1,
        "image_call_count": 1,
        "calls": [
            {"call_id": "title-grant-hardening", "feature": "pod.title"},
            {"call_id": "image-grant-hardening", "feature": "pod.image"},
        ],
        "encrypted_session_key": _encrypted_session_key(private_key),
    }


def _insert_partial_grant_then_fail(
    database_path: Path,
    account: dict[str, object],
    freeze_id: str,
    *_args,
) -> None:
    with transaction(database_path) as conn:
        conn.execute(
            """
            INSERT INTO billing_key_grants (
                grant_id, account_id, workspace_id, freeze_id, provider,
                key_label, granted_at, expires_at
            ) VALUES (
                'grant_partial_failure', ?, ?, ?, 'ark',
                'ark:text:short-lived', datetime('now'), '9999-12-31T00:00:00+00:00'
            )
            """,
            (account["account_id"], account["workspace_id"], freeze_id),
        )
    raise RuntimeError("sensitive-provider-error-must-not-escape")


def test_freeze_grant_failure_releases_lock_revokes_audit_and_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    headers, account_id = _register_login_and_fund(client, database_path)
    monkeypatch.setattr(
        auth_server,
        "_issue_pod_grant_envelope",
        _insert_partial_grant_then_fail,
    )

    failed = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload(private_key),
    )

    assert failed.status_code == 503
    assert failed.json()["detail"] == "POD grant issuance failed; reserved points were released"
    assert "sensitive-provider-error" not in failed.text
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        freeze = conn.execute(
            "SELECT status, charged_points, refunded_points FROM billing_batch_freezes"
        ).fetchone()
        calls = conn.execute(
            "SELECT status FROM billing_pod_calls ORDER BY ordinal"
        ).fetchall()
        grants = conn.execute(
            "SELECT revoked_at FROM billing_key_grants"
        ).fetchall()
        compensation = conn.execute(
            "SELECT status, error_code FROM billing_pod_grant_compensations"
        ).fetchone()
    assert dict(wallet) == {"points_balance": 1000, "locked_points": 0}
    assert dict(freeze) == {"status": "settled", "charged_points": 0, "refunded_points": 47}
    assert [row["status"] for row in calls] == ["no_return", "no_return"]
    assert grants and all(str(row["revoked_at"]) for row in grants)
    assert dict(compensation) == {"status": "compensated", "error_code": "grant_issuance_failed"}

    retried = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload(private_key),
    )
    assert retried.status_code == 409
    with transaction(database_path) as conn:
        wallet_after_retry = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        freezes = conn.execute("SELECT COUNT(*) AS total FROM billing_batch_freezes").fetchone()
    assert dict(wallet_after_retry) == {"points_balance": 1000, "locked_points": 0}
    assert freezes["total"] == 1


def test_failed_compensation_is_persisted_and_recovered_on_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    headers, account_id = _register_login_and_fund(client, database_path)
    monkeypatch.setattr(
        auth_server,
        "_issue_pod_grant_envelope",
        _insert_partial_grant_then_fail,
    )
    original_settle = auth_server.settle_pod_points
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("simulated settlement outage")
        return original_settle(*args, **kwargs)

    monkeypatch.setattr(auth_server, "settle_pod_points", fail_once)
    failed = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload(private_key),
    )

    assert failed.status_code == 503
    assert failed.json()["detail"] == "POD grant issuance failed; compensation is pending"
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        compensation = conn.execute(
            "SELECT status, error_code, attempt_count FROM billing_pod_grant_compensations"
        ).fetchone()
        grant = conn.execute("SELECT revoked_at FROM billing_key_grants").fetchone()
    assert dict(wallet) == {"points_balance": 1000, "locked_points": 47}
    assert compensation["status"] == "pending"
    assert compensation["error_code"] == "compensation_retry_failed"
    assert compensation["attempt_count"] >= 1
    assert grant["revoked_at"]

    TestClient(create_auth_app(database_path))

    with transaction(database_path) as conn:
        recovered_wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        recovered = conn.execute(
            "SELECT status, error_code FROM billing_pod_grant_compensations"
        ).fetchone()
        freeze = conn.execute("SELECT status FROM billing_batch_freezes").fetchone()
    assert dict(recovered_wallet) == {"points_balance": 1000, "locked_points": 0}
    assert dict(recovered) == {"status": "compensated", "error_code": "grant_issuance_failed"}
    assert freeze["status"] == "settled"


def test_freeze_and_regrant_require_current_create_permission_but_status_and_settle_do_not(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    headers, account_id = _register_login_and_fund(client, database_path)
    payload = _freeze_payload(private_key)
    created = client.post("/api/customer/billing/pod/freeze", headers=headers, json=payload)
    assert created.status_code == 200, created.text
    freeze_id = str(created.json()["freeze"]["freeze_id"])
    with transaction(database_path) as conn:
        grant_count = conn.execute(
            "SELECT COUNT(*) AS total FROM billing_key_grants WHERE freeze_id = ?",
            (freeze_id,),
        ).fetchone()["total"]
        conn.execute(
            "UPDATE auth_accounts SET role = 'viewer' WHERE account_id = ?",
            (account_id,),
        )

    denied_regrant = client.post(
        f"/api/customer/billing/pod/{freeze_id}/regrant",
        headers=headers,
        json={"encrypted_session_key": _encrypted_session_key(private_key)},
    )
    denied_freeze = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json={**payload, "idempotency_key": "pod-denied-after-role-change-0002"},
    )

    assert denied_regrant.status_code == 403
    assert denied_regrant.json()["detail"] == "permission required: pod_customization.create"
    assert denied_freeze.status_code == 403
    assert denied_freeze.json()["detail"] == "permission required: pod_customization.create"
    assert "grant_envelope" not in denied_regrant.text + denied_freeze.text
    with transaction(database_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS total FROM billing_key_grants WHERE freeze_id = ?",
            (freeze_id,),
        ).fetchone()["total"] == grant_count
        assert conn.execute(
            "SELECT COUNT(*) AS total FROM billing_batch_freezes"
        ).fetchone()["total"] == 1

    status = client.get(f"/api/customer/billing/pod/{freeze_id}", headers=headers)
    settled = client.post(
        "/api/customer/billing/pod/settle",
        headers=headers,
        json={
            "freeze_id": freeze_id,
            "items": [
                {"call_id": "title-grant-hardening", "feature": "pod.title", "status": "no_return"},
                {"call_id": "image-grant-hardening", "feature": "pod.image", "status": "no_return"},
            ],
        },
    )
    assert status.status_code == 200
    assert settled.status_code == 200, settled.text
    assert settled.json()["settle"]["refunded_points"] == 4.7
