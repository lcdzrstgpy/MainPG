from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from wh_local.data_collection.public_image_fetch import FetchedPublicImage
from wh_local.modules.product_processing.domain.models import DailySelectionHandoffEnvelope
from wh_local.modules.product_processing.domain.preview_images import PreviewImageManifest
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.media import GeneratedMedia
from wh_local.modules.product_processing.infrastructure.preview_image_repository import (
    PreviewSourceNotReady,
)
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.preview_image_service import PreviewImageService
from wh_local.modules.product_processing.service import (
    ProductProcessingService,
    ProductProcessingValidationError,
)


def test_preview_export_readiness_blocks_incomplete_manifest() -> None:
    result = {
        "optimized_title": "Test Product",
        "processing_scope": ["four_grid", "detail_images"],
        "provider_status_classes": {"four_grid": "success"},
    }
    assets = [
        {"id": "a1", "publication_status": "ready"},
        {"id": "a2", "publication_status": "ready"},
    ]

    incomplete = PreviewImageService._preview_export_readiness(
        result,
        PreviewImageManifest(
            main_asset_id="a1",
            carousel_asset_ids=("a1", "a2"),
            detail_asset_ids=(),
        ),
        assets,
    )
    assert incomplete["exportable"] is False
    assert any("轮播图需 4 张" in item for item in incomplete["export_blockers"])
    assert any("详情图未生成" in item for item in incomplete["export_blockers"])

    complete_assets = [
        {"id": asset_id, "publication_status": "ready"}
        for asset_id in ("a1", "a2", "a3", "a4", "d1")
    ]
    complete = PreviewImageService._preview_export_readiness(
        result,
        PreviewImageManifest(
            main_asset_id="a1",
            carousel_asset_ids=("a1", "a2", "a3", "a4"),
            detail_asset_ids=("d1",),
        ),
        complete_assets,
    )
    assert complete["exportable"] is True
    assert complete["export_blockers"] == []


def test_preview_export_readiness_blocks_failed_link_even_with_complete_manifest() -> None:
    result = {
        "optimized_title": "Test Product",
        "processing_scope": ["four_grid", "detail_images"],
        "provider_status_classes": {"four_grid": "success"},
        "status": "attention_required",
        "partial_result": True,
        "media_integrity": {"ok": False},
    }
    assets = [
        {"id": asset_id, "publication_status": "ready", "bucket": "processed"}
        for asset_id in ("a1", "a2", "a3", "a4", "d1")
    ]
    readiness = PreviewImageService._preview_export_readiness(
        result,
        PreviewImageManifest(
            main_asset_id="a1",
            carousel_asset_ids=("a1", "a2", "a3", "a4"),
            detail_asset_ids=("d1",),
        ),
        assets,
    )
    assert readiness["exportable"] is False
    assert any("上游厂家" in item for item in readiness["export_blockers"])


def test_preview_export_readiness_blocks_unprocessed_source_carousel() -> None:
    result = {
        "optimized_title": "Test Product",
        "processing_scope": ["four_grid", "detail_images"],
        # 老结果可能没有 four_grid 状态；只要四张轮播还是来源图就不能导出。
        "provider_status_classes": {},
    }
    assets = [
        {"id": "a1", "publication_status": "ready", "bucket": "source"},
        {"id": "a2", "publication_status": "ready", "bucket": "source"},
        {"id": "a3", "publication_status": "ready", "bucket": "source"},
        {"id": "a4", "publication_status": "ready", "bucket": "source"},
        {"id": "d1", "publication_status": "ready", "bucket": "processed"},
    ]
    readiness = PreviewImageService._preview_export_readiness(
        result,
        PreviewImageManifest(
            main_asset_id="a1",
            carousel_asset_ids=("a1", "a2", "a3", "a4"),
            detail_asset_ids=("d1",),
        ),
        assets,
    )
    assert readiness["exportable"] is False
    assert any("来源原图" in item for item in readiness["export_blockers"])


