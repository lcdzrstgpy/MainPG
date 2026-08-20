from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import inspect
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.modules.product_processing.domain.preview_images import (
    PreviewImageManifest,
    replace_carousel_slot,
)
from wh_local.modules.product_processing.preview_image_service import PreviewImageService
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia
from wh_local.modules.product_processing.infrastructure.preview_image_orm import (
    PreviewFinalizeRunRow,
    PreviewImagePublicationRow,
)
from wh_local.modules.product_processing.infrastructure.preview_image_repository import (
    PreviewPublicationConflict,
)
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing import service as service_module
from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.modules.product_processing.api.router import create_product_processing_router


def _jpeg(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (800, 800), color).save(buffer, format="JPEG", quality=94)
    return buffer.getvalue()


def _finished_service(tmp_path: Path) -> tuple[ProductProcessingService, dict, int]:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft, _ = service.create_draft(
        {"source_type": "manual", "title": "Source", "product_name": "Source", "skc": "S-1"},
        workspace_id="workspace-a",
    )
    task = service.repository.create_task(
        title="preview",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="workspace-a",
    )
    item = task["items"][0]
    result = {
        "product_draft_id": draft["id"],
        "optimized_title": "Final Product Title",
        "description": "DURABLE - Product description.",
        "skc": "S-1",
        "sku": "SKU-1",
        "category_path": "Home",
        "source_image_urls": [],
        "carousel_image_paths": [],
        "detail_image_paths": [],
        "product_dimensions": {"length_cm": 10, "width_cm": 8, "height_cm": 6, "weight_g": 100},
        "stock": 1,
    }
    finished = service.repository.finish_task(
        task["id"],
        [{"item_id": item["id"], "status": "completed", "reason": "", "result": result}],
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="workspace-a",
    )
    return service, finished, int(draft["id"])


def test_manifest_preserves_empty_lists_and_semantic_slot_after_reorder() -> None:
    manifest = PreviewImageManifest.from_value(
        {
            "main_asset_id": "a",
            "carousel_asset_ids": ["a", "dimension", "b"],
            "detail_asset_ids": [],
            "semantic_asset_ids": {"carousel.dimension_background": "dimension"},
        }
    )
    changed = replace_carousel_slot(manifest, "carousel.dimension_background", "new")
    assert changed.carousel_asset_ids == ("a", "new", "b")
    assert changed.semantic_asset_ids["carousel.dimension_background"] == "new"
    assert changed.as_dict()["detail_asset_ids"] == []


def test_manifest_uses_first_carousel_asset_as_its_only_main() -> None:
    manifest = PreviewImageManifest.from_value({
        "main_asset_id": "old-main",
        "carousel_asset_ids": ["b", "a", "b"],
        "library_asset_ids": ["source-1", "source-1"],
        "semantic_asset_ids": {"carousel.hero": "old-main", "carousel.detail": "gone"},
    })
    assert manifest.main_asset_id == "b"
    assert manifest.carousel_asset_ids == ("b", "a")
    assert manifest.library_asset_ids == ("source-1",)
    assert manifest.semantic_asset_ids == {"carousel.hero": "b"}


def test_manifest_promotes_lone_main_to_first_carousel_item() -> None:
    manifest = PreviewImageManifest.from_value({"main_asset_id": "only-main"})
    assert manifest.carousel_asset_ids == ("only-main",)
    assert manifest.main_asset_id == "only-main"


def test_schema_and_workspace_asset_boundaries(tmp_path: Path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'preview.sqlite3').as_posix()}")
    assert {
        "product_processing_preview_image_assets",
        "product_processing_preview_publications",
        "product_processing_preview_finalize_runs",
    }.issubset(set(inspect(database.engine).get_table_names()))
    assert "access_token" in {
        column["name"]
        for column in inspect(database.engine).get_columns(
            "product_processing_preview_image_assets"
        )
    }
    database.dispose()


