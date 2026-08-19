from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, inspect, select

import wh_local.modules.product_processing.service as service_module
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.orm import ProductDraftRow
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingConflict, ProductProcessingService


def _service(tmp_path: Path) -> ProductProcessingService:
    return ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def _draft(service: ProductProcessingService, title: str) -> dict:
    return service.create_draft(
        {"source_type": "manual", "title": title, "product_name": title},
        workspace_id="local",
    )[0]


def test_skip_duplicates_marks_only_drafts_in_created_task(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    processed = _draft(service, "already processed")
    pending = _draft(service, "pending")
    service.repository.mark_drafts_status([processed["id"]], "processed", workspace_id="local")
    monkeypatch.setattr(service, "_launch_background_execute", lambda *_args: True)

    response = service.process_drafts(
        {
            "draft_ids": [processed["id"], pending["id"]],
            "skip_duplicates": True,
            "async_mode": True,
        },
        idempotency_key="skip-once",
        workspace_id="local",
    )

    assert response["total_count"] == 1
    assert service.get_draft(processed["id"])["status"] == "processed"
    assert service.get_draft(pending["id"])["status"] == "processing"


def test_single_idempotency_is_checked_before_creating_draft(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(service, "_launch_background_execute", lambda *_args: True)

    first = service.process_single(
        {"title": "same product", "async_mode": True},
        idempotency_key="single-once",
        workspace_id="local",
    )
    second = service.process_single(
        {"title": "same product", "async_mode": True},
        idempotency_key="single-once",
        workspace_id="local",
    )

    assert second["task_id"] == first["task_id"]
    with service.repository.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProductDraftRow)) == 1


def test_retry_resets_only_selected_failed_items_and_preserves_success(tmp_path: Path) -> None:
    service = _service(tmp_path)
    drafts = [_draft(service, title) for title in ("success", "retry", "leave failed")]
    task = service.repository.create_task(
        title="retry",
        preflight_only=False,
        settings={},
        drafts=drafts,
        idempotency_key=None,
        workspace_id="local",
    )
    for item, status in zip(task["items"], ("completed", "failed", "attention_required"), strict=True):
        service.repository.update_item_progress(
            task["id"], item["id"], status=status, result={"marker": status}, workspace_id="local"
        )

    service.repository.reset_failed_items(task["id"], "local", draft_ids=[drafts[1]["id"]])
    refreshed = service.repository.get_task(task["id"], "local")

    assert [item["status"] for item in refreshed["items"]] == ["completed", "pending", "attention_required"]
    assert refreshed["success_count"] == 1
    assert refreshed["failed_count"] == 1


def test_executor_failure_restores_unfinished_draft_for_retry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    draft = _draft(service, "recover me")
    task = service.repository.create_task(
        title="failure",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )
    service.repository.mark_drafts_status([draft["id"]], "processing", workspace_id="local")
    assert service.repository.claim_task_execution(task["id"], "local") is True

    failed = service.repository.fail_task_execution(task["id"], "executor exploded", "local")

    assert failed["status"] == "failed"
    assert failed["items"][0]["status"] == "failed"
    assert service.get_draft(draft["id"])["status"] == "draft"


def test_clear_rejects_non_terminal_task(tmp_path: Path) -> None:
    service = _service(tmp_path)
    draft = _draft(service, "running")
    task = service.repository.create_task(
        title="running",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )

    with pytest.raises(ProductProcessingConflict):
        service.clear_task(task["id"], "local")


def test_media_config_prefers_independent_system_image_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        service_module,
        "resolve_ai_provider",
        lambda: {
            "api_key": "text-key",
            "base_url": "https://text.example/v1",
            "image_model": "fallback-image",
            "reference_image_model": "fallback-reference",
            "image_models": [],
            "_sys_image_ai": {
                "base_url": "https://image.example/v1",
                "api_key": "image-key",
                "model": "image-model",
                "reference_model": "reference-model",
            },
            "_sys_limits": {},
        },
    )

    config = ProductProcessingService._media_config_provider()

    assert config["image"]["base_url"] == "https://image.example/v1"
    assert config["image"]["api_key"] == "image-key"
    assert config["limits"]["image_workers"] == 4


