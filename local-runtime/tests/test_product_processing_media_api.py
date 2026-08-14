from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image

from wh_local.data_collection.public_image_fetch import FetchedPublicImage
from wh_local.modules.product_processing.domain.models import DailySelectionHandoffEnvelope
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia
from wh_local.modules.product_processing.infrastructure.media_asset_repository import MediaAssetRepository
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.media_asset_service import MediaAssetService
from wh_local.modules.product_processing.service import ProductProcessingService


def _jpeg(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="JPEG", quality=94)
    return buffer.getvalue()


def _handoff() -> DailySelectionHandoffEnvelope:
    return DailySelectionHandoffEnvelope(
        handoff_id="h-1",
        run_id="run-1",
        candidate_id="c-1",
        workspace_id="ws",
        payload_json=json.dumps(
            {
                "candidate": {
                    "candidate_id": "c-1",
                    "source_url": "https://detail.example.com/x.html",
                    "source_title": "Test product",
                },
                "images": {
                    "main": "https://img.example.com/main.jpg",
                    "gallery": ["https://img.example.com/g1.jpg"],
                    "detail": [],
                    "sku": [],
                },
                "skus": [],
                "attributes": {},
                "selection_metadata": {},
            },
            ensure_ascii=False,
        ),
        status="pending",
        idempotency_key="idem-1",
        created_at="2026-08-13T00:00:00+00:00",
    )


def test_draft_media_groups_without_managed_path(tmp_path: Path) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    result = service.consume_daily_selection_handoffs([_handoff()])
    draft = result["drafts"][0]
    assert draft["media_contract_version"] == 2

    media = service.draft_media(draft["id"], workspace_id="ws")
    assert media["contract_version"] == 2
    groups = media["groups"]
    assert len(groups["main"]) == 1
    assert len(groups["gallery"]) == 1
    assert groups["main"][0]["status"] == "pending"
    assert groups["main"][0]["preview_url"] == ""
    for group in groups.values():
        for item in group:
            assert "managed_path" not in item
            assert "access_token" not in item


def test_media_content_signature_and_retry(tmp_path: Path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'media.sqlite3').as_posix()}")
    assets = ProductProcessingAssets(tmp_path / "assets")
    service = MediaAssetService(MediaAssetRepository(database), assets)

    content = _jpeg("green")
    asset = service.register_local_asset("ws", "preview_upload", content, "image/jpeg")
    assert asset["status"] == "ready"

    query = parse_qs(urlsplit(asset["preview_url"]).query)
    expires = int(query["expires"][0])
    signature = query["signature"][0]
    path, media_type = service.media_asset_content(
        asset["id"], workspace_id="ws", expires=expires, signature=signature
    )
    assert path.read_bytes() == content
    assert media_type == "image/jpeg"
    with pytest.raises(LookupError):
        service.media_asset_content(
            asset["id"], workspace_id="ws", expires=expires, signature="0" * 64
        )

    remote = service.register_remote_asset("ws", "https://img.example.com/r.jpg")
    service.public_image_fetcher = lambda url: FetchedPublicImage(
        b"not an image", "application/octet-stream", url
    )
    service.materialize_pending(workspace_id="ws")
    assert service.get_asset(remote["id"], "ws")["status"] == "failed"
    retried = service.retry_asset(remote["id"], workspace_id="ws")
    assert retried["status"] == "pending"


def test_generated_preview_url_resolves_media_asset_id(tmp_path: Path) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft, _ = service.create_draft(
        {"source_type": "manual", "title": "T", "product_name": "T", "skc": "S-1"},
        workspace_id="ws",
    )
    task = service.repository.create_task(
        title="t", preflight_only=False, settings={}, drafts=[draft],
        idempotency_key=None, workspace_id="ws",
    )
    media = GeneratedMedia(
        stage="grid_image_1", content=_jpeg("green"), content_type="image/jpeg",
        suffix=".jpg", provider="test", model="test", reference_count=1,
    )
    asset = service.preview_images.register_generated(
        task_id=task["id"], product_draft_id=draft["id"], workspace_id="ws", media=media
    )
    resolved = service.preview_images.media_asset_id_for_preview_url(asset["preview_url"], "ws")
    assert resolved
    unified = service.media_assets.get_asset(resolved, "ws")
    assert unified["status"] == "ready"
    assert unified["origin"] == "ai_generated"


def test_legacy_preview_asset_with_internal_display_path_uses_referenced_preview(tmp_path: Path) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft, _ = service.create_draft(
        {"source_type": "manual", "title": "T", "product_name": "T", "skc": "S-1"},
        workspace_id="ws",
    )
    task = service.repository.create_task(
        title="t", preflight_only=False, settings={}, drafts=[draft],
        idempotency_key=None, workspace_id="ws",
    )
    generated = service.preview_images.register_generated(
        task_id=task["id"],
        product_draft_id=draft["id"],
        workspace_id="ws",
        media=GeneratedMedia(
            stage="grid_image_1", content=_jpeg("green"), content_type="image/jpeg",
            suffix=".jpg", provider="test", model="test", reference_count=1,
        ),
    )
    legacy = service.preview_images.repository.register_asset(
        workspace_id="ws",
        task_id=task["id"],
        product_draft_id=draft["id"],
        origin="generated",
        identity_hash="f" * 64,
        # A historical canvas acceptance wrote a display URL here as if it were
        # a local path. The public projection must heal this without exposing it
        # as a filesystem authority.
        managed_path=generated["preview_url"],
        source_url="",
        content_hash="",
        content_type="image/jpeg",
        byte_size=0,
        width=0,
        height=0,
    )

    projected = service.preview_images.public_asset(legacy)

    assert projected["preview_url"] == generated["preview_url"]