def test_source_main_fallback_uses_result_then_draft_sources() -> None:
    class _Repository:
        def get_draft(self, draft_id: int, workspace_id: str) -> dict:
            return {
                "id": draft_id,
                "image_url": "https://img.alicdn.com/main.jpg",
                "raw_payload": {
                    "source_image_urls": [
                        "https://img.alicdn.com/second.jpg",
                        "https://img.alicdn.com/third.jpg",
                    ]
                },
            }

    service = object.__new__(PreviewImageService)
    service.product_repository = _Repository()

    # 任务结果里的来源图优先
    assert service._source_main_fallback(
        7,
        {"source_image_urls": ["https://img.alicdn.com/first.jpg"]},
        "workspace-a",
    ) == "https://img.alicdn.com/first.jpg"
    # 结果缺失时回退草稿主图
    assert service._source_main_fallback(7, {}, "workspace-a") == (
        "https://img.alicdn.com/main.jpg"
    )
    # 草稿主图缺失时回退 raw_payload 来源图
    class _DraftOnlyRaw:
        def get_draft(self, draft_id: int, workspace_id: str) -> dict:
            return {"id": draft_id, "image_url": "", "raw_payload": {"source_image_urls": ["https://img.alicdn.com/raw.jpg"]}}

    service.product_repository = _DraftOnlyRaw()
    assert service._source_main_fallback(7, {}, "workspace-a") == (
        "https://img.alicdn.com/raw.jpg"
    )
    # 私有/不安全 URL 拒绝，返回空串（由调用方按无主图处理）
    class _PrivateRepo:
        def get_draft(self, draft_id: int, workspace_id: str) -> dict:
            return {"id": draft_id, "image_url": "http://localhost/main.jpg", "raw_payload": {}}

    service.product_repository = _PrivateRepo()
    assert service._source_main_fallback(7, {}, "workspace-a") == ""


def test_upload_and_generated_projection_are_local_and_keep_asset_identity(tmp_path: Path) -> None:
    service, task, draft_id = _finished_service(tmp_path)
    content = _jpeg()
    uploaded = service.register_preview_upload(
        task["id"],
        draft_id,
        content,
        "item.jpg",
        "image/jpeg",
        workspace_id="workspace-a",
    )
    assert uploaded["preview_url"].startswith("/api/product-processing/preview/assets/")
    assert uploaded["publication_status"] == "local"

    media = GeneratedMedia(
        stage="grid_image_1",
        content=_jpeg("blue"),
        content_type="image/jpeg",
        suffix=".jpg",
        provider="test",
        model="test",
        reference_count=1,
    )
    generated = service.preview_images.register_generated(
        task_id=task["id"],
        product_draft_id=draft_id,
        workspace_id="workspace-a",
        media=media,
    )
    projected = service.preview_images.project_item_images(
        task_id=task["id"],
        product_draft_id=draft_id,
        result={
            "optimized_title": "Title",
            "carousel_image_paths": [generated["preview_url"]],
            "detail_image_paths": [],
        },
        saved={},
        workspace_id="workspace-a",
    )
    assert projected["image_manifest"]["carousel_asset_ids"] == [generated["id"]]
    assert projected["image_manifest"]["main_asset_id"] == generated["id"]


def test_task_preview_exposes_each_draft_media_contract_version(tmp_path: Path) -> None:
    service, task, _draft_id = _finished_service(tmp_path)

    preview = service.task_preview(task["id"], workspace_id="workspace-a")

    assert preview["items"][0]["media_contract_version"] == 1


