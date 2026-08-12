from __future__ import annotations

from fastapi.testclient import TestClient

from wh_local.app.main import create_app
from wh_local.modules.ai_service.router import _station_api_key
from wh_local.modules.basic_settings.service import RuntimeAiConfig, RuntimeCosConfig, RuntimeSystemConfig


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
    b"IDAT\x08\xd7c\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb1"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_ai_service_local_routes_require_auth_and_store_uploaded_asset(tmp_path) -> None:
    client = TestClient(create_app(tmp_path / "workbench.sqlite3"))

    denied = client.get("/api/ai-service/bootstrap")
    assert denied.status_code == 401

    headers = {"Authorization": "Bearer dev-admin-token"}
    bootstrap = client.get("/api/ai-service/bootstrap", headers=headers)
    assert bootstrap.status_code == 200
    assert {item["id"] for item in bootstrap.json()["models"]} == {
        "deepseek-v4-flash", "deepseek-v4-pro", "gpt-5.6-terra",
        "gpt-image-2-1k", "gpt-image-2-2k", "gpt-image-2-4k",
    }

    uploaded = client.post(
        "/api/ai-service/assets",
        headers=headers,
        files={"file": ("cup.png", PNG, "image/png")},
    )
    assert uploaded.status_code == 200
    asset_id = uploaded.json()["asset_id"]
    content = client.get(f"/api/ai-service/assets/{asset_id}", headers=headers)
    assert content.status_code == 200
    assert content.content == PNG


def test_ai_service_uses_the_single_station_key_from_system_config() -> None:
    runtime = RuntimeSystemConfig(
        text_ai=RuntimeAiConfig(base_url="https://station-88.aicoming.top/v1", model="gpt-5.6-terra", api_key="station-key"),
        image_ai=RuntimeAiConfig(base_url="https://station-88.aicoming.top/v1", model="gpt-image-2-1k", api_key="station-key"),
        backup_image_ai=RuntimeAiConfig(base_url="", model="", api_key=""),
        cos=RuntimeCosConfig(bucket="", region="ap-guangzhou", secret_id="", secret_key=""),
        limits={},
        updates={},
    )

    assert _station_api_key(runtime) == "station-key"
