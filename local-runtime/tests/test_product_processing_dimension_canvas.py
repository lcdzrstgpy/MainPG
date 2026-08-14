from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, update

from wh_local.data_collection.public_image_fetch import FetchedPublicImage
from wh_local.modules.product_processing.api.dimension_canvas_router import create_dimension_canvas_router
from wh_local.modules.product_processing.api.router import create_product_processing_router
from wh_local.modules.product_processing.dimension_canvas_service import (
    DimensionCanvasConflict,
    DimensionCanvasService,
)
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.dimension_canvas_repository import DimensionCanvasRepository
from wh_local.modules.product_processing.infrastructure.dimension_canvas_repository import CanvasStateConflict
from wh_local.modules.product_processing.infrastructure.dimension_renderer import DimensionSourceInfo
from wh_local.modules.product_processing.infrastructure.dimension_canvas_orm import DimensionCanvasItemRow
from wh_local.modules.product_processing.infrastructure.preview_image_orm import PreviewImageAssetRow
from wh_local.modules.product_processing.infrastructure.orm import (
    ProcessingTaskItemRow,
    ProcessingTaskRow,
    ProductDraftRow,
)
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.modules.product_processing.infrastructure.media_asset_repository import MediaAssetRepository
from wh_local.modules.product_processing.media_asset_service import MediaAssetService


@dataclass(frozen=True)
class _Output:
    master_png_bytes: bytes
    jpeg_bytes: bytes
    content_hash: str
    width: int = 2000
    height: int = 2000


class _Renderer:
    def inspect_source(self, content: bytes) -> DimensionSourceInfo:
        if not content.startswith(b"valid-image"):
            raise ValueError("dimension_source_invalid")
        return DimensionSourceInfo(800, 600, "image/png", ".png")

    def render(self, request) -> _Output:
        content = b"rendered:" + request.source_bytes + str(request.annotations).encode()
        return _Output(b"master:" + content, content, hashlib.sha256(content).hexdigest())


def _dimensions(value: float = 10) -> dict:
    return {
        "length": {"value_cm": value, "provenance": "manual_confirmed", "evidence_ref": "manual"},
        "width": {"value_cm": 8, "provenance": "manual_confirmed", "evidence_ref": "manual"},
        "height": {"value_cm": 4, "provenance": "manual_confirmed", "evidence_ref": "manual"},
        "conflict": False,
    }


def _annotation(
    value: float = 10,
    line_width: str = "normal",
    endpoint_style: str = "arrow",
) -> dict:
    return {
        "id": "a1",
        "key": "length",
        "value_cm": value,
        "start": {"x": 0.15, "y": 0.8},
        "end": {"x": 0.85, "y": 0.8},
        "label": {"x": 0.5, "y": 0.7},
        "style": "auto",
        "line_width": line_width,
        "endpoint_style": endpoint_style,
    }


def test_annotation_line_width_is_normalized_and_validated() -> None:
    normalized = DimensionCanvasService._normalize_annotation(_annotation(line_width="thick"))
    assert normalized["line_width"] == "thick"
    with pytest.raises(ValueError):
        DimensionCanvasService._normalize_annotation(_annotation(line_width="extra-thick"))


def test_annotation_endpoint_style_is_normalized_and_validated() -> None:
    normalized = DimensionCanvasService._normalize_annotation(_annotation(endpoint_style="bar"))
    assert normalized["endpoint_style"] == "bar"
    with pytest.raises(ValueError):
        DimensionCanvasService._normalize_annotation(_annotation(endpoint_style="circle"))


def test_canvas_endpoint_style_is_accepted_and_validated() -> None:
    cleaned = DimensionCanvasService._validate_save_patch(
        {"canvas_settings": {"endpoint_style": "none"}}
    )
    assert cleaned["canvas_settings"]["endpoint_style"] == "none"
    with pytest.raises(ValueError, match="endpoint style"):
        DimensionCanvasService._validate_save_patch(
            {"canvas_settings": {"endpoint_style": "circle"}}
        )


