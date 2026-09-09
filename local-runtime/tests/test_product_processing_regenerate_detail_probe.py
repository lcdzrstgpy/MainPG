"""Regression tests for "update detail image" (regenerate_preview_detail_images).

Replicates the real user flow: seed a v2 product with ready media, promote
ready sources into the carousel manifest, call regenerate, then re-project and
assert the freshly synthesized detail image is visible again.

History / why this exists:
- The original code tried to read carousel bytes by treating manifest ids as
  unified media ids, but V2 manifest ids are no-copy proxy ids -> regenerate
  always failed with "暂无可用的轮播图".
- It then persisted the raw unified media id into detail_asset_ids, which the
  save_preview manifest validation rejects and the V2 projection cannot map
  back to a proxy -> the fresh detail never appeared.
- Fix: resolve proxy->media before reading bytes, and persist the preview row
  identity (asset["id"]) that both save_preview and the V2 projection accept.
"""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from wh_local.data_collection.public_image_fetch import FetchedPublicImage
from wh_local.modules.product_processing.domain.models import DailySelectionHandoffEnvelope
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService


def _jpeg(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="JPEG", quality=94)
    return buffer.getvalue()


def _v2_handoff() -> DailySelectionHandoffEnvelope:
    return DailySelectionHandoffEnvelope(
        handoff_id="h-probe",
        run_id="run-probe",
        candidate_id="c-probe",
        workspace_id="ws",
        payload_json=json.dumps(
            {
                "candidate": {
                    "candidate_id": "c-probe",
                    "source_url": "https://detail.example.com/proj.html",
                    "source_title": "Probe product",
                },
                "images": {
                    "main": "https://img.example.com/main.jpg",
                    "gallery": ["https://img.example.com/gallery.jpg", "https://img.example.com/g2.jpg"],
                    "detail": ["https://img.example.com/detail.jpg"],
                },
                "skus": [
                    {"sku_id": "sku-1", "image_url": "https://img.example.com/sku.jpg", "spec_text": "blue"}
                ],
                "attributes": {},
                "selection_metadata": {},
            },
            ensure_ascii=False,
        ),
        status="pending",
        idempotency_key="idem-probe",
        created_at="2026-08-14T00:00:00+00:00",
    )


def _seed(tmp_path: Path) -> tuple[ProductProcessingService, int, int]:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft = service.consume_daily_selection_handoffs([_v2_handoff()])["drafts"][0]
    assert draft["media_contract_version"] == 2
    task = service.repository.create_task(
        title="probe", preflight_only=False, settings={}, drafts=[draft],
        idempotency_key=None, workspace_id="ws",
    )
    service.media_assets.public_image_fetcher = lambda url: FetchedPublicImage(
        _jpeg("green"), "image/jpeg", url
    )
    service.media_assets.materialize_pending(workspace_id="ws")
    item = task["items"][0]
    result = {
        "product_draft_id": draft["id"],
        "optimized_title": "Probe product title",
        "description": "DURABLE - Probe product.",
        "skc": "PP-PROBE",
        "sku": "SKU-1",
        "source_image_urls": [
            "https://img.example.com/main.jpg", "https://img.example.com/gallery.jpg",
        ],
        "source_detail_image_urls": ["https://img.example.com/detail.jpg"],
        "carousel_image_paths": [],
        "detail_image_paths": [],
        "product_dimensions": {"length_cm": 1, "width_cm": 1, "height_cm": 1, "weight_g": 1},
    }
    service.repository.finish_task(
        task["id"],
        [{"item_id": item["id"], "status": "completed", "reason": "", "result": result}],
        output_file="", error_report_file="", video_manifest_file="", workspace_id="ws",
    )
    return service, int(task["id"]), int(draft["id"])


def _install_carousel_sources(
    service: ProductProcessingService, task_id: int, draft_id: int
) -> None:
    """Promote ready source media into the carousel + library so regenerate can read it."""
    item = service.task_preview(task_id, workspace_id="ws")["items"][0]
    ready_sources = [
        a for a in item["assets"]
        if a["bucket"] == "source" and a["source_kind"] in ("main", "gallery")
        and a["media_status"] == "ready"
    ]
    assert ready_sources
    proxy_ids = [a["id"] for a in ready_sources]
    service.save_task_preview(
        task_id,
        [{
            "product_draft_id": draft_id,
            "expected_preview_revision": item["preview_revision"],
            "expected_result_version": item["result_version"],
            "overrides": {
                "image_manifest_v2": {
                    "main_asset_id": ready_sources[0]["id"],
                    "carousel_asset_ids": proxy_ids,
                    "detail_asset_ids": [],
                    # source proxies must be in the library before serving as live carousel
                    "library_asset_ids": proxy_ids,
                    "semantic_asset_ids": {},
                }
            },
        }],
        workspace_id="ws",
    )


def test_regenerate_detail_image_produces_visible_detail(tmp_path: Path) -> None:
    service, task_id, draft_id = _seed(tmp_path)
    _install_carousel_sources(service, task_id, draft_id)

    result = service.regenerate_preview_detail_images(task_id, draft_id, workspace_id="ws")
    items = result["items"]
    assert len(items) == 1
    manifest = items[0]["image_manifest"]
    assert manifest["detail_asset_ids"], "detail_asset_ids should hold the fresh detail"
    assert items[0].get("detail_images"), "fresh detail image must be projectable/visible"

    # The returned detail id must be a real asset in the projection.
    asset_ids = {a["id"] for a in items[0]["assets"]}
    for detail_id in manifest["detail_asset_ids"]:
        assert detail_id in asset_ids, f"detail id {detail_id} missing from assets"
        assert detail_id in {a["id"] for a in items[0]["assets"] if a["media_status"] == "ready"}


def test_regenerate_is_repeatable_and_detail_grows(tmp_path: Path) -> None:
    service, task_id, draft_id = _seed(tmp_path)
    _install_carousel_sources(service, task_id, draft_id)

    first = service.regenerate_preview_detail_images(task_id, draft_id, workspace_id="ws")
    first_ids = set(first["items"][0]["image_manifest"]["detail_asset_ids"])
    assert len(first_ids) == 1

    # A second regenerate should also succeed and the previously generated
    # detail should still resolve (no dangling/unprojectable asset).
    second = service.regenerate_preview_detail_images(task_id, draft_id, workspace_id="ws")
    second_item = second["items"][0]
    second_ids = set(second_item["image_manifest"]["detail_asset_ids"])
    assert len(second_ids) >= 1
    asset_ids = {a["id"] for a in second_item["assets"]}
    assert second_ids <= asset_ids
    assert second_item.get("detail_images"), "detail must remain visible after second run"
