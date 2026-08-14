from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from wh_local.data_collection.public_image_fetch import FetchedPublicImage
from wh_local.modules.product_processing.domain.models import DailySelectionHandoffEnvelope
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import (
    ProductProcessingService,
    ProductProcessingValidationError,
)


def _jpeg(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="JPEG", quality=94)
    return buffer.getvalue()


def _v2_handoff() -> DailySelectionHandoffEnvelope:
    return DailySelectionHandoffEnvelope(
        handoff_id="h-proj",
        run_id="run-proj",
        candidate_id="c-proj",
        workspace_id="ws",
        payload_json=json.dumps(
            {
                "candidate": {
                    "candidate_id": "c-proj",
                    "source_url": "https://detail.example.com/proj.html",
                    "source_title": "Projection product",
                },
                "images": {
                    "main": "https://img.example.com/main.jpg",
                    "gallery": ["https://img.example.com/gallery.jpg"],
                    "detail": ["https://img.example.com/detail.jpg"],
                },
                "skus": [
                    {
                        "sku_id": "sku-1",
                        "image_url": "https://img.example.com/sku.jpg",
                        "spec_text": "blue",
                    }
                ],
                "attributes": {},
                "selection_metadata": {},
            },
            ensure_ascii=False,
        ),
        status="pending",
        idempotency_key="idem-proj",
        created_at="2026-08-14T00:00:00+00:00",
    )


def _seeded_v2_product(tmp_path: Path, *, materialize: bool) -> tuple[ProductProcessingService, int, int]:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft = service.consume_daily_selection_handoffs([_v2_handoff()])["drafts"][0]
    assert draft["media_contract_version"] == 2
    task = service.repository.create_task(
        title="projection",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="ws",
    )
    if materialize:
        service.media_assets.public_image_fetcher = lambda url: FetchedPublicImage(
            _jpeg("green"), "image/jpeg", url
        )
        service.media_assets.materialize_pending(workspace_id="ws")
    item = task["items"][0]
    result = {
        "product_draft_id": draft["id"],
        "optimized_title": "Projection product title",
        "description": "DURABLE - Projection product.",
        "skc": "PP-1",
        "sku": "SKU-1",
        "source_image_urls": [
            "https://img.example.com/main.jpg",
            "https://img.example.com/gallery.jpg",
        ],
        "source_detail_image_urls": ["https://img.example.com/detail.jpg"],
        "carousel_image_paths": [],
        "detail_image_paths": [],
        "product_dimensions": {"length_cm": 1, "width_cm": 1, "height_cm": 1, "weight_g": 1},
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


def test_v2_projection_exposes_original_main_gallery_sku_and_detail_without_copying(
    tmp_path: Path,
) -> None:
    service, task_id, _draft_id = _seeded_v2_product(tmp_path, materialize=True)
    preview = service.task_preview(task_id, workspace_id="ws")
    assets = preview["items"][0]["assets"]
    assert {(x["bucket"], x["source_kind"]) for x in assets} >= {
        ("source", "main"),
        ("source", "gallery"),
        ("source", "sku"),
        ("source", "detail"),
    }
    source = next(x for x in assets if x["source_kind"] == "main")
    assert source["media_asset_id"]
    assert source["preview_url"].startswith("/api/product-processing/media-assets/")
    assert source["media_status"] == "ready"
    # The proxy is not a storage authority; it must carry no local managed bytes.
    raw = service.preview_images.repository.get_asset(source["id"], "ws")
    assert raw is not None
    assert raw["managed_path"] == ""
    assert raw["source_url"] == ""


def test_v2_projection_pending_source_has_no_preview_url(tmp_path: Path) -> None:
    service, task_id, _draft_id = _seeded_v2_product(tmp_path, materialize=False)
    preview = service.task_preview(task_id, workspace_id="ws")
    assets = preview["items"][0]["assets"]
    pending = [x for x in assets if x["bucket"] == "source" and x["media_status"] != "ready"]
    assert pending
    assert all(x["preview_url"] == "" for x in pending)


def _save_manifest(
    service: ProductProcessingService,
    task_id: int,
    preview: dict,
    *,
    manifest: dict,
) -> None:
    service.save_task_preview(
        task_id,
        [
            {
                "product_draft_id": preview["product_draft_id"],
                "expected_preview_revision": preview["preview_revision"],
                "expected_result_version": preview["result_version"],
                "overrides": {"image_manifest_v2": manifest},
            }
        ],
        workspace_id="ws",
    )


def test_promoted_source_is_persisted_but_not_exported_until_selected(tmp_path: Path) -> None:
    service, task_id, _draft_id = _seeded_v2_product(tmp_path, materialize=True)
    preview = service.task_preview(task_id, workspace_id="ws")["items"][0]
    source = next(x for x in preview["assets"] if x["bucket"] == "source")
    _save_manifest(
        service,
        task_id,
        preview,
        manifest={
            "main_asset_id": "",
            "carousel_asset_ids": [],
            "detail_asset_ids": [],
            "library_asset_ids": [source["id"]],
            "semantic_asset_ids": {},
        },
    )
    projected = service.task_preview(task_id, workspace_id="ws")["items"][0]
    assert source["id"] in projected["image_manifest"]["library_asset_ids"]
    assert source["id"] not in projected["image_manifest"]["carousel_asset_ids"]


def test_source_selected_as_main_is_serialized_as_first_carousel_item(tmp_path: Path) -> None:
    service, task_id, _draft_id = _seeded_v2_product(tmp_path, materialize=True)
    preview = service.task_preview(task_id, workspace_id="ws")["items"][0]
    source = next(x for x in preview["assets"] if x["bucket"] == "source")
    _save_manifest(
        service,
        task_id,
        preview,
        manifest={
            "main_asset_id": source["id"],
            "carousel_asset_ids": [],
            "detail_asset_ids": [],
            "library_asset_ids": [source["id"]],
            "semantic_asset_ids": {},
        },
    )
    manifest = service.task_preview(task_id, workspace_id="ws")["items"][0]["image_manifest"]
    assert manifest["main_asset_id"] == source["id"]
    assert manifest["carousel_asset_ids"] == [source["id"]]


def test_source_selected_without_library_is_rejected(tmp_path: Path) -> None:
    service, task_id, _draft_id = _seeded_v2_product(tmp_path, materialize=True)
    preview = service.task_preview(task_id, workspace_id="ws")["items"][0]
    source = next(x for x in preview["assets"] if x["bucket"] == "source")
    with pytest.raises(ProductProcessingValidationError):
        _save_manifest(
            service,
            task_id,
            preview,
            manifest={
                "main_asset_id": source["id"],
                "carousel_asset_ids": [],
                "detail_asset_ids": [],
                "library_asset_ids": [],
                "semantic_asset_ids": {},
            },
        )

