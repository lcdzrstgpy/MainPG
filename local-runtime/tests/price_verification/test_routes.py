from __future__ import annotations

import sys
import json
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.contracts import PriceVerificationActor  # noqa: E402
from wh_local.price_verification.routes import (  # noqa: E402
    PriceVerificationRouteDependencies,
    register_price_verification_routes,
)


def auth() -> dict[str, str]:
    return {"Authorization": "Bearer workspace-a"}


def quote_fixture() -> dict[str, object]:
    path = Path(__file__).parent / "fixtures" / "temu_quote_popup_dom.json"
    return json.loads(path.read_text(encoding="utf-8"))


def client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    router = APIRouter()
    register_price_verification_routes(
        router,
        PriceVerificationRouteDependencies(
            resolve_actor=lambda: PriceVerificationActor(
                actor_id="user-a", workspace_id="workspace-a"
            ),
            database_path=tmp_path / "runtime.sqlite3",
            output_root=tmp_path / "outputs",
        ),
    )
    app.include_router(router)
    return TestClient(app)


def connect_plugin(client: TestClient) -> dict[str, str]:
    pairing = client.post(
        "/api/v1/price-verification/plugin/pairing-codes", headers=auth()
    )
    assert pairing.status_code == 200
    connected = client.post(
        "/plugin/connect",
        headers={"Authorization": f"Bearer {pairing.json()['code']}"},
        json={"browser_name": "Edge", "capabilities": {}},
    )
    assert connected.status_code == 200
    return {"id": connected.json()["session_id"], "token": connected.json()["session_token"]}


def post_plugin_result(
    client: TestClient, session_token: str, command_id: str, result: dict[str, object]
) -> None:
    polled = client.post("/plugin/poll", json={"session_token": session_token})
    assert polled.status_code == 200
    received = client.post(
        "/plugin/result",
        json={
            "session_token": session_token,
            "command_id": command_id,
            "status": "succeeded",
            "result": result,
        },
    )
    assert received.status_code == 200


def test_legacy_command_to_preview_uses_persisted_quote_snapshot(tmp_path: Path) -> None:
    app_client = client(tmp_path)
    session = connect_plugin(app_client)
    command = app_client.post(
        f"/plugin/sessions/{session['id']}/commands",
        json={"command_type": "temu_price_quote_discovery"},
        headers=auth(),
    )
    assert command.status_code == 200
    command_id = command.json()["command_id"]
    post_plugin_result(app_client, session["token"], command_id, quote_fixture())

    preview = app_client.post(
        "/local/price-quote-discovery/preview",
        json={"command_id": command_id},
        headers=auth(),
    )

    assert preview.status_code == 200
    assert preview.json()["counts"]["complete_quotes"] == 1
    assert preview.json()["quotes"][0]["skc_id"] == "SKC-1001"


def test_formal_quote_run_apis_and_legacy_commands_share_the_same_snapshot(tmp_path: Path) -> None:
    app_client = client(tmp_path)
    session = connect_plugin(app_client)
    command = app_client.post(
        "/api/v1/price-verification/quote-runs",
        json={"session_id": session["id"], "idempotency_key": "quote-a"},
        headers=auth(),
    )
    assert command.status_code == 200
    command_id = command.json()["command_id"]
    post_plugin_result(app_client, session["token"], command_id, quote_fixture())

    materialized = app_client.post(
        "/api/v1/price-verification/quote-runs",
        json={"command_id": command_id},
        headers=auth(),
    )
    assert materialized.status_code == 200
    run_id = materialized.json()["run_id"]

    run = app_client.get(f"/api/v1/price-verification/quote-runs/{run_id}", headers=auth())
    items = app_client.get(
        f"/api/v1/price-verification/quote-runs/{run_id}/items", headers=auth()
    )

    assert run.status_code == 200
    assert run.json()["command_id"] == command_id
    assert items.status_code == 200
    assert items.json()["counts"]["complete_quotes"] == 1