def _seed(database, workspace_id: str = "local", *, remote: bool = False) -> dict:
    with database.sessions.begin() as session:
        draft = ProductDraftRow(workspace_id=workspace_id, skc=f"{workspace_id}-skc", status="draft")
        session.add(draft)
        session.flush()
        value = "https://registered.example.test/source.jpg" if remote else "server-managed-source.jpg"
        task = ProcessingTaskRow(
            workspace_id=workspace_id,
            title="finished",
            status="completed",
            total_count=1,
            success_count=1,
        )
        session.add(task)
        session.flush()
        item = ProcessingTaskItemRow(
            task_id=task.id,
            product_draft_id=draft.id,
            skc=str(draft.skc),
            status="completed",
            result_json=json.dumps(
                {
                    "physical_dimensions": _dimensions(),
                    "image_manifest": [
                        {
                            "slot_id": "carousel.dimension_background",
                            "role": "dimension_background",
                            "value": value,
                        }
                    ],
                }
            ),
        )
        session.add(item)
        session.flush()
        return {"task_id": task.id, "task_item_id": item.id, "draft_id": draft.id}


@pytest.fixture
def service_fixture(tmp_path: Path):
    database = create_database(f"sqlite:///{(tmp_path / 'dimension-tests.sqlite3').as_posix()}")
    product_repository = ProductProcessingRepository(database)
    canvas_repository = DimensionCanvasRepository(database)
    loader_calls: list[dict] = []
    publisher_calls: list[dict] = []

    def loader(asset: dict) -> bytes:
        loader_calls.append(dict(asset))
        return b"deterministic-registered-image"

    def publisher(
        content: bytes,
        task_id: int,
        draft_id: int,
        render_revision: int,
        content_hash: str,
        workspace_id: str,
    ) -> dict:
        assert hashlib.sha256(content).hexdigest() == content_hash
        publisher_calls.append(
            {
                "task_id": task_id,
                "draft_id": draft_id,
                "render_revision": render_revision,
                "content_hash": content_hash,
                "workspace_id": workspace_id,
            }
        )
        return {
            "url": f"https://bucket.cos.ap-guangzhou.myqcloud.com/dimension/{content_hash}.jpg"
        }

    service = DimensionCanvasService(
        canvas_repository,
        product_repository,
        ProductProcessingAssets(tmp_path),
        _Renderer(),
        loader,
        publisher,
    )
    try:
        yield database, product_repository, canvas_repository, service, loader_calls, publisher_calls
    finally:
        service.close()
        database.dispose()


def _complete(service: DimensionCanvasService, seeded: dict, workspace_id: str = "local") -> dict:
    item = service.import_preview_item(
        seeded["task_id"], seeded["task_item_id"], workspace_id=workspace_id
    )
    selected = item["assets"][0]
    saved = service.save_item(
        item["id"],
        item["item_revision"],
        {
            "selected_source_asset_id": selected["id"],
            "physical_dimensions": _dimensions(),
            "annotations": [_annotation()],
        },
        workspace_id=workspace_id,
    )
    service.complete_item(item["id"], saved["item_revision"], workspace_id=workspace_id)
    return service.wait_for_test_render(item["id"], workspace_id=workspace_id)


def test_single_item_import_materializes_registered_asset_renders_and_submits(service_fixture) -> None:
    database, _product, repository, service, loader_calls, publisher_calls = service_fixture
    seeded = _seed(database, remote=True)
    completed = _complete(service, seeded)
    assert completed["state"] == "completed"
    assert len(loader_calls) == 1
    selected = repository.get_asset(completed["selected_source_asset_id"], completed["id"], "local")
    assert selected is not None
    assert selected["content_hash"] == hashlib.sha256(b"deterministic-registered-image").hexdigest()
    assert selected["managed_path"]
    change_set = service.submit_review(completed["batch_id"], workspace_id="local")
    repeated = service.submit_review(completed["batch_id"], workspace_id="local")
    assert change_set["item_count"] == 1
    assert repeated["id"] == change_set["id"]
    assert publisher_calls == []
    hydrated = service.get_item(completed["id"], workspace_id="local")
    rendered = next(asset for asset in hydrated["assets"] if asset["role"] == "rendered_dimension")
    assert rendered["preview_url"].startswith("/api/product-processing/dimension-canvas/assets/")
    assert change_set["items"][0]["new_image_url"].startswith("/api/product-processing/dimension-canvas/assets/")