def test_generated_grid_media_is_bound_to_v2_carousel_slots(tmp_path: Path) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft = service.consume_daily_selection_handoffs([_handoff()])["drafts"][0]
    task = service.repository.create_task(
        title="t", preflight_only=False, settings={}, drafts=[draft],
        idempotency_key=None, workspace_id="ws",
    )
    parts = [
        GeneratedMedia(
            stage=f"grid_image_{index}", content=_jpeg(color), content_type="image/jpeg",
            suffix=".jpg", provider="test", model="test", reference_count=1,
        )
        for index, color in enumerate(("red", "green", "blue", "yellow"), start=1)
    ]

    service._persist_media_for_preview(parts, task["id"], draft["id"], "ws")

    carousel = service.draft_media(draft["id"], workspace_id="ws")["groups"]["carousel"]
    assert [entry["slot_id"] for entry in carousel] == [
        "carousel.hero",
        "carousel.detail",
        "carousel.lifestyle",
        "carousel.dimension_background",
    ]
    assert all(service.media_assets.get_asset(entry["asset_id"], "ws")["origin"] == "ai_generated" for entry in carousel)


def test_retry_media_asset_starts_background_materialization(tmp_path: Path) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(
            create_database(f"sqlite:///{(tmp_path / 'media-retry.sqlite3').as_posix()}")
        ),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    remote = service.media_assets.register_remote_asset("ws", "https://img.example.com/retry.jpg")
    service.media_assets.public_image_fetcher = lambda url: FetchedPublicImage(
        b"not an image", "application/octet-stream", url
    )
    service.media_assets.materialize_pending(workspace_id="ws")
    assert service.media_assets.get_asset(remote["id"], "ws")["status"] == "failed"

    service.media_assets.public_image_fetcher = lambda url: FetchedPublicImage(
        _jpeg("blue"), "image/jpeg", url
    )
    service.retry_media_asset(remote["id"], workspace_id="ws")
    for _ in range(20):
        if service.media_assets.get_asset(remote["id"], "ws")["status"] == "ready":
            break
        time.sleep(0.02)

    assert service.media_assets.get_asset(remote["id"], "ws")["status"] == "ready"


def _seeded_full_v2_product(tmp_path: Path) -> tuple[ProductProcessingService, int, int]:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft = service.consume_daily_selection_handoffs([_handoff()])["drafts"][0]
    task = service.repository.create_task(
        title="e2e",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="ws",
    )
    service.media_assets.public_image_fetcher = lambda url: FetchedPublicImage(
        _jpeg("green"), "image/jpeg", url
    )
    service.media_assets.materialize_pending(workspace_id="ws")
    service.preview_images.register_generated(
        task_id=task["id"],
        product_draft_id=draft["id"],
        workspace_id="ws",
        media=GeneratedMedia(
            stage="grid_image_1", content=_jpeg("blue"), content_type="image/jpeg",
            suffix=".jpg", provider="test", model="test", reference_count=1,
        ),
    )
    item = task["items"][0]
    result = {
        "product_draft_id": draft["id"],
        "optimized_title": "E2E product",
        "description": "DURABLE - E2E product.",
        "source_image_urls": ["https://img.example.com/main.jpg", "https://img.example.com/g1.jpg"],
        "carousel_image_paths": [],
        "detail_image_paths": [],
    }
    service.repository.finish_task(
        task["id"],
        [{"item_id": item["id"], "status": "completed", "reason": "", "result": result}],
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="ws",
    )
    return service, int(task["id"]), int(draft["id"])


def test_v2_product_precheck_has_readonly_sources_and_processed_library(
    tmp_path: Path,
) -> None:
    service, task_id, draft_id = _seeded_full_v2_product(tmp_path)
    before = service.task_preview(task_id, workspace_id="ws")["items"][0]
    assert any(x["bucket"] == "source" for x in before["assets"])
    assert any(x["bucket"] == "processed" for x in before["assets"])

    source = next(x for x in before["assets"] if x["bucket"] == "source")
    service.save_task_preview(
        task_id,
        [{
            "product_draft_id": draft_id,
            "expected_preview_revision": before["preview_revision"],
            "expected_result_version": before["result_version"],
            "overrides": {"image_manifest_v2": {
                "main_asset_id": "",
                "carousel_asset_ids": [],
                "detail_asset_ids": [],
                "library_asset_ids": [source["id"]],
                "semantic_asset_ids": {},
            }},
        }],
        workspace_id="ws",
    )
    after = service.task_preview(task_id, workspace_id="ws")["items"][0]
    assert source["id"] in after["image_manifest"]["library_asset_ids"]
