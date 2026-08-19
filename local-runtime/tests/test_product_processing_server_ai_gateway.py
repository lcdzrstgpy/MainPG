from __future__ import annotations

import base64
import contextvars
import json
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


_EMAIL_CODE_SECRET = "gateway-test-secret-that-is-at-least-32-chars"


class _Response:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.closed = False

    def json(self) -> dict:
        return self.payload

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


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, dict[str, str]]:
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(auth_server.TencentCloudSESEmailSender, "from_env", lambda: object())
    db_path = tmp_path / "auth.sqlite3"
    client = TestClient(create_auth_app(db_path))
    headers = {"Authorization": f"Bearer {_register_and_login(client, db_path)}"}
    _grant_points(db_path)
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
        for index in range(3)
    ]

    assert statuses == [200, 200, 409]
    assert provider_calls == 2


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
    assert dict(row) == {"status": "succeeded", "charged_points": 30, "refunded_points": 20}


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
    assert dict(event) == {"status": "succeeded", "charged_points": 30, "refunded_points": 20}
    assert [dict(row) for row in settlement_entries] == [
        {"direction": "debit", "points_delta": 30},
        {"direction": "unlock", "points_delta": 20},
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
        raise requests.Timeout("provider failed")

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

    assert response.status_code == 502


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

    assert response.status_code == 502


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

    assert response.status_code == 502


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

        def get(self, url, **kwargs):
            requests_seen.append((url, kwargs))
            response = _Response({})
            response.content = b"image-bytes"
            response.headers = {"Content-Type": "image/png"}
            return response

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
