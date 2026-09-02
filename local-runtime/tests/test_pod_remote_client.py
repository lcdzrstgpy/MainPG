from __future__ import annotations

from wh_local.customer import remote_client as remote_client_module
from wh_local.customer.remote_client import CustomerAuthClient
from wh_local.modules.pod_customization.billing_contract import PodCallPlan
from wh_local.modules.pod_customization.remote_billing import RemotePodBillingCoordinator
from wh_local.session import Actor


def test_remote_client_wraps_pod_freeze_session_and_decrypts_grants_in_memory(monkeypatch) -> None:
    client = CustomerAuthClient("https://customer.example.test")
    assert hasattr(client, "freeze_pod_points"), "POD remote billing adapter is missing"
    calls: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        remote_client_module,
        "_new_pod_grant_session",
        lambda: ("rsa-wrapped-session", b"session-key"),
    )
    monkeypatch.setattr(
        remote_client_module,
        "_decrypt_pod_grant_envelope",
        lambda envelope, key: {
            "freeze_id": "pod-freeze-stable-key-0001",
            "expires_at": "2030-01-01T06:00:00+00:00",
            "keys": [{"provider": "ark", "api_key": "in-memory-only"}],
        },
    )

    def billing_post(path: str, token: str, payload: dict) -> dict:
        calls.append((path, token, payload))
        return {
            "ok": True,
            "freeze": {
                "freeze_id": "pod-freeze-stable-key-0001",
                "rule_version": 7,
                "expires_at": "2030-01-08T00:00:00+00:00",
                "grant_envelope": {"payload": "cipher", "nonce": "nonce", "tag": "tag"},
            },
        }

    monkeypatch.setattr(client, "_billing_post", billing_post)
    plan = {
        "idempotency_key": "pod-freeze-stable-key-0001",
        "title_call_count": 1,
        "image_call_count": 0,
        "calls": [{"call_id": "title-call-0001", "feature": "pod.title"}],
    }

    result = client.freeze_pod_points("remote-token", plan)

    assert calls == [
        (
            "/api/customer/billing/pod/freeze",
            "remote-token",
            {**plan, "encrypted_session_key": "rsa-wrapped-session"},
        )
    ]
    assert "encrypted_session_key" not in plan
    assert result["freeze"]["keys"] == {"ark": "in-memory-only"}
    assert result["freeze"]["expires_at"] == "2030-01-01T06:00:00+00:00"
    assert result["freeze"]["freeze_expires_at"] == "2030-01-08T00:00:00+00:00"
    assert "grant_envelope" not in result["freeze"]


def test_remote_client_pod_settle_status_and_regrant_use_dedicated_paths(monkeypatch) -> None:
    client = CustomerAuthClient("https://customer.example.test")
    assert hasattr(client, "regrant_pod_keys"), "POD regrant adapter is missing"
    posts: list[tuple[str, str, dict]] = []
    gets: list[str] = []
    monkeypatch.setattr(
        remote_client_module,
        "_new_pod_grant_session",
        lambda: ("rsa-wrapped-session", b"session-key"),
    )
    monkeypatch.setattr(
        remote_client_module,
        "_decrypt_pod_grant_envelope",
        lambda envelope, key: {
            "freeze_id": "pod-freeze-stable-key-0001",
            "expires_at": "2030-01-01T06:00:00+00:00",
            "keys": [{"provider": "wuyin", "api_key": "memory"}],
        },
    )

    def billing_post(path: str, token: str, payload: dict) -> dict:
        posts.append((path, token, payload))
        if path.endswith("/regrant"):
            return {
                "ok": True,
                "freeze_id": "pod-freeze-stable-key-0001",
                "rule_version": 7,
                "grant_envelope": {"payload": "cipher", "nonce": "nonce", "tag": "tag"},
            }
        return {"ok": True, "settle": {"status": "settled"}}

    def billing_result(func, path: str, **kwargs) -> dict:
        gets.append(path)
        return {"ok": True, "freeze": {"status": "frozen"}}

    monkeypatch.setattr(client, "_billing_post", billing_post)
    monkeypatch.setattr(client, "_billing_result", billing_result)

    settled = client.settle_pod_points(
        "remote-token",
        "pod-freeze-stable-key-0001",
        {"items": []},
    )
    status = client.pod_freeze_status("remote-token", "pod-freeze-stable-key-0001")
    regrant = client.regrant_pod_keys("remote-token", "pod-freeze-stable-key-0001")

    assert settled["settle"]["status"] == "settled"
    assert gets == ["/api/customer/billing/pod/pod-freeze-stable-key-0001"]
    assert posts[0][0] == "/api/customer/billing/pod/settle"
    assert posts[0][2]["freeze_id"] == "pod-freeze-stable-key-0001"
    assert posts[1][0] == "/api/customer/billing/pod/pod-freeze-stable-key-0001/regrant"
    assert posts[1][2] == {"encrypted_session_key": "rsa-wrapped-session"}
    assert status["freeze"]["status"] == "frozen"
    assert regrant["keys"] == {"wuyin": "memory"}
    assert regrant["rule_version"] == 7
    assert regrant["expires_at"] == "2030-01-01T06:00:00+00:00"
    assert "grant_envelope" not in regrant


def test_remote_client_uses_server_managed_pod_gateway_routes_without_key_grants(monkeypatch) -> None:
    client = CustomerAuthClient("https://customer.example.test")
    calls: list[tuple[str, str, dict]] = []

    def billing_post(path: str, token: str, payload: dict) -> dict:
        calls.append((path, token, payload))
        return {"ok": True, "result_url": "https://images.example.test/result.png"}

    monkeypatch.setattr(client, "_billing_post", billing_post)

    title = client.gateway_pod_title(
        "remote-token",
        {"usage_id": "use_pod_title", "messages": [{"role": "user", "content": "title"}]},
    )
    image = client.gateway_pod_image(
        "remote-token",
        {"usage_id": "use_pod_image", "prompt": "four images", "urls": [], "size": "1:1"},
    )

    assert title["ok"] is True
    assert image["result_url"] == "https://images.example.test/result.png"
    assert calls == [
        (
            "/api/customer/ai/pod/title",
            "remote-token",
            {"usage_id": "use_pod_title", "messages": [{"role": "user", "content": "title"}]},
        ),
        (
            "/api/customer/ai/pod/image",
            "remote-token",
            {"usage_id": "use_pod_image", "prompt": "four images", "urls": [], "size": "1:1"},
        ),
    ]


def test_server_managed_pod_billing_grant_never_requests_or_exposes_provider_keys() -> None:
    class RemoteClient:
        def freeze_batch_points(self, *_args, **_kwargs):
            raise AssertionError("server-managed POD must not request a provider-key grant")

        def settle_batch_points(self, *_args, **_kwargs):
            raise AssertionError("server-managed POD gateway settles provider outcomes itself")

    actor = Actor(id="user-1", username="user", role="admin", workspace_id="workspace-1")
    coordinator = RemotePodBillingCoordinator(
        RemoteClient(), lambda _actor: "customer-session-token", server_managed=True
    )
    plan = PodCallPlan.for_batch("batch-gateway", style_count=1)

    grant = coordinator.freeze(actor, plan)
    coordinator.settle(actor, grant, plan, ())

    assert grant.provider_keys == {}
    assert grant.provider_key("wuyin") == ""
    assert grant.remote_token == "customer-session-token"
