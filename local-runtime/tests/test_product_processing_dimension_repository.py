from __future__ import annotations

import json
from pathlib import Path

import pytest

from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.dimension_canvas_repository import (
    DimensionCanvasRepository,
    StaleCanvasRevision,
)
from wh_local.modules.product_processing.infrastructure.orm import (
    ProcessingTaskItemRow,
    ProcessingTaskRow,
    ProductDraftRow,
)


def _seed(database, root: Path, workspace_id: str = "local", count: int = 5) -> dict:
    source = root / f"{workspace_id}.jpg"
    source.write_bytes(b"managed-source")
    with database.sessions.begin() as session:
        task = ProcessingTaskRow(
            workspace_id=workspace_id,
            title=f"task-{workspace_id}",
            status="completed",
            total_count=count,
            success_count=count,
        )
        session.add(task)
        session.flush()
        items = []
        for index in range(count):
            draft = ProductDraftRow(
                workspace_id=workspace_id,
                skc=f"{workspace_id}-{index}",
                image_path=str(source),
                status="draft",
            )
            session.add(draft)
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
                                "value": str(source),
                            }
                        ],
                    }
                ),
            )
            session.add(item)
            session.flush()
            items.append({"id": item.id, "draft_id": draft.id})
        return {"id": task.id, "items": items}


def _dimensions(value: float = 10) -> dict:
    return {
        "length": {"value_cm": value, "provenance": "manual_confirmed", "evidence_ref": "manual"},
        "width": {"value_cm": 8, "provenance": "manual_confirmed", "evidence_ref": "manual"},
        "height": {"value_cm": 4, "provenance": "manual_confirmed", "evidence_ref": "manual"},
        "conflict": False,
    }


def _annotation(value: float = 10) -> dict:
    return {
        "id": "line-1",
        "key": "length",
        "value_cm": value,
        "start": {"x": 0.15, "y": 0.8},
        "end": {"x": 0.85, "y": 0.8},
        "label": {"x": 0.5, "y": 0.7},
        "style": "auto",
    }


@pytest.fixture
def canvas(tmp_path: Path):
    database = create_database("sqlite:///:memory:")
    try:
        yield database, DimensionCanvasRepository(database), tmp_path
    finally:
        database.dispose()


def test_import_subset_keeps_task_identity_and_is_idempotent(canvas) -> None:
    database, repository, root = canvas
    task = _seed(database, root)
    requested = [task["items"][1]["id"], task["items"][4]["id"]]
    first = repository.import_task_items(task["id"], requested, "local")
    second = repository.import_task_items(task["id"], [requested[1]], "local")
    assert [item["task_item_id"] for item in first["items"]] == requested
    assert second["items"][0]["id"] == first["items"][1]["id"]


def test_stale_autosave_cannot_overwrite_newer_revision(canvas) -> None:
    database, repository, root = canvas
    task = _seed(database, root, count=1)
    item = repository.import_task_items(task["id"], [task["items"][0]["id"]], "local")["items"][0]
    saved = repository.save_item(item["id"], 0, {"annotations": [_annotation()]}, "local")
    assert saved["item_revision"] == 1
    with pytest.raises(StaleCanvasRevision):
        repository.save_item(item["id"], 0, {"annotations": []}, "local")


def test_cross_workspace_ids_are_invisible_and_cannot_mutate(canvas) -> None:
    database, repository, root = canvas
    local_task = _seed(database, root, "local", 1)
    other_task = _seed(database, root, "other", 1)
    local_item = repository.import_task_items(
        local_task["id"], [local_task["items"][0]["id"]], "local"
    )["items"][0]
    other_item = repository.import_task_items(
        other_task["id"], [other_task["items"][0]["id"]], "other"
    )["items"][0]
    assert repository.get_item(other_item["id"], "local") is None
    assert repository.list_assets(other_item["id"], "local") == []
    with pytest.raises(LookupError):
        repository.save_item(other_item["id"], 0, {"annotations": []}, "local")
    unchanged = repository.get_item(local_item["id"], "local")
    assert unchanged is not None and unchanged["item_revision"] == 0


def test_selected_asset_must_belong_to_same_canvas_item(canvas) -> None:
    database, repository, root = canvas
    task = _seed(database, root, count=2)
    batch = repository.import_task_items(task["id"], [value["id"] for value in task["items"]], "local")
    first, second = batch["items"]
    foreign_asset = repository.list_assets(second["id"], "local")[0]
    with pytest.raises(LookupError):
        repository.save_item(
            first["id"],
            0,
            {"selected_source_asset_id": foreign_asset["id"]},
            "local",
        )


def test_edit_after_render_start_invalidates_old_worker_and_completed_asset(canvas) -> None:
    database, repository, root = canvas
    task = _seed(database, root, count=1)
    item = repository.import_task_items(task["id"], [task["items"][0]["id"]], "local")["items"][0]
    asset = repository.list_assets(item["id"], "local")[0]
    saved = repository.save_item(
        item["id"],
        0,
        {
            "selected_source_asset_id": asset["id"],
            "physical_dimensions": _dimensions(),
            "annotations": [_annotation()],
        },
        "local",
    )
    rendering = repository.mark_rendering(item["id"], saved["item_revision"], "local")
    edited = repository.save_item(
        item["id"],
        rendering["item_revision"],
        {"physical_dimensions": _dimensions(12), "annotations": [_annotation(12)]},
        "local",
    )
    stale = repository.finish_render(
        item["id"],
        rendering["render_revision"],
        {"managed_path": str(root / "old.jpg"), "content_hash": "old"},
        "local",
    )
    assert stale is None
    assert edited["state"] == "editing"
    assert edited["render_asset_id"] == ""
    assert edited["rendered_input_hash"] == ""


def test_reimport_marks_edited_item_conflict_when_preview_revision_changed(canvas) -> None:
    database, repository, root = canvas
    task = _seed(database, root, count=1)
    item = repository.import_task_items(task["id"], [task["items"][0]["id"]], "local")["items"][0]
    repository.save_item(item["id"], 0, {"annotations": [_annotation()]}, "local")
    with database.sessions.begin() as session:
        draft = session.get(ProductDraftRow, task["items"][0]["draft_id"])
        assert draft is not None
        draft.preview_revision = 1
    again = repository.import_task_items(task["id"], [task["items"][0]["id"]], "local")["items"][0]
    assert again["state"] == "conflict"
    assert again["error_code"] == "source_preview_changed"
