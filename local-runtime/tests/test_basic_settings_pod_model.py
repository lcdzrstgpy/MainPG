from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.db import init_db, transaction
from wh_local.modules.basic_settings.router import create_router
from wh_local.modules.basic_settings.schemas import SystemConfigUpdate
from wh_local.modules.basic_settings.service import CONFIG_KEY, SystemConfigService
from wh_local.session import Actor, actor_from_authorization


def _service(database_path: Path) -> SystemConfigService:
    init_db(database_path)
    return SystemConfigService(database_path)


def _client(database_path: Path) -> TestClient:
    init_db(database_path)
    app = FastAPI()
    app.include_router(create_router(database_path))
    app.dependency_overrides[actor_from_authorization] = lambda: Actor(
        "tester", "tester", "admin"
    )
    return TestClient(app)


def test_pod_image_model_defaults_to_image_gpt(tmp_path: Path) -> None:
    service = _service(tmp_path / "workbench.sqlite3")

    payload = service.get_pod_image_model()

    assert payload["ok"] is True
    assert payload["model"] == "image_gpt"
    assert {choice["value"] for choice in payload["choices"]} == {"image_gpt", "image_gpt_2.5"}


def test_saving_pod_model_does_not_touch_ai_processing_model(tmp_path: Path) -> None:
    service = _service(tmp_path / "workbench.sqlite3")
    service.save_image_model("image_gpt", actor_id="tester")

    service.save_pod_image_model("image_gpt_2.5", actor_id="tester")

    assert service.get_pod_image_model()["model"] == "image_gpt_2.5"
    # POD 切换不得影响 AI处理 的生图模型。
    assert service.get_image_model()["model"] == "image_gpt"


def test_saving_ai_processing_model_does_not_touch_pod_model(tmp_path: Path) -> None:
    service = _service(tmp_path / "workbench.sqlite3")
    service.save_pod_image_model("image_gpt_2.5", actor_id="tester")

    service.save_image_model("image_gpt", actor_id="tester")

    assert service.get_image_model()["model"] == "image_gpt"
    # AI处理 切换不得影响 POD 的生图模型。
    assert service.get_pod_image_model()["model"] == "image_gpt_2.5"


def test_full_system_config_save_preserves_pod_model(tmp_path: Path) -> None:
    service = _service(tmp_path / "workbench.sqlite3")
    service.save_pod_image_model("image_gpt_2.5", actor_id="tester")

    # 整表保存系统配置（前端表单提交路径）不得重置 POD 模型。
    service.save_config(SystemConfigUpdate(), actor_id="tester")

    assert service.get_pod_image_model()["model"] == "image_gpt_2.5"


def test_unknown_pod_model_falls_back_to_default(tmp_path: Path) -> None:
    service = _service(tmp_path / "workbench.sqlite3")

    service.save_pod_image_model("gpt-image-2-4k", actor_id="tester")

    assert service.get_pod_image_model()["model"] == "image_gpt"


def test_legacy_database_without_pod_section_gains_default(tmp_path: Path) -> None:
    database_path = tmp_path / "workbench.sqlite3"
    service = _service(database_path)
    legacy_config = {
        "ai": {"base_url": "https://station-88.aicoming.top/v1", "model": "gpt-5.6-terra"},
        "image": {
            "base_url": "https://station-88.aicoming.top/v1",
            "model": "image_gpt_2.5",
            "reference_model": "image_gpt_2.5",
        },
        "backup_image": {"base_url": "", "model": "", "reference_model": ""},
        "cos": {"bucket": "", "region": "ap-guangzhou"},
        "limits": {},
        "updates": {},
    }
    with transaction(database_path) as conn:
        conn.execute(
            "INSERT INTO workbench_settings(key, value_json, updated_by, updated_at) VALUES(?, ?, ?, ?)",
            (CONFIG_KEY, json.dumps(legacy_config), "legacy", "2026-01-01T00:00:00Z"),
        )

    # 旧库没有 pod_image 段：加载时补默认值，且不回退 AI处理 已选模型。
    assert service.get_pod_image_model()["model"] == "image_gpt"
    assert service.get_image_model()["model"] == "image_gpt_2.5"