def test_engine_status_reports_enabled_capability_blockers_without_provider_probe(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)

    class _LocalMediaStatus:
        def __init__(self, **_kwargs) -> None:
            pass

        def status(self) -> dict:
            return {"image_configured": False}

    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.setattr(service_module, "ocr_gate_enabled", lambda: True)
    monkeypatch.setattr(
        service_module,
        "ocr_diagnostics",
        lambda: {"ready": False, "reason": "RapidOCR 模型不可用"},
    )
    monkeypatch.setattr(service_module, "_media_types", lambda: (_LocalMediaStatus, RuntimeError, RuntimeError))
    monkeypatch.setattr(
        service_module,
        "resolve_ai_provider",
        lambda: {
            "provider": "test",
            "base_url": "https://relay.example/v1",
            "api_key": "",
            "text_model": "text-model",
            "image_model": "image-model",
            "reference_image_model": "image-model",
        },
    )
    status = service.engine_status()

    assert status["ready"] is False
    assert status["diagnostics"]["capabilities"]["text_ai"]["ready"] is False
    assert status["diagnostics"]["capabilities"]["image_ai"]["ready"] is False
    assert status["diagnostics"]["capabilities"]["ocr"]["reason"] == "RapidOCR 模型不可用"
    assert status["unavailable_reasons"] == [
        "文本 AI 已启用，但服务端未配置 ARK_API_KEY",
        "图片 AI 已启用，但未配置可用的图片服务地址/API Key，或 Pillow 图片依赖不可用",
        "RapidOCR 模型不可用",
    ]


def test_engine_status_splits_doubao_text_key_from_image_provider(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)

    class _LocalMediaStatus:
        def __init__(self, **_kwargs) -> None:
            pass

        def status(self) -> dict:
            return {"image_configured": False}

    monkeypatch.setenv("ARK_API_KEY", "server-side-ark-key")
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "ocr_gate_enabled", lambda: False)
    monkeypatch.setattr(
        service_module,
        "_media_types",
        lambda: (_LocalMediaStatus, RuntimeError, RuntimeError),
    )
    monkeypatch.setattr(
        service_module,
        "resolve_ai_provider",
        lambda: {
            "provider": "test",
            "base_url": "",
            "api_key": "",
            "image_model": "",
            "reference_image_model": "",
        },
    )

    status = service.engine_status()

    assert status["diagnostics"]["capabilities"]["text_ai"]["ready"] is True
    assert status["diagnostics"]["capabilities"]["image_ai"]["ready"] is False
    assert status["diagnostics"]["config"]["ai_provider"] == "doubao"
    assert status["diagnostics"]["config"]["ai_configured"] is True