def test_repeated_submit_after_acceptance_returns_existing_change_set(service_fixture) -> None:
    database, _product, _repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database)
    completed = _complete(service, seeded)

    submitted = service.submit_review(completed["batch_id"], workspace_id="local")
    accepted = service.accept_change_set(submitted["id"], workspace_id="local")
    repeated = service.submit_review(completed["batch_id"], workspace_id="local")

    assert accepted["status"] == "resolved"
    assert repeated["id"] == submitted["id"]
    assert repeated["status"] == "resolved"


def test_user_upload_is_registered_once_and_available_as_local_preview(service_fixture) -> None:
    database, _product, repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database)
    item = service.import_preview_item(
        seeded["task_id"], seeded["task_item_id"], workspace_id="local"
    )

    first = service.upload_asset(
        item["id"],
        b"valid-image-content",
        "custom.png",
        "image/png",
        workspace_id="local",
    )
    repeated = service.upload_asset(
        item["id"],
        b"valid-image-content",
        "renamed.png",
        "image/png",
        workspace_id="local",
    )

    assert first["asset_id"] == repeated["asset_id"]
    uploaded = repository.get_asset(first["asset_id"], item["id"], "local")
    assert uploaded is not None
    assert uploaded["role"] == "user_upload"
    assert uploaded["availability"] == "local"
    hydrated = service.get_item(item["id"], workspace_id="local")
    preview = next(asset for asset in hydrated["assets"] if asset["id"] == first["asset_id"])
    assert preview["preview_url"].startswith("/api/product-processing/dimension-canvas/assets/")


def test_user_upload_endpoint_rejects_invalid_bytes(service_fixture) -> None:
    database, _product, _repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database)
    item = service.import_preview_item(
        seeded["task_id"], seeded["task_item_id"], workspace_id="local"
    )
    app = FastAPI()
    app.include_router(create_dimension_canvas_router(service))

    response = TestClient(app).post(
        f"/dimension-canvas/items/{item['id']}/assets",
        files={"file": ("bad.jpg", b"not-an-image", "image/jpeg")},
        headers={"X-Workspace-ID": "local"},
    )

    assert response.status_code == 400
    assert "dimension_source_invalid" in response.text


def test_redraw_uses_new_render_revision_and_new_change_set_key(service_fixture) -> None:
    database, _product, _repository, service, _calls, publisher_calls = service_fixture
    seeded = _seed(database)
    first = _complete(service, seeded)
    first_set = service.submit_review(first["batch_id"], workspace_id="local")
    edited = service.save_item(
        first["id"],
        first["item_revision"],
        {"physical_dimensions": _dimensions(12), "annotations": [_annotation(12)]},
        workspace_id="local",
    )
    service.complete_item(first["id"], edited["item_revision"], workspace_id="local")
    second = service.wait_for_test_render(first["id"], workspace_id="local")
    second_set = service.submit_review(second["batch_id"], workspace_id="local")
    assert second["render_revision"] == first["render_revision"] + 1
    assert second_set["idempotency_key"] != first_set["idempotency_key"]
    assert publisher_calls == []


