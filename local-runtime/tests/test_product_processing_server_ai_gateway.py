from __future__ import annotations

import base64
import contextvars
import json
import socket
import sqlite3
import threading
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

from wh_local.customer import auth_server
from wh_local.customer.auth_server import create_auth_app
from wh_local.customer.auth_service import _email_code_digest
from wh_local.db import init_db, transaction
from wh_local.modules.basic_settings import service as basic_settings_service
from wh_local.modules.product_processing.server_ai_proxy import remote_token, server_ai_context, usage_id
from wh_local.modules.product_processing import provider_config
import wh_local.modules.product_processing.service as product_service_module
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure import media as media_module
from wh_local.modules.product_processing.infrastructure.media import ProductImageProcessor
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.modules.product_processing.domain import policy as url_policy


_EMAIL_CODE_SECRET = "gateway-test-secret-that-is-at-least-32-chars"


class _Response:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.closed = False

    def json(self) -> dict:
        return self.payload

    def iter_content(self, chunk_size: int = 64 * 1024):
        encoded = json.dumps(self.payload).encode("utf-8")
        for offset in range(0, len(encoded), chunk_size):
            yield encoded[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def raise_for_status(self) -> None:
        if not self.ok:
            raise RuntimeError(f"HTTP {self.status_code}")


class _InvalidJsonResponse(_Response):
    def __init__(self, *, status_code: int = 200) -> None:
        super().__init__({}, status_code=status_code)

    def json(self) -> dict:
        raise ValueError("invalid provider json")

    def iter_content(self, chunk_size: int = 64 * 1024):
        yield b"{invalid-json"


class _TestServerBillingClient:
    """Exercise the real auth-server billing routes from the local service."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _post(self, path: str, token: str, payload: dict) -> dict:
        response = self.client.post(path, headers=self._headers(token), json=payload)
        response.raise_for_status()
        return response.json()

    def reserve_ai_usage(self, token: str, payload: dict) -> dict:
        return self._post("/api/customer/billing/usage/reserve", token, payload)

    def settle_ai_usage_success(self, token: str, usage: str, payload: dict) -> dict:
        return self._post(f"/api/customer/billing/usage/{usage}/succeed", token, payload)

    def settle_ai_usage_failure(self, token: str, usage: str, payload: dict) -> dict:
        return self._post(f"/api/customer/billing/usage/{usage}/fail", token, payload)


def _register_and_login(
    client: TestClient,
    db_path: Path,
    *,
    username: str = "gateway_user",
) -> str:
    email = f"{username}@example.test"
    verification_id = f"ver_{username}"
    with transaction(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO invitation_codes (code, max_uses, used_count, expires_at, created_by, created_at) VALUES ('MAINPG-GATEWAY', 10, 0, '', 'test', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO auth_email_verifications (verification_id, email, token_hash, purpose, expires_at) VALUES (?, ?, ?, 'register', '9999-12-31T00:00:00+00:00')",
            (verification_id, email, _email_code_digest(_EMAIL_CODE_SECRET, verification_id, email, "register", "654321")),
        )
    assert client.post("/api/customer/register", json={
        "username": username, "email": email, "email_code": "654321",
        "password": "StrongPassword123!", "invitation_code": "MAINPG-GATEWAY", "workspace_code": f"{username}-ws",
    }).status_code == 200
    login = client.post("/api/customer/login", json={"username": username, "password": "StrongPassword123!"})
    assert login.status_code == 200
    return str(login.json()["token"])


def _grant_points(db_path: Path) -> None:
    with transaction(db_path) as conn:
        account = conn.execute("SELECT account_id, workspace_id FROM auth_accounts WHERE username = 'gateway_user'").fetchone()
        conn.execute(
            "INSERT INTO billing_wallets (account_id, workspace_id, points_balance) VALUES (?, ?, 1000)",
            (account["account_id"], account["workspace_id"]),
        )


def _reserved_usage(client: TestClient, headers: dict[str, str], feature_key: str, suffix: str) -> str:
    response = client.post("/api/customer/billing/usage/reserve", headers=headers, json={
        "feature_key": feature_key, "idempotency_key": f"gateway-reservation-{suffix:0>8}",
    })
    assert response.status_code == 200
    return str(response.json()["usage"]["usage_id"])


def test_init_db_upgrades_legacy_gateway_rows_with_recovery_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy-gateway.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE billing_ai_gateway_requests (
                usage_id TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                account_id TEXT NOT NULL,
                feature_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'in_progress',
                response_json TEXT NOT NULL DEFAULT '',
                attempt_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (usage_id, request_hash)
            )
            """
        )

    init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(billing_ai_gateway_requests)")}
    assert {"lease_expires_at", "phase", "provider_task_id"} <= columns


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, dict[str, str]]:
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(auth_server.TencentCloudSESEmailSender, "from_env", lambda: object())
    db_path = tmp_path / "auth.sqlite3"
    client = TestClient(create_auth_app(db_path))
    headers = {"Authorization": f"Bearer {_register_and_login(client, db_path)}"}
    _grant_points(db_path)
    monkeypatch.setattr(
        url_policy.socket,
        "getaddrinfo",
        lambda _host, port, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))
        ],
    )
    return client, headers


def test_server_ai_context_is_scoped_and_propagates_to_worker() -> None:
    assert remote_token() == ""
    assert usage_id("text") == ""
    with server_ai_context("remote-token", {"text": "use-text", "image_grid": "use-image"}):
        context = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(context.run, lambda: (remote_token(), usage_id("text")))
            assert future.result() == ("remote-token", "use-text")
    assert remote_token() == ""
    assert usage_id("text") == ""


def test_chat_gateway_uses_server_secret_without_returning_it(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "chat")
    provider_requests: list[dict] = []
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")

    def fake_post(_url, **kwargs):
        provider_requests.append(kwargs)
        return _Response({
            "api_key": "server-secret",
            "trace": {"secret": "server-secret"},
            "choices": [{"message": {"content": "safe reply"}}],
        })

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    chat = client.post("/api/customer/ai/chat", headers=headers, json={
        "model": "gpt-5.6-terra", "usage_id": usage, "messages": [{"role": "user", "content": "hello"}],
    })

    assert chat.status_code == 200
    assert provider_requests[0]["headers"]["Authorization"] == "Bearer server-secret"
    assert "server-secret" not in json.dumps(chat.json())