def test_finalize_publishes_only_retained_assets_and_replays_idempotently(tmp_path: Path) -> None:
    service, task, draft_id = _finished_service(tmp_path)
    retained = service.register_preview_upload(
        task["id"], draft_id, _jpeg("green"), "kept.jpg", "image/jpeg", workspace_id="workspace-a"
    )
    removed = service.register_preview_upload(
        task["id"], draft_id, _jpeg("yellow"), "removed.jpg", "image/jpeg", workspace_id="workspace-a"
    )
    calls: list[str] = []

    def publisher(content: bytes, _content_type: str, _suffix: str, digest: str, _workspace: str) -> str:
        assert hashlib.sha256(content).hexdigest() == digest
        calls.append(digest)
        return f"https://bucket.cos.test/preview/{digest}.jpg"

    service.preview_images.publisher = publisher
    service.preview_images.trusted_public_url = lambda value: str(value).startswith("https://bucket.cos.test/")
    preview = service.task_preview(task["id"], workspace_id="workspace-a")["items"][0]
    items = [
        {
            "product_draft_id": draft_id,
            "expected_preview_revision": preview["preview_revision"],
            "expected_result_version": preview["result_version"],
            "overrides": {
                "title": preview["title"],
                "description": preview["description"],
                "core_fields": preview["core_fields"],
                "image_manifest_v2": {
                    "main_asset_id": retained["id"],
                    "carousel_asset_ids": [retained["id"]],
                    "detail_asset_ids": [],
                    "semantic_asset_ids": {"carousel.hero": retained["id"]},
                },
            },
        }
    ]
    started = service.preview_images.begin_finalize(
        task["id"], items, workspace_id="workspace-a", idempotency_key="finalize-1", launch=False
    )
    repeated = service.preview_images.begin_finalize(
        task["id"], items, workspace_id="workspace-a", idempotency_key="finalize-1", launch=False
    )
    assert repeated["id"] == started["id"]
    completed = service.preview_images.run_finalize(started["id"], workspace_id="workspace-a")
    assert completed["status"] == "completed", completed["errors"]
    assert completed["workbook_ready"] is True
    assert len(calls) == 1
    removed_row = service.preview_images.repository.get_asset(removed["id"], "workspace-a")
    assert removed_row is not None and removed_row["availability"] == "local"
    workbook = service.preview_images.finalize_download_path(
        started["id"], task["id"], workspace_id="workspace-a"
    )
    assert workbook.is_file()
    assert workbook.name != f"dxm_import_task_{task['id']}_final.xlsx"


