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
