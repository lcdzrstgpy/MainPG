from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.routes import (  # noqa: E402
    PriceVerificationRouteDependencies,
    register_price_verification_routes,
)


@dataclass(frozen=True)
class _HostActor:
    id: str
    username: str
    role: str


def _host_actor(authorization: str | None = Header(default=None)) -> _HostActor:
    if authorization != "Bearer dev-admin-token":
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return _HostActor(id="local-demo-admin", username="local-demo", role="admin")


def _host_equivalent_client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    router = APIRouter()
    register_price_verification_routes(
        router,
        PriceVerificationRouteDependencies(
            resolve_actor=_host_actor,
            database_path=tmp_path / "runtime.sqlite3",
            output_root=tmp_path / "outputs",
        ),
    )
    app.include_router(router)
    return TestClient(app)


def test_host_equivalent_routes_allow_pairing_code_connect_without_business_token(
    tmp_path: Path,
) -> None:
    """Isolated because app.main is blocked by provider.py:482 IndentationError."""
    client = _host_equivalent_client(tmp_path)
    pairing = client.post(
        "/api/v1/price-verification/plugin/pairing-codes",
        headers={"Authorization": "Bearer dev-admin-token"},
    )
    assert pairing.status_code == 200

    connected = client.post(
        "/plugin/connect",
        headers={"Authorization": f"Bearer {pairing.json()['code']}"},
        json={"browser_name": "Edge", "capabilities": {}},
    )
    assert connected.status_code == 200

    pairing_as_business_token = client.get(
        "/api/v1/price-verification/plugin/sessions",
        headers={"Authorization": f"Bearer {pairing.json()['code']}"},
    )
    business_sessions = client.get(
        "/api/v1/price-verification/plugin/sessions",
        headers={"Authorization": "Bearer dev-admin-token"},
    )

    assert pairing_as_business_token.status_code == 401
    assert business_sessions.status_code == 200
    assert business_sessions.json()["sessions"][0]["id"] == connected.json()["session_id"]
