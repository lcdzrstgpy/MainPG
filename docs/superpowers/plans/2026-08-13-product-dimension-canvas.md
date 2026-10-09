# Product Dimension Canvas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic product-dimension canvas that supports single-item and batch workflows, renders accurate 2000×2000 dimension images, and applies reviewed slot-level image changes without slowing the product-processing pipeline.

**Architecture:** Keep shipping dimensions and product-body dimensions separate. Add a bounded backend dimension-canvas service with persistent SQLite batches/items/assets/change sets, semantic image slots, versioned autosave, Pillow rendering, and reviewed slot patches. Add a focused React workspace with SVG editing, lazy asset loading, batch queue, persistent notifications, and theme-token styling.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy 2, SQLite, Pillow, React 18, TypeScript 5.6, Vite 5, SVG, Node 22 built-in test runner, pytest.

---

## Source Design

Implement against:

- `docs/superpowers/specs/2026-08-13-product-dimension-canvas-design.md`

Do not add a provider call, deploy, package, publish to a marketplace, or import into Dianxiaomi while executing this plan. COS publication through the existing configured media path is allowed only in an explicitly authorized acceptance run; local automated tests must inject a deterministic publisher.

## File Map

### Backend files to create

- `local-runtime/wh_local/modules/product_processing/domain/physical_dimensions.py` — verified product-body dimension parsing and provenance.
- `local-runtime/wh_local/modules/product_processing/domain/image_slots.py` — semantic carousel slots and patch application.
- `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_orm.py` — canvas batch, item, asset, change-set, change-item, and notification rows.
- `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_repository.py` — workspace-scoped persistence and optimistic concurrency.
- `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_renderer.py` — deterministic Pillow renderer.
- `local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py` — import, autosave, render, submit-review, accept, conflict, and recovery orchestration.
- `local-runtime/wh_local/modules/product_processing/api/dimension_canvas_schemas.py` — request/response contracts.
- `local-runtime/wh_local/modules/product_processing/api/dimension_canvas_router.py` — `/dimension-canvas` routes.

### Backend files to modify

- `local-runtime/wh_local/modules/product_processing/domain/prompts.py` — make panel 4 annotation-ready and forbid AI dimension marks.
- `local-runtime/wh_local/modules/product_processing/infrastructure/orm.py` — add `preview_revision` to product drafts.
- `local-runtime/wh_local/modules/product_processing/infrastructure/database.py` — load new metadata and migrate `preview_revision`.
- `local-runtime/wh_local/modules/product_processing/infrastructure/assets.py` — content-addressed dimension source/master/JPEG paths.
- `local-runtime/wh_local/modules/product_processing/infrastructure/repository.py` — versioned preview save.
- `local-runtime/wh_local/modules/product_processing/domain/workbooks.py` — apply semantic slot patches without losing the summary image.
- `local-runtime/wh_local/modules/product_processing/service.py` — emit physical dimensions/image manifest and expose a media publication adapter.
- `local-runtime/wh_local/modules/product_processing/api/router.py` — construct and include the dimension-canvas router.
- `local-runtime/requirements.txt` — require Pillow.
- `local-runtime/wh_local/modules/product_processing/requirements.txt` — require Pillow for module packaging.
- `local-runtime/wh_local/modules/product_processing/README.md` — document the optional post-processing workflow.

### Frontend files to create

- `web-frontend/src/modules/product_processing/types/dimensionCanvas.ts` — canvas API and editor types.
- `web-frontend/src/modules/product_processing/api/dimensionCanvasApi.ts` — typed API functions.
- `web-frontend/src/modules/product_processing/data/dimensionCanvasModel.ts` — pure editor/queue/annotation reducers.
- `web-frontend/src/modules/product_processing/data/dimensionCanvasModel.test.ts` — Node tests for semantic editor logic.
- `web-frontend/src/modules/product_processing/hooks/useDimensionCanvasAutosave.ts` — debounced versioned autosave.
- `web-frontend/src/modules/product_processing/components/DimensionCanvasStage.tsx` — image and SVG annotation surface.
- `web-frontend/src/modules/product_processing/components/DimensionCanvasToolbar.tsx` — editor tools.
- `web-frontend/src/modules/product_processing/components/DimensionCanvasQueue.tsx` — right-side status queue and scoped wheel navigation.
- `web-frontend/src/modules/product_processing/components/DimensionCanvasImportDialog.tsx` — completed-task import and eligibility groups.
- `web-frontend/src/modules/product_processing/components/DimensionChangeSetReview.tsx` — before/after review and conflict actions.
- `web-frontend/src/modules/product_processing/pages/DimensionCanvasPage.tsx` — workspace composition.
- `web-frontend/src/modules/product_processing/styles/dimension-canvas.css` — theme-token layout and responsive behavior.

### Frontend files to modify

- `web-frontend/src/app/navigation/modules.ts` — add the visible `dimension_canvas` module.
- `web-frontend/src/app/layout/TopNavigation.tsx` — carry canvas batch/item tab context.
- `web-frontend/src/app/layout/WorkspaceShell.tsx` — open canvas tabs, poll notifications, and keep active work mounted.
- `web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx` — single-item entry and returned change-set review.
- `web-frontend/src/modules/product_processing/pages/ProductProcessingTaskPage.tsx` — relabel existing shipping-dimension scope.
- `web-frontend/src/modules/product_processing/types/index.ts` — preview revision, image-slot, and physical-dimension response fields.

### Tests to create or extend

- `local-runtime/tests/test_product_processing_physical_dimensions.py`
- `local-runtime/tests/test_product_processing_dimension_schema.py`
- `local-runtime/tests/test_product_processing_dimension_repository.py`
- `local-runtime/tests/test_product_processing_dimension_renderer.py`
- `local-runtime/tests/test_product_processing_dimension_canvas.py`
- `local-runtime/tests/test_product_processing_preview_overrides.py`

## Task 1: Separate Product-Body Dimensions and Remove AI Dimension Marks

**Files:**

- Create: `local-runtime/wh_local/modules/product_processing/domain/physical_dimensions.py`
- Create: `local-runtime/tests/test_product_processing_physical_dimensions.py`
- Modify: `local-runtime/wh_local/modules/product_processing/domain/prompts.py:160-284`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:1640-1750`

- [ ] **Step 1: Write failing physical-dimension provenance tests**

```python
from wh_local.modules.product_processing.domain.physical_dimensions import extract_physical_dimensions


def test_extracts_explicit_product_size_only() -> None:
    result = extract_physical_dimensions({"source_attributes": {"产品尺寸": "30×20×10cm"}})
    assert result.model_dump(mode="json") == {
        "length": {"value_cm": 30.0, "provenance": "source_confirmed", "evidence_ref": "source_attributes.产品尺寸"},
        "width": {"value_cm": 20.0, "provenance": "source_confirmed", "evidence_ref": "source_attributes.产品尺寸"},
        "height": {"value_cm": 10.0, "provenance": "source_confirmed", "evidence_ref": "source_attributes.产品尺寸"},
        "conflict": False,
    }


def test_rejects_package_dimensions_for_canvas() -> None:
    result = extract_physical_dimensions({"source_attributes": {"包装尺寸": "40×30×20cm"}})
    assert result.length.provenance == "package_estimate"
    assert result.width.provenance == "package_estimate"
    assert result.height.provenance == "package_estimate"


def test_conflicting_product_sizes_remain_unconfirmed() -> None:
    result = extract_physical_dimensions(
        {"source_attributes": {"产品尺寸": "30×20×10cm", "成品尺寸": "31×20×10cm"}}
    )
    assert result.conflict is True
    assert result.length.provenance == "unconfirmed"
```

- [ ] **Step 2: Run the new tests and verify import failure**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_physical_dimensions.py -q
```

Expected: collection fails because `physical_dimensions` does not exist.

- [ ] **Step 3: Implement strict product-dimension parsing**

Create these public contracts and function:

