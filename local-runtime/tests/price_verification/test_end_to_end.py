from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.app.main import create_app  # noqa: E402


def test_host_registers_price_verification_routes_after_existing_modules(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "runtime.sqlite3"))
    response = client.get(
        "/api/v1/price-verification/plugin/sessions",
        headers={"Authorization": "Bearer dev-admin-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"sessions": []}