def test_accept_conflict_does_not_silently_overwrite_target_slot(service_fixture) -> None:
    database, product, _repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database)
    completed = _complete(service, seeded)
    change_set = service.submit_review(completed["batch_id"], workspace_id="local")
    draft = product.get_draft(seeded["draft_id"], workspace_id="local")
    assert draft is not None
    product.save_draft_preview_overrides(
        seeded["draft_id"],
        {"image_slot_overrides": {"carousel.dimension_background": {"url": "manual-new.jpg"}}},
        expected_revision=draft["preview_revision"],
        workspace_id="local",
    )
    accepted = service.accept_change_set(change_set["id"], workspace_id="local")
    assert accepted["accepted_count"] == 0
    assert accepted["conflict_count"] == 1
    after = product.get_draft(seeded["draft_id"], workspace_id="local")
    assert after is not None
    assert after["preview_overrides"]["image_slot_overrides"]["carousel.dimension_background"]["url"] == "manual-new.jpg"


def test_accept_merges_only_target_slot_and_preserves_legacy_carousel(service_fixture) -> None:
    database, product, _repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database)
    draft = product.get_draft(seeded["draft_id"], workspace_id="local")
    assert draft is not None
    product.save_draft_preview_overrides(
        seeded["draft_id"],
        {"carousel_images": ["one.jpg", "two.jpg", "three.jpg", "old.jpg"], "title": "keep"},
        expected_revision=draft["preview_revision"],
        workspace_id="local",
    )
    completed = _complete(service, seeded)
    change_set = service.submit_review(completed["batch_id"], workspace_id="local")
    accepted = service.accept_change_set(change_set["id"], workspace_id="local")
    assert accepted["accepted_count"] == 1
    after = product.get_draft(seeded["draft_id"], workspace_id="local")
    assert after is not None
    overrides = after["preview_overrides"]
    assert overrides["carousel_images"] == ["one.jpg", "two.jpg", "three.jpg", "old.jpg"]
    assert overrides["title"] == "keep"
    manifest = overrides["image_manifest_v2"]
    assert manifest["carousel_asset_ids"][3]
    assert "image_slot_overrides" not in overrides


def test_submit_review_blocks_when_preview_changed_after_canvas_import(service_fixture) -> None:
    database, product, _repository, service, _calls, publisher_calls = service_fixture
    seeded = _seed(database)
    imported = service.import_preview_item(
        seeded["task_id"], seeded["task_item_id"], workspace_id="local"
    )
    draft = product.get_draft(seeded["draft_id"], workspace_id="local")
    assert draft is not None
    product.save_draft_preview_overrides(
        seeded["draft_id"],
        {"image_slot_overrides": {"carousel.dimension_background": {"url": "manual-new.jpg"}}},
        expected_revision=draft["preview_revision"],
        workspace_id="local",
    )
    selected = imported["assets"][0]
    saved = service.save_item(
        imported["id"],
        imported["item_revision"],
        {
            "selected_source_asset_id": selected["id"],
            "physical_dimensions": _dimensions(),
            "annotations": [_annotation()],
        },
        workspace_id="local",
    )
    service.complete_item(imported["id"], saved["item_revision"], workspace_id="local")
    completed = service.wait_for_test_render(imported["id"], workspace_id="local")
    with pytest.raises(DimensionCanvasConflict, match="重新导入"):
        service.submit_review(completed["batch_id"], workspace_id="local")
    assert publisher_calls == []


def test_submit_review_never_calls_cos_publisher(service_fixture) -> None:
    database, _product, repository, service, _calls, publisher_calls = service_fixture
    seeded = _seed(database)
    completed = _complete(service, seeded)
    change_set = service.submit_review(completed["batch_id"], workspace_id="local")
    assert publisher_calls == []
    assert change_set["items"][0]["new_image_url"].startswith("/api/product-processing/dimension-canvas/assets/")
    assert repository.list_notifications("local")


