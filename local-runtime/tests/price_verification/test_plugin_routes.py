from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.contracts import PriceVerificationActor  # noqa: E402
from wh_local.price_verification.plugin.routes import (  # noqa: E402
    PluginBridgeRouteDependencies,
    register_plugin_bridge_routes,
)
from wh_local.price_verification.plugin.service import PluginBridgeService  # noqa: E402
from wh_local.price_verification.repository import PriceVerificationRepository  # noqa: E402


def _client(tmp_path: Path) -> tuple[TestClient, PluginBridgeService]:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    service = PluginBridgeService(repository=repository)
    app = FastAPI()
    router = APIRouter()
    register_plugin_bridge_routes(
        router,
        PluginBridgeRouteDependencies(
            service=service,
            resolve_actor=lambda: PriceVerificationActor(actor_id="user-A", workspace_id="A"),
        ),
    )
    app.include_router(router)
    return TestClient(app), service


def test_connect_uses_pairing_bearer_header_and_returns_only_session_token(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
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
    client, service = _client(tmp_path)
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
    client, service = _client(tmp_path)
    pairing = service.issue_pairing_code(PriceVerificationActor(actor_id="user-A", workspace_id="A"))

    response = client.post(
        "/plugin/connect",
        headers={"Authorization": f"Bearer {pairing.code}"},
        json={"browser_name": "Edge", "capabilities": {"action": "update_price"}},
    )

    assert response.status_code == 422
    assert "platform write" in response.json()["detail"]
