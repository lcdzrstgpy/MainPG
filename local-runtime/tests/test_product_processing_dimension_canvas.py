from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.modules.product_processing.api.dimension_canvas_router import create_dimension_canvas_router
from wh_local.modules.product_processing.api.router import create_product_processing_router
from wh_local.modules.product_processing.dimension_canvas_service import DimensionCanvasService
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.dimension_canvas_repository import DimensionCanvasRepository
from wh_local.modules.product_processing.infrastructure.orm import (
    ProcessingTaskItemRow,
    ProcessingTaskRow,
    ProductDraftRow,
)
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService


@dataclass(frozen=True)
class _Output:
    master_png_bytes: bytes
    jpeg_bytes: bytes
    content_hash: str
    width: int = 2000
    height: int = 2000


class _Renderer:
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


def _annotation(value: float = 10) -> dict:
    return {
        "id": "a1",
        "key": "length",
        "value_cm": value,
        "start": {"x": 0.15, "y": 0.8},
        "end": {"x": 0.85, "y": 0.8},
        "label": {"x": 0.5, "y": 0.7},
        "style": "auto",
    }


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

    def loader(asset: dict) -> bytes:
        loader_calls.append(dict(asset))
        return b"deterministic-registered-image"

    service = DimensionCanvasService(
        canvas_repository,
        product_repository,
        ProductProcessingAssets(tmp_path),
        _Renderer(),
        loader,
    )
    try:
        yield database, product_repository, canvas_repository, service, loader_calls
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
    database, _product, repository, service, loader_calls = service_fixture
    seeded = _seed(database, remote=True)
    completed = _complete(service, seeded)
    assert completed["state"] == "completed"
    assert len(loader_calls) == 1
    selected = repository.get_asset(completed["selected_source_asset_id"], completed["id"], "local")
    assert selected is not None
    assert selected["content_hash"] == hashlib.sha256(b"deterministic-registered-image").hexdigest()
    assert selected["managed_path"]
    change_set = service.submit_review(completed["batch_id"], workspace_id="local")
    assert change_set["item_count"] == 1
    hydrated = service.get_item(completed["id"], workspace_id="local")
    rendered = next(asset for asset in hydrated["assets"] if asset["role"] == "rendered_dimension")
    assert rendered["preview_url"].startswith("/pp-media/")
    assert change_set["items"][0]["new_image_url"].startswith("/pp-media/")


def test_redraw_uses_new_render_revision_and_new_change_set_key(service_fixture) -> None:
    database, _product, _repository, service, _calls = service_fixture
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


def test_accept_conflict_does_not_silently_overwrite_target_slot(service_fixture) -> None:
    database, product, _repository, service, _calls = service_fixture
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
    database, product, _repository, service, _calls = service_fixture
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
    assert set(overrides["image_slot_overrides"]) == {"carousel.dimension_background"}


def test_cross_workspace_forged_ids_return_404_and_schema_rejects_workspace(service_fixture) -> None:
    database, _product, _repository, service, _calls = service_fixture
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


def test_api_retry_accepts_empty_body_and_uses_server_revision(service_fixture) -> None:
    database, _product, _repository, service, _calls = service_fixture
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