def test_database_publish_claim_prevents_duplicate_cos_upload(service_fixture) -> None:
    database, _product, repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database)
    completed = _complete(service, seeded)
    claimed = repository.claim_item_publish(
        completed["id"], completed["render_asset_id"], completed["render_revision"], "local"
    )
    assert claimed["availability"] == "local"
    claim_token = claimed["_publish_claim_token"]
    with pytest.raises(CanvasStateConflict, match="already publishing"):
        repository.claim_item_publish(
            completed["id"], completed["render_asset_id"], completed["render_revision"], "local"
        )
    repository.release_item_publish(
        completed["id"],
        completed["render_asset_id"],
        "local",
        claim_token=claim_token,
        error_message="test release",
    )
    assert service.get_item(completed["id"], workspace_id="local")["state"] == "completed"


def test_new_service_does_not_release_an_active_publish_lease(service_fixture, tmp_path: Path) -> None:
    database, product, repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database)
    completed = _complete(service, seeded)
    repository.claim_item_publish(
        completed["id"], completed["render_asset_id"], completed["render_revision"], "local"
    )
    second = DimensionCanvasService(
        repository,
        product,
        ProductProcessingAssets(tmp_path),
        _Renderer(),
    )
    try:
        assert second.get_item(completed["id"], workspace_id="local")["state"] == "publishing"
        with pytest.raises(CanvasStateConflict, match="already publishing"):
            repository.claim_item_publish(
                completed["id"], completed["render_asset_id"], completed["render_revision"], "local"
            )
    finally:
        second.close()


def test_expired_publish_lease_is_reclaimed_and_old_worker_cannot_finish(service_fixture) -> None:
    database, _product, repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database)
    completed = _complete(service, seeded)
    first = repository.claim_item_publish(
        completed["id"], completed["render_asset_id"], completed["render_revision"], "local"
    )
    old_token = first["_publish_claim_token"]
    with database.sessions.begin() as session:
        session.execute(
            update(DimensionCanvasItemRow)
            .where(DimensionCanvasItemRow.id == completed["id"])
            .values(publish_claimed_at="2000-01-01T00:00:00+00:00")
        )
    second = repository.claim_item_publish(
        completed["id"], completed["render_asset_id"], completed["render_revision"], "local"
    )
    new_token = second["_publish_claim_token"]
    assert new_token != old_token
    repository.release_item_publish(
        completed["id"],
        completed["render_asset_id"],
        "local",
        claim_token=old_token,
        error_message="late old worker",
    )
    assert service.get_item(completed["id"], workspace_id="local")["state"] == "publishing"
    with pytest.raises(CanvasStateConflict, match="claim changed"):
        repository.mark_asset_published(
            completed["render_asset_id"],
            completed["id"],
            "local",
            public_url="https://bucket.cos.ap-guangzhou.myqcloud.com/dimension/stale.jpg",
            claim_token=old_token,
        )
    published = repository.mark_asset_published(
        completed["render_asset_id"],
        completed["id"],
        "local",
        public_url="https://bucket.cos.ap-guangzhou.myqcloud.com/dimension/current.jpg",
        claim_token=new_token,
    )
    assert published["availability"] == "published"


def test_submit_review_ignores_legacy_publisher_and_keeps_render_local(service_fixture) -> None:
    database, _product, repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database)
    completed = _complete(service, seeded)
    change_set = service.submit_review(completed["batch_id"], workspace_id="local")
    assert change_set["items"][0]["new_image_url"].startswith("/api/product-processing/dimension-canvas/assets/")
    asset = repository.get_asset(completed["render_asset_id"], completed["id"], "local")
    assert asset is not None
    assert asset["availability"] == "local"


def _v2_jpeg(color: str = "red") -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="JPEG", quality=94)
    return buffer.getvalue()


