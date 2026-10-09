from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, inspect, select

import wh_local.modules.product_processing.service as service_module
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.orm import (
    ProcessingTaskItemRow,
    ProcessingTaskRow,
    ProductDraftRow,
)
from wh_local.modules.product_processing.infrastructure.repository import (
    ProductProcessingRepository,
    dumps,
)
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


def test_history_source_lookup_uses_latest_nonduplicate_exact_title_in_workspace(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    def add_history(
        *, workspace: str, skc: str, title: str, offer_id: str, updated_at: str
    ) -> None:
        with service.repository.database.sessions.begin() as session:
            task = ProcessingTaskRow(
                workspace_id=workspace,
                title="history",
                status="completed",
                total_count=1,
                success_count=1,
                created_at=updated_at,
                updated_at=updated_at,
            )
            task.items.append(
                ProcessingTaskItemRow(
                    skc=skc,
                    title=title,
                    status="completed",
                    result_json=dumps(
                        {
                            "skc": skc,
                            "optimized_title": title,
                            "source_url": f"https://detail.1688.com/offer/{offer_id}.html",
                        }
                    ),
                    created_at=updated_at,
                    updated_at=updated_at,
                )
            )
            session.add(task)

    add_history(
        workspace="workspace",
        skc="older-valid",
        title="Fancy Mug",
        offer_id="333333",
        updated_at="2026-08-01T00:00:00+00:00",
    )
    add_history(
        workspace="workspace",
        skc="duplicate",
        title="  FANCY   MUG  ",
        offer_id="222222",
        updated_at="2026-08-02T00:00:00+00:00",
    )
    add_history(
        workspace="workspace",
        skc="newer-candidate",
        title="Fancy Mug",
        offer_id="666666",
        updated_at="2026-08-02T12:00:00+00:00",
    )
    add_history(
        workspace="workspace",
        skc="current-skc",
        title="Fancy Mug",
        offer_id="111111",
        updated_at="2026-08-03T00:00:00+00:00",
    )
    add_history(
        workspace="other-workspace",
        skc="other-user",
        title="Fancy Mug",
        offer_id="999999",
        updated_at="2026-08-04T00:00:00+00:00",
    )
    add_history(
        workspace="workspace",
        skc="fuzzy-only",
        title="Fancy Mug Set",
        offer_id="444444",
        updated_at="2026-08-05T00:00:00+00:00",
    )

    matched = service.latest_completed_sources_by_title(
        [
            {
                "skc": "current-skc",
                # This captured title is intentionally wrong. The lookup must
                # resolve the current SKC's stored AI title instead.
                "title": "captured title must not be used",
                "excluded_offer_ids": ["222222"],
            }
        ],
        workspace_id="workspace",
    )

    assert [item["history_skc"] for item in matched["current-skc"]] == [
        "newer-candidate",
        "older-valid",
    ]
    assert matched["current-skc"][0]["source_url"].endswith("/666666.html")
    assert matched["current-skc"][1]["source_url"].endswith("/333333.html")
    assert matched["current-skc"][0]["current_ai_title"] == "Fancy Mug"


def test_history_source_lookup_requires_current_skc_ai_title(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with service.repository.database.sessions.begin() as session:
        task = ProcessingTaskRow(
            workspace_id="workspace",
            title="history",
            status="completed",
            total_count=1,
            success_count=1,
        )
        task.items.append(
            ProcessingTaskItemRow(
                skc="other-skc",
                title="Captured title",
                status="completed",
                result_json=dumps(
                    {
                        "skc": "other-skc",
                        "optimized_title": "Captured title",
                        "source_url": "https://detail.1688.com/offer/555555.html",
                    }
                ),
            )
        )
        session.add(task)

    assert service.latest_completed_sources_by_title(
        [{"skc": "missing-current-skc", "title": "Captured title"}],
        workspace_id="workspace",
    ) == {}


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


def test_delete_undo_restores_only_latest_workspace_batch(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _draft(service, "first")
    second = _draft(service, "second")
    other = service.create_draft(
        {"source_type": "manual", "title": "other workspace", "product_name": "other workspace"},
        workspace_id="other",
    )[0]
    service.delete_drafts([first["id"], second["id"]], workspace_id="local")
    service.delete_drafts([other["id"]], workspace_id="other")

    restored = service.restore_drafts([second["id"], first["id"], other["id"]], workspace_id="local")

    assert restored == {
        "restored_count": 2,
        "ids": [second["id"], first["id"]],
        "status": "draft",
    }
    assert service.get_draft(first["id"], workspace_id="local")["status"] == "draft"
    assert service.get_draft(second["id"], workspace_id="local")["status"] == "draft"
    assert service.repository.get_draft(other["id"], include_deleted=True, workspace_id="other")["status"] == "deleted"
    assert service.restore_drafts([first["id"]], workspace_id="local")["restored_count"] == 0


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
    monkeypatch.setattr(
        service_module,
        "default_config",
        lambda: SimpleNamespace(customer_auth_base_url=""),
    )
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
        "文本 AI 已启用，但未配置客户认证服务地址",
        "图片 AI 已启用，但客户认证服务地址、服务端图片网关或 Pillow 图片依赖不可用",
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
    assert status["diagnostics"]["config"]["ai_provider"] == "server-managed"
    assert status["diagnostics"]["config"]["ai_configured"] is True


def test_engine_status_server_managed_ai_needs_auth_base_url_not_local_keys(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)

    class _ServerMediaStatus:
        def __init__(self, **_kwargs) -> None:
            pass

        def status(self) -> dict:
            return {"image_configured": True, "backup_image_configured": False}

    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("WH_AI_API_KEY", raising=False)
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "ocr_gate_enabled", lambda: False)
    monkeypatch.setattr(
        service_module,
        "default_config",
        lambda: SimpleNamespace(customer_auth_base_url="https://auth.example.test"),
    )
    monkeypatch.setattr(
        service_module,
        "_media_types",
        lambda: (_ServerMediaStatus, RuntimeError, RuntimeError),
    )
    monkeypatch.setattr(
        service_module.importlib.util,
        "find_spec",
        lambda _name: object(),
    )

    status = service.engine_status()

    assert status["diagnostics"]["capabilities"]["text_ai"] == {
        "enabled": True,
        "ready": True,
        "reason": "",
    }
    assert status["diagnostics"]["capabilities"]["image_ai"]["ready"] is True
    assert status["diagnostics"]["config"]["ai_provider"] == "server-managed"
    assert status["diagnostics"]["config"]["ai_configured"] is True


def test_engine_status_server_managed_ai_fails_closed_without_auth_base_url(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)

    class _ServerMediaStatus:
        def __init__(self, **_kwargs) -> None:
            pass

        def status(self) -> dict:
            return {"image_configured": True}

    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "ocr_gate_enabled", lambda: False)
    monkeypatch.setattr(
        service_module,
        "default_config",
        lambda: SimpleNamespace(customer_auth_base_url=""),
    )
    monkeypatch.setattr(
        service_module,
        "_media_types",
        lambda: (_ServerMediaStatus, RuntimeError, RuntimeError),
    )

    status = service.engine_status()

    assert status["diagnostics"]["capabilities"]["text_ai"]["ready"] is False
    assert status["diagnostics"]["capabilities"]["image_ai"]["ready"] is False
    assert all("ARK_API_KEY" not in reason for reason in status["unavailable_reasons"])


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


def test_generation_references_prefer_all_ready_local_images_then_remote_fallback(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    draft = _draft(service, "cached source references")
    main_url = "https://images.example.test/main.jpg"
    fallback_url = "https://images.example.test/fallback.jpg"
    service.repository.preserve_source_images(
        task_id=None,
        product_draft_id=draft["id"],
        source_urls=[main_url],
        detail_urls=[fallback_url],
    )

    paths_by_url: dict[str, str] = {}
    for image in service.repository.claim_syncable_source_images(draft["id"], "local"):
        path = service.assets.save_source_image(
            f"cached:{image['url']}".encode(),
            image["url"],
            "image/jpeg",
        )
        assert service.repository.complete_source_image(
            image["id"],
            str(path),
            image["_sync_claim_token"],
            "local",
        )
        paths_by_url[image["url"]] = str(path)

    values, local_count = service._generation_reference_values(
        draft["id"],
        [main_url],
        "local",
    )

    assert local_count == 2
    assert values[:2] == [paths_by_url[main_url], paths_by_url[fallback_url]]
    assert values[2:] == [main_url]