def _jpeg(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="JPEG", quality=94)
    return buffer.getvalue()


def _v2_handoff(index: int = 0) -> DailySelectionHandoffEnvelope:
    suffix = f"-{index}" if index else ""
    return DailySelectionHandoffEnvelope(
        handoff_id=f"h-proj{suffix}",
        run_id=f"run-proj{suffix}",
        candidate_id=f"c-proj{suffix}",
        workspace_id="ws",
        payload_json=json.dumps(
            {
                "candidate": {
                    "candidate_id": f"c-proj{suffix}",
                    "source_url": f"https://detail.example.com/proj{suffix}.html",
                    "source_title": f"Projection product {index}",
                },
                "images": {
                    "main": f"https://img.example.com/{index}/main.jpg",
                    "gallery": [f"https://img.example.com/{index}/gallery.jpg"],
                    "detail": [f"https://img.example.com/{index}/detail.jpg"],
                },
                "skus": [
                    {
                        "sku_id": f"sku-{index}",
                        "image_url": f"https://img.example.com/{index}/sku.jpg",
                        "spec_text": "blue",
                    }
                ],
                "attributes": {},
                "selection_metadata": {},
            },
            ensure_ascii=False,
        ),
        status="pending",
        idempotency_key=f"idem-proj{suffix}",
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


def test_task_preview_preloads_media_and_registers_proxies_in_bulk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预检首屏不得逐素材查媒体/逐素材开写事务。"""
    service, task_id, _draft_id = _seeded_v2_product(tmp_path, materialize=True)
    media_assets = service.preview_images.media_assets
    assert media_assets is not None
    original_get_asset = media_assets.get_asset
    get_asset_calls: list[str] = []

    def tracked_get_asset(asset_id: str, workspace_id: str):
        get_asset_calls.append(str(asset_id))
        return original_get_asset(asset_id, workspace_id)

    monkeypatch.setattr(media_assets, "get_asset", tracked_get_asset)
    proxy_batches: list[int] = []
    original_register_proxies = (
        service.preview_images.register_media_proxies_for_task
    )

    def tracked_register_proxies(**kwargs):
        proxy_batches.append(len(kwargs.get("items") or ()))
        return original_register_proxies(**kwargs)

    monkeypatch.setattr(
        service.preview_images,
        "register_media_proxies_for_task",
        tracked_register_proxies,
    )

    preview = service.task_preview(task_id, workspace_id="ws")

    assert preview["items"][0]["assets"]
    # 绑定/资产/媒体都由 task_preview 批量预载；投影阶段不应再逐个 get_asset。
    assert get_asset_calls == []
    # 整任务只开一次批量注册，一轮就覆盖了 main/gallery/sku/detail。
    assert len(proxy_batches) == 1
    assert proxy_batches[0] >= 4


def test_preview_lazy_media_limit_and_backfill(tmp_path: Path) -> None:
    """首屏只投影前 N 条，其余链接可通过 media 端点按需补齐。"""
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    drafts = [
        service.consume_daily_selection_handoffs([_v2_handoff(index)])["drafts"][0]
        for index in (0, 1, 2)
    ]
    service.media_assets.public_image_fetcher = lambda url: FetchedPublicImage(
        _jpeg("green"), "image/jpeg", url
    )
    service.media_assets.materialize_pending(workspace_id="ws", limit=500)
    task = service.repository.create_task(
        title="lazy",
        preflight_only=False,
        settings={},
        drafts=drafts,
        idempotency_key=None,
        workspace_id="ws",
    )
    results = []
    for item, draft in zip(task["items"], drafts):
        results.append(
            {
                "item_id": item["id"],
                "status": "completed",
                "reason": "",
                "result": {
                    "product_draft_id": draft["id"],
                    "optimized_title": f"Lazy product {draft['id']}",
                    "description": "DURABLE - lazy product.",
                    "source_image_urls": [
                        f"https://img.example.com/{draft['id']}/main.jpg"
                    ],
                    "source_detail_image_urls": [
                        f"https://img.example.com/{draft['id']}/detail.jpg"
                    ],
                    "carousel_image_paths": [],
                    "detail_image_paths": [],
                },
            }
        )
    service.repository.finish_task(
        task["id"],
        results,
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="ws",
    )

    preview = service.task_preview(
        task["id"], workspace_id="ws", item_limit=2, media_limit=1
    )
    assert preview["item_count"] == 3
    assert len(preview["items"]) == 2
    assert preview["has_more"] is True
    assert preview["item_summary"]["total"] == 3
    assert preview["items"][0]["media_loaded"] is True
    assert preview["items"][0]["assets"]
    assert preview["items"][1]["media_loaded"] is False
    assert preview["items"][1]["assets"] == []

    second_draft_id = int(preview["items"][1]["product_draft_id"])
    media = service.task_preview_media(task["id"], [second_draft_id], workspace_id="ws")
    payload = media["items"][str(second_draft_id)]
    assert payload["assets"]
    assert "image_manifest" in payload

    page2 = service.task_preview(
        task["id"], workspace_id="ws", item_limit=2, item_offset=2, media_limit=0
    )
    assert len(page2["items"]) == 1
    assert page2["has_more"] is False
    assert page2["items"][0]["media_loaded"] is False


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

def test_pending_source_cannot_be_added_to_library_or_selected_for_export(tmp_path: Path) -> None:
    service, task_id, _draft_id = _seeded_v2_product(tmp_path, materialize=False)
    preview = service.task_preview(task_id, workspace_id="ws")["items"][0]
    source = next(x for x in preview["assets"] if x["bucket"] == "source")
    assert source["media_status"] == "pending"

    with pytest.raises(ProductProcessingValidationError, match="同步完成"):
        _save_manifest(
            service,
            task_id,
            preview,
            manifest={
                "main_asset_id": source["id"],
                "carousel_asset_ids": [source["id"]],
                "detail_asset_ids": [],
                "library_asset_ids": [source["id"]],
                "semantic_asset_ids": {},
            },
        )

    with pytest.raises(PreviewSourceNotReady, match="同步完成"):
        service.preview_images.begin_finalize(
            task_id,
            [{
                "product_draft_id": preview["product_draft_id"],
                "expected_preview_revision": preview["preview_revision"],
                "expected_result_version": preview["result_version"],
                "overrides": {"image_manifest_v2": {
                    "main_asset_id": source["id"],
                    "carousel_asset_ids": [source["id"]],
                    "detail_asset_ids": [],
                    "library_asset_ids": [source["id"]],
                    "semantic_asset_ids": {},
                }},
            }],
            workspace_id="ws",
            launch=False,
        )


def test_same_source_media_preserves_every_bound_source_kind(tmp_path: Path) -> None:
    service, task_id, draft_id = _seeded_v2_product(tmp_path, materialize=True)
    main_binding = next(
        binding
        for binding in service.media_assets.list_bindings("ws", product_draft_id=draft_id)
        if binding["role"] == "main"
    )
    service.media_assets.bind_asset(
        workspace_id="ws",
        asset_id=main_binding["asset_id"],
        product_draft_id=draft_id,
        role="gallery",
        sort_order=99,
    )

    assets = service.task_preview(task_id, workspace_id="ws")["items"][0]["assets"]
    same_media = [
        asset
        for asset in assets
        if asset["media_asset_id"] == main_binding["asset_id"]
    ]
    assert {asset["source_kind"] for asset in same_media} == {"main", "gallery"}
    assert len({asset["id"] for asset in same_media}) == 2


def test_saved_legacy_preview_asset_ids_resolve_to_current_v2_proxies(tmp_path: Path) -> None:
    service, task_id, draft_id = _seeded_v2_product(tmp_path, materialize=True)
    # A generated preview asset row (canvas-era writeback style) is NOT the
    # proxy the reloaded V2 pool serves. Its ID must be translated.
    generated = service.preview_images.register_generated(
        task_id=task_id,
        product_draft_id=draft_id,
        workspace_id="ws",
        media=GeneratedMedia(
            stage="grid_image_1", content=_jpeg("blue"), content_type="image/jpeg",
            suffix=".jpg", provider="test", model="test", reference_count=1,
        ),
    )
    legacy_id = generated["id"]
    assert legacy_id != generated["media_asset_id"]

    preview = service.task_preview(task_id, workspace_id="ws")["items"][0]
    _save_manifest(
        service,
        task_id,
        preview,
        manifest={
            "main_asset_id": legacy_id,
            "carousel_asset_ids": [legacy_id],
            "detail_asset_ids": [legacy_id],
            "library_asset_ids": [legacy_id],
            "semantic_asset_ids": {"carousel.hero": legacy_id},
        },
    )

    reloaded = service.task_preview(task_id, workspace_id="ws")["items"][0]
    manifest = reloaded["image_manifest"]
    assert manifest["main_asset_id"] != legacy_id
    assert manifest["main_asset_id"] in {asset["id"] for asset in reloaded["assets"]}
    assert manifest["carousel_asset_ids"] == [manifest["main_asset_id"]]
    assert manifest["semantic_asset_ids"].get("carousel.hero") == manifest["main_asset_id"]
    proxy = next(
        asset for asset in reloaded["assets"] if asset["id"] == manifest["main_asset_id"]
    )
    assert proxy["media_asset_id"] == generated["media_asset_id"]
    assert proxy["preview_url"]
    assert len(manifest["detail_asset_ids"]) == 1
    assert manifest["detail_asset_ids"][0] == manifest["main_asset_id"]


def _seeded_v1_product(tmp_path: Path) -> tuple[ProductProcessingService, int, int]:
    """手动采集类 v1 草稿：源图只存 result.source_image_urls，无 v2 远程绑定。"""
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    draft = service.repository.create_draft(
        {
            "workspace_id": "ws",
            "source_type": "web_manual_capture",
            "title": "v1 source pool product",
            "image_url": "https://img.example.com/main.jpg",
        }
    )
    assert draft["media_contract_version"] == 1
    task = service.repository.create_task(
        title="v1-source-pool",
        preflight_only=False,
        settings={},
        drafts=[draft],
        idempotency_key=None,
        workspace_id="ws",
    )
    item = task["items"][0]
    result = {
        "product_draft_id": draft["id"],
        "optimized_title": "v1 source pool title",
        "description": "POINT - v1 source pool product.",
        "skc": "PP-V1",
        "sku": "SKU-V1",
        "source_image_urls": [
            "https://img.example.com/main.jpg",
            "https://img.example.com/gallery.jpg",
            "https://img.example.com/gallery2.jpg",
        ],
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


def test_v1_projection_registers_source_urls_into_source_pool(tmp_path: Path) -> None:
    """v1 草稿（手动采集）即使源图不进轮播/详情，预检源池也必须展示原图。"""
    service, task_id, _draft_id = _seeded_v1_product(tmp_path)
    preview = service.task_preview(task_id, workspace_id="ws")["items"][0]
    sources = [asset for asset in preview["assets"] if asset["bucket"] == "source"]
    assert len(sources) == 3, [asset["bucket"] for asset in preview["assets"]]
    assert next(asset for asset in sources if asset["source_kind"] == "main")["preview_url"] == "https://img.example.com/main.jpg"
    gallery_urls = {
        asset["preview_url"] for asset in sources if asset["source_kind"] == "gallery"
    }
    assert gallery_urls == {
        "https://img.example.com/gallery.jpg",
        "https://img.example.com/gallery2.jpg",
    }
    assert all(asset["origin"] == "source" for asset in sources)