def test_v2_import_snapshots_ready_media_and_marks_pending_unavailable(tmp_path: Path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'v2-dimension.sqlite3').as_posix()}")
    assets = ProductProcessingAssets(tmp_path / "assets")
    product_repository = ProductProcessingRepository(database)
    canvas_repository = DimensionCanvasRepository(database)
    media_assets = MediaAssetService(MediaAssetRepository(database), assets)

    ready_main = media_assets.register_local_asset("local", "preview_upload", _v2_jpeg("green"), "image/jpeg")
    ready_carousel = media_assets.register_local_asset("local", "ai_generated", _v2_jpeg("blue"), "image/jpeg")
    pending = media_assets.register_remote_asset("local", "https://img.example.com/pending.jpg")

    with database.sessions.begin() as session:
        draft = ProductDraftRow(workspace_id="local", media_contract_version=2, skc="v2-skc", status="draft")
        session.add(draft)
        session.flush()
        task = ProcessingTaskRow(
            workspace_id="local", title="finished", status="completed", total_count=1, success_count=1
        )
        session.add(task)
        session.flush()
        draft_id = int(draft.id)
        item = ProcessingTaskItemRow(
            task_id=task.id,
            product_draft_id=draft_id,
            skc="v2-skc",
            status="completed",
            result_json=json.dumps(
                {
                    "physical_dimensions": _dimensions(),
                    "image_manifest_v2": {
                        "main_asset_id": ready_main["id"],
                        "carousel_asset_ids": [ready_carousel["id"]],
                        "detail_asset_ids": [],
                        "semantic_asset_ids": {"carousel.dimension_background": ready_carousel["id"]},
                    },
                }
            ),
        )
        session.add(item)
        session.flush()
        task_id = int(task.id)
        task_item_id = int(item.id)

    media_assets.bind_asset(workspace_id="local", asset_id=ready_main["id"], product_draft_id=draft_id, role="main")
    media_assets.bind_asset(workspace_id="local", asset_id=pending["id"], product_draft_id=draft_id, role="gallery", sort_order=0)

    loader_calls: list[dict] = []
    service = DimensionCanvasService(
        canvas_repository,
        product_repository,
        assets,
        _Renderer(),
        lambda asset: loader_calls.append(dict(asset)) or b"",
        media_assets=media_assets,
    )
    try:
        eligibility = service.task_eligibility(task_id, workspace_id="local")
        assert [entry["task_item_id"] for entry in eligibility["ready"]] == [task_item_id]
        assert eligibility["asset_failed"] == []
        hydrated = service.import_preview_item(task_id, task_item_id, workspace_id="local")
    finally:
        service.close()

    by_role = {asset["role"]: asset for asset in hydrated["assets"]}
    assert by_role["source"]["source_media_asset_id"] == ready_main["id"]
    assert by_role["source"]["availability"] == "local"
    gallery = next(asset for asset in hydrated["assets"] if asset["role"] == "gallery")
    assert gallery["availability"] == "unavailable"
    assert gallery["preview_url"] == ""

    ready_canvas = canvas_repository.get_asset(by_role["source"]["id"], hydrated["id"], "local")
    assert ready_canvas["managed_path"]
    assert ready_canvas["source_media_asset_id"] == ready_main["id"]
    assert loader_calls == []


