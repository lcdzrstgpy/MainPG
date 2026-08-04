from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.contracts import (  # noqa: E402
    PluginCommandRequest,
    PriceVerificationActor,
)
from wh_local.price_verification.plugin.routes import (  # noqa: E402
    PluginBridgeRouteDependencies,
    register_plugin_bridge_routes,
)
from wh_local.price_verification.plugin.service import PluginBridgeService  # noqa: E402
from wh_local.price_verification.repository import PriceVerificationRepository  # noqa: E402


def _client(
    service: PluginBridgeService,
    *,
    workspace_id: str,
) -> TestClient:
    app = FastAPI()
    router = APIRouter()
    register_plugin_bridge_routes(
        router,
        PluginBridgeRouteDependencies(
            service=service,
            resolve_actor=lambda: PriceVerificationActor(
                actor_id=f"user-{workspace_id}", workspace_id=workspace_id
            ),
        ),
    )
    app.include_router(router)
    return TestClient(app)


def _service(tmp_path: Path) -> tuple[PluginBridgeService, PriceVerificationRepository]:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    return PluginBridgeService(repository=repository), repository


def test_connect_uses_pairing_bearer_header_and_returns_only_session_token(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    client = _client(service, workspace_id="A")
    pairing = service.issue_pairing_code(PriceVerificationActor(actor_id="user-A", workspace_id="A"))

    response = client.post(
        "/plugin/connect",
        headers={"Authorization": f"Bearer {pairing.code}"},
        json={"browser_name": "Edge", "capabilities": {"temu_price_quote_discovery": True}},
    )

    assert response.status_code == 200
    assert response.json()["session_token"]
    assert "pairing_code" not in response.json()


def test_poll_and_result_accept_session_token_only_in_json(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    client = _client(service, workspace_id="A")
    pairing = service.issue_pairing_code(PriceVerificationActor(actor_id="user-A", workspace_id="A"))
    session = service.connect(pairing.code, browser_name="Edge", capabilities={})

    bad_poll = client.post(
        "/plugin/poll",
        headers={"Authorization": f"Bearer {session.token}"},
        json={"session_token": session.token},
    )
    bad_result = client.post(
        "/plugin/result",
        headers={"Authorization": f"Bearer {session.token}"},
        json={"session_token": session.token, "command_id": "missing", "status": "succeeded", "result": {}},
    )

    assert bad_poll.status_code == 401
    assert bad_result.status_code == 401


def test_routes_reject_platform_write_before_service_persistence(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    client = _client(service, workspace_id="A")
    pairing = service.issue_pairing_code(PriceVerificationActor(actor_id="user-A", workspace_id="A"))

    response = client.post(
        "/plugin/connect",
        headers={"Authorization": f"Bearer {pairing.code}"},
        json={"browser_name": "Edge", "capabilities": {"action": "update_price"}},
    )

    assert response.status_code == 422
    assert "platform write" in response.json()["detail"]


def test_workspace_cannot_consume_another_workspaces_pairing_code(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    client_a = _client(service, workspace_id="A")
    client_b = _client(service, workspace_id="B")
    pairing = service.issue_pairing_code(PriceVerificationActor(actor_id="user-A", workspace_id="A"))

    rejected = client_b.post(
        "/plugin/connect",
        headers={"Authorization": f"Bearer {pairing.code}"},
        json={"browser_name": "Edge", "capabilities": {}},
    )
    connected = client_a.post(
        "/plugin/connect",
        headers={"Authorization": f"Bearer {pairing.code}"},
        json={"browser_name": "Edge", "capabilities": {}},
    )

    assert rejected.status_code == 404
    assert "not found" in rejected.json()["detail"]
    assert connected.status_code == 200


def test_valid_session_cannot_access_another_workspaces_command(tmp_path: Path) -> None:
    service, repository = _service(tmp_path)
    client_a = _client(service, workspace_id="A")
    session_a = service.connect(
        service.issue_pairing_code(PriceVerificationActor(actor_id="user-A", workspace_id="A")).code,
        browser_name="Edge",
        capabilities={},
    )
    session_b = service.connect(
        service.issue_pairing_code(PriceVerificationActor(actor_id="user-B", workspace_id="B")).code,
        browser_name="Chrome",
        capabilities={},
    )
    command_b = repository.create_command(
        workspace_id="B",
        session_id=session_b.session_id,
        request=PluginCommandRequest(
            command_type="temu_price_quote_discovery", payload={}, idempotency_key="workspace-b-command"
        ),
    )

    not_found = client_a.post(
        "/plugin/result",
        json={
            "session_token": session_a.token,
            "command_id": command_b.command_id,
            "status": "succeeded",
            "result": {},
        },
    )
    unauthenticated = client_a.post(
        "/plugin/result",
        json={
            "session_token": "invalid-session-token",
            "command_id": command_b.command_id,
            "status": "succeeded",
            "result": {},
        },
    )

    assert not_found.status_code == 404
    assert "not found" in not_found.json()["detail"]
    assert unauthenticated.status_code == 401