def test_long_item_refreshes_employee_visible_heartbeat(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    draft = _draft(service, "slow item")
    task = service.repository.create_task(
        title="heartbeat",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )
    monkeypatch.setattr(service_module, "_TASK_HEARTBEAT_SECONDS", 0.01)
    heartbeat_seen = service_module.threading.Event()
    original_update = service.repository.update_item_progress

    def track_update(*args, **kwargs):
        result = original_update(*args, **kwargs)
        if "心跳正常" in str(kwargs.get("reason") or ""):
            heartbeat_seen.set()
        return result

    monkeypatch.setattr(service.repository, "update_item_progress", track_update)

    def slow_operation() -> dict:
        assert heartbeat_seen.wait(timeout=1.0)
        return {"status": "completed"}

    result = service._run_with_item_heartbeat(
        task["id"],
        task["items"][0]["id"],
        "local",
        slow_operation,
    )
    refreshed = service.repository.get_task(task["id"], "local")

    assert result == {"status": "completed"}
    assert refreshed["items"][0]["status"] == "running"
    assert "心跳正常" in refreshed["items"][0]["reason"]
    assert refreshed["items"][0]["updated_at"] > task["items"][0]["updated_at"]


def test_stage_receipt_upsert_is_workspace_scoped_and_preserves_creation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    draft = _draft(service, "receipt")
    task = service.repository.create_task(
        title="receipt",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )
    item_id = task["items"][0]["id"]

    first = service.repository.upsert_stage_receipt(
        task["id"],
        item_id,
        "structured_text",
        input_hash="hash-one",
        output_data={"title": "first", "workspace_id": "untrusted-client-value"},
        workspace_id="local",
    )
    second = service.repository.upsert_stage_receipt(
        task["id"],
        item_id,
        "structured_text",
        input_hash="hash-two",
        output_data={"title": "second"},
        workspace_id="local",
    )

    assert second["id"] == first["id"]
    assert second["created_at"] == first["created_at"]
    assert second["workspace_id"] == "local"
    assert second["input_hash"] == "hash-two"
    assert second["output"] == {"title": "second"}
    assert service.repository.load_stage_receipt(
        task["id"], item_id, "structured_text", workspace_id="other"
    ) is None
    with pytest.raises(LookupError):
        service.repository.upsert_stage_receipt(
            task["id"],
            item_id,
            "structured_text",
            input_hash="attacker-hash",
            output_data={"title": "overwrite"},
            workspace_id="other",
        )


def test_stage_receipt_invalid_and_downstream_deletes_are_item_scoped(tmp_path: Path) -> None:
    service = _service(tmp_path)
    drafts = [_draft(service, title) for title in ("one", "two")]
    task = service.repository.create_task(
        title="receipts",
        preflight_only=False,
        settings={},
        drafts=drafts,
        idempotency_key=None,
        workspace_id="local",
    )
    first_item, second_item = (item["id"] for item in task["items"])
    for item_id in (first_item, second_item):
        for stage in ("structured_text", "images", "dimensions"):
            service.repository.upsert_stage_receipt(
                task["id"],
                item_id,
                stage,
                input_hash=f"{item_id}-{stage}",
                output_data={"stage": stage},
                workspace_id="local",
            )

    assert service.repository.delete_invalid_stage_receipt(
        task["id"],
        first_item,
        "structured_text",
        expected_input_hash="changed-input",
        workspace_id="local",
    ) is True
    assert service.repository.delete_downstream_stage_receipts(
        task["id"],
        first_item,
        ["images", "dimensions", "images"],
        workspace_id="local",
    ) == 2

    assert service.repository.load_stage_receipt(
        task["id"], first_item, "structured_text", workspace_id="local"
    ) is None
    assert service.repository.load_stage_receipt(
        task["id"], second_item, "images", workspace_id="local"
    )["output"] == {"stage": "images"}


def test_retry_clears_terminal_result_but_keeps_stage_receipt(tmp_path: Path) -> None:
    service = _service(tmp_path)
    draft = _draft(service, "retry receipt")
    task = service.repository.create_task(
        title="retry receipt",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="local",
    )
    item_id = task["items"][0]["id"]
    service.repository.upsert_stage_receipt(
        task["id"],
        item_id,
        "structured_text",
        input_hash="stable-input",
        output_data={"title": "paid output"},
        workspace_id="local",
    )
    service.repository.update_item_progress(
        task["id"],
        item_id,
        status="failed",
        result={"terminal": "failure"},
        workspace_id="local",
    )

    assert service.repository.reset_failed_items(task["id"], "local") is True

    refreshed = service.repository.get_task(task["id"], "local")
    receipt = service.repository.load_stage_receipt(
        task["id"], item_id, "structured_text", workspace_id="local"
    )
    assert refreshed["items"][0]["status"] == "pending"
    assert refreshed["items"][0]["result"] == {}
    assert receipt["output"] == {"title": "paid output"}


def test_database_init_recreates_missing_stage_receipt_table(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'legacy.sqlite3').as_posix()}"
    database = create_database(database_url)
    with database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE product_processing_stage_receipts")
    database.dispose()

    reopened = create_database(database_url)
    try:
        assert "product_processing_stage_receipts" in inspect(reopened.engine).get_table_names()
    finally:
        reopened.dispose()