def test_v2_canvas_refresh_materializes_media_that_finished_syncing_later(tmp_path: Path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'v2-refresh.sqlite3').as_posix()}")
    assets = ProductProcessingAssets(tmp_path / "assets")
    product_repository = ProductProcessingRepository(database)
    canvas_repository = DimensionCanvasRepository(database)
    remote_bytes = _v2_jpeg("orange")
    media_assets = MediaAssetService(
        MediaAssetRepository(database),
        assets,
        public_image_fetcher=lambda _url: FetchedPublicImage(
            content=remote_bytes,
            media_type="image/jpeg",
            final_url="https://img.example.com/later.jpg",
        ),
    )
    pending = media_assets.register_remote_asset("local", "https://img.example.com/later.jpg")

    with database.sessions.begin() as session:
        draft = ProductDraftRow(workspace_id="local", media_contract_version=2, skc="v2-later", status="draft")
        session.add(draft)
        session.flush()
        task = ProcessingTaskRow(workspace_id="local", title="finished", status="completed", total_count=1, success_count=1)
        session.add(task)
        session.flush()
        item = ProcessingTaskItemRow(
            task_id=task.id,
            product_draft_id=draft.id,
            skc="v2-later",
            status="completed",
            result_json=json.dumps({"physical_dimensions": _dimensions()}),
        )
        session.add(item)
        session.flush()
        draft_id, task_id, task_item_id = int(draft.id), int(task.id), int(item.id)

    media_assets.bind_asset(
        workspace_id="local",
        asset_id=pending["id"],
        product_draft_id=draft_id,
        role="detail",
        slot_id="detail_1",
    )
    service = DimensionCanvasService(
        canvas_repository,
        product_repository,
        assets,
        _Renderer(),
        media_assets=media_assets,
    )
    try:
        imported = service.import_preview_item(task_id, task_item_id, workspace_id="local")
        detail = next(asset for asset in imported["assets"] if asset["role"] == "detail")
        assert detail["availability"] == "unavailable"

        assert media_assets.materialize_pending(workspace_id="local") == {"claimed": 1, "ready": 1, "retryable": 0, "failed": 0}

        refreshed = service.get_item(imported["id"], workspace_id="local")
        detail = next(asset for asset in refreshed["assets"] if asset["role"] == "detail")
        assert detail["availability"] == "local"
        assert detail["preview_url"] == (
            f"/api/product-processing/dimension-canvas/assets/{detail['id']}/image?workspace_id=local"
        )
        assert service.dimension_asset_image_path(detail["id"], workspace_id="local").read_bytes() == remote_bytes
    finally:
        service.close()
        database.dispose()


class _JpegRenderer:
    def inspect_source(self, content: bytes) -> DimensionSourceInfo:
        return DimensionSourceInfo(64, 64, "image/jpeg", ".jpg")

    def render(self, request) -> _Output:
        content = _v2_jpeg("purple")
        return _Output(b"master", content, hashlib.sha256(content).hexdigest(), width=64, height=64)


def test_v2_acceptance_creates_dimension_binding(tmp_path: Path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'v2-accept.sqlite3').as_posix()}")
    assets = ProductProcessingAssets(tmp_path / "assets")
    product_repository = ProductProcessingRepository(database)
    canvas_repository = DimensionCanvasRepository(database)
    media_assets = MediaAssetService(MediaAssetRepository(database), assets)

    ready_main = media_assets.register_local_asset("local", "preview_upload", _v2_jpeg("green"), "image/jpeg")

    with database.sessions.begin() as session:
        draft = ProductDraftRow(workspace_id="local", media_contract_version=2, skc="v2-skc", status="draft")
        session.add(draft)
        session.flush()
        task = ProcessingTaskRow(workspace_id="local", title="finished", status="completed", total_count=1, success_count=1)
        session.add(task)
        session.flush()
        draft_id = int(draft.id)
        item = ProcessingTaskItemRow(
            task_id=task.id, product_draft_id=draft_id, skc="v2-skc", status="completed",
            # V2 results can retain these legacy display URLs for compatibility.
            # They must never be treated as managed filesystem paths when a
            # dimension-canvas acceptance builds the preview manifest.
            result_json=json.dumps({
                "physical_dimensions": _dimensions(),
                "image_manifest_v2": {"main_asset_id": ready_main["id"]},
                "image_manifest": [{
                    "slot_id": "carousel.hero",
                    "role": "hero",
                    "value": "/api/product-processing/preview/assets/old-preview/content?workspace_id=local",
                }],
            }),
        )
        session.add(item)
        session.flush()
        task_id = int(task.id)
        task_item_id = int(item.id)

    media_assets.bind_asset(workspace_id="local", asset_id=ready_main["id"], product_draft_id=draft_id, role="main")

    service = DimensionCanvasService(
        canvas_repository, product_repository, assets, _JpegRenderer(),
        media_assets=media_assets,
    )
    try:
        imported = service.import_preview_item(task_id, task_item_id, workspace_id="local")
        selected = imported["assets"][0]
        saved = service.save_item(
            imported["id"], imported["item_revision"],
            {"selected_source_asset_id": selected["id"], "physical_dimensions": _dimensions(), "annotations": [_annotation()]},
            workspace_id="local",
        )
        service.complete_item(imported["id"], saved["item_revision"], workspace_id="local")
        completed = service.wait_for_test_render(imported["id"], workspace_id="local")
        assert completed["state"] == "completed"
        change_set = service.submit_review(completed["batch_id"], workspace_id="local")
        accepted = service.accept_change_set(change_set["id"], workspace_id="local")
    finally:
        service.close()

    dimensions = media_assets.list_bindings("local", product_draft_id=draft_id)
    dimension_bindings = [b for b in dimensions if b["role"] == "dimension" and b["active"] == 1]
    assert len(dimension_bindings) == 1
    assert dimension_bindings[0]["slot_id"] == "carousel.dimension_background"
    asset = media_assets.get_asset(dimension_bindings[0]["asset_id"], "local")
    assert asset["origin"] == "dimension_rendered"
    with database.sessions() as session:
        preview_assets = session.scalars(
            select(PreviewImageAssetRow).where(
                PreviewImageAssetRow.product_draft_id == draft_id,
                PreviewImageAssetRow.workspace_id == "local",
            )
        ).all()
    assert all(not row.managed_path.startswith("/api/") for row in preview_assets)