```python
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel

DimensionProvenance = Literal["source_confirmed", "manual_confirmed", "unconfirmed", "package_estimate"]
_TRIPLE = re.compile(r"(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|毫米|厘米)?", re.I)
_PRODUCT_KEYS = ("产品尺寸", "商品尺寸", "成品尺寸", "product size", "item size")
_PACKAGE_KEYS = ("包装", "包裹", "外箱", "package", "carton", "shipping")


class DimensionValue(BaseModel):
    value_cm: float | None = None
    provenance: DimensionProvenance = "unconfirmed"
    evidence_ref: str = ""


class PhysicalDimensions(BaseModel):
    length: DimensionValue = DimensionValue()
    width: DimensionValue = DimensionValue()
    height: DimensionValue = DimensionValue()
    conflict: bool = False

    @property
    def drawable(self) -> bool:
        allowed = {"source_confirmed", "manual_confirmed"}
        return not self.conflict and all(
            item.value_cm is not None and item.value_cm > 0 and item.provenance in allowed
            for item in (self.length, self.width, self.height)
        )


def extract_physical_dimensions(raw: dict[str, Any]) -> PhysicalDimensions:
    attributes = raw.get("source_attributes") or {}
    if not isinstance(attributes, dict):
        return PhysicalDimensions()
    candidates: list[tuple[tuple[float, float, float], str, DimensionProvenance]] = []
    for key, value in attributes.items():
        key_text = str(key).strip().casefold()
        match = _TRIPLE.search(str(value or ""))
        if not match:
            continue
        unit = (match.group(4) or "cm").casefold()
        scale = 0.1 if unit in {"mm", "毫米"} else 1.0
        triple = tuple(float(match.group(index)) * scale for index in (1, 2, 3))
        reference = f"source_attributes.{key}"
        if any(marker in key_text for marker in _PACKAGE_KEYS):
            candidates.append((triple, reference, "package_estimate"))
        elif any(marker in key_text for marker in _PRODUCT_KEYS):
            candidates.append((triple, reference, "source_confirmed"))
    product = [candidate for candidate in candidates if candidate[2] == "source_confirmed"]
    if len({candidate[0] for candidate in product}) > 1:
        return PhysicalDimensions(conflict=True)
    chosen = product[0] if product else next(iter(candidates), None)
    if chosen is None:
        return PhysicalDimensions()
    values, reference, provenance = chosen
    return PhysicalDimensions(
        length=DimensionValue(value_cm=values[0], provenance=provenance, evidence_ref=reference),
        width=DimensionValue(value_cm=values[1], provenance=provenance, evidence_ref=reference),
        height=DimensionValue(value_cm=values[2], provenance=provenance, evidence_ref=reference),
    )
```

Do not scan title text or generic dimension triples for product-body truth.

- [ ] **Step 4: Add physical dimensions and semantic image roles to processing results**

Import `extract_physical_dimensions`, compute it beside `product_dimensions`, and add:

```python
physical_dimensions = extract_physical_dimensions(raw).model_dump(mode="json")

image_manifest = [
    {"slot_id": "carousel.hero", "role": "hero", "value": grid_image_paths[0]} if len(grid_image_paths) > 0 else None,
    {"slot_id": "carousel.detail", "role": "detail", "value": grid_image_paths[1]} if len(grid_image_paths) > 1 else None,
    {"slot_id": "carousel.lifestyle", "role": "lifestyle", "value": grid_image_paths[2]} if len(grid_image_paths) > 2 else None,
    {"slot_id": "carousel.dimension_background", "role": "dimension_background", "value": grid_image_paths[3]} if len(grid_image_paths) > 3 else None,
]
image_manifest = [entry for entry in image_manifest if entry is not None]
```

Persist `physical_dimensions` and `image_manifest` in the result. Keep existing `product_dimensions` unchanged for shipping export compatibility.

- [ ] **Step 5: Replace panel-4 prompt rules**

Replace the panel-4 dimension-generation instructions with this contract in both image templates:

```text
Panel 4 - Dimension Annotation Background (bottom-right):
- Create a clean front, side, or top view that is suitable for later deterministic dimension annotation.
- Keep the complete product sharp and leave 12%-18% clear space around it.
- Never render measurements, numbers, units, dimension lines, arrows, rulers, scales, labels, or size claims.
- If no useful orthographic view is possible, create a clean alternate product angle with the same empty safe area.
```

Add `numbers, measurement units, rulers, size labels, dimension arrows` to the forbidden list for panel 4 only. Do not send physical dimensions to the image provider.

- [ ] **Step 6: Run focused tests and commit**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_physical_dimensions.py local-runtime/tests/test_product_processing_preview_overrides.py -q
git diff --check
```

Expected: all tests pass and no whitespace errors.

Commit:

```powershell
git add local-runtime/wh_local/modules/product_processing/domain/physical_dimensions.py local-runtime/wh_local/modules/product_processing/domain/prompts.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/test_product_processing_physical_dimensions.py
git commit -m "feat(product-processing): separate physical dimensions"
```

## Task 2: Add Preview Revision and Canvas Persistence Schema

**Files:**

- Create: `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_orm.py`
- Create: `local-runtime/tests/test_product_processing_dimension_schema.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/orm.py:15-46`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/database.py:8-62`

- [ ] **Step 1: Write schema migration tests**

```python
from sqlalchemy import inspect

from wh_local.modules.product_processing.infrastructure.database import create_database


def test_dimension_canvas_schema_and_preview_revision_exist() -> None:
    database = create_database("sqlite:///:memory:")
    inspector = inspect(database.engine)
    assert {
        "product_processing_dimension_batches",
        "product_processing_dimension_items",
        "product_processing_dimension_assets",
        "product_processing_dimension_change_sets",
        "product_processing_dimension_change_items",
        "product_processing_dimension_notifications",
    }.issubset(set(inspector.get_table_names()))
    draft_columns = {column["name"] for column in inspector.get_columns("product_processing_drafts")}
    assert "preview_revision" in draft_columns
```

