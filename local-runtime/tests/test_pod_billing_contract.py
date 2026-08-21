from __future__ import annotations

import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi import HTTPException
from fastapi.testclient import TestClient

from wh_local.customer.auth_server import create_auth_app
from wh_local.customer.auth_service import _email_code_digest
from wh_local.db import transaction
from wh_local import pod_billing as pod_billing_module
from wh_local.session import Actor


_EMAIL_CODE_SECRET = "pod-billing-test-secret-that-is-at-least-32-chars"
_INVITE_CODE = "MAINPG-POD-BILL"
_ARK_SENTINEL = "ark-pod-secret-must-stay-inside-envelope"
_WUYIN_SENTINEL = "wuyin-pod-secret-must-stay-inside-envelope"


def _register_and_login(
    client: TestClient,
    database_path: Path,
    *,
    username: str = "pod_billing_user",
    workspace_code: str = "pod-billing-ws",
) -> tuple[str, str]:
    verification_id = f"ver_{username}"
    email = f"{username}@example.test"
    email_code = "654321"
    with transaction(database_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO invitation_codes
                (code, max_uses, used_count, expires_at, created_by, created_at)
            VALUES (?, 20, 0, '', 'test', datetime('now'))
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
    response = client.post(
        "/api/customer/register",
        json={
            "username": username,
            "email": email,
            "email_code": email_code,
            "password": "StrongPassword123!",
            "invitation_code": _INVITE_CODE,
            "workspace_code": workspace_code,
        },
    )
    assert response.status_code == 200
    login = client.post(
        "/api/customer/login",
        json={"username": username, "password": "StrongPassword123!"},
    )
    assert login.status_code == 200
    return login.json()["token"], login.json()["account"]["account_id"]


def _grant_points(database_path: Path, account_id: str, *, units: int = 1000) -> None:
    with transaction(database_path) as conn:
        account = conn.execute(
            "SELECT workspace_id FROM auth_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO billing_wallets (account_id, workspace_id, points_balance)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET points_balance = excluded.points_balance
            """,
            (account_id, account["workspace_id"], units),
        )


def _configure_rsa(monkeypatch) -> rsa.RSAPrivateKey:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    monkeypatch.setenv("WH_AUTH_RSA_PRIVATE_KEY", private_pem)
    return private_key


def _encrypted_session(private_key: rsa.RSAPrivateKey) -> tuple[str, bytes]:
    session_key = b"p" * 32
    encrypted = private_key.public_key().encrypt(
        session_key,
        asymmetric_padding.OAEP(
            mgf=asymmetric_padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(encrypted).decode("ascii"), session_key


def _decrypt_envelope(envelope: dict[str, str], session_key: bytes) -> dict:
    decryptor = Cipher(
        algorithms.AES(session_key),
        modes.GCM(base64.b64decode(envelope["nonce"]), base64.b64decode(envelope["tag"])),
    ).decryptor()
    plaintext = decryptor.update(base64.b64decode(envelope["payload"])) + decryptor.finalize()
    return json.loads(plaintext.decode("utf-8"))


def _freeze_payload(encrypted_session_key: str, *, idempotency_key: str = "pod-freeze-stable-key-0001") -> dict:
    return {
        "idempotency_key": idempotency_key,
        "title_call_count": 1,
        "image_call_count": 2,
        "calls": [
            {"call_id": "title-call-0001", "feature": "pod.title"},
            {"call_id": "image-call-0001", "feature": "pod.image"},
            {"call_id": "image-call-0002", "feature": "pod.image"},
        ],
        "encrypted_session_key": encrypted_session_key,
    }


def _configure_pod_pricing(client: TestClient, headers: dict[str, str]) -> int:
    response = client.put(
        "/api/admin/billing/pricing/pod",
        headers=headers,
        json={
            "items": {
                "pod.title": {"charge_points": 1.5},
                "pod.image": {"charge_points": 3.2},
            },
            "change_reason": "configure POD launch pricing",
        },
    )
    assert response.status_code == 200, response.text
    pricing = response.json()["pricing"]
    assert pricing["point_unit_scale"] == 10
    assert pricing["items"]["pod.title"]["charge_units"] == 15
    assert pricing["items"]["pod.image"]["charge_units"] == 32
    product_pricing = client.get(
        "/api/admin/billing/pricing/items",
        headers=headers,
    ).json()["pricing"]
    assert product_pricing["max_charge_per_link"] == 45
    assert "pod.title" not in product_pricing["items"]
    return pricing["rule_version"]


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path, rsa.RSAPrivateKey]:
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    monkeypatch.setattr(
        "wh_local.customer.auth_server._server_provider_secret",
        lambda kind, _environment: _ARK_SENTINEL if kind == "text" else _WUYIN_SENTINEL,
    )
    database_path = tmp_path / "pod-billing.sqlite3"
    return TestClient(create_auth_app(database_path)), database_path, _configure_rsa(monkeypatch)


def test_pod_freeze_fails_closed_until_both_prices_are_configured(tmp_path: Path, monkeypatch) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id)
    encrypted_session_key, _ = _encrypted_session(private_key)

    response = client.post(
        "/api/customer/billing/pod/freeze",
        headers={"Authorization": f"Bearer {token}"},
        json=_freeze_payload(encrypted_session_key),
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "POD pricing is not configured"


def test_pod_freeze_accepts_200_style_plan_and_rejects_1001_calls(tmp_path: Path, monkeypatch) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id, units=100_000)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)
    encrypted_session_key, _ = _encrypted_session(private_key)
    calls = [
        {
            "call_id": f"batch-max:style:{style}:image:{attempt}",
            "feature": "pod.image",
        }
        for style in range(1, 201)
        for attempt in range(1, 3)
    ] + [
        {
            "call_id": f"batch-max:style:{style}:title:{attempt}",
            "feature": "pod.title",
        }
        for style in range(1, 201)
        for attempt in range(1, 4)
    ]

    accepted = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json={
            "idempotency_key": "pod-batch-max-200-styles",
            "title_call_count": 600,
            "image_call_count": 400,
            "calls": calls,
            "encrypted_session_key": encrypted_session_key,
        },
    )
    rejected = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json={
            "idempotency_key": "pod-batch-over-maximum",
            "title_call_count": 601,
            "image_call_count": 400,
            "calls": [
                *calls,
                {"call_id": "batch-max:style:201:title:1", "feature": "pod.title"},
            ],
            "encrypted_session_key": encrypted_session_key,
        },
    )

    assert accepted.status_code == 200, accepted.text
    assert len(accepted.json()["freeze"]["calls"]) == 1000
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "POD total call count must be 1..1000"


def test_pod_freeze_rejects_invalid_envelope_before_locking_points(tmp_path: Path, monkeypatch) -> None:
    client, database_path, _ = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)

    response = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload("not-a-valid-rsa-envelope"),
    )

    assert response.status_code == 400
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        freezes = conn.execute("SELECT COUNT(*) AS total FROM billing_pod_freezes").fetchone()
    assert wallet["locked_points"] == 0
    assert freezes["total"] == 0


def test_pod_plan_persistence_failure_rolls_back_the_wallet_lock(tmp_path: Path, monkeypatch) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)
    encrypted_session_key, _ = _encrypted_session(private_key)
    assert hasattr(pod_billing_module, "_persist_pod_plan"), "POD plan writer must be transactional"
    monkeypatch.setattr(
        pod_billing_module,
        "_persist_pod_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated crash")),
    )
    failure_client = TestClient(client.app, raise_server_exceptions=False)

    response = failure_client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload(encrypted_session_key),
    )

    assert response.status_code == 500
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        batch_freezes = conn.execute("SELECT COUNT(*) AS total FROM billing_batch_freezes").fetchone()
    assert wallet["locked_points"] == 0
    assert batch_freezes["total"] == 0


def test_pod_freeze_grants_only_an_encrypted_envelope_and_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id)
    headers = {"Authorization": f"Bearer {token}"}
    version = _configure_pod_pricing(client, headers)
    encrypted_session_key, session_key = _encrypted_session(private_key)
    payload = _freeze_payload(encrypted_session_key)

    first = client.post("/api/customer/billing/pod/freeze", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    freeze = first.json()["freeze"]
    assert freeze["rule_version"] == version
    assert freeze["frozen_points"] == 7.9
    assert set(freeze["grant_envelope"]) == {"payload", "nonce", "tag", "expires_at"}
    response_text = first.text
    assert _ARK_SENTINEL not in response_text
    assert _WUYIN_SENTINEL not in response_text
    decrypted = _decrypt_envelope(freeze["grant_envelope"], session_key)
    assert freeze["grant_envelope"]["expires_at"] == decrypted["expires_at"]
    assert {item["expires_at"] for item in decrypted["keys"]} == {decrypted["expires_at"]}
    assert decrypted["expires_at"] <= freeze["expires_at"]
    grants = decrypted["keys"]
    assert {item["provider"]: item["api_key"] for item in grants} == {
        "ark": _ARK_SENTINEL,
        "wuyin": _WUYIN_SENTINEL,
    }

    repeated = client.post("/api/customer/billing/pod/freeze", headers=headers, json=payload)
    assert repeated.status_code == 200
    assert repeated.json()["freeze"]["freeze_id"] == freeze["freeze_id"]
    assert repeated.json()["freeze"]["already_frozen"] is True

    changed_plan = _freeze_payload(encrypted_session_key)
    changed_plan["calls"][2]["call_id"] = "image-call-changed"
    rejected = client.post("/api/customer/billing/pod/freeze", headers=headers, json=changed_plan)
    assert rejected.status_code == 409

    with transaction(database_path) as conn:
        dump = "\n".join(conn.iterdump())
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    assert _ARK_SENTINEL not in dump
    assert _WUYIN_SENTINEL not in dump
    assert dict(wallet) == {"points_balance": 1000, "locked_points": 79}


def test_pod_freeze_replay_rejects_expired_freeze_before_sweep_or_grant(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)
    encrypted_session_key, _ = _encrypted_session(private_key)
    payload = _freeze_payload(encrypted_session_key)
    first = client.post("/api/customer/billing/pod/freeze", headers=headers, json=payload)
    assert first.status_code == 200
    freeze = first.json()["freeze"]
    with transaction(database_path) as conn:
        conn.execute(
            """
            UPDATE billing_batch_freezes
            SET expires_at = '2020-01-01T00:00:00+00:00'
            WHERE freeze_id = ?
            """,
            (freeze["freeze_id"],),
        )
        grants_before = conn.execute(
            "SELECT COUNT(*) AS total FROM billing_key_grants WHERE freeze_id = ?",
            (freeze["freeze_id"],),
        ).fetchone()["total"]
    replay_session_key, _ = _encrypted_session(private_key)
    replay_payload = _freeze_payload(replay_session_key)

    replay = client.post("/api/customer/billing/pod/freeze", headers=headers, json=replay_payload)

    assert replay.status_code == 409
    assert replay.json()["detail"] == "POD freeze is no longer active"
    assert "grant_envelope" not in replay.text
    with transaction(database_path) as conn:
        stored = conn.execute(
            "SELECT status FROM billing_batch_freezes WHERE freeze_id = ?",
            (freeze["freeze_id"],),
        ).fetchone()
        grants_after = conn.execute(
            "SELECT COUNT(*) AS total FROM billing_key_grants WHERE freeze_id = ?",
            (freeze["freeze_id"],),
        ).fetchone()["total"]
    assert stored["status"] == "frozen"
    assert grants_after == grants_before


def test_concurrent_different_pod_plans_with_same_idempotency_key_allow_one_winner(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path, _ = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)
    with transaction(database_path) as conn:
        workspace_id = str(conn.execute(
            "SELECT workspace_id FROM auth_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()["workspace_id"])
    actor = Actor(
        id=account_id,
        username="pod_billing_user",
        role="admin",
        workspace_id=workspace_id,
    )
    barrier = threading.Barrier(2)
    original_pricing = pod_billing_module.pod_pricing_items

    def synchronized_pricing(*args, **kwargs):
        value = original_pricing(*args, **kwargs)
        barrier.wait(timeout=5)
        return value

    monkeypatch.setattr(pod_billing_module, "pod_pricing_items", synchronized_pricing)
    shared_idempotency_key = "pod-concurrent-plan-attack-0001"
    plans = [
        [{"call_id": "title-plan-alpha", "feature": "pod.title"}],
        [{"call_id": "title-plan-bravo", "feature": "pod.title"}],
    ]

    def attack(calls: list[dict[str, str]]):
        try:
            return pod_billing_module.freeze_pod_points(
                database_path,
                actor,
                calls=calls,
                title_call_count=1,
                image_call_count=0,
                idempotency_key=shared_idempotency_key,
            )
        except HTTPException as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attack, plans))

    successes = [value for value in outcomes if isinstance(value, dict)]
    conflicts = [value for value in outcomes if isinstance(value, HTTPException)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409
    winner_calls = successes[0]["calls"]
    stored = pod_billing_module.pod_freeze_status(
        database_path,
        successes[0]["freeze_id"],
        expected_account_id=account_id,
    )
    assert [
        {"call_id": item["call_id"], "feature": item["feature"]}
        for item in stored["calls"]
    ] == winner_calls
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    assert wallet["locked_points"] == 15


def test_pod_idempotency_namespace_does_not_collide_with_product_batch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id, units=2000)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)
    shared_key = "shared-client-idempotency-0001"
    product = client.post(
        "/api/customer/billing/batch/freeze",
        headers=headers,
        json={"link_count": 1, "idempotency_key": shared_key},
    )
    assert product.status_code == 200
    encrypted_session_key, _ = _encrypted_session(private_key)

    pod = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload(encrypted_session_key, idempotency_key=shared_key),
    )

    assert pod.status_code == 200, pod.text
    assert pod.json()["freeze"]["freeze_id"] != product.json()["freeze"]["freeze_id"]


def test_pod_settle_uses_frozen_version_refunds_no_return_and_replays_result(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)
    encrypted_session_key, _ = _encrypted_session(private_key)
    freeze = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload(encrypted_session_key),
    ).json()["freeze"]

    # Product pricing revisions must carry POD items into the new rule.
    product_change = client.put(
        "/api/admin/billing/pricing/items",
        headers=headers,
        json={
            "items": {
                "title": {"charge_points": 8},
                "description": {"charge_points": 6},
                "product_dimensions": {"charge_points": 7},
                "four_grid": {"charge_points": 12},
                "detail_images": {"charge_points": 10},
            },
            "change_reason": "product-only revision",
        },
    )
    assert product_change.status_code == 200

    # A later POD price change must not change the frozen rule version and must
    # carry product prices forward as well.
    changed = client.put(
        "/api/admin/billing/pricing/pod",
        headers=headers,
        json={
            "items": {
                "pod.title": {"charge_points": 2},
                "pod.image": {"charge_points": 4},
            },
            "change_reason": "future POD price",
        },
    )
    assert changed.status_code == 200
    assert client.get(
        "/api/admin/billing/pricing/items",
        headers=headers,
    ).json()["pricing"]["items"]["description"]["charge_points"] == 6

    items = [
        {"call_id": "title-call-0001", "feature": "pod.title", "status": "success"},
        {"call_id": "image-call-0001", "feature": "pod.image", "status": "success"},
        {"call_id": "image-call-0002", "feature": "pod.image", "status": "no_return"},
    ]
    settled = client.post(
        "/api/customer/billing/pod/settle",
        headers=headers,
        json={"freeze_id": freeze["freeze_id"], "items": items},
    )
    assert settled.status_code == 200, settled.text
    result = settled.json()["settle"]
    assert result["charged_points"] == 4.7
    assert result["refunded_points"] == 3.2
    assert result["already_settled"] is False

    repeated = client.post(
        "/api/customer/billing/pod/settle",
        headers=headers,
        json={"freeze_id": freeze["freeze_id"], "items": list(reversed(items))},
    )
    assert repeated.status_code == 200
    assert repeated.json()["settle"] == {**result, "already_settled": True}

    replayed_freeze = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload(encrypted_session_key),
    )
    assert replayed_freeze.status_code == 409
    assert "grant_envelope" not in replayed_freeze.text

    status = client.get(
        f"/api/customer/billing/pod/{freeze['freeze_id']}",
        headers=headers,
    )
    assert status.status_code == 200
    status_freeze = status.json()["freeze"]
    assert status_freeze["status"] == "settled"
    assert status_freeze["rule_version"] == freeze["rule_version"]
    assert "grant_envelope" not in status.text
    assert [item["call_id"] for item in status_freeze["calls"]] == [
        "title-call-0001",
        "image-call-0001",
        "image-call-0002",
    ]

    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    assert dict(wallet) == {"points_balance": 953, "locked_points": 0}


def test_pod_settle_rejects_any_plan_mismatch_without_releasing_lock(tmp_path: Path, monkeypatch) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)
    encrypted_session_key, _ = _encrypted_session(private_key)
    freeze = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload(encrypted_session_key),
    ).json()["freeze"]

    response = client.post(
        "/api/customer/billing/pod/settle",
        headers=headers,
        json={
            "freeze_id": freeze["freeze_id"],
            "items": [
                {"call_id": "title-call-0001", "feature": "pod.title", "status": "success"},
                {"call_id": "image-call-0001", "feature": "pod.image", "status": "no_return"},
            ],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "settlement calls must exactly match the frozen plan"

    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
    assert wallet["locked_points"] == 79


def test_pod_outcome_persistence_failure_rolls_back_wallet_settlement(tmp_path: Path, monkeypatch) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)
    encrypted_session_key, _ = _encrypted_session(private_key)
    freeze = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload(encrypted_session_key),
    ).json()["freeze"]
    assert hasattr(
        pod_billing_module,
        "_persist_pod_settlement",
    ), "POD outcome writer must be transactional"
    monkeypatch.setattr(
        pod_billing_module,
        "_persist_pod_settlement",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated crash")),
    )
    failure_client = TestClient(client.app, raise_server_exceptions=False)

    response = failure_client.post(
        "/api/customer/billing/pod/settle",
        headers=headers,
        json={
            "freeze_id": freeze["freeze_id"],
            "items": [
                {"call_id": "title-call-0001", "feature": "pod.title", "status": "success"},
                {"call_id": "image-call-0001", "feature": "pod.image", "status": "success"},
                {"call_id": "image-call-0002", "feature": "pod.image", "status": "no_return"},
            ],
        },
    )

    assert response.status_code == 500
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        stored = conn.execute(
            "SELECT status FROM billing_batch_freezes WHERE freeze_id = ?",
            (freeze["freeze_id"],),
        ).fetchone()
    assert dict(wallet) == {"points_balance": 1000, "locked_points": 79}
    assert stored["status"] == "frozen"


def test_pod_retry_freezes_only_the_failed_call(tmp_path: Path, monkeypatch) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    _grant_points(database_path, account_id)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)
    encrypted_session_key, _ = _encrypted_session(private_key)
    first_plan = {
        "idempotency_key": "pod-initial-two-call-plan-0001",
        "title_call_count": 1,
        "image_call_count": 1,
        "calls": [
            {"call_id": "title-success-0001", "feature": "pod.title"},
            {"call_id": "image-failed-0001", "feature": "pod.image"},
        ],
        "encrypted_session_key": encrypted_session_key,
    }
    initial = client.post("/api/customer/billing/pod/freeze", headers=headers, json=first_plan).json()["freeze"]
    client.post(
        "/api/customer/billing/pod/settle",
        headers=headers,
        json={
            "freeze_id": initial["freeze_id"],
            "items": [
                {"call_id": "title-success-0001", "feature": "pod.title", "status": "success"},
                {"call_id": "image-failed-0001", "feature": "pod.image", "status": "no_return"},
            ],
        },
    )
    retry_session_key, _ = _encrypted_session(private_key)
    retry = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json={
            "idempotency_key": "pod-image-retry-plan-0001",
            "title_call_count": 0,
            "image_call_count": 1,
            "calls": [{"call_id": "image-retry-0001", "feature": "pod.image"}],
            "encrypted_session_key": retry_session_key,
        },
    )
    assert retry.status_code == 200
    assert retry.json()["freeze"]["frozen_points"] == 3.2


def test_pod_status_is_owner_private_and_regrant_is_encrypted(tmp_path: Path, monkeypatch) -> None:
    client, database_path, private_key = _client(tmp_path, monkeypatch)
    token, account_id = _register_and_login(client, database_path)
    other_token, other_account_id = _register_and_login(
        client,
        database_path,
        username="other_pod_user",
        workspace_code="other-pod-ws",
    )
    _grant_points(database_path, account_id)
    _grant_points(database_path, other_account_id)
    headers = {"Authorization": f"Bearer {token}"}
    _configure_pod_pricing(client, headers)
    encrypted_session_key, _ = _encrypted_session(private_key)
    freeze = client.post(
        "/api/customer/billing/pod/freeze",
        headers=headers,
        json=_freeze_payload(encrypted_session_key),
    ).json()["freeze"]

    assert client.get(
        f"/api/customer/billing/pod/{freeze['freeze_id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    ).status_code == 404

    new_encrypted_session_key, new_session_key = _encrypted_session(private_key)
    regrant = client.post(
        f"/api/customer/billing/pod/{freeze['freeze_id']}/regrant",
        headers=headers,
        json={"encrypted_session_key": new_encrypted_session_key},
    )
    assert regrant.status_code == 200, regrant.text
    assert _ARK_SENTINEL not in regrant.text
    assert _WUYIN_SENTINEL not in regrant.text
    decrypted = _decrypt_envelope(regrant.json()["grant_envelope"], new_session_key)
    assert {item["provider"] for item in decrypted["keys"]} == {"ark", "wuyin"}

    with transaction(database_path) as conn:
        conn.execute(
            "UPDATE auth_accounts SET account_status = 'disabled' WHERE account_id = ?",
            (account_id,),
        )
    revoked = client.post(
        f"/api/customer/billing/pod/{freeze['freeze_id']}/regrant",
        headers=headers,
        json={"encrypted_session_key": new_encrypted_session_key},
    )
    assert revoked.status_code == 403
    assert _ARK_SENTINEL not in revoked.text
    assert _WUYIN_SENTINEL not in revoked.text