def test_cross_workspace_forged_ids_return_404_and_schema_rejects_workspace(service_fixture) -> None:
    database, _product, _repository, service, _calls, _publisher_calls = service_fixture
    other = _seed(database, "other")
    item = service.import_preview_item(other["task_id"], other["task_item_id"], workspace_id="other")
    app = FastAPI()
    app.include_router(create_dimension_canvas_router(service))
    client = TestClient(app)
    assert client.get(
        f"/dimension-canvas/items/{item['id']}", headers={"X-Workspace-ID": "local"}
    ).status_code == 404
    response = client.post(
        "/dimension-canvas/items/import-preview-item",
        json={"task_id": other["task_id"], "task_item_id": other["task_item_id"], "workspace_id": "other"},
        headers={"X-Workspace-ID": "local"},
    )
    assert response.status_code == 422


def test_dimension_asset_image_accepts_workspace_query_for_browser_images(service_fixture) -> None:
    database, _product, _repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database, "other")
    item = service.import_preview_item(
        seeded["task_id"], seeded["task_item_id"], workspace_id="other"
    )
    uploaded = service.upload_asset(
        item["id"],
        b"valid-image-content",
        "other.png",
        "image/png",
        workspace_id="other",
    )
    app = FastAPI()
    app.include_router(create_dimension_canvas_router(service))

    response = TestClient(app).get(
        f"/dimension-canvas/assets/{uploaded['asset_id']}/image?workspace_id=other"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"


def test_api_retry_accepts_empty_body_and_uses_server_revision(service_fixture) -> None:
    database, _product, _repository, service, _calls, _publisher_calls = service_fixture
    seeded = _seed(database)
    completed = _complete(service, seeded)
    app = FastAPI()
    app.include_router(create_dimension_canvas_router(service))
    response = TestClient(app).post(
        f"/dimension-canvas/items/{completed['id']}/retry-render",
        json={},
        headers={"X-Workspace-ID": "local"},
    )
    assert response.status_code == 200


def test_dimension_router_is_mounted_under_product_processing(tmp_path: Path) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'mounted-router.sqlite3').as_posix()}")
    service = ProductProcessingService(
        ProductProcessingRepository(database),
        ProductProcessingAssets(tmp_path),
    )
    app = FastAPI()
    app.include_router(create_product_processing_router(service))
    try:
        response = TestClient(app).get(
            "/product-processing/dimension-canvas/importable-tasks",
            headers={"X-Workspace-ID": "local"},
        )
        assert response.status_code == 200
        assert response.json() == []
    finally:
        getattr(service, "_dimension_canvas_service").close()
        database.dispose()