- [ ] **Step 2: Verify the schema test fails**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_dimension_schema.py -q
```

Expected: assertions fail for missing tables and `preview_revision`.

- [ ] **Step 3: Add `preview_revision` and focused canvas ORM rows**

Add this field to `ProductDraftRow`:

```python
preview_revision: Mapped[int] = mapped_column(Integer, default=0)
```

Create six ORM classes in `dimension_canvas_orm.py`. Use these table names and constraints exactly:

```python
class DimensionCanvasBatchRow(Base):
    __tablename__ = "product_processing_dimension_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    source_task_id: Mapped[int] = mapped_column(ForeignKey("product_processing_tasks.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class DimensionCanvasItemRow(Base):
    __tablename__ = "product_processing_dimension_items"
    __table_args__ = (
        UniqueConstraint("workspace_id", "task_id", "task_item_id", "product_draft_id", name="uq_dimension_item_identity"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("product_processing_dimension_batches.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    task_item_id: Mapped[int] = mapped_column(Integer, index=True)
    product_draft_id: Mapped[int] = mapped_column(Integer, index=True)
    skc: Mapped[str] = mapped_column(String(255), default="")
    source_preview_revision: Mapped[int] = mapped_column(Integer, default=0)
    selected_source_asset_id: Mapped[str] = mapped_column(String(36), default="")
    target_slot_id: Mapped[str] = mapped_column(String(128), default="carousel.dimension_background")
    physical_dimensions_json: Mapped[str] = mapped_column(Text, default="{}")
    annotations_json: Mapped[str] = mapped_column(Text, default="[]")
    canvas_settings_json: Mapped[str] = mapped_column(Text, default="{}")
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    item_revision: Mapped[int] = mapped_column(Integer, default=0)
    render_revision: Mapped[int] = mapped_column(Integer, default=0)
    render_asset_id: Mapped[str] = mapped_column(String(36), default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class DimensionCanvasAssetRow(Base):
    __tablename__ = "product_processing_dimension_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("product_processing_dimension_items.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    managed_path: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(128), default="")
    availability: Mapped[str] = mapped_column(String(32), default="metadata", index=True)
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)


class DimensionCanvasChangeSetRow(Base):
    __tablename__ = "product_processing_dimension_change_sets"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_dimension_change_set_idempotency"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("product_processing_dimension_batches.id", ondelete="CASCADE"), index=True)
    source_task_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_review", index=True)
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    accepted_at: Mapped[str] = mapped_column(String(64), default="")


class DimensionCanvasChangeItemRow(Base):
    __tablename__ = "product_processing_dimension_change_items"
    __table_args__ = (
        UniqueConstraint("change_set_id", "dimension_item_id", name="uq_dimension_change_item"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    change_set_id: Mapped[str] = mapped_column(ForeignKey("product_processing_dimension_change_sets.id", ondelete="CASCADE"), index=True)
    dimension_item_id: Mapped[str] = mapped_column(ForeignKey("product_processing_dimension_items.id", ondelete="CASCADE"), index=True)
    product_draft_id: Mapped[int] = mapped_column(Integer, index=True)
    base_preview_revision: Mapped[int] = mapped_column(Integer)
    target_slot_id: Mapped[str] = mapped_column(String(128))
    base_asset_json: Mapped[str] = mapped_column(Text, default="{}")
    replacement_asset_json: Mapped[str] = mapped_column(Text, default="{}")
    physical_dimensions_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    conflict_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    resolved_at: Mapped[str] = mapped_column(String(64), default="")


class DimensionCanvasNotificationRow(Base):
    __tablename__ = "product_processing_dimension_notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    change_set_id: Mapped[str] = mapped_column(ForeignKey("product_processing_dimension_change_sets.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(64), default="dimension_change_set")
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    read_at: Mapped[str] = mapped_column(String(64), default="")
```

- [ ] **Step 4: Register metadata and migration fallback**

In `database.py`, import `dimension_canvas_orm` after importing `Base` so SQLAlchemy sees the tables:

```python
from . import dimension_canvas_orm as _dimension_canvas_orm  # noqa: F401
```

Add this migration entry:

```python
"product_processing_drafts": [
    ("preview_overrides_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("preview_revision", "INTEGER NOT NULL DEFAULT 0"),
],
```

- [ ] **Step 5: Run schema tests and commit**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_dimension_schema.py -q
```

Expected: pass.

Commit:

```powershell
git add local-runtime/wh_local/modules/product_processing/infrastructure/orm.py local-runtime/wh_local/modules/product_processing/infrastructure/database.py local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_orm.py local-runtime/tests/test_product_processing_dimension_schema.py
git commit -m "feat(product-processing): add dimension canvas schema"
```

## Task 3: Implement Stable Batch Import and Optimistic Autosave Persistence

**Files:**

- Create: `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_repository.py`
- Create: `local-runtime/tests/test_product_processing_dimension_repository.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/repository.py:169-185`

- [ ] **Step 1: Write repository identity, subset, and stale-save tests**

```python
import pytest

from wh_local.modules.product_processing.infrastructure.dimension_canvas_repository import (
    DimensionCanvasRepository,
    StaleCanvasRevision,
)


def test_import_subset_keeps_task_identity_and_is_idempotent(seed_finished_task) -> None:
    repository, task = seed_finished_task(item_count=5)
    canvas = DimensionCanvasRepository(repository.database)
    first = canvas.import_task_items(task["id"], [task["items"][1]["id"], task["items"][4]["id"]], "local")
    second = canvas.import_task_items(task["id"], [task["items"][4]["id"]], "local")
    assert [item["task_item_id"] for item in first["items"]] == [task["items"][1]["id"], task["items"][4]["id"]]
    assert second["items"][0]["id"] == first["items"][1]["id"]


def test_stale_autosave_cannot_overwrite_newer_revision(seed_dimension_item) -> None:
    canvas, item = seed_dimension_item()
    saved = canvas.save_item(item["id"], expected_revision=0, patch={"annotations": [{"id": "a1"}]}, workspace_id="local")
    assert saved["item_revision"] == 1
    with pytest.raises(StaleCanvasRevision):
        canvas.save_item(item["id"], expected_revision=0, patch={"annotations": []}, workspace_id="local")
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_dimension_repository.py -q
```

Expected: import failure for the missing repository.

- [ ] **Step 3: Implement workspace-scoped repository operations**

Implement committed transactions for import and optimistic save first:

```python
class StaleCanvasRevision(RuntimeError):
    pass


class DimensionCanvasRepository:
    def __init__(self, database: ProductProcessingDatabase):
        self.database = database

    def import_task_items(self, task_id: int, task_item_ids: list[int], workspace_id: str) -> dict[str, Any]:
        requested = list(dict.fromkeys(task_item_ids))
        if not requested:
            raise ValueError("at least one task item is required")
        with self.database.sessions.begin() as session:
            task = session.get(ProcessingTaskRow, task_id)
            if task is None or task.workspace_id != workspace_id:
                raise LookupError("product processing task not found")
            if task.status not in {"completed", "failed", "partial_failure"}:
                raise ValueError("product processing task is not finished")
            rows = session.scalars(
                select(ProcessingTaskItemRow).where(
                    ProcessingTaskItemRow.task_id == task_id,
                    ProcessingTaskItemRow.id.in_(requested),
                    ProcessingTaskItemRow.product_draft_id.is_not(None),
                    ProcessingTaskItemRow.status == "completed",
                )
            ).all()
            by_id = {row.id: row for row in rows}
            missing = [item_id for item_id in requested if item_id not in by_id]
            if missing:
                raise LookupError(f"task items not found: {missing}")
            existing_rows = session.scalars(
                select(DimensionCanvasItemRow).where(
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.task_id == task_id,
                    DimensionCanvasItemRow.task_item_id.in_(requested),
                )
            ).all()
            existing = {row.task_item_id: row for row in existing_rows}
            batch_id = str(uuid5(NAMESPACE_URL, f"mainpg:{workspace_id}:dimension:{task_id}"))
            batch = session.get(DimensionCanvasBatchRow, batch_id)
            if batch is None:
                batch = DimensionCanvasBatchRow(id=batch_id, workspace_id=workspace_id, source_task_id=task_id)
                session.add(batch)
            output: list[DimensionCanvasItemRow] = []
            for task_item_id in requested:
                row = existing.get(task_item_id)
                if row is None:
                    task_item = by_id[task_item_id]
                    draft = session.get(ProductDraftRow, task_item.product_draft_id)
                    row = DimensionCanvasItemRow(
                        id=str(uuid4()), batch_id=batch_id, workspace_id=workspace_id,
                        task_id=task_id, task_item_id=task_item.id,
                        product_draft_id=int(task_item.product_draft_id), skc=task_item.skc,
                        source_preview_revision=int(draft.preview_revision if draft else 0),
                    )
                    session.add(row)
                output.append(row)
            session.flush()
            return {"id": batch.id, "items": [self._item(row) for row in output]}

    def save_item(self, item_id: str, expected_revision: int, patch: dict[str, Any], workspace_id: str) -> dict[str, Any]:
        json_fields = {
            "physical_dimensions": "physical_dimensions_json",
            "annotations": "annotations_json",
            "canvas_settings": "canvas_settings_json",
        }
        scalar_fields = {"selected_source_asset_id", "target_slot_id", "state"}
        with self.database.sessions.begin() as session:
            row = session.get(DimensionCanvasItemRow, item_id)
            if row is None or row.workspace_id != workspace_id:
                raise LookupError("dimension canvas item not found")
            if row.item_revision != expected_revision:
                raise StaleCanvasRevision(f"expected revision {expected_revision}, current {row.item_revision}")
            for public_name, column_name in json_fields.items():
                if public_name in patch:
                    setattr(row, column_name, dumps(patch[public_name]))
            for name in scalar_fields:
                if name in patch:
                    setattr(row, name, str(patch[name] or ""))
            row.item_revision += 1
            row.updated_at = utc_now()
            session.flush()
            return self._item(row)

    @staticmethod
    def _item(row: DimensionCanvasItemRow) -> dict[str, Any]:
        return {
            "id": row.id, "batch_id": row.batch_id, "workspace_id": row.workspace_id,
            "task_id": row.task_id, "task_item_id": row.task_item_id,
            "product_draft_id": row.product_draft_id, "skc": row.skc,
            "source_preview_revision": row.source_preview_revision,
            "selected_source_asset_id": row.selected_source_asset_id,
            "target_slot_id": row.target_slot_id,
            "physical_dimensions": loads(row.physical_dimensions_json, {}),
            "annotations": loads(row.annotations_json, []),
            "canvas_settings": loads(row.canvas_settings_json, {}),
            "state": row.state, "item_revision": row.item_revision,
            "render_revision": row.render_revision, "render_asset_id": row.render_asset_id,
            "error_code": row.error_code, "error_message": row.error_message,
        }
```

`import_task_items` must query `ProcessingTaskItemRow` by both task ID and requested item IDs, preserve requested order, reject non-completed task states, and reuse an existing identity row. It must never match by SKC or list index.

`save_item` must update only allowed JSON fields, compare `item_revision`, increment once, and raise `StaleCanvasRevision` when `row.item_revision != expected_revision`.

Add `get_batch`, `list_batches`, and `get_item` as workspace-filtered reads returning the same `_item()` shape. Implement `mark_rendering`, `finish_render`, and `fail_render` with the same revision comparison; each transition increments `render_revision` once and accepts completion only when the worker's render revision equals the row's current value. Implement `recover_rendering_items` as one update from `rendering` to `render_retryable`, returning the affected row count.

- [ ] **Step 4: Make preview saves increment `preview_revision`**

Change `save_draft_preview_overrides` to accept `expected_revision: int | None = None`, compare when supplied, increment on every successful write, and return the new revision. Add `preview_revision` to `_draft()` output.

The comparison must happen inside the same transaction as the write.

- [ ] **Step 5: Run repository tests and commit**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_dimension_repository.py local-runtime/tests/test_product_processing_preview_overrides.py -q
```

Expected: pass.

Commit:

```powershell
git add local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_repository.py local-runtime/wh_local/modules/product_processing/infrastructure/repository.py local-runtime/tests/test_product_processing_dimension_repository.py local-runtime/tests/test_product_processing_preview_overrides.py
git commit -m "feat(product-processing): persist dimension canvas drafts"
```

## Task 4: Add Semantic Image Slots and Preserve Unchanged Carousel Images

**Files:**

- Create: `local-runtime/wh_local/modules/product_processing/domain/image_slots.py`
- Modify: `local-runtime/wh_local/modules/product_processing/domain/workbooks.py:182-302`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:1007-1230`
- Modify: `local-runtime/tests/test_product_processing_preview_overrides.py`

- [ ] **Step 1: Add failing tests for slot-only replacement and summary preservation**

```python
def test_dimension_slot_patch_preserves_other_carousel_and_summary(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task = _create_task_with_result(service)
    draft_id = task["items"][0]["product_draft_id"]
    service.save_task_preview(
        task["id"],
        [{
            "product_draft_id": draft_id,
            "overrides": {
                "image_slot_overrides": {
                    "carousel.dimension_background": {"url": "https://user.example.com/dimension.jpg"}
                }
            },
        }],
        workspace_id="local",
    )
    preview = service.task_preview(task["id"], workspace_id="local")
    assert preview["items"][0]["carousel_images"] == [
        "https://cos.example.com/c1.jpg",
        "https://cos.example.com/c2.jpg",
        "https://cos.example.com/c3.jpg",
        "https://user.example.com/dimension.jpg",
    ]
    row = _dxm_single_export_row({**_base_result(), "preview_overrides": preview["items"][0]["overrides"]}, None)
    assert row[18].splitlines() == [
        "https://cos.example.com/c1.jpg",
        "https://cos.example.com/c2.jpg",
        "https://cos.example.com/c3.jpg",
        "https://user.example.com/dimension.jpg",
        "https://cos.example.com/summary.jpg",
    ]
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_preview_overrides.py::test_dimension_slot_patch_preserves_other_carousel_and_summary -q
```

Expected: fail because `image_slot_overrides` is ignored.

- [ ] **Step 3: Implement slot normalization and patch application**

Create:

```python
from __future__ import annotations

from typing import Any

DEFAULT_SLOT_IDS = (
    "carousel.hero",
    "carousel.detail",
    "carousel.lifestyle",
    "carousel.dimension_background",
)


def base_image_slots(result: dict[str, Any]) -> list[dict[str, str]]:
    manifest = result.get("image_manifest")
    if isinstance(manifest, list) and manifest:
        return [dict(entry) for entry in manifest if isinstance(entry, dict) and entry.get("value")]
    values = [str(value) for value in result.get("carousel_image_paths") or [] if str(value or "").strip()]
    return [
        {"slot_id": DEFAULT_SLOT_IDS[index] if index < len(DEFAULT_SLOT_IDS) else f"carousel.extra.{index + 1}", "value": value}
        for index, value in enumerate(values)
    ]


def apply_slot_overrides(result: dict[str, Any], preview_overrides: dict[str, Any]) -> list[dict[str, str]]:
    legacy = preview_overrides.get("carousel_images")
    slots = base_image_slots({**result, "carousel_image_paths": legacy}) if isinstance(legacy, list) and legacy else base_image_slots(result)
    patches = preview_overrides.get("image_slot_overrides") or {}
    by_id = {str(slot["slot_id"]): dict(slot) for slot in slots}
    for slot_id, patch in patches.items():
        url = str((patch or {}).get("url") or "").strip()
        if not url:
            continue
        if slot_id in by_id:
            by_id[slot_id]["value"] = url
        elif slot_id == "carousel.dimension_background":
            insert_at = min(3, len(slots))
            slot = {"slot_id": slot_id, "value": url}
            slots.insert(insert_at, slot)
            by_id[slot_id] = slot
    return slots
```

- [ ] **Step 4: Use slot results in preview and workbook export**

In `_clean_preview_overrides`, accept only a dict of slot IDs with non-empty HTTP(S) URLs for `image_slot_overrides`.

In `_preview_item`, return:

```python
slots = apply_slot_overrides(result, saved)
carousel_sources = [slot["value"] for slot in slots]
```

Add `image_slots` and `preview_revision` to each preview item.

In `_dxm_single_export_row`, resolve slots first, then append `grid_image_summary_path` exactly once. Legacy full-array overrides remain readable, but no new dimension-canvas path writes them.

- [ ] **Step 5: Run preview/export tests and commit**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_preview_overrides.py -q
```

Expected: all tests pass.

Commit:

```powershell
git add local-runtime/wh_local/modules/product_processing/domain/image_slots.py local-runtime/wh_local/modules/product_processing/domain/workbooks.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/test_product_processing_preview_overrides.py
git commit -m "feat(product-processing): apply image slot patches"
```

## Task 5: Build the Deterministic 2000×2000 Renderer

**Files:**

- Create: `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_renderer.py`
- Create: `local-runtime/tests/test_product_processing_dimension_renderer.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/assets.py:19-75`
- Modify: `local-runtime/requirements.txt`
- Modify: `local-runtime/wh_local/modules/product_processing/requirements.txt`

- [ ] **Step 1: Add Pillow dependency and failing render test**

Add `Pillow>=10,<13` to both requirement files.

```python
from io import BytesIO

from PIL import Image

from wh_local.modules.product_processing.infrastructure.dimension_renderer import (
    DimensionAnnotation,
    DimensionRenderRequest,
    DimensionRenderer,
)


def test_renderer_outputs_crisp_2000_square() -> None:
    source = Image.new("RGB", (1000, 1000), "white")
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    request = DimensionRenderRequest(
        source_bytes=buffer.getvalue(),
        annotations=[DimensionAnnotation(key="length", value_cm=10, start=(0.15, 0.8), end=(0.85, 0.8), label=(0.5, 0.75))],
    )
    output = DimensionRenderer().render(request)
    rendered = Image.open(BytesIO(output.jpeg_bytes))
    assert rendered.size == (2000, 2000)
    assert output.content_hash
    assert len(output.master_png_bytes) > len(buffer.getvalue())
```

- [ ] **Step 2: Run the render test and verify import failure**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_dimension_renderer.py -q
```

Expected: collection fails because `dimension_renderer` does not exist.

- [ ] **Step 3: Implement renderer contracts and drawing**

Use Pydantic or frozen dataclasses with these fields:

```python
class DimensionAnnotation(BaseModel):
    key: Literal["length", "width", "height", "custom"]
    value_cm: float
    start: tuple[float, float]
    end: tuple[float, float]
    label: tuple[float, float]
    style: Literal["auto", "dark", "light"] = "auto"


class DimensionRenderRequest(BaseModel):
    source_bytes: bytes
    annotations: list[DimensionAnnotation]
    output_size: int = 2000
    fit: Literal["contain", "cover"] = "contain"
```

`render()` must:

1. Decode and transpose EXIF orientation.
2. Convert to RGB.
3. Create a 2000 square background.
4. Apply contain or cover layout.
5. Convert normalized points to output pixels.
6. Draw a line, two filled arrow heads, and a centered `10 cm` label with a contrasting stroke.
7. Reject values `<= 0`, out-of-range coordinates, empty annotations, invalid images, and labels outside a 5% safe margin.
8. Encode a PNG master and JPEG quality 95 output.
9. Return SHA-256, width, height, and both byte strings.

Use the exact Windows target font `C:/Windows/Fonts/segoeuib.ttf`, already used by the local detail renderer. Check it before render; if absent, fail with `dimension_font_missing`. Do not silently switch fonts, because text bounds must stay deterministic across accepted renders.

- [ ] **Step 4: Add content-addressed asset writes**

Add:

```python
def save_dimension_asset(self, content: bytes, *, kind: str, suffix: str) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    safe_kind = "master" if kind == "master" else "published"
    root = self.output_root / "dimension-canvas" / safe_kind / digest[:2]
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{digest}{suffix}"
    if not path.exists():
        path.write_bytes(content)
    return path
```

- [ ] **Step 5: Run renderer tests and commit**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_dimension_renderer.py -q
```

Expected: pass.

Commit:

```powershell
git add local-runtime/requirements.txt local-runtime/wh_local/modules/product_processing/requirements.txt local-runtime/wh_local/modules/product_processing/infrastructure/dimension_renderer.py local-runtime/wh_local/modules/product_processing/infrastructure/assets.py local-runtime/tests/test_product_processing_dimension_renderer.py
git commit -m "feat(product-processing): render dimension images locally"
```

## Task 6: Complete the Single-Item Backend API

**Files:**

- Create: `local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py`
- Create: `local-runtime/wh_local/modules/product_processing/api/dimension_canvas_schemas.py`
- Create: `local-runtime/wh_local/modules/product_processing/api/dimension_canvas_router.py`
- Create: `local-runtime/tests/test_product_processing_dimension_canvas.py`
- Modify: `local-runtime/wh_local/modules/product_processing/api/router.py:15-52,446-449`
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:159-180`

- [ ] **Step 1: Write a failing single-item lifecycle test**

```python
def test_single_item_import_autosave_render_and_submit(seed_finished_task, tmp_path) -> None:
    service, task = seed_finished_task(item_count=1, assets_root=tmp_path)
    item = service.import_preview_item(task["id"], task["items"][0]["id"], workspace_id="local")
    saved = service.save_item(
        item["id"],
        expected_revision=0,
        patch={
            "selected_source_asset_id": item["assets"][0]["id"],
            "physical_dimensions": manual_dimensions(10, 8, 4),
            "annotations": [length_annotation(10)],
        },
        workspace_id="local",
    )
    rendering = service.complete_item(item["id"], expected_revision=saved["item_revision"], workspace_id="local")
    finished = service.wait_for_test_render(rendering["id"])
    assert finished["state"] == "completed"
    change_set = service.submit_review(item["batch_id"], workspace_id="local")
    assert change_set["item_count"] == 1
```

- [ ] **Step 2: Run the lifecycle test and verify failure**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_dimension_canvas.py::test_single_item_import_autosave_render_and_submit -q
```

Expected: import failure for `DimensionCanvasService`.

- [ ] **Step 3: Implement service orchestration with bounded workers**

Constructor:

```python
class DimensionCanvasService:
    def __init__(
        self,
        canvas_repository: DimensionCanvasRepository,
        product_repository: ProductProcessingRepository,
        assets: ProductProcessingAssets,
        renderer: DimensionRenderer,
        source_loader: Callable[[dict[str, Any]], bytes],
        publisher: Callable[[bytes, int, int, int], dict[str, Any]],
        *,
        max_workers: int = 3,
    ):
        self.canvas_repository = canvas_repository
        self.product_repository = product_repository
        self.assets = assets
        self.renderer = renderer
        self.source_loader = source_loader
        self.publisher = publisher
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="dimension-canvas")
        self.canvas_repository.recover_rendering_items()
```

Implement the public flow with these concrete transition calls:

```python
def import_preview_item(self, task_id: int, task_item_id: int, *, workspace_id: str) -> dict[str, Any]:
    batch = self.canvas_repository.import_task_items(task_id, [task_item_id], workspace_id)
    return self._hydrate_item(batch["items"][0], workspace_id)


def get_item(self, item_id: str, *, workspace_id: str) -> dict[str, Any]:
    item = self.canvas_repository.get_item(item_id, workspace_id)
    if item is None:
        raise ProductProcessingNotFound("dimension canvas item not found")
    return self._hydrate_item(item, workspace_id)


def save_item(self, item_id: str, expected_revision: int, patch: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
    cleaned = self._validate_save_patch(patch)
    saved = self.canvas_repository.save_item(item_id, expected_revision, cleaned, workspace_id)
    return self._hydrate_item(saved, workspace_id)


def complete_item(self, item_id: str, expected_revision: int, *, workspace_id: str) -> dict[str, Any]:
    item = self.get_item(item_id, workspace_id=workspace_id)
    self._validate_complete(item)
    rendering = self.canvas_repository.mark_rendering(item_id, expected_revision, workspace_id)
    self.executor.submit(self._render_item, item_id, rendering["render_revision"], workspace_id)
    return rendering


def submit_review(self, batch_id: str, *, workspace_id: str) -> dict[str, Any]:
    batch = self.canvas_repository.get_batch(batch_id, workspace_id)
    if batch is None:
        raise ProductProcessingNotFound("dimension canvas batch not found")
    completed_ids = [item["id"] for item in batch["items"] if item["state"] == "completed"]
    if not completed_ids:
        raise ValueError("batch has no completed dimension items")
    key_source = f"{batch_id}:" + ":".join(sorted(completed_ids))
    key = hashlib.sha256(key_source.encode("utf-8")).hexdigest()
    return self.canvas_repository.create_change_set(batch_id, completed_ids, key, workspace_id)


def close(self) -> None:
    self.executor.shutdown(wait=False, cancel_futures=False)
```

Before `complete_item`, validate a selected asset, all referenced dimensions as `source_confirmed` or `manual_confirmed`, at least one annotation, normalized points, and a target slot. Rendering must mark state before executor submission. The worker must persist local output before publication and must never call a model.

`_render_item` must reload the item and selected asset, call `source_loader`, build `DimensionRenderRequest`, save master/output assets, call `publisher`, create the rendered asset row, and finish the exact render revision. Catch `ValueError`, `OSError`, and Pillow decode errors, normalize the message, and call `fail_render` for that render revision. `_hydrate_item` must attach task-derived asset metadata without downloading bytes. `_validate_save_patch` must parse `PhysicalDimensions` and `DimensionAnnotation` before persistence.

- [ ] **Step 4: Add API schemas and routes**

Use typed requests:

```python
class ImportPreviewItemRequest(BaseModel):
    task_id: int
    task_item_id: int


class SaveDimensionItemRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    selected_source_asset_id: str | None = None
    target_slot_id: str | None = None
    physical_dimensions: dict[str, Any] | None = None
    annotations: list[dict[str, Any]] | None = None
    canvas_settings: dict[str, Any] | None = None


class CompleteDimensionItemRequest(BaseModel):
    expected_revision: int = Field(ge=0)
```

Routes:

```text
POST /dimension-canvas/items/import-preview-item
GET /dimension-canvas/items/{item_id}
PATCH /dimension-canvas/items/{item_id}
POST /dimension-canvas/items/{item_id}/complete
POST /dimension-canvas/batches/{batch_id}/submit-review
```

Map stale revisions to HTTP 409, missing objects to 404, and validation to 400. Include the subrouter from `create_product_processing_router` and close its executor in the existing lifespan.

Expose a public `publish_dimension_media(content, task_id, draft_id, render_revision)` adapter on `ProductProcessingService`. It may use the existing COS/media configuration, but always returns the local managed URL when remote publication is unavailable.

- [ ] **Step 5: Run backend lifecycle and route tests, then commit**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_dimension_canvas.py -q
```

Expected: pass with injected local source loader and publisher; no provider calls.

Commit:

```powershell
git add local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py local-runtime/wh_local/modules/product_processing/api/dimension_canvas_schemas.py local-runtime/wh_local/modules/product_processing/api/dimension_canvas_router.py local-runtime/wh_local/modules/product_processing/api/router.py local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/test_product_processing_dimension_canvas.py
git commit -m "feat(product-processing): add dimension canvas API"
```

## Task 7: Add Frontend Types, API, and Pure Editor Model

**Files:**

- Create: `web-frontend/src/modules/product_processing/types/dimensionCanvas.ts`
- Create: `web-frontend/src/modules/product_processing/api/dimensionCanvasApi.ts`
- Create: `web-frontend/src/modules/product_processing/data/dimensionCanvasModel.ts`
- Create: `web-frontend/src/modules/product_processing/data/dimensionCanvasModel.test.ts`
- Modify: `web-frontend/src/modules/product_processing/types/index.ts:164-215`

- [ ] **Step 1: Write failing editor model tests**

```typescript
import assert from "node:assert/strict";
import test from "node:test";

import { addAnnotation, changeDimensionValue, nextQueueItem } from "./dimensionCanvasModel.ts";
import type { DimensionAnnotation, DimensionKey, EditorState } from "../types/dimensionCanvas.ts";

function annotation(id: string, key: DimensionKey, valueCm: number): DimensionAnnotation {
  return {
    id,
    key,
    valueCm,
    start: { x: 0.1, y: 0.8 },
    end: { x: 0.9, y: 0.8 },
    label: { x: 0.5, y: 0.75 },
    style: "auto",
  };
}

function fixtureState(input: { length?: number; annotations?: DimensionAnnotation[] } = {}): EditorState {
  return {
    selectedAssetId: "asset-1",
    targetSlotId: "carousel.dimension_background",
    dimensions: {
      length: { valueCm: input.length ?? 10, provenance: "manual_confirmed", evidenceRef: "manual" },
      width: { valueCm: 8, provenance: "manual_confirmed", evidenceRef: "manual" },
      height: { valueCm: 4, provenance: "manual_confirmed", evidenceRef: "manual" },
      conflict: false,
    },
    annotations: input.annotations ?? [],
    activeTool: "select",
    selectedAnnotationId: null,
  };
}

test("dimension value change updates every semantic annotation", () => {
  const state = fixtureState({ length: 10, annotations: [annotation("a", "length", 10), annotation("b", "length", 10)] });
  const next = changeDimensionValue(state, "length", 12);
  assert.deepEqual(next.annotations.map((item) => item.valueCm), [12, 12]);
});

test("annotation points are normalized and immutable", () => {
  const state = fixtureState();
  const next = addAnnotation(state, "length", { x: 0.1, y: 0.8 }, { x: 0.9, y: 0.8 });
  assert.equal(state.annotations.length, 0);
  assert.deepEqual(next.annotations[0].start, { x: 0.1, y: 0.8 });
});

test("queue navigation keeps sparse stable ids", () => {
  assert.equal(nextQueueItem(["item-2", "item-5", "item-9"], "item-5", 1), "item-9");
});
```

- [ ] **Step 2: Run the Node test and verify import failure**

Run:

```powershell
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/dimensionCanvasModel.test.ts
```

Expected: fail because `dimensionCanvasModel.ts` does not exist.

- [ ] **Step 3: Define exact API/editor types**

Include:

```typescript
export type DimensionKey = "length" | "width" | "height" | "custom";
export type DimensionProvenance = "source_confirmed" | "manual_confirmed" | "unconfirmed" | "package_estimate";
export type CanvasItemState = "pending" | "editing" | "needs_dimensions" | "asset_failed" | "rendering" | "render_retryable" | "completed" | "submitted" | "conflict" | "accepted" | "skipped";

export interface NormalizedPoint { x: number; y: number }
export interface DimensionAnnotation {
  id: string;
  key: DimensionKey;
  valueCm: number;
  start: NormalizedPoint;
  end: NormalizedPoint;
  label: NormalizedPoint;
  style: "auto" | "dark" | "light";
}

export interface DimensionValue {
  valueCm: number | null;
  provenance: DimensionProvenance;
  evidenceRef: string;
}

export interface PhysicalDimensions {
  length: DimensionValue;
  width: DimensionValue;
  height: DimensionValue;
  conflict: boolean;
}

export interface DimensionAsset {
  id: string;
  role: string;
  previewUrl: string;
  width: number;
  height: number;
  availability: "metadata" | "ready" | "failed";
}

export interface EditorState {
  selectedAssetId: string;
  targetSlotId: string;
  dimensions: PhysicalDimensions;
  annotations: DimensionAnnotation[];
  activeTool: DimensionKey | "select";
  selectedAnnotationId: string | null;
}

export interface DimensionCanvasItem {
  id: string;
  batchId: string;
  taskId: number;
  taskItemId: number;
  productDraftId: number;
  skc: string;
  state: CanvasItemState;
  itemRevision: number;
  renderRevision: number;
  assets: DimensionAsset[];
  editor: EditorState;
  errorCode: string;
  errorMessage: string;
}

export interface DimensionCanvasBatch {
  id: string;
  sourceTaskId: number;
  status: string;
  items: DimensionCanvasItem[];
}

export interface DimensionChangeSet {
  id: string;
  sourceTaskId: number;
  status: string;
  itemCount: number;
  acceptedCount: number;
  conflictCount: number;
}

export interface SaveDimensionItemRequest {
  expected_revision: number;
  selected_source_asset_id: string;
  target_slot_id: string;
  physical_dimensions: PhysicalDimensions;
  annotations: DimensionAnnotation[];
  canvas_settings: { fit: "contain" | "cover"; style: "auto" | "dark" | "light" };
}
```

Keep components camelCase. Convert snake_case API payloads in `dimensionCanvasApi.ts` through one `mapItem()` function so transport naming does not leak into editor state.

- [ ] **Step 4: Implement pure immutable helpers and typed API functions**

Implement the pure model with immutable updates:

```typescript
export function clampPoint(point: NormalizedPoint): NormalizedPoint {
  return { x: Math.min(1, Math.max(0, point.x)), y: Math.min(1, Math.max(0, point.y)) };
}

export function addAnnotation(state: EditorState, key: DimensionKey, start: NormalizedPoint, end: NormalizedPoint): EditorState {
  const valueCm = key === "custom" ? 0 : state.dimensions[key].valueCm ?? 0;
  const safeStart = clampPoint(start);
  const safeEnd = clampPoint(end);
  const label = { x: (safeStart.x + safeEnd.x) / 2, y: Math.max(0.05, (safeStart.y + safeEnd.y) / 2 - 0.05) };
  const next: DimensionAnnotation = {
    id: crypto.randomUUID(), key, valueCm, start: safeStart, end: safeEnd, label, style: "auto",
  };
  return { ...state, annotations: [...state.annotations, next], selectedAnnotationId: next.id };
}

export function updateAnnotation(state: EditorState, id: string, patch: Partial<DimensionAnnotation>): EditorState {
  return { ...state, annotations: state.annotations.map((item) => item.id === id ? { ...item, ...patch } : item) };
}

export function removeAnnotation(state: EditorState, id: string): EditorState {
  return { ...state, annotations: state.annotations.filter((item) => item.id !== id), selectedAnnotationId: null };
}

export function changeDimensionValue(state: EditorState, key: Exclude<DimensionKey, "custom">, valueCm: number): EditorState {
  return {
    ...state,
    dimensions: {
      ...state.dimensions,
      [key]: { valueCm, provenance: "manual_confirmed", evidenceRef: "manual" },
    },
    annotations: state.annotations.map((item) => item.key === key ? { ...item, valueCm } : item),
  };
}

export function nextQueueItem(ids: string[], currentId: string, direction: -1 | 1): string {
  const index = Math.max(0, ids.indexOf(currentId));
  return ids[Math.min(ids.length - 1, Math.max(0, index + direction))] ?? "";
}

export function canComplete(state: EditorState): { ok: boolean; reason: string } {
  if (!state.selectedAssetId) return { ok: false, reason: "请选择尺寸图素材" };
  if (!state.targetSlotId) return { ok: false, reason: "请选择回写位置" };
  if (state.annotations.length === 0) return { ok: false, reason: "请至少绘制一条尺寸线" };
  const allowed = new Set(["source_confirmed", "manual_confirmed"]);
  const invalid = state.annotations.find((item) => item.key !== "custom" && !allowed.has(state.dimensions[item.key].provenance));
  return invalid ? { ok: false, reason: "标注尺寸尚未确认" } : { ok: true, reason: "" };
}
```

`dimensionCanvasApi.ts` must wrap the routes from Task 6 through `ppRequest`; components must not construct route strings directly.

- [ ] **Step 5: Run model tests and TypeScript build, then commit**

Run:

```powershell
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/dimensionCanvasModel.test.ts
npm.cmd --prefix web-frontend run build
```

Expected: tests and build pass.

Commit:

```powershell
git add web-frontend/src/modules/product_processing/types/dimensionCanvas.ts web-frontend/src/modules/product_processing/api/dimensionCanvasApi.ts web-frontend/src/modules/product_processing/data/dimensionCanvasModel.ts web-frontend/src/modules/product_processing/data/dimensionCanvasModel.test.ts web-frontend/src/modules/product_processing/types/index.ts
git commit -m "feat(web): add dimension canvas client model"
```

## Task 8: Wire Navigation and the Single-Item Precheck Entry

**Files:**

- Create: `web-frontend/src/modules/product_processing/pages/DimensionCanvasPage.tsx`
- Create: `web-frontend/src/modules/product_processing/styles/dimension-canvas.css`
- Modify: `web-frontend/src/app/navigation/modules.ts:1-40`
- Modify: `web-frontend/src/app/layout/TopNavigation.tsx:6-17`
- Modify: `web-frontend/src/app/layout/WorkspaceShell.tsx:1-245`
- Modify: `web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx:43-408`
- Modify: `web-frontend/src/modules/product_processing/pages/ProductProcessingTaskPage.tsx:18-30`

- [ ] **Step 1: Add the visible module and tab context**

Add `"dimension_canvas"` to `WorkspaceModuleId` and:

```typescript
{ id: "dimension_canvas", label: "尺寸画布", icon: "", iconClass: "iconfont icon-ruler", description: "精确制作并审核商品尺寸图" }
```

Extend `WorkspaceTab`:

```typescript
dimensionBatchId?: string;
dimensionItemId?: string;
returnTaskId?: number;
```

- [ ] **Step 2: Add a minimal page with load/empty/error states**

`DimensionCanvasPage` props:

```typescript
type Props = {
  initialBatchId?: string;
  initialItemId?: string;
  onOpenPrecheck: (taskId: number, changeSetId?: string) => void;
};
```

The page must render a command bar, import button, historical batches, and a clear empty state. Do not add canvas interactions in this task.

- [ ] **Step 3: Add an idempotent single-item open callback**

In `WorkspaceShell`, implement:

```typescript
const openDimensionItem = async (taskId: number, taskItemId: number) => {
  const item = await importPreviewItem({ task_id: taskId, task_item_id: taskItemId });
  const existing = tabs.find((tab) => tab.dimensionItemId === item.id);
  if (existing) {
    setActiveTabKey(existing.key);
    return;
  }
  const key = `dimension-canvas-${item.id}`;
  setTabs((current) => [...current, {
    key,
    moduleId: "dimension_canvas",
    label: `尺寸·${item.skc || item.product_draft_id}`,
    icon: "↔",
    dimensionBatchId: item.batch_id,
    dimensionItemId: item.id,
    returnTaskId: taskId,
  }]);
  setActiveTabKey(key);
};
```

Use an in-flight guard keyed by `taskId:taskItemId` so double clicks share one request.

- [ ] **Step 4: Add “添加尺寸图” to each precheck product card**

Extend precheck props with `onOpenDimensionItem(taskId, taskItemId)`. Place the button in the image-side header, not inside a thumbnail click target:

```tsx
<button className="btn-mini primary" onClick={() => onOpenDimensionItem(taskId, item.item_id)}>
  添加尺寸图
</button>
```

Keep source thumbnail preview behavior unchanged.

Rename the existing processing-scope label from “产品尺寸” to “物流包裹尺寸/重量”. In precheck, rename the existing `length_cm` / `width_cm` / `height_cm` labels to “物流包裹长/宽/高(cm)”. Do not reuse those inputs for canvas dimensions; the canvas reads `physical_dimensions` from its own contract.

- [ ] **Step 5: Build and commit**

Run:

```powershell
npm.cmd --prefix web-frontend run build
```

Expected: build passes.

Commit:

```powershell
git add web-frontend/src/app/navigation/modules.ts web-frontend/src/app/layout/TopNavigation.tsx web-frontend/src/app/layout/WorkspaceShell.tsx web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx web-frontend/src/modules/product_processing/pages/ProductProcessingTaskPage.tsx web-frontend/src/modules/product_processing/pages/DimensionCanvasPage.tsx web-frontend/src/modules/product_processing/styles/dimension-canvas.css
git commit -m "feat(web): open dimension canvas from precheck"
```

## Task 9: Build SVG Editing, Toolbar, Queue, and Autosave

**Files:**

- Create: `web-frontend/src/modules/product_processing/components/DimensionCanvasStage.tsx`
- Create: `web-frontend/src/modules/product_processing/components/DimensionCanvasToolbar.tsx`
- Create: `web-frontend/src/modules/product_processing/components/DimensionCanvasQueue.tsx`
- Create: `web-frontend/src/modules/product_processing/hooks/useDimensionCanvasAutosave.ts`
- Modify: `web-frontend/src/modules/product_processing/pages/DimensionCanvasPage.tsx`
- Modify: `web-frontend/src/modules/product_processing/styles/dimension-canvas.css`

- [ ] **Step 1: Implement pointer-to-normalized coordinate conversion**

Use the SVG bounding rectangle, not page coordinates:

```typescript
function eventPoint(event: React.PointerEvent<SVGSVGElement>): NormalizedPoint {
  const rect = event.currentTarget.getBoundingClientRect();
  return clampPoint({
    x: (event.clientX - rect.left) / rect.width,
    y: (event.clientY - rect.top) / rect.height,
  });
}
```

Capture the pointer on down, update the preview on move, and commit one annotation on up. Ignore drags shorter than 1% of canvas width.

- [ ] **Step 2: Render semantic SVG annotations**

Render the selected image below an SVG layer. Each annotation must contain one line, two arrow heads, one text element, and large transparent endpoint hit targets. Use `vectorEffect="non-scaling-stroke"` for editor clarity; backend remains final-render authority.

Bind the label to `annotation.valueCm` and format with:

```typescript
export function formatCentimeters(value: number): string {
  return `${Number(value.toFixed(2))} cm`;
}
```

- [ ] **Step 3: Implement toolbar and completion gate**

Toolbar actions:

- select;
- length, width, height, custom;
- undo, redo, delete;
- fit, zoom in, zoom out, reset;
- auto, dark, light style.

Disable a dimension tool when its value is missing, `unconfirmed`, or `package_estimate`. Show the exact reason from `canComplete()` beside the complete action.

- [ ] **Step 4: Implement debounced latest-wins autosave**

Hook contract:

```typescript
export function useDimensionCanvasAutosave(
  item: DimensionCanvasItem | null,
  editor: EditorState,
  save: (request: SaveDimensionItemRequest) => Promise<DimensionCanvasItem>,
): { state: "idle" | "saving" | "saved" | "error"; retry: () => void }
```

Use a 450ms debounce, one in-flight request, a generation counter, and queued latest snapshot. Only the response matching the latest generation may replace local server state. On 409, reload the item and show a conflict banner; never silently discard local edits.

- [ ] **Step 5: Implement scoped wheel queue navigation**

Only `DimensionCanvasQueue` handles `wheel`. Require `Math.abs(deltaY) >= 12`, throttle to one switch per 220ms, call `preventDefault()` inside the queue, and leave all other page scrolling unchanged. Add explicit previous/next buttons and arrow-key support when focus is inside the page.

- [ ] **Step 6: Run tests/build and commit**

Run:

```powershell
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/dimensionCanvasModel.test.ts
npm.cmd --prefix web-frontend run build
```

Expected: pass.

Commit:

```powershell
git add web-frontend/src/modules/product_processing/components/DimensionCanvasStage.tsx web-frontend/src/modules/product_processing/components/DimensionCanvasToolbar.tsx web-frontend/src/modules/product_processing/components/DimensionCanvasQueue.tsx web-frontend/src/modules/product_processing/hooks/useDimensionCanvasAutosave.ts web-frontend/src/modules/product_processing/pages/DimensionCanvasPage.tsx web-frontend/src/modules/product_processing/styles/dimension-canvas.css
git commit -m "feat(web): add dimension canvas editor"
```

## Task 10: Add Batch Import, Lazy Assets, Partial Completion, and Recovery

**Files:**

- Create: `web-frontend/src/modules/product_processing/components/DimensionCanvasImportDialog.tsx`
- Modify: `web-frontend/src/modules/product_processing/pages/DimensionCanvasPage.tsx`
- Modify: `web-frontend/src/modules/product_processing/api/dimensionCanvasApi.ts`
- Modify: `local-runtime/wh_local/modules/product_processing/api/dimension_canvas_schemas.py`
- Modify: `local-runtime/wh_local/modules/product_processing/api/dimension_canvas_router.py`
- Modify: `local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py`
- Modify: `local-runtime/tests/test_product_processing_dimension_canvas.py`

- [ ] **Step 1: Add a failing 100-item sparse batch test**

```python
def test_batch_import_and_partial_submit_keep_sparse_identity(seed_finished_task) -> None:
    service, task = seed_finished_task(item_count=100)
    selected = [item["id"] for index, item in enumerate(task["items"]) if index % 3 != 1]
    batch = service.import_task(task["id"], selected, workspace_id="local")
    assert [item["task_item_id"] for item in batch["items"]] == selected
    completed_ids = complete_every_other_item(service, batch["items"])
    change_set = service.submit_review(batch["id"], workspace_id="local")
    assert {item["dimension_item_id"] for item in change_set["items"]} == set(completed_ids)
```

- [ ] **Step 2: Implement batch import endpoints and eligibility groups**

Add:

```text
GET /dimension-canvas/importable-tasks
GET /dimension-canvas/tasks/{task_id}/eligibility
POST /dimension-canvas/batches/import-task
GET /dimension-canvas/batches
GET /dimension-canvas/batches/{batch_id}
```

`eligibility` must return `ready`, `needs_dimensions`, `existing_dimension`, and `asset_failed` arrays keyed by task item ID. It may inspect known asset roles and physical-dimension provenance, but it must not use OCR or AI to declare an existing image correct.

- [ ] **Step 3: Implement import dialog and stable queue**

Dialog flow:

1. Select completed task.
2. Load eligibility once.
3. Show four groups and product checkboxes.
4. Default-select `ready`; leave other groups unselected.
5. For `existing_dimension`, offer keep, remake, or skip.
6. Submit selected task item IDs exactly as displayed.

The queue key is `dimension_item_id`; never use array index or SKC as a React key.

- [ ] **Step 4: Add lazy materialization and adjacent prefetch**

Batch import stores asset metadata only. When an item opens, materialize only its selected preview source; after it becomes viewable, preload thumbnails for the next item. Use content hash deduplication. Do not fetch all 100 full-resolution assets during import.

- [ ] **Step 5: Add recovery and partial completion**

On service construction, move persisted `rendering` items to `render_retryable`. The UI must show retry for those items. `submit-review` includes only `completed` items not already included in an open change set. Pending, skipped, failed, and editing items remain in the batch.

- [ ] **Step 6: Run tests/build and commit**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_dimension_canvas.py -q
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/dimensionCanvasModel.test.ts
npm.cmd --prefix web-frontend run build
```

Expected: pass.

Commit:

```powershell
git add local-runtime/wh_local/modules/product_processing/api/dimension_canvas_schemas.py local-runtime/wh_local/modules/product_processing/api/dimension_canvas_router.py local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py local-runtime/tests/test_product_processing_dimension_canvas.py web-frontend/src/modules/product_processing/components/DimensionCanvasImportDialog.tsx web-frontend/src/modules/product_processing/pages/DimensionCanvasPage.tsx web-frontend/src/modules/product_processing/api/dimensionCanvasApi.ts
git commit -m "feat(product-processing): add batch dimension workflow"
```

## Task 11: Add Persistent Notifications, Review Diff, and Conflict-Safe Acceptance

**Files:**

- Create: `web-frontend/src/modules/product_processing/components/DimensionChangeSetReview.tsx`
- Modify: `web-frontend/src/app/layout/WorkspaceShell.tsx`
- Modify: `web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx`
- Modify: `web-frontend/src/modules/product_processing/api/dimensionCanvasApi.ts`
- Modify: `local-runtime/wh_local/modules/product_processing/api/dimension_canvas_router.py`
- Modify: `local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_repository.py`
- Modify: `local-runtime/tests/test_product_processing_dimension_canvas.py`

- [ ] **Step 1: Add failing acceptance and conflict tests**

```python
def test_accept_updates_only_dimension_slot(seed_completed_change_set) -> None:
    service, change_set, before = seed_completed_change_set()
    accepted = service.accept_change_set(change_set["id"], workspace_id="local")
    after = service.product_repository.get_draft(before["draft_id"], workspace_id="local")
    assert accepted["accepted_count"] == 1
    assert after["preview_overrides"]["image_slot_overrides"]["carousel.dimension_background"]["url"].endswith("dimension.jpg")
    assert after["preview_overrides"].get("carousel_images") == before["preview_overrides"].get("carousel_images")


def test_accept_detects_target_slot_revision_conflict(seed_completed_change_set) -> None:
    service, change_set, before = seed_completed_change_set()
    mutate_dimension_slot_after_canvas_import(service.product_repository, before["draft_id"])
    result = service.accept_change_set(change_set["id"], workspace_id="local")
    assert result["accepted_count"] == 0
    assert result["conflict_count"] == 1
```

- [ ] **Step 2: Implement change-set and notification repository methods**

Expose:

```python
def create_change_set(self, batch_id: str, completed_item_ids: list[str], idempotency_key: str, workspace_id: str) -> dict[str, Any]
def get_change_set(self, change_set_id: str, workspace_id: str) -> dict[str, Any] | None
def accept_change_item(self, change_item_id: str, expected_preview_revision: int, workspace_id: str) -> dict[str, Any]
def reject_change_item(self, change_item_id: str, workspace_id: str) -> dict[str, Any]
def list_notifications(self, workspace_id: str, after: str = "") -> list[dict[str, Any]]
def mark_notification_read(self, notification_id: str, workspace_id: str) -> dict[str, Any]
```

Acceptance must compare the saved base preview revision and the current target-slot value. It may accept a revision change caused only by unrelated title/description edits after producing a diff; any target-slot or physical-dimension change is a conflict.

- [ ] **Step 3: Add review and notification routes**

```text
GET /dimension-canvas/change-sets/{change_set_id}
POST /dimension-canvas/change-sets/{change_set_id}/accept
POST /dimension-canvas/change-sets/{change_set_id}/items/{change_item_id}/accept
POST /dimension-canvas/change-sets/{change_set_id}/items/{change_item_id}/reject
GET /dimension-canvas/notifications
POST /dimension-canvas/notifications/{notification_id}/read
```

Batch acceptance processes each item in its own transaction and returns accepted/conflict/rejected counts. One conflict cannot roll back accepted siblings.

- [ ] **Step 4: Add visible-only notification refresh**

In `WorkspaceShell`:

- fetch once after authenticated workspace entry;
- fetch on `window.focus`;
- poll every 15 seconds only when `document.visibilityState === "visible"`;
- stop timer while hidden;
- dispatch an in-memory `mainpg:dimension-change-set` event after local submit for immediate same-window refresh.

Before implementing this account-scoped refresh path, run the project `evolution-guard` once for the unchanged cache/concurrency plan. The implementation must keep workspace scope, one in-flight notification request, latest-wins response handling, visibility pause, and timer cleanup in the same hook.

Show a badge on the dimension-canvas module and a workspace notice linking to the affected precheck task.

- [ ] **Step 5: Add before/after review and precise acceptance**

`DimensionChangeSetReview` shows SKC, old target image, new dimension image, physical dimensions, target slot, and status. Actions: accept all non-conflicts, accept one, reject one, open conflict. Accepting refreshes the precheck item and keeps every unchanged thumbnail in place.

- [ ] **Step 6: Run tests/build and commit**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests/test_product_processing_dimension_canvas.py local-runtime/tests/test_product_processing_preview_overrides.py -q
npm.cmd --prefix web-frontend run build
```

Expected: pass.

Commit:

```powershell
git add local-runtime/wh_local/modules/product_processing/api/dimension_canvas_router.py local-runtime/wh_local/modules/product_processing/dimension_canvas_service.py local-runtime/wh_local/modules/product_processing/infrastructure/dimension_canvas_repository.py local-runtime/tests/test_product_processing_dimension_canvas.py web-frontend/src/modules/product_processing/components/DimensionChangeSetReview.tsx web-frontend/src/app/layout/WorkspaceShell.tsx web-frontend/src/modules/product_processing/pages/ProductProcessingPrecheckPage.tsx web-frontend/src/modules/product_processing/api/dimensionCanvasApi.ts
git commit -m "feat(product-processing): review dimension image changes"
```

## Task 12: Theme Coverage, Performance Gates, Full Regression, and Documentation

**Files:**

- Modify: `web-frontend/src/modules/product_processing/styles/dimension-canvas.css`
- Modify: `local-runtime/tests/test_product_processing_dimension_canvas.py`
- Modify: `local-runtime/tests/test_product_processing_dimension_renderer.py`
- Modify: `local-runtime/wh_local/modules/product_processing/README.md`

- [ ] **Step 1: Use only existing theme semantic variables**

All dimension-canvas colors must resolve through:

```css
var(--theme-module-surface)
var(--theme-module-surface-raised)
var(--theme-module-surface-soft)
var(--theme-module-border)
var(--theme-module-border-strong)
var(--theme-text-primary)
var(--theme-text-secondary)
var(--theme-text-muted)
var(--theme-primary)
var(--theme-module-primary-strong)
var(--theme-module-overlay)
```

Export image colors come from canvas style settings, never from page theme variables. Add a 1100px breakpoint that turns the right queue into a drawer and keeps the canvas within viewport width.

- [ ] **Step 2: Add deterministic performance and no-provider tests**

```python
def test_import_100_items_does_not_materialize_images(seed_finished_task, recording_loader) -> None:
    service, task = seed_finished_task(item_count=100, source_loader=recording_loader)
    service.import_task(task["id"], [item["id"] for item in task["items"]], workspace_id="local")
    assert recording_loader.calls == []


def test_dimension_canvas_never_calls_ai(seed_finished_task, forbidden_ai_client) -> None:
    service, task = seed_finished_task(item_count=1, ai_client=forbidden_ai_client)
    complete_one_valid_dimension_item(service, task)
    assert forbidden_ai_client.calls == []
```

Add a renderer benchmark assertion using five warmed local renders and a generous CI ceiling of 3 seconds per image. Record the actual local median separately; do not make network publication part of the unit-test timing.

- [ ] **Step 3: Run the full backend and frontend suites**

Run:

```powershell
& '.\local-runtime\.venv\Scripts\python.exe' -m pytest local-runtime/tests -q
node --test --experimental-strip-types web-frontend/src/modules/product_processing/data/dimensionCanvasModel.test.ts web-frontend/src/modules/ai_service/data/composerDraft.test.ts web-frontend/src/modules/ai_service/data/assetDownload.test.ts web-frontend/src/modules/ai_service/data/aiServiceDemo.test.ts
npm.cmd --prefix web-frontend run build
git diff --check
```

Expected: all Python tests, all Node tests, production build, and whitespace check pass.

- [ ] **Step 4: Run local browser acceptance without provider calls**

Use the running local frontend/backend or restart them through the project restart gate. Create a deterministic local fixture task and verify:

1. Sidebar shows “尺寸画布” under all four themes.
2. Precheck “添加尺寸图” opens one reusable item tab.
3. Top assets switch independently from the target slot.
4. Package-only dimensions cannot arm a drawing tool.
5. Manual-confirmed length draws a double arrow.
6. Changing 10 to 12 updates every bound label.
7. Queue wheel switches only while pointer is over the right queue.
8. Refresh restores the draft.
9. Completing two sparse items and submitting creates one persistent notice.
10. Accepting changes replaces only each dimension slot; summary image and other carousel images remain unchanged.
11. Narrow viewport has no page-level horizontal overflow.
12. Browser console has no new errors.

Capture screenshots for classic, diamond, desktop, and narrow viewport under the task verification output directory. Do not use a real provider or COS upload for this acceptance.

- [ ] **Step 5: Update module documentation**

Document:

- physical versus shipping dimension ownership;
- optional dimension-canvas workflow;
- semantic image slots;
- local deterministic renderer;
- API route list;
- recovery and conflict behavior;
- exact test commands;
- explicit statement that no AI call is used for dimension drawing.

- [ ] **Step 6: Final hygiene and commit**

Verify the intended write set before staging. Preserve unrelated user files and ignored runtime outputs.

```powershell
git status --short
git diff --check
git add -- web-frontend/src/modules/product_processing/styles/dimension-canvas.css local-runtime/tests/test_product_processing_dimension_canvas.py local-runtime/tests/test_product_processing_dimension_renderer.py local-runtime/wh_local/modules/product_processing/README.md docs/superpowers/plans/2026-08-13-product-dimension-canvas.md
git diff --cached --check
git commit -m "feat(product-processing): complete dimension canvas workflow"
```

Expected: commit succeeds; tracked workspace is clean afterward. Unrelated changes and ignored runtime outputs remain untouched.

## Final Acceptance Gate

Do not call the feature complete until all conditions hold:

- Product-body dimensions never inherit `package_estimate` values.
- Panel 4 contains no AI-generated numbers, units, rulers, arrows, or dimension labels.
- Single and batch imports share one item identity contract.
- Autosave stale responses cannot overwrite newer edits.
- 100 sparse items produce zero cross-product image changes.
- Applying a change set changes only the target slot.
- Grid summary and all unmodified images keep their order.
- Local output is `2000×2000`, decodable, and text remains inside safe margins.
- Full backend tests, Node model tests, Vite build, four-theme browser checks, and narrow-layout checks pass.
- No model, marketplace, Dianxiaomi, deploy, package, or production write occurred during local verification.