def test_chat_gateway_replays_identical_success_without_second_provider_call(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "replay")
    provider_calls = 0
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")

    def fake_post(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({"choices": [{"message": {"content": "cached reply"}}]})

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    payload = {
        "model": "gpt-5.6-terra",
        "usage_id": usage,
        "messages": [{"role": "user", "content": "same request"}],
    }

    first = client.post("/api/customer/ai/chat", headers=headers, json=payload)
    second = client.post("/api/customer/ai/chat", headers=headers, json=payload)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert provider_calls == 1
    with transaction(tmp_path / "auth.sqlite3") as conn:
        rows = conn.execute(
            "SELECT status, response_json FROM billing_ai_gateway_requests WHERE usage_id = ?",
            (usage,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "succeeded"
    assert "server-secret" not in rows[0]["response_json"]


def test_chat_gateway_rejects_excess_distinct_requests_before_provider_call(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "bounded")
    provider_calls = 0
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")

    def fake_post(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({"choices": [{"message": {"content": "reply"}}]})

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    statuses = [
        client.post("/api/customer/ai/chat", headers=headers, json={
            "usage_id": usage,
            "messages": [{"role": "user", "content": f"request-{index}"}],
        }).status_code
        for index in range(17)
    ]

    assert statuses == ([200] * 16) + [409]
    assert provider_calls == 16


def test_chat_gateway_fails_closed_for_in_progress_identical_request(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "in-progress")
    messages = [{"role": "user", "content": "same request"}]
    request_hash = auth_server._gateway_request_hash({"model": auth_server.TEXT_MODEL, "messages": messages})
    with transaction(tmp_path / "auth.sqlite3") as conn:
        account = conn.execute(
            "SELECT account_id FROM auth_accounts WHERE username = 'gateway_user'"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO billing_ai_gateway_requests (
                usage_id, request_hash, account_id, feature_key, status
            ) VALUES (?, ?, ?, 'product_processing.text', 'in_progress')
            """,
            (usage, request_hash, account["account_id"]),
        )
    provider_calls = 0
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")

    def fake_post(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({})

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    response = client.post("/api/customer/ai/chat", headers=headers, json={
        "usage_id": usage, "messages": messages,
    })

    assert response.status_code == 409
    assert provider_calls == 0


def test_chat_gateway_reclaims_expired_in_progress_lease(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "expired-lease")
    messages = [{"role": "user", "content": "recover stale request"}]
    request_hash = auth_server._gateway_request_hash(
        {"model": auth_server.TEXT_MODEL, "messages": messages}
    )
    with transaction(tmp_path / "auth.sqlite3") as conn:
        account = conn.execute(
            "SELECT account_id FROM auth_accounts WHERE username = 'gateway_user'"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO billing_ai_gateway_requests (
                usage_id, request_hash, account_id, feature_key, status,
                lease_expires_at, phase
            ) VALUES (?, ?, ?, 'product_processing.text', 'in_progress',
                      '2000-01-01T00:00:00+00:00', 'claimed')
            """,
            (usage, request_hash, account["account_id"]),
        )
    provider_calls = 0
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")

    def fake_post(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({"choices": [{"message": {"content": "recovered"}}]})

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    response = client.post(
        "/api/customer/ai/chat",
        headers=headers,
        json={"usage_id": usage, "messages": messages},
    )

    assert response.status_code == 200
    assert provider_calls == 1
    with transaction(tmp_path / "auth.sqlite3") as conn:
        row = conn.execute(
            "SELECT status, attempt_count FROM billing_ai_gateway_requests WHERE usage_id = ?",
            (usage,),
        ).fetchone()
    assert dict(row) == {"status": "succeeded", "attempt_count": 2}


def test_chat_gateway_retries_same_hash_after_sanitized_provider_failure(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "retry")
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")
    responses = [requests.Timeout("provider socket detail"), _Response({"choices": [{"message": {"content": "ok"}}]})]

    def fake_post(*_args, **_kwargs):
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    payload = {"usage_id": usage, "messages": [{"role": "user", "content": "retry me"}]}

    first = client.post("/api/customer/ai/chat", headers=headers, json=payload)
    second = client.post("/api/customer/ai/chat", headers=headers, json=payload)

    assert first.status_code == 503
    assert second.status_code == 200


def test_chat_gateway_blocks_cross_account_usage_before_provider_call(tmp_path: Path, monkeypatch) -> None:
    client, owner_headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, owner_headers, "product_processing.text", "owner")
    other_token = _register_and_login(client, tmp_path / "auth.sqlite3", username="gateway_other")
    provider_calls = 0
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")

    def fake_post(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({})

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    response = client.post(
        "/api/customer/ai/chat",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"usage_id": usage, "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 404
    assert provider_calls == 0


def test_gateway_rejects_malicious_payload_after_invalid_usage_check(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)

    chat = client.post("/api/customer/ai/chat", headers=headers, json={
        "usage_id": "use_missing",
        "messages": [{"role": "user", "content": [{"type": "tool", "payload": {"nested": "bad"}}]}],
    })
    image = client.post("/api/customer/ai/image", headers=headers, json={
        "usage_id": "use_missing",
        "prompt": "",
        "urls": ["https://127.0.0.1/private.png"],
    })

    assert chat.status_code == 404
    assert image.status_code == 404


def test_failure_settlement_charges_succeeded_gateway_request(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "success-fail")
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")
    monkeypatch.setattr(
        auth_server.requests,
        "post",
        lambda *_args, **_kwargs: _Response({"choices": [{"message": {"content": "ok"}}]}),
    )
    assert client.post("/api/customer/ai/chat", headers=headers, json={
        "usage_id": usage, "messages": [{"role": "user", "content": "hello"}],
    }).status_code == 200

    failed = client.post(
        f"/api/customer/billing/usage/{usage}/fail",
        headers=headers,
        json={"error_message": "client claims failure"},
    )

    assert failed.status_code == 200
    assert failed.json()["status"] == "succeeded"
    with transaction(tmp_path / "auth.sqlite3") as conn:
        row = conn.execute(
            "SELECT status, charged_points, refunded_points FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage,),
        ).fetchone()
    assert dict(row) == {"status": "succeeded", "charged_points": 50, "refunded_points": 0}


def test_local_durable_attempt_defers_to_gateway_provider_activity(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    auth_db = tmp_path / "auth.sqlite3"
    with transaction(auth_db) as conn:
        account_id = str(
            conn.execute(
                "SELECT account_id FROM auth_accounts WHERE username = 'gateway_user'"
            ).fetchone()["account_id"]
        )
    token = headers["Authorization"].removeprefix("Bearer ")
    local_service = ProductProcessingService(
        ProductProcessingRepository(
            create_database(f"sqlite:///{tmp_path / 'product-processing.sqlite3'}")
        ),
        ProductProcessingAssets(tmp_path / "product-assets"),
    )
    draft, _created = local_service.create_draft(
        {
            "source_type": "manual",
            "title": "gateway billed product",
            "image_url": "https://images.example.test/product.jpg",
        }
    )
    settings = {
        "processing_scope": ["title"],
        "title_optimize": True,
        "description": False,
        "size": False,
        "grid_image": False,
        "image_rewrite": False,
        "_billing": {
            "account_id": account_id,
            "source_ref": "integration:gateway-provider-activity",
            "pricing_version": "v1",
        },
    }
    task = local_service.repository.create_task(
        title="gateway billing integration",
        preflight_only=False,
        settings=settings,
        drafts=[draft],
        idempotency_key=None,
    )
    item_id = int(task["items"][0]["id"])
    local_service._task_remote_tokens[int(task["id"])] = token
    test_server = _TestServerBillingClient(client)
    monkeypatch.setattr(
        product_service_module,
        "CustomerAuthClient",
        lambda *_args, **_kwargs: test_server,
    )

    usages = local_service._reserve_product_processing_item_usage(
        int(task["id"]), item_id, settings
    )
    usage = usages["text"]
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")
    monkeypatch.setattr(
        auth_server.requests,
        "post",
        lambda *_args, **_kwargs: _Response(
            {"choices": [{"message": {"content": "provider completed"}}]}
        ),
    )
    assert client.post(
        "/api/customer/ai/chat",
        headers=headers,
        json={
            "usage_id": usage,
            "messages": [{"role": "user", "content": "bill this call"}],
        },
    ).status_code == 200

    local_service._settle_product_processing_item_failure_for_item(
        int(task["id"]), item_id, {"reason": "later business validation failed"}
    )
    # Re-entering the local failure path must not charge or refund again.
    local_service._settle_product_processing_item_failure_for_item(
        int(task["id"]), item_id, {"reason": "duplicate callback"}
    )
    repeated_server_settlement = client.post(
        f"/api/customer/billing/usage/{usage}/fail",
        headers=headers,
        json={"error_message": "replayed network request"},
    )
    assert repeated_server_settlement.status_code == 200
    assert repeated_server_settlement.json()["status"] == "succeeded"

    attempts = local_service.repository.product_billing_attempts(
        task_id=int(task["id"]), item_id=item_id
    )
    assert attempts[0]["settlement_state"] == "settled_succeeded"
    assert attempts[0]["remote_status"] == "succeeded"
    with transaction(auth_db) as conn:
        event = conn.execute(
            "SELECT status, charged_points, refunded_points FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage,),
        ).fetchone()
        settlement_entries = conn.execute(
            """
            SELECT direction, points_delta FROM billing_point_ledger
            WHERE source_id = ? AND direction IN ('debit', 'unlock')
            ORDER BY direction
            """,
            (usage,),
        ).fetchall()
    assert dict(event) == {"status": "succeeded", "charged_points": 50, "refunded_points": 0}
    assert [dict(row) for row in settlement_entries] == [
        {"direction": "debit", "points_delta": 50},
    ]


def test_failure_settlement_rejects_in_progress_gateway_request(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "progress-fail")
    with transaction(tmp_path / "auth.sqlite3") as conn:
        account = conn.execute(
            "SELECT account_id FROM auth_accounts WHERE username = 'gateway_user'"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO billing_ai_gateway_requests (
                usage_id, request_hash, account_id, feature_key, status
            ) VALUES (?, ?, ?, 'product_processing.text', 'in_progress')
            """,
            (usage, "request-in-progress", account["account_id"]),
        )

    failed = client.post(
        f"/api/customer/billing/usage/{usage}/fail",
        headers=headers,
        json={"error_message": "racing failure"},
    )

    assert failed.status_code == 409
    assert failed.json() == {"detail": "provider request is still in progress"}


def test_failure_settlement_expires_stale_gateway_claim_before_refund(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "stale-settle")
    with transaction(tmp_path / "auth.sqlite3") as conn:
        account = conn.execute(
            "SELECT account_id FROM auth_accounts WHERE username = 'gateway_user'"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO billing_ai_gateway_requests (
                usage_id, request_hash, account_id, feature_key, status,
                lease_expires_at, phase
            ) VALUES (?, 'stale-request', ?, 'product_processing.text', 'in_progress',
                      '2000-01-01T00:00:00+00:00', 'claimed')
            """,
            (usage, account["account_id"]),
        )

    failed = client.post(
        f"/api/customer/billing/usage/{usage}/fail",
        headers=headers,
        json={"error_message": "worker crashed"},
    )

    assert failed.status_code == 200
    assert failed.json()["status"] == "failed"
    with transaction(tmp_path / "auth.sqlite3") as conn:
        gateway = conn.execute(
            "SELECT status, phase FROM billing_ai_gateway_requests WHERE usage_id = ?",
            (usage,),
        ).fetchone()
    assert dict(gateway) == {"status": "failed", "phase": "lease_expired"}


def test_failure_settlement_releases_usage_when_all_gateway_attempts_failed(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "failed-release")
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")
    monkeypatch.setattr(
        auth_server.requests,
        "post",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(requests.Timeout("provider failed")),
    )
    assert client.post("/api/customer/ai/chat", headers=headers, json={
        "usage_id": usage, "messages": [{"role": "user", "content": "hello"}],
    }).status_code == 503

    failed = client.post(
        f"/api/customer/billing/usage/{usage}/fail",
        headers=headers,
        json={"error_message": "provider failed"},
    )

    assert failed.status_code == 200
    with transaction(tmp_path / "auth.sqlite3") as conn:
        row = conn.execute(
            "SELECT status FROM billing_ai_usage_events WHERE usage_id = ?", (usage,)
        ).fetchone()
    assert row["status"] == "failed"


def test_chat_gateway_caps_failed_same_hash_retries(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "retry-cap")
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")
    provider_calls = 0

    def fail(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise requests.Timeout("provider failed")

    monkeypatch.setattr(auth_server.requests, "post", fail)
    payload = {"usage_id": usage, "messages": [{"role": "user", "content": "retry"}]}
    statuses = [client.post("/api/customer/ai/chat", headers=headers, json=payload).status_code for _ in range(4)]

    assert statuses == [503, 503, 503, 409]
    assert provider_calls == 3


def test_image_gateway_caps_failed_same_hash_retries(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "image-retry-cap")
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    provider_calls = 0

    def fail(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({}, status_code=503)

    monkeypatch.setattr(auth_server.requests, "post", fail)
    payload = {"usage_id": usage, "prompt": "same image", "size": "1:1"}
    statuses = [client.post("/api/customer/ai/image", headers=headers, json=payload).status_code for _ in range(6)]

    assert statuses == [503, 503, 503, 503, 503, 409]
    assert provider_calls == 5


def test_chat_gateway_rejects_missing_secret_wrong_feature_and_settled_usage(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    image_usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "wrong")
    wrong_feature = client.post("/api/customer/ai/chat", headers=headers, json={
        "usage_id": image_usage, "messages": [{"role": "user", "content": "hello"}],
    })
    assert wrong_feature.status_code == 400

    text_usage = _reserved_usage(client, headers, "product_processing.text", "secret")
    missing_secret = client.post("/api/customer/ai/chat", headers=headers, json={
        "usage_id": text_usage, "messages": [{"role": "user", "content": "hello"}],
    })
    assert missing_secret.status_code == 503

    assert client.post(f"/api/customer/billing/usage/{text_usage}/succeed", headers=headers, json={}).status_code == 200
    settled = client.post("/api/customer/ai/chat", headers=headers, json={
        "usage_id": text_usage, "messages": [{"role": "user", "content": "hello"}],
    })
    assert settled.status_code == 409


def test_chat_gateway_rejects_provider_redirect(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "redirect")
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")
    monkeypatch.setattr(
        auth_server.requests,
        "post",
        lambda *_args, **_kwargs: _Response({}, status_code=302),
    )

    response = client.post("/api/customer/ai/chat", headers=headers, json={
        "usage_id": usage, "messages": [{"role": "user", "content": "hello"}],
    })

    assert response.status_code == 502


def test_chat_gateway_streams_bounded_json_and_rejects_non_utf8(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "non-utf8")
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")
    request_kwargs: dict = {}

    class _NonUtf8Response:
        status_code = 200

        def iter_content(self, chunk_size: int = 64 * 1024):
            yield b"\xff\xfe"

        def json(self):
            raise AssertionError("response.json must not load an unbounded body")

        def close(self) -> None:
            pass

    def fake_post(*_args, **kwargs):
        request_kwargs.update(kwargs)
        return _NonUtf8Response()

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    response = client.post(
        "/api/customer/ai/chat",
        headers=headers,
        json={"usage_id": usage, "messages": [{"role": "user", "content": "hello"}]},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "server text provider returned invalid JSON"}
    assert request_kwargs["stream"] is True


def test_image_gateway_uses_server_secret_and_returns_only_result_url(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "image")
    provider_requests: list[dict] = []
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")

    def fake_post(_url, **kwargs):
        provider_requests.append(kwargs)
        return _Response({"code": 200, "data": {"id": "task-123"}})

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    monkeypatch.setattr(auth_server.requests, "get", lambda *_args, **_kwargs: _Response({"code": 200, "data": {"url": "https://images.example.test/result.png"}}))
    image = client.post("/api/customer/ai/image", headers=headers, json={"usage_id": usage, "prompt": "a product image", "size": "1:1"})

    assert image.status_code == 200
    assert image.json()["result_url"] == "https://images.example.test/result.png"
    assert provider_requests[0]["headers"]["Authorization"] == "server-secret"
    assert "server-secret" not in json.dumps(image.json())


def test_image_gateway_forwards_only_allowlisted_public_reference_url(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(
        client, headers, "product_processing.image_grid_2k", "allowlisted-reference"
    )
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setenv(
        "WH_AI_REFERENCE_HOST_ALLOWLIST",
        "trusted.example.test,*.assets.example.test",
    )
    submitted: list[dict] = []

    def provider_post(*_args, **kwargs):
        submitted.append(kwargs["json"])
        return _Response({"code": 200, "data": {"id": "task-allowlisted"}})

    monkeypatch.setattr(auth_server.requests, "post", provider_post)
    monkeypatch.setattr(
        auth_server.requests,
        "get",
        lambda *_args, **_kwargs: _Response(
            {"code": 200, "data": {"url": "https://images.example.test/result.png"}}
        ),
    )
    reference_url = "https://cdn.assets.example.test/source.jpg"
    response = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={
            "usage_id": usage,
            "prompt": "product",
            "size": "1:1",
            "urls": [reference_url],
        },
    )

    assert response.status_code == 200
    assert submitted[0]["urls"] == [reference_url]


def test_image_gateway_rejects_public_reference_outside_trusted_allowlist(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(
        client, headers, "product_processing.image_grid_2k", "untrusted-reference"
    )
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setenv("WH_AI_REFERENCE_HOST_ALLOWLIST", "trusted.example.test")
    provider_calls = 0

    def provider_post(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({"code": 200, "data": {"id": "must-not-run"}})

    monkeypatch.setattr(auth_server.requests, "post", provider_post)
    response = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={
            "usage_id": usage,
            "prompt": "product",
            "size": "1:1",
            "urls": ["https://rebind.example.test/source.jpg"],
        },
    )

    assert response.status_code == 400
    assert provider_calls == 0


@pytest.mark.parametrize(
    "answers",
    [
        ("127.0.0.1",),
        ("8.8.8.8", "10.0.0.8"),
        None,
    ],
)
def test_image_gateway_rejects_reference_url_when_dns_is_not_all_public(
    tmp_path: Path, monkeypatch, answers: tuple[str, ...] | None
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "ssrf-dns")
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setenv("WH_AI_REFERENCE_HOST_ALLOWLIST", "reference.example.test")
    provider_calls = 0

    def resolve(_host, port, **_kwargs):
        if answers is None:
            raise socket.gaierror("DNS unavailable")
        return [
            (
                socket.AF_INET6 if ":" in address else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (address, port),
            )
            for address in answers
        ]

    def provider_post(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({"code": 200, "data": {"id": "must-not-run"}})

    monkeypatch.setattr(url_policy.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(auth_server.requests, "post", provider_post)
    response = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={
            "usage_id": usage,
            "prompt": "product",
            "size": "1:1",
            "urls": ["https://reference.example.test/source.jpg"],
        },
    )

    assert response.status_code == 400
    assert provider_calls == 0


def test_image_gateway_rejects_invalid_reference_port_without_provider_call(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(
        client, headers, "product_processing.image_grid_2k", "invalid-reference-port"
    )
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setenv("WH_AI_REFERENCE_HOST_ALLOWLIST", "reference.example.test")
    provider_calls = 0

    def provider_post(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({"code": 200, "data": {"id": "must-not-run"}})

    monkeypatch.setattr(auth_server.requests, "post", provider_post)
    response = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={
            "usage_id": usage,
            "prompt": "product",
            "size": "1:1",
            "urls": ["https://reference.example.test:99999/source.jpg"],
        },
    )

    assert response.status_code == 400
    assert provider_calls == 0


def test_image_gateway_persists_submit_before_poll_and_resumes_without_duplicate_submit(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "image-crash")
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    submit_calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        return _Response({"code": 200, "data": {"id": "task-crash-recovery"}})

    poll_results: list[object] = [
        KeyboardInterrupt("simulated process crash after submit"),
        "https://images.example.test/recovered.png",
    ]

    def fake_poll(_api_key: str, task_id: str) -> str:
        assert task_id == "task-crash-recovery"
        result = poll_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return str(result)

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    monkeypatch.setattr(auth_server, "_poll_server_wuyin", fake_poll)
    payload = {"usage_id": usage, "prompt": "recover image", "size": "1:1"}

    with pytest.raises(KeyboardInterrupt):
        client.post("/api/customer/ai/image", headers=headers, json=payload)

    with transaction(tmp_path / "auth.sqlite3") as conn:
        after_crash = conn.execute(
            """
            SELECT status, phase, provider_task_id
            FROM billing_ai_gateway_requests WHERE usage_id = ?
            """,
            (usage,),
        ).fetchone()
        conn.execute(
            """
            UPDATE billing_ai_gateway_requests
            SET lease_expires_at = '2000-01-01T00:00:00+00:00'
            WHERE usage_id = ?
            """,
            (usage,),
        )
    assert dict(after_crash) == {
        "status": "in_progress",
        "phase": "polling",
        "provider_task_id": "task-crash-recovery",
    }

    recovered = client.post("/api/customer/ai/image", headers=headers, json=payload)

    assert recovered.status_code == 200
    assert recovered.json()["result_url"] == "https://images.example.test/recovered.png"
    assert submit_calls == 1


def test_image_submit_crash_before_task_persistence_never_automatically_resubmits(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(
        client,
        headers,
        "product_processing.image_grid_2k",
        "submit-uncertain-crash",
    )
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    submit_calls = 0
    record_calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        return _Response({"code": 200, "data": {"id": f"task-{submit_calls}"}})

    def crash_first_record(*_args, **_kwargs):
        nonlocal record_calls
        record_calls += 1
        if record_calls == 1:
            raise KeyboardInterrupt("crash after upstream accepted submit")
        raise AssertionError("uncertain usage must not submit again")

    original_record = auth_server._record_gateway_provider_task
    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    monkeypatch.setattr(
        auth_server,
        "_record_gateway_provider_task",
        crash_first_record,
    )
    payload = {"usage_id": usage, "prompt": "uncertain submit", "size": "1:1"}

    with pytest.raises(KeyboardInterrupt):
        client.post("/api/customer/ai/image", headers=headers, json=payload)

    with transaction(tmp_path / "auth.sqlite3") as conn:
        after_crash = conn.execute(
            """
            SELECT status, phase, provider_task_id
            FROM billing_ai_gateway_requests WHERE usage_id = ?
            """,
            (usage,),
        ).fetchone()
    assert dict(after_crash) == {
        "status": "in_progress",
        "phase": "submitting",
        "provider_task_id": "",
    }

    fresh_changed_hash = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={**payload, "prompt": "changed while submit lease is fresh"},
    )
    assert fresh_changed_hash.status_code == 409
    assert submit_calls == 1

    with transaction(tmp_path / "auth.sqlite3") as conn:
        conn.execute(
            """
            UPDATE billing_ai_gateway_requests
            SET lease_expires_at = '2000-01-01T00:00:00+00:00'
            WHERE usage_id = ?
            """,
            (usage,),
        )

    replay = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={**payload, "size": "16:9"},
    )
    assert replay.status_code == 503
    assert replay.json() == {"detail": "server image submit outcome is uncertain"}
    assert submit_calls == 1
    with transaction(tmp_path / "auth.sqlite3") as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM billing_ai_gateway_requests WHERE usage_id = ?",
            (usage,),
        ).fetchone()[0] == 1

    failed = client.post(
        f"/api/customer/billing/usage/{usage}/fail",
        headers=headers,
        json={"error_message": "submit outcome uncertain"},
    )
    assert failed.status_code == 200
    assert failed.json()["usage"]["status"] == "failed"

    replacement = _reserved_usage(
        client,
        headers,
        "product_processing.image_grid_2k",
        "submit-uncertain-replacement",
    )
    monkeypatch.setattr(auth_server, "_record_gateway_provider_task", original_record)
    monkeypatch.setattr(
        auth_server,
        "_poll_server_wuyin",
        lambda *_args, **_kwargs: "https://images.example.test/replacement.png",
    )
    replacement_response = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={**payload, "usage_id": replacement},
    )

    assert replacement_response.status_code == 200
    assert submit_calls == 2


def test_image_submit_database_failure_after_acceptance_is_submit_uncertain(
    tmp_path: Path, monkeypatch
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(
        client,
        headers,
        "product_processing.image_grid_2k",
        "submit-uncertain-database",
    )
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    submit_calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        return _Response({"code": 200, "data": {"id": "accepted-upstream-task"}})

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    monkeypatch.setattr(
        auth_server,
        "_record_gateway_provider_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )
    payload = {"usage_id": usage, "prompt": "uncertain database save", "size": "1:1"}

    first = client.post("/api/customer/ai/image", headers=headers, json=payload)
    second = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={**payload, "prompt": "changed hash after database failure", "size": "3:2"},
    )

    assert first.status_code == second.status_code == 503
    assert first.json() == second.json() == {
        "detail": "server image submit outcome is uncertain"
    }
    assert submit_calls == 1
    with transaction(tmp_path / "auth.sqlite3") as conn:
        row = conn.execute(
            "SELECT status, phase FROM billing_ai_gateway_requests WHERE usage_id = ?",
            (usage,),
        ).fetchone()
    assert dict(row) == {"status": "failed", "phase": "submit_uncertain"}


@pytest.mark.parametrize(
    "submit_result",
    [
        requests.Timeout("body containing server-secret"),
        _InvalidJsonResponse(status_code=200),
    ],
)
def test_image_submit_uncertain_response_is_not_retried_same_usage(
    tmp_path: Path, monkeypatch, submit_result: object
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(
        client,
        headers,
        "product_processing.image_grid_2k",
        "submit-uncertain-network",
    )
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    submit_calls = 0

    def uncertain(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        if isinstance(submit_result, BaseException):
            raise submit_result
        return submit_result

    monkeypatch.setattr(auth_server.requests, "post", uncertain)
    payload = {"usage_id": usage, "prompt": "uncertain network", "size": "1:1"}

    first = client.post("/api/customer/ai/image", headers=headers, json=payload)
    second = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={**payload, "prompt": "changed hash after uncertain response", "size": "2:3"},
    )

    assert first.status_code == second.status_code == 503
    assert first.json() == second.json() == {
        "detail": "server image submit outcome is uncertain"
    }
    assert submit_calls == 1
    with transaction(tmp_path / "auth.sqlite3") as conn:
        row = conn.execute(
            "SELECT status, phase FROM billing_ai_gateway_requests WHERE usage_id = ?",
            (usage,),
        ).fetchone()
        row_count = conn.execute(
            "SELECT COUNT(*) FROM billing_ai_gateway_requests WHERE usage_id = ?",
            (usage,),
        ).fetchone()[0]
    assert dict(row) == {"status": "failed", "phase": "submit_uncertain"}
    assert row_count == 1


@pytest.mark.parametrize(
    ("poll_result", "expected_status"),
    [
        (requests.Timeout("poll timeout"), 503),
        (_InvalidJsonResponse(), 502),
        (_Response({}, status_code=302), 502),
        (_Response({}, status_code=503), 503),
    ],
)
def test_image_poll_uncertainty_retains_provider_task_evidence(
    tmp_path: Path,
    monkeypatch,
    poll_result: object,
    expected_status: int,
) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "poll-evidence")
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setattr(
        auth_server.requests,
        "post",
        lambda *_args, **_kwargs: _Response({"code": 200, "data": {"id": "task-evidence"}}),
    )

    def fake_get(*_args, **_kwargs):
        if isinstance(poll_result, BaseException):
            raise poll_result
        return poll_result

    monkeypatch.setattr(auth_server.requests, "get", fake_get)
    response = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={"usage_id": usage, "prompt": "retain evidence", "size": "1:1"},
    )

    assert response.status_code == expected_status
    with transaction(tmp_path / "auth.sqlite3") as conn:
        row = conn.execute(
            """
            SELECT status, phase, provider_task_id
            FROM billing_ai_gateway_requests WHERE usage_id = ?
            """,
            (usage,),
        ).fetchone()
    assert dict(row) == {
        "status": "in_progress",
        "phase": "polling",
        "provider_task_id": "task-evidence",
    }


def test_image_gateway_enforces_distinct_request_bound_before_provider_call(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "bounded-image")
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setattr(auth_server.time, "sleep", lambda *_args: None)
    provider_calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({"code": 200, "data": {"id": f"task-{provider_calls}"}})

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    monkeypatch.setattr(
        auth_server.requests,
        "get",
        lambda *_args, **_kwargs: _Response({
            "code": 200,
            "data": {"url": "https://images.example.test/result.png"},
        }),
    )

    statuses = [
        client.post("/api/customer/ai/image", headers=headers, json={
            "usage_id": usage, "prompt": f"product-{index}", "size": "1:1",
        }).status_code
        for index in range(14)
    ]

    assert statuses == [200] * 13 + [409]
    assert provider_calls == 13


def test_image_gateway_rejects_malformed_provider_envelope(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "malformed")
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setattr(
        auth_server.requests,
        "post",
        lambda *_args, **_kwargs: _Response([]),
    )

    response = client.post(
        "/api/customer/ai/image",
        headers=headers,
        json={"usage_id": usage, "prompt": "a product image", "size": "1:1"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "server image submit outcome is uncertain"}


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/image.png",
        "https://127.0.0.1/image.png",
        "https://169.254.1.1/image.png",
        "https://user:pass@images.example.test/image.png",
    ],
)
def test_image_gateway_rejects_non_public_reference_urls(tmp_path: Path, monkeypatch, url: str) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", url)
    monkeypatch.delenv("WH_WUYIN_IMAGE_API_KEY", raising=False)

    response = client.post("/api/customer/ai/image", headers=headers, json={
        "usage_id": usage, "prompt": "product", "size": "1:1", "urls": [url],
    })

    assert response.status_code == 400


@pytest.mark.parametrize(
    "content",
    [
        [{"type": "tool", "payload": {"url": "https://images.example.test/a.png"}}],
        [{"type": "image_url", "image_url": {"url": "https://127.0.0.1/a.png"}}],
        [{"type": "image_url", "image_url": {"url": "https://user:pass@images.example.test/a.png"}}],
    ],
)
def test_chat_gateway_rejects_unsupported_or_unsafe_content_parts(tmp_path: Path, monkeypatch, content) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", json.dumps(content))
    monkeypatch.delenv("WH_TEXT_API_KEY", raising=False)

    response = client.post("/api/customer/ai/chat", headers=headers, json={
        "usage_id": usage, "messages": [{"role": "user", "content": content}],
    })

    assert response.status_code == 400


def test_chat_gateway_accepts_bounded_data_image_and_rejects_oversize(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.text", "data-image")
    monkeypatch.setattr(auth_server, "MAX_CHAT_IMAGE_BYTES", 4, raising=False)
    monkeypatch.setenv("WH_TEXT_API_KEY", "server-secret")
    provider_calls = 0

    def fake_post(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return _Response({"choices": [{"message": {"content": "vision"}}]})

    monkeypatch.setattr(auth_server.requests, "post", fake_post)

    def payload(raw: bytes) -> dict:
        return {
            "usage_id": usage,
            "messages": [{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(raw).decode()}},
                {"type": "text", "text": "describe"},
            ]}],
        }

    accepted = client.post("/api/customer/ai/chat", headers=headers, json=payload(b"1234"))
    rejected = client.post("/api/customer/ai/chat", headers=headers, json=payload(b"12345"))

    assert accepted.status_code == 200
    assert rejected.status_code == 400
    assert provider_calls == 1


def test_image_poll_rejects_redirect_without_waiting(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "poll-redirect")
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setattr(auth_server.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(
        auth_server.requests,
        "post",
        lambda *_args, **_kwargs: _Response({"code": 200, "data": {"id": "task-redirect"}}),
    )
    get_calls = 0

    def fake_get(*_args, **_kwargs):
        nonlocal get_calls
        get_calls += 1
        return _Response({}, status_code=302)

    monkeypatch.setattr(auth_server.requests, "get", fake_get)
    response = client.post("/api/customer/ai/image", headers=headers, json={
        "usage_id": usage, "prompt": "product", "size": "1:1",
    })

    assert response.status_code == 502
    assert get_calls == 1


def test_image_poll_rejects_invalid_json_without_waiting(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "poll-json")
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setattr(auth_server.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(
        auth_server.requests,
        "post",
        lambda *_args, **_kwargs: _Response({"code": 200, "data": {"id": "task-json"}}),
    )
    get_calls = 0

    def fake_get(*_args, **_kwargs):
        nonlocal get_calls
        get_calls += 1
        return _InvalidJsonResponse()

    monkeypatch.setattr(auth_server.requests, "get", fake_get)
    response = client.post("/api/customer/ai/image", headers=headers, json={
        "usage_id": usage, "prompt": "product", "size": "1:1",
    })

    assert response.status_code == 502
    assert get_calls == 1


def test_image_poll_rejects_private_result_url_without_waiting(monkeypatch) -> None:
    monotonic_values = iter([0.0, 0.0, 621.0])
    monkeypatch.setattr(auth_server.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(auth_server.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(
        auth_server.requests,
        "get",
        lambda *_args, **_kwargs: _Response({
            "code": 200,
            "data": {"url": "https://127.0.0.1/private.png"},
        }),
    )

    with pytest.raises(auth_server.HTTPException) as captured:
        auth_server._poll_server_wuyin("server-secret", "task-private")

    assert captured.value.status_code == 502


def test_image_submit_rejects_invalid_json(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "submit-json")
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setattr(auth_server.requests, "post", lambda *_args, **_kwargs: _InvalidJsonResponse())

    response = client.post("/api/customer/ai/image", headers=headers, json={
        "usage_id": usage, "prompt": "product", "size": "1:1",
    })

    assert response.status_code == 503
    assert response.json() == {"detail": "server image submit outcome is uncertain"}


def test_image_submit_rejects_malformed_provider_code(tmp_path: Path, monkeypatch) -> None:
    client, headers = _client(tmp_path, monkeypatch)
    usage = _reserved_usage(client, headers, "product_processing.image_grid_2k", "bad-code")
    monkeypatch.setenv("WH_WUYIN_IMAGE_API_KEY", "server-secret")
    monkeypatch.setattr(
        auth_server.requests,
        "post",
        lambda *_args, **_kwargs: _Response({"code": "invalid", "data": {"id": "task"}}),
    )
    response = client.post("/api/customer/ai/image", headers=headers, json={
        "usage_id": usage, "prompt": "product", "size": "1:1",
    })

    assert response.status_code == 503
    assert response.json() == {"detail": "server image submit outcome is uncertain"}


def test_provider_config_is_server_managed_and_ignores_local_upstream_keys(monkeypatch) -> None:
    from types import SimpleNamespace

    runtime_config = SimpleNamespace(
        text_ai=SimpleNamespace(base_url="https://local-text.example/v1", api_key="local-text-secret"),
        image_ai=SimpleNamespace(model="image_gpt", configured=True, api_key="local-image-secret"),
        backup_image_ai=SimpleNamespace(
            base_url="https://direct-backup.example/v1",
            api_key="local-backup-secret",
            model="backup-model",
            reference_model="backup-reference",
            configured=True,
        ),
        cos=SimpleNamespace(configured=False),
        limits={},
        updates={},
    )
    monkeypatch.setattr(provider_config, "_try_system_runtime_config", lambda: runtime_config)
    monkeypatch.setenv("WH_AI_API_KEY", "local-secret")

    resolved = provider_config.resolve_ai_provider()
    summary = provider_config.ai_provider_summary()

    assert resolved["base_url"] == "server-managed"
    assert resolved["api_key"] == ""
    assert resolved["_sys_image_ai"]["base_url"] == "server-managed-wuyin"
    assert resolved["_sys_image_ai"]["api_key"] == "server-managed"
    assert resolved["_sys_backup_image_ai"] is None
    assert summary["api_key_masked"] == "server-managed"
    assert summary["enabled"] is True
    serialized = json.dumps({"resolved": resolved, "summary": summary})
    assert "local-secret" not in serialized
    assert "local-text-secret" not in serialized
    assert "local-image-secret" not in serialized
    assert "local-backup-secret" not in serialized
    assert "direct-backup.example" not in serialized
    providers = ProductImageProcessor._providers({
        "image": resolved["_sys_image_ai"],
        "backup_image": {
            "base_url": "https://direct-backup.example/v1",
            "api_key": "local-backup-secret",
            "model": "backup-model",
        },
    })
    assert [item["base_url"] for item in providers] == ["server-managed-wuyin"]
    assert "local-backup-secret" not in json.dumps(providers)


def test_provider_resolution_uses_public_and_cos_only_runtime_accessor(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "settings.sqlite3"
    init_db(db_path)
    with transaction(db_path) as conn:
        conn.executemany(
            "INSERT INTO secret_values(scope, name, ciphertext, updated_at) VALUES (?, ?, ?, datetime('now'))",
            [
                ("ai", "api_key", "ai-secret-ciphertext"),
                ("image", "api_key", "image-secret-ciphertext"),
                ("backup_image", "api_key", "backup-secret-ciphertext"),
                ("cos", "secret_id", "cos-id-ciphertext"),
                ("cos", "secret_key", "cos-key-ciphertext"),
            ],
        )
    decrypted: list[str] = []

    def fake_decrypt(ciphertext: str) -> str:
        decrypted.append(ciphertext)
        if not ciphertext.startswith("cos-"):
            raise AssertionError("AI provider secret must not be decrypted")
        return "cos-value"

    monkeypatch.setattr(basic_settings_service, "decrypt_secret", fake_decrypt)
    monkeypatch.setattr(
        basic_settings_service.SystemConfigService,
        "get_runtime_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("secret runtime path called")),
    )
    monkeypatch.setattr(provider_config, "_system_config_db_path", str(db_path))

    resolved = provider_config.resolve_ai_provider()

    assert resolved["_sys_cos"]["secret_id"] == "cos-value"
    assert resolved["_sys_cos"]["secret_key"] == "cos-value"
    assert decrypted == ["cos-id-ciphertext", "cos-key-ciphertext"]


def test_image_adapter_calls_platform_gateway_and_downloads_safe_result(monkeypatch) -> None:
    requests_seen: list[tuple[str, dict]] = []

    class _MediaSession:
        def post(self, url, **kwargs):
            requests_seen.append((url, kwargs))
            return _Response({"ok": True, "result_url": "https://images.example.test/result.png"})

    monkeypatch.setattr(media_module, "_SESSION", _MediaSession())
    monkeypatch.setattr(media_module, "is_safe_external_url", lambda _url: True)
    monkeypatch.setattr(
        media_module,
        "_download_pinned_public_image",
        lambda *_args, **_kwargs: (b"image-bytes", "image/png"),
    )
    processor = ProductImageProcessor(lambda: {})
    provider = {
        "base_url": "server-managed-wuyin",
        "api_key": "server-managed",
        "model": "image_gpt",
        "reference_model": "image_gpt",
        "image_size": "2048x2048",
    }

    with server_ai_context("platform-token", {"image_grid": "usage-image"}):
        content, content_type = processor._request_edit(
            provider,
            "product prompt",
            [(b"source", "source.jpg", "image/jpeg", "https://images.example.test/source.jpg")],
        )

    assert (content, content_type) == (b"image-bytes", "image/png")
    gateway_url, gateway_request = requests_seen[0]
    assert gateway_url.endswith("/api/customer/ai/image")
    assert gateway_request["headers"]["Authorization"] == "Bearer platform-token"
    assert gateway_request["json"]["usage_id"] == "usage-image"
    assert gateway_request["json"]["urls"] == ["https://images.example.test/source.jpg"]


def test_server_managed_images_are_not_globally_serialized(monkeypatch) -> None:
    entered = threading.Barrier(2)
    active_lock = threading.Lock()
    active = 0
    max_active = 0
    requests_seen: list[str] = []

    class _ConcurrentMediaSession:
        def post(self, _url, **kwargs):
            nonlocal active, max_active
            usage = str(kwargs["json"]["usage_id"])
            with active_lock:
                requests_seen.append(usage)
                active += 1
                max_active = max(max_active, active)
            try:
                entered.wait(timeout=2)
                return _Response({"ok": True, "result_url": "https://images.example.test/result.png"})
            finally:
                with active_lock:
                    active -= 1

    monkeypatch.setattr(media_module, "_SESSION", _ConcurrentMediaSession())
    monkeypatch.setattr(media_module, "is_safe_external_url", lambda _url: True)
    monkeypatch.setattr(
        media_module,
        "_download_pinned_public_image",
        lambda *_args, **_kwargs: (b"image-bytes", "image/png"),
    )
    provider = {
        "base_url": "server-managed-wuyin",
        "api_key": "server-managed",
        "model": "image_gpt",
        "reference_model": "image_gpt",
        "image_size": "2048x2048",
    }

    def request_image(index: int) -> tuple[bytes, str]:
        processor = ProductImageProcessor(lambda: {})
        with server_ai_context(f"platform-token-{index}", {"image_grid": f"usage-{index}"}):
            return processor._request_edit(
                provider,
                f"product prompt {index}",
                [(b"source", "source.jpg", "image/jpeg", "https://images.example.test/source.jpg")],
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request_image, (1, 2)))

    assert results == [(b"image-bytes", "image/png")] * 2
    assert sorted(requests_seen) == ["usage-1", "usage-2"]
    assert max_active == 2


@pytest.mark.parametrize(
    ("status_code", "expected_class", "retryable"),
    [
        (402, "billing_payment_required", False),
        (403, "billing_forbidden", False),
        (409, "gateway_in_progress", True),
        (502, "gateway_bad_response", True),
        (503, "gateway_unavailable", True),
    ],
)
def test_image_adapter_preserves_stable_gateway_status_without_body_leak(
    monkeypatch, status_code: int, expected_class: str, retryable: bool
) -> None:
    secret = "remote-body-secret platform-token"

    class _MediaSession:
        def post(self, *_args, **_kwargs):
            return _Response({"detail": secret}, status_code=status_code)

    monkeypatch.setattr(media_module, "_SESSION", _MediaSession())
    processor = ProductImageProcessor(lambda: {})
    provider = {
        "base_url": "server-managed-wuyin",
        "api_key": "server-managed",
        "model": "image_gpt",
        "reference_model": "image_gpt",
        "image_size": "2048x2048",
    }

    with server_ai_context("platform-token", {"image_grid": "usage-image"}):
        with pytest.raises(media_module.MediaProcessingError) as caught:
            processor._request_server_managed_wuyin_image(
                provider,
                "product prompt",
                [],
                timeout_seconds=60,
            )

    assert caught.value.status_code == status_code
    assert caught.value.status_class == expected_class
    assert secret not in str(caught.value)
    assert "platform-token" not in str(caught.value)
    assert (media_module._retry_class(caught.value) not in {"non_retryable_4xx", "non_retryable_local"}) is retryable


def test_provider_result_download_pins_validated_ip_and_keeps_original_tls_host(
    monkeypatch,
) -> None:
    resolver_calls = 0
    connections: list[tuple[str, str, int, float]] = []
    requests_seen: list[tuple[str, str, dict[str, str]]] = []

    def resolve(url: str):
        nonlocal resolver_calls
        resolver_calls += 1
        return SimpleNamespace(
            url=url,
            hostname="images.example.test",
            port=443,
            addresses=("8.8.8.8",),
        )

    class PinnedConnection:
        def __init__(self, hostname: str, pinned_address: str, port: int, timeout: float):
            connections.append((hostname, pinned_address, port, timeout))

        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            requests_seen.append((method, path, headers))

        def getresponse(self):
            return SimpleNamespace(
                status=200,
                getheader=lambda name, default="": "image/png" if name == "Content-Type" else default,
                read=lambda limit: b"pinned-image",
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(media_module, "resolve_safe_external_url", resolve)
    monkeypatch.setattr(media_module, "_PinnedHTTPSConnection", PinnedConnection)

    content, content_type = media_module._download_pinned_public_image(
        "https://images.example.test/result.png?x=1",
        timeout_seconds=20,
    )

    assert (content, content_type) == (b"pinned-image", "image/png")
    assert resolver_calls == 1
    assert connections == [("images.example.test", "8.8.8.8", 443, 20)]
    assert requests_seen == [
        ("GET", "/result.png?x=1", {"Host": "images.example.test", "Accept": "image/*"})
    ]


def test_provider_result_download_rejects_redirect_without_re_resolving(monkeypatch) -> None:
    resolver_calls = 0

    def resolve(url: str):
        nonlocal resolver_calls
        resolver_calls += 1
        if resolver_calls > 1:
            raise AssertionError("validated hostname must not be resolved again")
        return SimpleNamespace(
            url=url,
            hostname="images.example.test",
            port=443,
            addresses=("8.8.8.8",),
        )

    class RedirectConnection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs) -> None:
            pass

        def getresponse(self):
            return SimpleNamespace(
                status=302,
                getheader=lambda *_args, **_kwargs: "https://127.0.0.1/private",
                read=lambda _limit: b"",
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr(media_module, "resolve_safe_external_url", resolve)
    monkeypatch.setattr(media_module, "_PinnedHTTPSConnection", RedirectConnection)

    with pytest.raises(media_module.MediaProcessingError, match="redirected"):
        media_module._download_pinned_public_image(
            "https://images.example.test/result.png",
            timeout_seconds=20,
        )

    assert resolver_calls == 1


def test_provider_result_download_rejects_unresolved_url_with_stable_error(monkeypatch) -> None:
    monkeypatch.setattr(media_module, "resolve_safe_external_url", lambda _url: None)

    with pytest.raises(media_module.MediaProcessingError, match="safe public URL"):
        media_module._download_pinned_public_image(
            "https://unresolved.example.test/result.png"
        )


def test_pinned_image_download_rejects_oversized_stream(monkeypatch) -> None:
    monkeypatch.setattr(
        media_module,
        "resolve_safe_external_url",
        lambda url: SimpleNamespace(
            url=url,
            hostname="images.example.test",
            port=443,
            addresses=("8.8.8.8",),
        ),
    )

    class OversizedConnection:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs) -> None:
            pass

        def getresponse(self):
            return SimpleNamespace(
                status=200,
                getheader=lambda *_args, **_kwargs: "image/png",
                read=lambda limit: b"x" * limit,
            )

        def close(self) -> None:
            pass

    monkeypatch.setattr(media_module, "_PinnedHTTPSConnection", OversizedConnection)

    with pytest.raises(media_module.MediaProcessingError, match="download limit"):
        media_module._download_pinned_public_image(
            "https://images.example.test/large.png"
        )