def test_finalize_uses_existing_static_image_host_when_cos_is_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final export must keep working through the existing /pp-media host.

    The deferred-finalization flow stores images locally first.  A configured
    static image host therefore replaces COS for final-publication when COS is
    deliberately not configured; it must not be rejected at request time.
    """
    # Ensure this test exercises the static-host branch even on machines whose
    # project has a git-ignored cos.local.json already configured.
    for name in ("WH_COS_BUCKET", "WH_COS_REGION", "WH_COS_SECRET_ID", "WH_COS_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(service_module, "_cos_local_config_paths", lambda: [])
    monkeypatch.setenv("WH_MEDIA_BASE_URL", "https://images.example.test")
    service, task, draft_id = _finished_service(tmp_path)
    content = _jpeg("green")
    retained = service.register_preview_upload(
        task["id"], draft_id, content, "kept.jpg", "image/jpeg", workspace_id="workspace-a"
    )
    monkeypatch.setattr(service.preview_images, "_launch", lambda *_args, **_kwargs: False)
    preview = service.task_preview(task["id"], workspace_id="workspace-a")["items"][0]
    items = [
        {
            "product_draft_id": draft_id,
            "expected_preview_revision": preview["preview_revision"],
            "expected_result_version": preview["result_version"],
            "overrides": {
                "title": preview["title"],
                "description": preview["description"],
                "core_fields": preview["core_fields"],
                "image_manifest_v2": {
                    "main_asset_id": retained["id"],
                    "carousel_asset_ids": [retained["id"]],
                    "detail_asset_ids": [],
                    "semantic_asset_ids": {"carousel.hero": retained["id"]},
                },
            },
        }
    ]

    started = service.begin_preview_finalize(
        task["id"], items, workspace_id="workspace-a", idempotency_key="static-host"
    )
    completed = service.preview_images.run_finalize(started["id"], workspace_id="workspace-a")

    assert completed["status"] == "completed", completed["errors"]
    digest = hashlib.sha256(content).hexdigest()
    publication = service.preview_images.repository.get_publication("workspace-a", digest)
    assert publication is not None
    assert publication["public_url"].startswith(
        "https://images.example.test/pp-media/preview-assets/"
    )


def test_static_image_host_reads_the_existing_system_public_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WH_MEDIA_BASE_URL", raising=False)
    monkeypatch.setattr(
        service_module,
        "resolve_ai_provider",
        lambda: {"_sys_updates": {"public_base_url": "https://images.example.test/base/"}},
    )

    assert service_module._media_public_base_url() == "https://images.example.test/base"


def test_publication_and_finalize_leases_fence_late_workers(tmp_path: Path) -> None:
    service, task, draft_id = _finished_service(tmp_path)
    asset = service.register_preview_upload(
        task["id"], draft_id, _jpeg("orange"), "lease.jpg", "image/jpeg", workspace_id="workspace-a"
    )
    repository = service.preview_images.repository
    stored_asset = repository.get_asset(asset["id"], "workspace-a")
    assert stored_asset is not None
    digest = str(stored_asset["content_hash"])

    first_publication = repository.claim_publication(
        "workspace-a",
        digest,
        content_type="image/jpeg",
        byte_size=int(stored_asset["byte_size"]),
    )
    with repository.database.sessions.begin() as session:
        row = session.get(PreviewImagePublicationRow, ("workspace-a", digest))
        assert row is not None
        row.claimed_at = "2000-01-01T00:00:00+00:00"
    second_publication = repository.claim_publication(
        "workspace-a",
        digest,
        content_type="image/jpeg",
        byte_size=int(stored_asset["byte_size"]),
    )
    with pytest.raises(PreviewPublicationConflict, match="claim changed"):
        repository.mark_publication_succeeded(
            "workspace-a",
            digest,
            str(first_publication["claim_token"]),
            f"https://bucket.cos.test/preview/{digest}.jpg",
        )
    published = repository.mark_publication_succeeded(
        "workspace-a",
        digest,
        str(second_publication["claim_token"]),
        f"https://bucket.cos.test/preview/{digest}.jpg",
    )
    assert published["status"] == "published"

    preview = service.task_preview(task["id"], workspace_id="workspace-a")["items"][0]
    items = [
        {
            "product_draft_id": draft_id,
            "expected_preview_revision": preview["preview_revision"],
            "expected_result_version": preview["result_version"],
            "overrides": {
                "title": preview["title"],
                "description": preview["description"],
                "core_fields": preview["core_fields"],
                "image_manifest_v2": {
                    "main_asset_id": asset["id"],
                    "carousel_asset_ids": [asset["id"]],
                    "detail_asset_ids": [],
                    "semantic_asset_ids": {"carousel.hero": asset["id"]},
                },
            },
        }
    ]
    started = service.preview_images.begin_finalize(
        task["id"], items, workspace_id="workspace-a", idempotency_key="lease-finalize", launch=False
    )
    first_finalize = repository.claim_finalize_run(started["id"], "workspace-a")
    with repository.database.sessions.begin() as session:
        row = session.get(PreviewFinalizeRunRow, started["id"])
        assert row is not None
        row.claimed_at = "2000-01-01T00:00:00+00:00"
    second_finalize = repository.claim_finalize_run(started["id"], "workspace-a")
    with pytest.raises(PreviewPublicationConflict, match="claim changed"):
        repository.mark_finalize_failed(
            started["id"],
            "workspace-a",
            str(first_finalize["claim_token"]),
            [{"code": "late_worker", "message": "late"}],
        )
    failed = repository.mark_finalize_failed(
        started["id"],
        "workspace-a",
        str(second_finalize["claim_token"]),
        [{"code": "expected", "message": "expected"}],
    )
    assert failed["status"] == "publish_failed"


def test_finalize_snapshot_becomes_stale_when_task_item_result_changes(tmp_path: Path) -> None:
    service, task, draft_id = _finished_service(tmp_path)
    asset = service.register_preview_upload(
        task["id"], draft_id, _jpeg("cyan"), "version.jpg", "image/jpeg", workspace_id="workspace-a"
    )
    preview = service.task_preview(task["id"], workspace_id="workspace-a")["items"][0]
    items = [
        {
            "product_draft_id": draft_id,
            "expected_preview_revision": preview["preview_revision"],
            "expected_result_version": preview["result_version"],
            "overrides": {
                "title": preview["title"],
                "description": preview["description"],
                "core_fields": preview["core_fields"],
                "image_manifest_v2": {
                    "main_asset_id": asset["id"],
                    "carousel_asset_ids": [asset["id"]],
                    "detail_asset_ids": [],
                    "semantic_asset_ids": {"carousel.hero": asset["id"]},
                },
            },
        }
    ]
    started = service.preview_images.begin_finalize(
        task["id"], items, workspace_id="workspace-a", idempotency_key="result-version", launch=False
    )
    claimed = service.preview_images.repository.claim_finalize_run(started["id"], "workspace-a")
    current = service.repository.get_task(task["id"], "workspace-a")
    item = current["items"][0]
    changed_result = {**item["result"], "optimized_title": "Changed After Review"}
    service.repository.update_item_progress(
        task["id"],
        item["id"],
        status="completed",
        result=changed_result,
        workspace_id="workspace-a",
    )

    completed = service.preview_images.repository.mark_finalize_completed(
        started["id"],
        "workspace-a",
        str(claimed["claim_token"]),
        workbook_path=str(tmp_path / "must-not-be-used.xlsx"),
        row_count=1,
        product_count=1,
        snapshot=claimed["snapshot"],
    )

    assert completed["status"] == "stale"
    assert completed["workbook_ready"] is False


def test_startup_requeues_interrupted_finalize_and_releases_publication_lease(
    tmp_path: Path, monkeypatch
) -> None:
    service, task, draft_id = _finished_service(tmp_path)
    asset = service.register_preview_upload(
        task["id"], draft_id, _jpeg("magenta"), "restart.jpg", "image/jpeg", workspace_id="workspace-a"
    )
    preview = service.task_preview(task["id"], workspace_id="workspace-a")["items"][0]
    started = service.preview_images.begin_finalize(
        task["id"],
        [
            {
                "product_draft_id": draft_id,
                "expected_preview_revision": preview["preview_revision"],
                "expected_result_version": preview["result_version"],
                "overrides": {
                    "title": preview["title"],
                    "description": preview["description"],
                    "core_fields": preview["core_fields"],
                    "image_manifest_v2": {
                        "main_asset_id": asset["id"],
                        "carousel_asset_ids": [asset["id"]],
                        "detail_asset_ids": [],
                        "semantic_asset_ids": {"carousel.hero": asset["id"]},
                    },
                },
            }
        ],
        workspace_id="workspace-a",
        idempotency_key="restart-finalize",
        launch=False,
    )
    repository = service.preview_images.repository
    repository.claim_finalize_run(started["id"], "workspace-a")
    stored_asset = repository.get_asset(asset["id"], "workspace-a")
    digest = str(stored_asset["content_hash"])
    repository.claim_publication(
        "workspace-a",
        digest,
        content_type=str(stored_asset["content_type"]),
        byte_size=int(stored_asset["byte_size"]),
    )
    launched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service.preview_images,
        "_launch",
        lambda run_id, workspace_id: launched.append((run_id, workspace_id)) or True,
    )

    recovered = service.preview_images.recover_background_work()

    run = repository.get_finalize_run(started["id"], "workspace-a")
    publication = repository.get_publication("workspace-a", digest)
    recovered_asset = repository.get_asset(asset["id"], "workspace-a")
    assert recovered == {"queued": 1, "launched": 1}
    assert launched == [(started["id"], "workspace-a")]
    assert run["status"] == "queued" and run["claim_token"] == ""
    assert publication["status"] == "publish_failed" and publication["claim_token"] == ""
    assert recovered_asset["availability"] == "local"


def test_asset_and_finalize_api_are_workspace_scoped_and_never_use_old_upload_route(tmp_path: Path) -> None:
    service, task, draft_id = _finished_service(tmp_path)
    calls: list[str] = []
    service.preview_images.publisher = lambda content, _type, _suffix, digest, _workspace: (
        calls.append(digest) or f"https://bucket.cos.test/preview/{digest}.jpg"
    )
    service.preview_images.trusted_public_url = lambda value: str(value).startswith("https://bucket.cos.test/")
    service.engine_status = lambda: {"diagnostics": {"config": {"cos_configured": True}}}  # type: ignore[method-assign]
    app = FastAPI()
    app.include_router(create_product_processing_router(service))
    client = TestClient(app)
    headers = {"X-Workspace-ID": "workspace-a"}

    uploaded = client.post(
        f"/product-processing/tasks/{task['id']}/preview/assets",
        data={"draft_id": str(draft_id)},
        files={"image_files": ("item.jpg", _jpeg("purple"), "image/jpeg")},
        headers=headers,
    )
    assert uploaded.status_code == 200
    asset = uploaded.json()["assets"][0]
    assert asset["preview_url"].startswith("/api/product-processing/preview/assets/")
    content_path = asset["preview_url"].removeprefix("/api")
    assert client.get(content_path).status_code == 200
    assert client.get(
        content_path,
        headers={"X-Workspace-ID": "workspace-b"},
    ).status_code == 404
    assert calls == []
    assert client.post(
        f"/product-processing/tasks/{task['id']}/preview/images",
        headers=headers,
    ).status_code == 404

    preview = client.get(
        f"/product-processing/tasks/{task['id']}/preview",
        headers=headers,
    ).json()["items"][0]
    body = {
        "items": [
            {
                "product_draft_id": draft_id,
                "expected_preview_revision": preview["preview_revision"],
                "expected_result_version": preview["result_version"],
                "overrides": {
                    "title": preview["title"],
                    "description": preview["description"],
                    "core_fields": preview["core_fields"],
                    "image_manifest_v2": {
                        "main_asset_id": asset["id"],
                        "carousel_asset_ids": [asset["id"]],
                        "detail_asset_ids": [],
                        "semantic_asset_ids": {"carousel.hero": asset["id"]},
                    },
                },
            }
        ]
    }
    started = client.post(
        f"/product-processing/tasks/{task['id']}/preview/finalize",
        json=body,
        headers={**headers, "Idempotency-Key": "api-finalize-1"},
    )
    assert started.status_code == 202
    assert started.json()["status"] in {"queued", "publishing", "completed"}
    assert client.get(
        f"/product-processing/tasks/{task['id']}/preview/finalize/{started.json()['id']}",
        headers={"X-Workspace-ID": "workspace-b"},
    ).status_code == 404


def test_preview_exclude_removes_item_from_finalize_eligibility(tmp_path: Path) -> None:
    """排除的单条链接不再参与预检列表与最终导出校验，可随时恢复。"""
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    drafts = []
    for index in range(2):
        draft, _ = service.create_draft(
            {
                "source_type": "manual",
                "title": f"Source {index}",
                "product_name": f"Source {index}",
                "skc": f"S-{index}",
            },
            workspace_id="workspace-a",
        )
        drafts.append(draft)
    task = service.repository.create_task(
        title="preview",
        preflight_only=False,
        settings={},
        drafts=drafts,
        idempotency_key=None,
        workspace_id="workspace-a",
    )
    item_results = []
    for item in task["items"]:
        draft = next(value for value in drafts if value["id"] == item["product_draft_id"])
        item_results.append({
            "item_id": item["id"],
            "status": "completed",
            "reason": "",
            "result": {
                "product_draft_id": draft["id"],
                "optimized_title": f"Final {draft['skc']}",
                "description": "DURABLE - Product description.",
                "skc": draft["skc"],
                "sku": f"SKU-{draft['id']}",
                "category_path": "Home",
                "source_image_urls": [],
                "carousel_image_paths": [],
                "detail_image_paths": [],
                "product_dimensions": {"length_cm": 10, "width_cm": 8, "height_cm": 6, "weight_g": 100},
                "stock": 1,
            },
        })
    service.repository.finish_task(
        task["id"],
        item_results,
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="workspace-a",
    )
    draft_ids = sorted(value["id"] for value in drafts)
    keep, excluded = draft_ids

    preview = service.task_preview(task["id"], workspace_id="workspace-a")
    assert {int(item["product_draft_id"]) for item in preview["items"]} == set(draft_ids)
    assert all(item["excluded"] is False for item in preview["items"])

    preview_by_draft = {int(item["product_draft_id"]): item for item in preview["items"]}
    full_items = []
    for draft_id in draft_ids:
        item = preview_by_draft[draft_id]
        full_items.append({
            "product_draft_id": draft_id,
            "expected_preview_revision": item["preview_revision"],
            "expected_result_version": item["result_version"],
            "overrides": {
                "title": item["title"],
                "description": item["description"],
                "core_fields": item["core_fields"],
                "image_manifest_v2": item["image_manifest"],
            },
        })
    old_run = service.preview_images.begin_finalize(
        task["id"], full_items, workspace_id="workspace-a", idempotency_key="old-full", launch=False
    )
    old_snapshot = service.preview_images.repository.get_finalize(
        old_run["id"], workspace_id="workspace-a"
    )["snapshot"]
    assert {entry["product_draft_id"] for entry in old_snapshot} == set(draft_ids)
    # 模拟「删除链接之前创建的旧失败 run」：发布失败、可重试。
    claimed = service.preview_images.repository.claim_finalize_run(old_run["id"], "workspace-a")
    service.preview_images.repository.mark_finalize_failed(
        old_run["id"],
        "workspace-a",
        str(claimed["claim_token"]),
        [{"code": "preview_finalize_failed", "message": "boom"}],
    )

    updated = service.set_preview_item_excluded(task["id"], excluded, workspace_id="workspace-a")
    assert updated["excluded_draft_ids"] == [excluded]
    visible = [item for item in updated["items"] if not item["excluded"]]
    assert {int(item["product_draft_id"]) for item in visible} == {keep}
    assert next(item for item in updated["items"] if int(item["product_draft_id"]) == excluded)["excluded"] is True

    retried = service.preview_images.retry_finalize(old_run["id"], workspace_id="workspace-a", launch=False)
    retried_snapshot = service.preview_images.repository.get_finalize(
        old_run["id"], workspace_id="workspace-a"
    )["snapshot"]
    assert retried["status"] == "queued"
    assert {entry["product_draft_id"] for entry in retried_snapshot} == {keep}

    kept_asset = service.register_preview_upload(
        task["id"], keep, _jpeg("green"), "kept.jpg", "image/jpeg", workspace_id="workspace-a"
    )
    keep_preview = next(item for item in updated["items"] if int(item["product_draft_id"]) == keep)
    items = [{
        "product_draft_id": keep,
        "expected_preview_revision": keep_preview["preview_revision"],
        "expected_result_version": keep_preview["result_version"],
        "overrides": {
            "title": keep_preview["title"],
            "description": keep_preview["description"],
            "core_fields": keep_preview["core_fields"],
            "image_manifest_v2": {
                "main_asset_id": kept_asset["id"],
                "carousel_asset_ids": [kept_asset["id"]],
                "detail_asset_ids": [],
                "semantic_asset_ids": {"carousel.hero": kept_asset["id"]},
            },
        },
    }]
    started = service.preview_images.begin_finalize(
        task["id"], items, workspace_id="workspace-a", idempotency_key="exclude-finalize", launch=False
    )
    assert started["status"] == "queued"

    excluded_preview = next(item for item in updated["items"] if int(item["product_draft_id"]) == excluded)
    excluded_items = [{
        "product_draft_id": excluded,
        "expected_preview_revision": excluded_preview["preview_revision"],
        "expected_result_version": excluded_preview["result_version"],
        "overrides": {
            "title": excluded_preview["title"],
            "description": excluded_preview["description"],
            "core_fields": excluded_preview["core_fields"],
            "image_manifest_v2": excluded_preview["image_manifest"],
        },
    }]
    with pytest.raises(ValueError, match="must contain every exportable draft"):
        service.preview_images.begin_finalize(
            task["id"], excluded_items, workspace_id="workspace-a", idempotency_key="excluded-only", launch=False
        )

    restored = service.set_preview_item_excluded(task["id"], excluded, excluded=False, workspace_id="workspace-a")
    assert restored["excluded_draft_ids"] == []
    assert {int(item["product_draft_id"]) for item in restored["items"]} == set(draft_ids)
