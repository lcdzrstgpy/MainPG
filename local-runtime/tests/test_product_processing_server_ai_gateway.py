from __future__ import annotations

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from wh_local.customer import auth_server
from wh_local.customer.auth_server import create_auth_app
from wh_local.customer.auth_service import _email_code_digest
from wh_local.db import transaction
from wh_local.modules.product_processing.server_ai_proxy import remote_token, server_ai_context, usage_id
from wh_local.modules.product_processing import provider_config
from wh_local.modules.product_processing.infrastructure import media as media_module
from wh_local.modules.product_processing.infrastructure.media import ProductImageProcessor


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


def _register_and_login(client: TestClient, db_path: Path) -> str:
    email = "gateway_user@example.test"
    verification_id = "ver_gateway_user"
    with transaction(db_path) as conn:
        conn.execute(
            "INSERT INTO invitation_codes (code, max_uses, used_count, expires_at, created_by, created_at) VALUES ('MAINPG-GATEWAY', 10, 0, '', 'test', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO auth_email_verifications (verification_id, email, token_hash, purpose, expires_at) VALUES (?, ?, ?, 'register', '9999-12-31T00:00:00+00:00')",
            (verification_id, email, _email_code_digest(_EMAIL_CODE_SECRET, verification_id, email, "register", "654321")),
        )
    assert client.post("/api/customer/register", json={
        "username": "gateway_user", "email": email, "email_code": "654321",
        "password": "StrongPassword123!", "invitation_code": "MAINPG-GATEWAY", "workspace_code": "gateway-ws",
    }).status_code == 200
    login = client.post("/api/customer/login", json={"username": "gateway_user", "password": "StrongPassword123!"})
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
        return _Response({"choices": [{"message": {"content": "safe reply"}}]})

    monkeypatch.setattr(auth_server.requests, "post", fake_post)
    chat = client.post("/api/customer/ai/chat", headers=headers, json={
        "model": "gpt-5.6-terra", "usage_id": usage, "messages": [{"role": "user", "content": "hello"}],
    })

    assert chat.status_code == 200
    assert provider_requests[0]["headers"]["Authorization"] == "Bearer server-secret"
    assert "server-secret" not in json.dumps(chat.json())


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


def test_provider_config_is_server_managed_and_ignores_local_upstream_keys(monkeypatch) -> None:
    monkeypatch.setattr(provider_config, "_try_system_runtime_config", lambda: None)
    monkeypatch.setenv("WH_AI_API_KEY", "local-secret")

    resolved = provider_config.resolve_ai_provider()
    summary = provider_config.ai_provider_summary()

    assert resolved["base_url"] == "server-managed"
    assert resolved["api_key"] == ""
    assert resolved["_sys_image_ai"]["base_url"] == "server-managed-wuyin"
    assert resolved["_sys_image_ai"]["api_key"] == "server-managed"
    assert summary["api_key_masked"] == "server-managed"
    assert summary["enabled"] is True
    assert "local-secret" not in json.dumps({"resolved": resolved, "summary": summary})


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
