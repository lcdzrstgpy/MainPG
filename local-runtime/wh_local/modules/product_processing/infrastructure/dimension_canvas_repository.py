from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..domain.policy import is_safe_external_url
from ..domain.physical_dimensions import prefill_physical_dimensions
from ..domain.preview_images import MANIFEST_KEY, SLOT_INDEX, PreviewImageManifest, replace_carousel_slot
from .database import ProductProcessingDatabase
from .dimension_canvas_orm import (
    DimensionCanvasAssetRow,
    DimensionCanvasBatchRow,
    DimensionCanvasChangeItemRow,
    DimensionCanvasChangeSetRow,
    DimensionCanvasItemRow,
    DimensionCanvasNotificationRow,
)
from .orm import ProcessingTaskItemRow, ProcessingTaskRow, ProductDraftRow, utc_now
from .preview_image_orm import PreviewImageAssetRow
from .media_asset_orm import MediaAssetRow, MediaBindingRow
from .media_asset_repository import media_binding_key


class StaleCanvasRevision(RuntimeError):
    pass


class CanvasStateConflict(RuntimeError):
    pass


_PUBLISH_LEASE_SECONDS = 180


def _publish_lease_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=_PUBLISH_LEASE_SECONDS)).isoformat()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _hash(value: Any) -> str:
    return hashlib.sha256(_dumps(value).encode("utf-8")).hexdigest()


def _asset_identity(asset: DimensionCanvasAssetRow) -> str:
    return str(asset.content_hash or asset.id)


class DimensionCanvasRepository:
    """Workspace-scoped persistence for deterministic dimension canvases.

    Every public lookup accepts an explicit workspace. Editor input is frozen in
    ``canvas_settings_json`` at render start because the schema intentionally has
    no mutable worker-side payload columns.
    """

    _EDITOR_FIELDS = {
        "selected_source_asset_id",
        "target_slot_id",
        "physical_dimensions",
        "annotations",
        "canvas_settings",
    }

    def __init__(self, database: ProductProcessingDatabase):
        self.database = database

    def import_task_items(
        self,
        task_id: int,
        task_item_ids: list[int],
        workspace_id: str,
    ) -> dict[str, Any]:
        requested = list(dict.fromkeys(int(value) for value in task_item_ids))
        if not requested:
            raise ValueError("at least one task item is required")
        with self.database.sessions.begin() as session:
            task = session.scalar(
                select(ProcessingTaskRow).where(
                    ProcessingTaskRow.id == task_id,
                    ProcessingTaskRow.workspace_id == workspace_id,
                )
            )
            if task is None:
                raise LookupError("product processing task not found")
            if task.status not in {"completed", "failed", "partial_failure", "completed_with_attention"}:
                raise ValueError("product processing task is not finished")
            task_items = session.scalars(
                select(ProcessingTaskItemRow).where(
                    ProcessingTaskItemRow.task_id == task_id,
                    ProcessingTaskItemRow.id.in_(requested),
                    ProcessingTaskItemRow.product_draft_id.is_not(None),
                    ProcessingTaskItemRow.status == "completed",
                )
            ).all()
            by_id = {row.id: row for row in task_items}
            missing = [item_id for item_id in requested if item_id not in by_id]
            if missing:
                raise LookupError(f"task items not found: {missing}")

            draft_ids = [int(by_id[item_id].product_draft_id) for item_id in requested]
            drafts = session.scalars(
                select(ProductDraftRow).where(
                    ProductDraftRow.id.in_(draft_ids),
                    ProductDraftRow.workspace_id == workspace_id,
                    ProductDraftRow.status != "deleted",
                )
            ).all()
            draft_by_id = {row.id: row for row in drafts}
            if len(draft_by_id) != len(set(draft_ids)):
                raise LookupError("product draft not found")

            existing_rows = session.scalars(
                select(DimensionCanvasItemRow).where(
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.task_id == task_id,
                    DimensionCanvasItemRow.task_item_id.in_(requested),
                )
            ).all()
            existing = {row.task_item_id: row for row in existing_rows}
            batch_id = str(uuid5(NAMESPACE_URL, f"mainpg:{workspace_id}:dimension:{task_id}"))
            batch = session.scalar(
                select(DimensionCanvasBatchRow).where(
                    DimensionCanvasBatchRow.id == batch_id,
                    DimensionCanvasBatchRow.workspace_id == workspace_id,
                )
            )
            if batch is None:
                batch = DimensionCanvasBatchRow(
                    id=batch_id,
                    workspace_id=workspace_id,
                    source_task_id=task_id,
                    status="draft",
                )
                session.add(batch)
                session.flush()

            output: list[DimensionCanvasItemRow] = []
            for task_item_id in requested:
                row = existing.get(task_item_id)
                task_item = by_id[task_item_id]
                draft = draft_by_id[int(task_item.product_draft_id)]
                if row is None:
                    result = _loads(task_item.result_json, {})
                    row = DimensionCanvasItemRow(
                        id=str(uuid4()),
                        batch_id=batch.id,
                        workspace_id=workspace_id,
                        task_id=task_id,
                        task_item_id=task_item.id,
                        product_draft_id=draft.id,
                        skc=str(task_item.skc or ""),
                        source_preview_revision=int(draft.preview_revision or 0),
                        physical_dimensions_json=_dumps(
                            prefill_physical_dimensions(result).model_dump(mode="json")
                        ),
                    )
                    session.add(row)
                    session.flush()
                    self._register_task_assets(session, row, task_item, draft, workspace_id)
                elif int(row.source_preview_revision) != int(draft.preview_revision or 0):
                    if int(row.item_revision) == 0 and row.state == "pending":
                        row.source_preview_revision = int(draft.preview_revision or 0)
                        result = _loads(task_item.result_json, {})
                        row.physical_dimensions_json = _dumps(
                            prefill_physical_dimensions(result).model_dump(mode="json")
                        )
                        old_assets = session.scalars(
                            select(DimensionCanvasAssetRow).where(
                                DimensionCanvasAssetRow.item_id == row.id,
                                DimensionCanvasAssetRow.workspace_id == workspace_id,
                            )
                        ).all()
                        for old_asset in old_assets:
                            session.delete(old_asset)
                        session.flush()
                        self._register_task_assets(session, row, task_item, draft, workspace_id)
                    else:
                        row.state = "conflict"
                        row.error_code = "source_preview_changed"
                        row.error_message = "source preview changed after canvas editing began"
                        row.updated_at = utc_now()
                output.append(row)

            batch.counts_json = _dumps(self._batch_counts(session, batch.id, workspace_id))
            batch.updated_at = utc_now()
            session.flush()
            return {**self._batch(batch), "items": [self._item(row) for row in output]}

    def list_batches(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            rows = session.scalars(
                select(DimensionCanvasBatchRow)
                .where(DimensionCanvasBatchRow.workspace_id == workspace_id)
                .order_by(DimensionCanvasBatchRow.updated_at.desc())
            ).all()
            return [self._batch(row) for row in rows]

    def get_batch(self, batch_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            batch = session.scalar(
                select(DimensionCanvasBatchRow).where(
                    DimensionCanvasBatchRow.id == batch_id,
                    DimensionCanvasBatchRow.workspace_id == workspace_id,
                )
            )
            if batch is None:
                return None
            items = session.scalars(
                select(DimensionCanvasItemRow)
                .where(
                    DimensionCanvasItemRow.batch_id == batch_id,
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                )
                .order_by(DimensionCanvasItemRow.created_at, DimensionCanvasItemRow.id)
            ).all()
            task_items = session.scalars(
                select(ProcessingTaskItemRow).where(
                    ProcessingTaskItemRow.id.in_([row.task_item_id for row in items])
                )
            ).all()
            results = {row.id: _loads(row.result_json, {}) for row in task_items}
            return {
                **self._batch(batch),
                "items": [self._item_with_result(row, results.get(row.task_item_id, {})) for row in items],
            }

    def get_item(self, item_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = self._get_item_row(session, item_id, workspace_id)
            if row is None:
                return None
            task_item = session.get(ProcessingTaskItemRow, row.task_item_id)
            result = _loads(task_item.result_json, {}) if task_item is not None else {}
            return self._item_with_result(row, result)

    def register_uploaded_asset(
        self,
        item_id: str,
        workspace_id: str,
        *,
        managed_path: str,
        content_hash: str,
        width: int,
        height: int,
        content_type: str,
    ) -> dict[str, Any]:
        """Register one validated, server-managed user upload for this item."""

        asset_id = str(
            uuid5(NAMESPACE_URL, f"mainpg:{workspace_id}:{item_id}:user-upload:{content_hash}")
        )
        with self.database.sessions.begin() as session:
            if self._get_item_row(session, item_id, workspace_id) is None:
                raise LookupError("dimension canvas item not found")
            row = session.scalar(
                select(DimensionCanvasAssetRow).where(
                    DimensionCanvasAssetRow.id == asset_id,
                    DimensionCanvasAssetRow.item_id == item_id,
                    DimensionCanvasAssetRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                row = DimensionCanvasAssetRow(
                    id=asset_id,
                    workspace_id=workspace_id,
                    item_id=item_id,
                    role="user_upload",
                )
                session.add(row)
            row.managed_path = str(managed_path)
            row.content_hash = str(content_hash)
            row.width = int(width)
            row.height = int(height)
            row.content_type = str(content_type)
            row.availability = "local"
            session.flush()
            return self._asset(row)

    def list_assets(self, item_id: str, workspace_id: str) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            if self._get_item_row(session, item_id, workspace_id) is None:
                return []
            rows = session.scalars(
                select(DimensionCanvasAssetRow)
                .where(
                    DimensionCanvasAssetRow.item_id == item_id,
                    DimensionCanvasAssetRow.workspace_id == workspace_id,
                )
                .order_by(DimensionCanvasAssetRow.created_at, DimensionCanvasAssetRow.id)
            ).all()
            return [self._asset(row) for row in rows]

    def get_asset(self, asset_id: str, item_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(DimensionCanvasAssetRow).where(
                    DimensionCanvasAssetRow.id == asset_id,
                    DimensionCanvasAssetRow.item_id == item_id,
                    DimensionCanvasAssetRow.workspace_id == workspace_id,
                )
            )
            return self._asset(row) if row else None

    def find_asset(self, asset_id: str, workspace_id: str) -> dict[str, Any] | None:
        """Look up one canvas asset by id across items (server-side image proxy)."""
        with self.database.sessions() as session:
            row = session.scalar(
                select(DimensionCanvasAssetRow).where(
                    DimensionCanvasAssetRow.id == asset_id,
                    DimensionCanvasAssetRow.workspace_id == workspace_id,
                )
            )
            return self._asset(row) if row else None

    def materialize_asset(
        self,
        asset_id: str,
        item_id: str,
        workspace_id: str,
        *,
        managed_path: str,
        content_hash: str,
        width: int = 0,
        height: int = 0,
        content_type: str = "",
    ) -> dict[str, Any]:
        """Attach server-produced local evidence to one registered asset identity."""
        if not managed_path or not content_hash:
            raise ValueError("materialized asset path and hash are required")
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(DimensionCanvasAssetRow).where(
                    DimensionCanvasAssetRow.id == asset_id,
                    DimensionCanvasAssetRow.item_id == item_id,
                    DimensionCanvasAssetRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                raise LookupError("dimension canvas asset not found")
            row.managed_path = str(managed_path)
            row.content_hash = str(content_hash)
            row.width = int(width)
            row.height = int(height)
            row.content_type = str(content_type or row.content_type or "")
            row.availability = "local"
            session.flush()
            return self._asset(row)

    def mark_asset_published(
        self,
        asset_id: str,
        item_id: str,
        workspace_id: str,
        *,
        public_url: str,
        claim_token: str,
    ) -> dict[str, Any]:
        """Persist a server-published URL; client-local paths never enter this seam."""
        normalized = str(public_url or "").strip()
        if not normalized.lower().startswith("https://") or not is_safe_external_url(normalized):
            raise ValueError("published dimension asset URL must be public HTTPS")
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(DimensionCanvasAssetRow).where(
                    DimensionCanvasAssetRow.id == asset_id,
                    DimensionCanvasAssetRow.item_id == item_id,
                    DimensionCanvasAssetRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                raise LookupError("dimension canvas asset not found")
            if row.role != "rendered_dimension":
                raise CanvasStateConflict("only rendered dimension assets may be published")
            existing = str(row.source_url or "").strip()
            if existing and existing != normalized:
                raise CanvasStateConflict("dimension asset already has a different published URL")
            row.source_url = normalized
            row.availability = "published"
            claimed = session.execute(
                update(DimensionCanvasItemRow)
                .where(
                    DimensionCanvasItemRow.id == item_id,
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.render_asset_id == asset_id,
                    DimensionCanvasItemRow.state == "publishing",
                    DimensionCanvasItemRow.publish_claim_token == str(claim_token),
                )
                .values(
                    state="completed",
                    publish_claim_token="",
                    publish_claimed_at="",
                    error_code="",
                    error_message="",
                    updated_at=utc_now(),
                )
            )
            if claimed.rowcount != 1:
                raise CanvasStateConflict("dimension publish claim changed concurrently")
            session.flush()
            return self._asset(row)

    def claim_item_publish(
        self,
        item_id: str,
        render_asset_id: str,
        render_revision: int,
        workspace_id: str,
    ) -> dict[str, Any]:
        """Atomically claim one COS upload across processes using the item state."""
        with self.database.sessions.begin() as session:
            # A crashed worker's lease can be reclaimed by the next submit request;
            # active claims are never cleared merely because another service starts.
            session.execute(
                update(DimensionCanvasItemRow)
                .where(
                    DimensionCanvasItemRow.id == item_id,
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.state == "publishing",
                    DimensionCanvasItemRow.publish_claimed_at < _publish_lease_cutoff(),
                )
                .values(
                    state="completed",
                    publish_claim_token="",
                    publish_claimed_at="",
                    error_code="dimension_publish_lease_expired",
                    error_message="expired COS publication lease was reclaimed",
                    updated_at=utc_now(),
                )
            )
            claim_token = uuid4().hex
            result = session.execute(
                update(DimensionCanvasItemRow)
                .where(
                    DimensionCanvasItemRow.id == item_id,
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.state == "completed",
                    DimensionCanvasItemRow.render_asset_id == render_asset_id,
                    DimensionCanvasItemRow.render_revision == int(render_revision),
                )
                .values(
                    state="publishing",
                    publish_claim_token=claim_token,
                    publish_claimed_at=utc_now(),
                    error_code="",
                    error_message="",
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                raise CanvasStateConflict("dimension image is already publishing or changed")
            row = session.scalar(
                select(DimensionCanvasAssetRow).where(
                    DimensionCanvasAssetRow.id == render_asset_id,
                    DimensionCanvasAssetRow.item_id == item_id,
                    DimensionCanvasAssetRow.workspace_id == workspace_id,
                )
            )
            if row is None or row.availability not in {"local", "published"}:
                raise CanvasStateConflict("rendered dimension asset is not publishable")
            return {**self._asset(row), "_publish_claim_token": claim_token}

    def release_item_publish(
        self,
        item_id: str,
        render_asset_id: str,
        workspace_id: str,
        *,
        claim_token: str,
        error_message: str,
    ) -> None:
        """Make an unsuccessful publication retryable without re-rendering."""
        with self.database.sessions.begin() as session:
            session.execute(
                update(DimensionCanvasItemRow)
                .where(
                    DimensionCanvasItemRow.id == item_id,
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.render_asset_id == render_asset_id,
                    DimensionCanvasItemRow.state == "publishing",
                    DimensionCanvasItemRow.publish_claim_token == str(claim_token),
                )
                .values(
                    state="completed",
                    publish_claim_token="",
                    publish_claimed_at="",
                    error_code="dimension_publish_failed",
                    error_message=str(error_message)[:500],
                    updated_at=utc_now(),
                )
            )

    def save_item(
        self,
        item_id: str,
        expected_revision: int,
        patch: dict[str, Any],
        workspace_id: str,
    ) -> dict[str, Any]:
        unknown = set(patch) - self._EDITOR_FIELDS
        if unknown:
            raise ValueError(f"unsupported canvas fields: {sorted(unknown)}")
        with self.database.sessions.begin() as session:
            row = self._get_item_row(session, item_id, workspace_id)
            if row is None:
                raise LookupError("dimension canvas item not found")
            if int(row.item_revision) != int(expected_revision):
                raise StaleCanvasRevision(
                    f"expected revision {expected_revision}, current {row.item_revision}"
                )
            if row.state == "publishing":
                raise CanvasStateConflict("dimension image is being published")
            if "selected_source_asset_id" in patch:
                asset_id = str(patch.get("selected_source_asset_id") or "")
                if asset_id:
                    asset = session.scalar(
                        select(DimensionCanvasAssetRow).where(
                            DimensionCanvasAssetRow.id == asset_id,
                            DimensionCanvasAssetRow.item_id == row.id,
                            DimensionCanvasAssetRow.workspace_id == workspace_id,
                        )
                    )
                    if asset is None:
                        raise LookupError("dimension canvas asset not found")

            values: dict[str, Any] = {
                "item_revision": int(row.item_revision) + 1,
                "updated_at": utc_now(),
                "state": "editing",
                "render_input_hash": "",
                "rendered_input_hash": "",
                "render_asset_id": "",
                "error_code": "",
                "error_message": "",
            }
            if "selected_source_asset_id" in patch:
                values["selected_source_asset_id"] = str(patch.get("selected_source_asset_id") or "")
            if "target_slot_id" in patch:
                values["target_slot_id"] = str(patch.get("target_slot_id") or "")
            if "physical_dimensions" in patch:
                values["physical_dimensions_json"] = _dumps(patch["physical_dimensions"])
            if "annotations" in patch:
                values["annotations_json"] = _dumps(patch["annotations"])
            settings = (
                dict(patch.get("canvas_settings") or {})
                if "canvas_settings" in patch
                else _loads(row.canvas_settings_json, {})
            )
            for key in (
                "_render_input_hash",
                "_rendered_input_hash",
                "_render_editor_snapshot",
                "_render_asset_snapshot",
                "_render_revision",
            ):
                settings.pop(key, None)
            values["canvas_settings_json"] = _dumps(settings)
            result = session.execute(
                update(DimensionCanvasItemRow)
                .where(
                    DimensionCanvasItemRow.id == item_id,
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.item_revision == expected_revision,
                )
                .values(**values)
            )
            if result.rowcount != 1:
                raise StaleCanvasRevision("canvas item changed concurrently")
            session.flush()
            saved = self._get_item_row(session, item_id, workspace_id)
            assert saved is not None
            return self._item(saved)

    def mark_rendering(
        self,
        item_id: str,
        expected_revision: int,
        workspace_id: str,
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = self._get_item_row(session, item_id, workspace_id)
            if row is None:
                raise LookupError("dimension canvas item not found")
            if int(row.item_revision) != int(expected_revision):
                raise StaleCanvasRevision(
                    f"expected revision {expected_revision}, current {row.item_revision}"
                )
            asset = self._selected_asset_row(session, row, workspace_id)
            render_revision = int(row.render_revision) + 1
            input_hash = self._input_hash(row, asset)
            settings = _loads(row.canvas_settings_json, {})
            settings.update(
                {
                    "_render_input_hash": input_hash,
                    "_render_editor_snapshot": self._editor_snapshot(row),
                    "_render_asset_snapshot": self._asset(asset),
                    "_render_revision": render_revision,
                }
            )
            settings.pop("_rendered_input_hash", None)
            result = session.execute(
                update(DimensionCanvasItemRow)
                .where(
                    DimensionCanvasItemRow.id == item_id,
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.item_revision == expected_revision,
                )
                .values(
                    state="rendering",
                    item_revision=int(row.item_revision) + 1,
                    render_revision=render_revision,
                    render_input_hash=input_hash,
                    rendered_input_hash="",
                    render_asset_id="",
                    canvas_settings_json=_dumps(settings),
                    error_code="",
                    error_message="",
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                raise StaleCanvasRevision("canvas item changed concurrently")
            session.flush()
            saved = self._get_item_row(session, item_id, workspace_id)
            assert saved is not None
            return self._item(saved)

    def finish_render(
        self,
        item_id: str,
        render_revision: int,
        rendered_asset: dict[str, Any],
        workspace_id: str,
    ) -> dict[str, Any] | None:
        """Finish only the exact frozen render; stale worker results are discarded."""
        with self.database.sessions.begin() as session:
            row = self._get_item_row(session, item_id, workspace_id)
            if row is None:
                return None
            settings = _loads(row.canvas_settings_json, {})
            frozen_hash = str(row.render_input_hash or "")
            asset = self._selected_asset_row(session, row, workspace_id)
            current_hash = self._input_hash(row, asset)
            if (
                row.state != "rendering"
                or int(row.render_revision) != int(render_revision)
                or not frozen_hash
                or current_hash != frozen_hash
            ):
                return None
            output = DimensionCanvasAssetRow(
                id=str(rendered_asset.get("id") or uuid4()),
                workspace_id=workspace_id,
                item_id=item_id,
                role="rendered_dimension",
                source_url=str(rendered_asset.get("source_url") or rendered_asset.get("url") or ""),
                managed_path=str(rendered_asset.get("managed_path") or ""),
                content_hash=str(rendered_asset.get("content_hash") or ""),
                width=int(rendered_asset.get("width") or 0),
                height=int(rendered_asset.get("height") or 0),
                content_type=str(rendered_asset.get("content_type") or "image/jpeg"),
                availability=str(rendered_asset.get("availability") or "local"),
            )
            session.add(output)
            session.flush()
            settings["_rendered_input_hash"] = frozen_hash
            result = session.execute(
                update(DimensionCanvasItemRow)
                .where(
                    DimensionCanvasItemRow.id == item_id,
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.state == "rendering",
                    DimensionCanvasItemRow.render_revision == render_revision,
                )
                .values(
                    state="completed",
                    render_asset_id=output.id,
                    rendered_input_hash=frozen_hash,
                    item_revision=int(row.item_revision) + 1,
                    canvas_settings_json=_dumps(settings),
                    error_code="",
                    error_message="",
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                session.delete(output)
                return None
            session.flush()
            saved = self._get_item_row(session, item_id, workspace_id)
            return self._item(saved) if saved else None

    def fail_render(
        self,
        item_id: str,
        render_revision: int,
        error_code: str,
        error_message: str,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        with self.database.sessions.begin() as session:
            row = self._get_item_row(session, item_id, workspace_id)
            if row is None:
                return None
            result = session.execute(
                update(DimensionCanvasItemRow)
                .where(
                    DimensionCanvasItemRow.id == item_id,
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.state == "rendering",
                    DimensionCanvasItemRow.render_revision == render_revision,
                )
                .values(
                    state="render_retryable",
                    item_revision=int(row.item_revision) + 1,
                    error_code=str(error_code)[:64],
                    error_message=str(error_message)[:500],
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                return None
            session.flush()
            saved = self._get_item_row(session, item_id, workspace_id)
            return self._item(saved) if saved else None

    def recover_rendering_items(self, workspace_id: str | None = None) -> int:
        values = {
            "state": "render_retryable",
            "error_code": "render_interrupted",
            "error_message": "render worker stopped before completion",
            "updated_at": utc_now(),
        }
        with self.database.sessions.begin() as session:
            statement = update(DimensionCanvasItemRow).where(DimensionCanvasItemRow.state == "rendering")
            if workspace_id is not None:
                statement = statement.where(DimensionCanvasItemRow.workspace_id == workspace_id)
            result = session.execute(statement.values(**values))
            publishing = update(DimensionCanvasItemRow).where(DimensionCanvasItemRow.state == "publishing")
            if workspace_id is not None:
                publishing = publishing.where(DimensionCanvasItemRow.workspace_id == workspace_id)
            publishing = publishing.where(
                DimensionCanvasItemRow.publish_claimed_at < _publish_lease_cutoff()
            )
            recovered_publish = session.execute(
                publishing.values(
                    state="completed",
                    publish_claim_token="",
                    publish_claimed_at="",
                    error_code="dimension_publish_interrupted",
                    error_message="COS publication stopped before completion; retry review submission",
                    updated_at=utc_now(),
                )
            )
            return int(result.rowcount or 0) + int(recovered_publish.rowcount or 0)

    def create_change_set(
        self,
        batch_id: str,
        completed_item_ids: list[str],
        idempotency_key: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        requested = sorted(set(str(value) for value in completed_item_ids))
        if not requested:
            raise ValueError("at least one completed item is required")
        with self.database.sessions.begin() as session:
            existing = session.scalar(
                select(DimensionCanvasChangeSetRow).where(
                    DimensionCanvasChangeSetRow.workspace_id == workspace_id,
                    DimensionCanvasChangeSetRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return self._change_set_with_items(session, existing)
            batch = session.scalar(
                select(DimensionCanvasBatchRow).where(
                    DimensionCanvasBatchRow.id == batch_id,
                    DimensionCanvasBatchRow.workspace_id == workspace_id,
                )
            )
            if batch is None:
                raise LookupError("dimension canvas batch not found")
            items = session.scalars(
                select(DimensionCanvasItemRow).where(
                    DimensionCanvasItemRow.id.in_(requested),
                    DimensionCanvasItemRow.batch_id == batch_id,
                    DimensionCanvasItemRow.workspace_id == workspace_id,
                    DimensionCanvasItemRow.state == "completed",
                )
            ).all()
            by_id = {row.id: row for row in items}
            if set(by_id) != set(requested):
                raise CanvasStateConflict("completed canvas items changed before review submission")
            change_set_id = str(uuid5(NAMESPACE_URL, f"dimension-change-set:{workspace_id}:{idempotency_key}"))
            inserted = session.execute(
                sqlite_insert(DimensionCanvasChangeSetRow)
                .values(
                    id=change_set_id,
                    workspace_id=workspace_id,
                    batch_id=batch_id,
                    source_task_id=batch.source_task_id,
                    status="pending_review",
                    idempotency_key=idempotency_key,
                    counts_json=_dumps(
                        {"item_count": len(requested), "accepted_count": 0, "conflict_count": 0}
                    ),
                    created_at=utc_now(),
                    accepted_at="",
                )
                .on_conflict_do_nothing(index_elements=["workspace_id", "idempotency_key"])
            )
            change_set = session.scalar(
                select(DimensionCanvasChangeSetRow).where(
                    DimensionCanvasChangeSetRow.workspace_id == workspace_id,
                    DimensionCanvasChangeSetRow.idempotency_key == idempotency_key,
                )
            )
            if change_set is None:
                raise CanvasStateConflict("review submission claim could not be loaded")
            if inserted.rowcount != 1:
                return self._change_set_with_items(session, change_set)
            for item_id in requested:
                item = by_id[item_id]
                settings = _loads(item.canvas_settings_json, {})
                selected = self._selected_asset_row(session, item, workspace_id)
                if self._input_hash(item, selected) != str(item.rendered_input_hash or ""):
                    raise CanvasStateConflict("canvas item changed after render")
                replacement = session.scalar(
                    select(DimensionCanvasAssetRow).where(
                        DimensionCanvasAssetRow.id == item.render_asset_id,
                        DimensionCanvasAssetRow.item_id == item.id,
                        DimensionCanvasAssetRow.workspace_id == workspace_id,
                    )
                )
                if replacement is None:
                    raise CanvasStateConflict("rendered asset not found")
                if (
                    replacement.role != "rendered_dimension"
                    or replacement.availability not in {"local", "published"}
                    or not str(replacement.managed_path or "").strip()
                    or not str(replacement.content_hash or "").strip()
                ):
                    raise CanvasStateConflict("rendered dimension asset is not available locally")
                draft = session.scalar(
                    select(ProductDraftRow).where(
                        ProductDraftRow.id == item.product_draft_id,
                        ProductDraftRow.workspace_id == workspace_id,
                    )
                )
                if draft is None:
                    raise LookupError("product draft not found")
                if int(draft.preview_revision or 0) != int(item.source_preview_revision or 0):
                    raise CanvasStateConflict("product preview changed after dimension canvas import")
                base_slot, base_dimensions = self._current_review_inputs(session, item, draft)
                base_asset = {
                    "slot": base_slot,
                    "slot_hash": _hash(base_slot),
                    "physical_dimensions_hash": _hash(base_dimensions),
                    "render_revision": int(item.render_revision),
                    "render_input_hash": str(item.rendered_input_hash or ""),
                }
                session.add(
                    DimensionCanvasChangeItemRow(
                        id=str(uuid4()),
                        workspace_id=workspace_id,
                        change_set_id=change_set.id,
                        dimension_item_id=item.id,
                        product_draft_id=item.product_draft_id,
                        base_preview_revision=int(draft.preview_revision or 0),
                        target_slot_id=item.target_slot_id,
                        base_asset_json=_dumps(base_asset),
                        replacement_asset_json=_dumps(self._asset(replacement)),
                        physical_dimensions_json=item.physical_dimensions_json,
                        status="pending",
                    )
                )
            notification = DimensionCanvasNotificationRow(
                id=str(uuid4()),
                workspace_id=workspace_id,
                change_set_id=change_set.id,
                payload_json=_dumps(
                    {
                        "change_set_id": change_set.id,
                        "source_task_id": batch.source_task_id,
                        "item_count": len(requested),
                    }
                ),
            )
            session.add(notification)
            session.flush()
            return self._change_set_with_items(session, change_set)

    def get_change_set(self, change_set_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(DimensionCanvasChangeSetRow).where(
                    DimensionCanvasChangeSetRow.id == change_set_id,
                    DimensionCanvasChangeSetRow.workspace_id == workspace_id,
                )
            )
            return self._change_set_with_items(session, row) if row else None

    def get_change_item(self, change_item_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(DimensionCanvasChangeItemRow).where(
                    DimensionCanvasChangeItemRow.id == str(change_item_id),
                    DimensionCanvasChangeItemRow.workspace_id == str(workspace_id),
                )
            )
            return self._change_item(row) if row else None

    def accept_change_item(
        self,
        change_item_id: str,
        workspace_id: str,
        preview_managed_path: str | None = None,
        dimension_media_asset_id: str = "",
    ) -> dict[str, Any]:
        """Atomically merge one immutable slot patch; no client revision is trusted."""
        with self.database.sessions.begin() as session:
            change_item = session.scalar(
                select(DimensionCanvasChangeItemRow).where(
                    DimensionCanvasChangeItemRow.id == change_item_id,
                    DimensionCanvasChangeItemRow.workspace_id == workspace_id,
                )
            )
            if change_item is None:
                raise LookupError("dimension change item not found")
            if change_item.status == "accepted":
                return self._change_item(change_item)
            if change_item.status != "pending":
                raise CanvasStateConflict(f"dimension change item is {change_item.status}")
            canvas_item = self._get_item_row(session, change_item.dimension_item_id, workspace_id)
            draft = session.scalar(
                select(ProductDraftRow).where(
                    ProductDraftRow.id == change_item.product_draft_id,
                    ProductDraftRow.workspace_id == workspace_id,
                )
            )
            if canvas_item is None or draft is None:
                raise LookupError("dimension change target not found")
            base = _loads(change_item.base_asset_json, {})
            current_slot, current_dimensions = self._current_review_inputs(
                session,
                canvas_item,
                draft,
                target_slot_id=change_item.target_slot_id,
            )
            conflict: dict[str, Any] = {}
            if _hash(current_slot) != str(base.get("slot_hash") or ""):
                conflict["target_slot"] = "changed"
            if _hash(current_dimensions) != str(base.get("physical_dimensions_hash") or ""):
                conflict["physical_dimensions"] = "changed"
            if conflict:
                change_item.status = "conflict"
                change_item.conflict_json = _dumps(conflict)
                change_item.resolved_at = utc_now()
                self._refresh_change_set_counts(session, change_item.change_set_id, workspace_id)
                session.flush()
                return self._change_item(change_item)

            replacement = _loads(change_item.replacement_asset_json, {})
            replacement_id = str(replacement.get("id") or "")
            managed_asset = session.scalar(
                select(DimensionCanvasAssetRow).where(
                    DimensionCanvasAssetRow.id == replacement_id,
                    DimensionCanvasAssetRow.item_id == canvas_item.id,
                    DimensionCanvasAssetRow.workspace_id == workspace_id,
                )
            )
            if managed_asset is None or managed_asset.role != "rendered_dimension":
                raise CanvasStateConflict("replacement asset is not a managed canvas render")
            if (
                managed_asset.availability not in {"local", "published"}
                or not str(managed_asset.managed_path or "").strip()
                or not str(managed_asset.content_hash or "").strip()
            ):
                raise CanvasStateConflict("replacement dimension asset is not available locally")
            overrides = _loads(draft.preview_overrides_json, {})
            preview_asset = self._register_dimension_preview_asset(
                session,
                canvas_item,
                managed_asset,
                workspace_id,
                preview_managed_path=preview_managed_path,
            )
            manifest = self._manifest_for_acceptance(
                session, canvas_item, draft, overrides, workspace_id
            )
            overrides[MANIFEST_KEY] = replace_carousel_slot(
                manifest,
                change_item.target_slot_id,
                preview_asset.id,
            ).as_dict()
            # Once a v2 manifest exists, the semantic slot is owned by its stable
            # asset ID. A stale legacy URL patch must not shadow the accepted image.
            patches = dict(overrides.get("image_slot_overrides") or {})
            patches.pop(change_item.target_slot_id, None)
            if patches:
                overrides["image_slot_overrides"] = patches
            else:
                overrides.pop("image_slot_overrides", None)
            current_revision = int(draft.preview_revision or 0)
            updated = session.execute(
                update(ProductDraftRow)
                .where(
                    ProductDraftRow.id == draft.id,
                    ProductDraftRow.workspace_id == workspace_id,
                    ProductDraftRow.preview_revision == current_revision,
                )
                .values(
                    preview_overrides_json=_dumps(overrides),
                    preview_revision=current_revision + 1,
                    updated_at=utc_now(),
                )
            )
            if updated.rowcount != 1:
                change_item.status = "conflict"
                change_item.conflict_json = _dumps({"preview_revision": "changed_concurrently"})
                change_item.resolved_at = utc_now()
                self._refresh_change_set_counts(session, change_item.change_set_id, workspace_id)
                session.flush()
                return self._change_item(change_item)
            change_item.status = "accepted"
            change_item.resolved_at = utc_now()
            change_item.conflict_json = "{}"
            if dimension_media_asset_id:
                self._replace_dimension_binding(
                    session,
                    workspace_id,
                    draft.id,
                    change_item.target_slot_id,
                    dimension_media_asset_id,
                )
            self._refresh_change_set_counts(session, change_item.change_set_id, workspace_id)
            session.flush()
            return self._change_item(change_item)

    def _replace_dimension_binding(
        self,
        session,
        workspace_id: str,
        product_draft_id: int,
        slot_id: str,
        media_asset_id: str,
    ) -> None:
        """Activate the new dimension binding and deactivate the former one."""
        media = session.scalar(
            select(MediaAssetRow).where(
                MediaAssetRow.id == media_asset_id,
                MediaAssetRow.workspace_id == workspace_id,
            )
        )
        if media is None:
            return
        source_identity = str(media.content_hash or media.id or "")
        session.execute(
            update(MediaBindingRow)
            .where(
                MediaBindingRow.workspace_id == workspace_id,
                MediaBindingRow.product_draft_id == int(product_draft_id),
                MediaBindingRow.role == "dimension",
                MediaBindingRow.slot_id == str(slot_id or ""),
                MediaBindingRow.active == 1,
            )
            .values(active=0, updated_at=utc_now())
        )
        binding_key = media_binding_key(
            product_draft_id, "dimension", str(slot_id or ""), "", "", source_identity, 0
        )
        existing = session.scalar(
            select(MediaBindingRow).where(
                MediaBindingRow.workspace_id == workspace_id,
                MediaBindingRow.binding_key == binding_key,
            )
        )
        if existing is None:
            session.add(
                MediaBindingRow(
                    workspace_id=workspace_id,
                    asset_id=media_asset_id,
                    product_draft_id=int(product_draft_id),
                    role="dimension",
                    slot_id=str(slot_id or ""),
                    sort_order=0,
                    binding_key=binding_key,
                    active=1,
                )
            )
        else:
            existing.asset_id = media_asset_id
            existing.active = 1
            existing.updated_at = utc_now()

    def reject_change_item(self, change_item_id: str, workspace_id: str) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(DimensionCanvasChangeItemRow).where(
                    DimensionCanvasChangeItemRow.id == change_item_id,
                    DimensionCanvasChangeItemRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                raise LookupError("dimension change item not found")
            if row.status == "pending":
                row.status = "rejected"
                row.resolved_at = utc_now()
                self._refresh_change_set_counts(session, row.change_set_id, workspace_id)
            session.flush()
            return self._change_item(row)

    def list_notifications(self, workspace_id: str, after: str = "") -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            statement = select(DimensionCanvasNotificationRow).where(
                DimensionCanvasNotificationRow.workspace_id == workspace_id
            )
            if after:
                statement = statement.where(DimensionCanvasNotificationRow.created_at > after)
            rows = session.scalars(statement.order_by(DimensionCanvasNotificationRow.created_at)).all()
            return [self._notification(row) for row in rows]

    def mark_notification_read(self, notification_id: str, workspace_id: str) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(DimensionCanvasNotificationRow).where(
                    DimensionCanvasNotificationRow.id == notification_id,
                    DimensionCanvasNotificationRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                raise LookupError("dimension notification not found")
            if not row.read_at:
                row.read_at = utc_now()
            session.flush()
            return self._notification(row)

    def _register_task_assets(
        self,
        session,
        item: DimensionCanvasItemRow,
        task_item: ProcessingTaskItemRow,
        draft: ProductDraftRow,
        workspace_id: str,
    ) -> None:
        if int(draft.media_contract_version or 1) >= 2:
            self._register_v2_media_assets(session, item, task_item, draft, workspace_id)
            return
        result = _loads(task_item.result_json, {})
        candidates: list[tuple[str, str]] = []
        if draft.image_path:
            candidates.append(("source", str(draft.image_path)))
        if draft.image_url:
            candidates.append(("source", str(draft.image_url)))
        if task_item.image_url:
            candidates.append(("task_source", str(task_item.image_url)))
        manifest = result.get("image_manifest") or []
        if isinstance(manifest, list):
            for entry in manifest:
                if isinstance(entry, dict) and entry.get("value"):
                    candidates.append((str(entry.get("role") or entry.get("slot_id") or "generated"), str(entry["value"])))
        for index, value in enumerate(result.get("carousel_image_paths") or []):
            if value:
                candidates.append((f"carousel_{index + 1}", str(value)))
        unique = list(dict.fromkeys(candidates))
        for index, (role, value) in enumerate(unique):
            is_remote = value.lower().startswith(("http://", "https://"))
            asset_id = str(uuid5(NAMESPACE_URL, f"mainpg:{workspace_id}:{item.id}:{role}:{index}:{value}"))
            session.add(
                DimensionCanvasAssetRow(
                    id=asset_id,
                    workspace_id=workspace_id,
                    item_id=item.id,
                    role=role[:64],
                    source_url=value if is_remote else "",
                    managed_path="" if is_remote else value,
                    availability="metadata",
                )
            )

    def _register_v2_media_assets(
        self,
        session,
        item: DimensionCanvasItemRow,
        task_item: ProcessingTaskItemRow,
        draft: ProductDraftRow,
        workspace_id: str,
    ) -> None:
        """Register V2 canvas assets by asset_id from the manifest and bindings.

        Ready assets stay ``metadata`` until the service snapshots their bytes into
        the dimension workspace; non-ready assets are marked ``unavailable`` so the
        editor never yields a broken URL.
        """
        result = _loads(task_item.result_json, {})
        manifest = result.get("image_manifest_v2") or {}
        ordered: list[tuple[str, str]] = []

        def add(asset_id: Any, role: str) -> None:
            normalized = str(asset_id or "").strip()
            if not normalized or any(existing == normalized for existing, _ in ordered):
                return
            ordered.append((normalized, str(role)[:64]))

        add(manifest.get("main_asset_id"), "source")
        for index, value in enumerate(manifest.get("carousel_asset_ids") or []):
            add(value, f"carousel_{index + 1}")
        for index, value in enumerate(manifest.get("detail_asset_ids") or []):
            add(value, f"detail_{index + 1}")
        semantic = manifest.get("semantic_asset_ids") or {}
        if isinstance(semantic, dict):
            for slot_id, value in semantic.items():
                if isinstance(value, str):
                    add(value, str(slot_id))
        bindings = session.scalars(
            select(MediaBindingRow)
            .where(
                MediaBindingRow.workspace_id == workspace_id,
                MediaBindingRow.product_draft_id == draft.id,
                MediaBindingRow.active == 1,
            )
            .order_by(MediaBindingRow.sort_order, MediaBindingRow.created_at, MediaBindingRow.id)
        ).all()
        for binding in bindings:
            role = str(binding.role or "gallery")
            if role == "sku" and binding.sku_id:
                role = f"sku_{binding.sku_id}"
            add(binding.asset_id, role)

        for asset_id, role in ordered:
            media = session.scalar(
                select(MediaAssetRow).where(
                    MediaAssetRow.id == asset_id,
                    MediaAssetRow.workspace_id == workspace_id,
                )
            )
            if media is None:
                continue
            ready = str(media.status or "") == "ready"
            session.add(
                DimensionCanvasAssetRow(
                    id=str(uuid5(NAMESPACE_URL, f"mainpg:{workspace_id}:{item.id}:v2:{asset_id}")),
                    workspace_id=workspace_id,
                    item_id=item.id,
                    role=role,
                    source_url="",
                    managed_path="",
                    source_media_asset_id=asset_id,
                    content_hash=str(media.content_hash or "") if ready else "",
                    width=int(media.width or 0),
                    height=int(media.height or 0),
                    content_type=str(media.content_type or ""),
                    availability="metadata" if ready else "unavailable",
                )
            )

    @staticmethod
    def _editor_snapshot(row: DimensionCanvasItemRow) -> dict[str, Any]:
        return {
            "selected_source_asset_id": row.selected_source_asset_id,
            "target_slot_id": row.target_slot_id,
            "physical_dimensions": _loads(row.physical_dimensions_json, {}),
            "annotations": _loads(row.annotations_json, []),
            "canvas_settings": {
                key: value
                for key, value in _loads(row.canvas_settings_json, {}).items()
                if not str(key).startswith("_render")
            },
        }

    def _input_hash(self, row: DimensionCanvasItemRow, asset: DimensionCanvasAssetRow) -> str:
        return _hash({"editor": self._editor_snapshot(row), "asset": _asset_identity(asset)})

    @staticmethod
    def _get_item_row(session, item_id: str, workspace_id: str) -> DimensionCanvasItemRow | None:
        return session.scalar(
            select(DimensionCanvasItemRow).where(
                DimensionCanvasItemRow.id == item_id,
                DimensionCanvasItemRow.workspace_id == workspace_id,
            )
        )

    @staticmethod
    def _selected_asset_row(session, item: DimensionCanvasItemRow, workspace_id: str) -> DimensionCanvasAssetRow:
        if not item.selected_source_asset_id:
            raise CanvasStateConflict("selected source asset is required")
        asset = session.scalar(
            select(DimensionCanvasAssetRow).where(
                DimensionCanvasAssetRow.id == item.selected_source_asset_id,
                DimensionCanvasAssetRow.item_id == item.id,
                DimensionCanvasAssetRow.workspace_id == workspace_id,
            )
        )
        if asset is None:
            raise LookupError("dimension canvas asset not found")
        return asset

    @staticmethod
    def _task_result(session, item: DimensionCanvasItemRow) -> dict[str, Any]:
        task_item = session.scalar(
            select(ProcessingTaskItemRow).where(
                ProcessingTaskItemRow.id == item.task_item_id,
                ProcessingTaskItemRow.task_id == item.task_id,
            )
        )
        return _loads(task_item.result_json, {}) if task_item else {}

    @staticmethod
    def _register_dimension_preview_asset(
        session,
        item: DimensionCanvasItemRow,
        asset: DimensionCanvasAssetRow,
        workspace_id: str,
        *,
        preview_managed_path: str | None = None,
    ) -> PreviewImageAssetRow:
        identity_hash = hashlib.sha256(f"dimension:{asset.id}".encode("utf-8")).hexdigest()
        preview_id = str(
            uuid5(
                NAMESPACE_URL,
                f"preview-asset:{workspace_id}:{item.task_id}:{item.product_draft_id}:{identity_hash}",
            )
        )
        session.execute(
            sqlite_insert(PreviewImageAssetRow)
            .values(
                id=preview_id,
                workspace_id=workspace_id,
                task_id=item.task_id,
                product_draft_id=item.product_draft_id,
                origin="dimension",
                source_asset_id=asset.id,
                identity_hash=identity_hash,
                managed_path=str(preview_managed_path or asset.managed_path),
                source_url="",
                content_hash=asset.content_hash,
                content_type=asset.content_type or "image/jpeg",
                byte_size=0,
                width=int(asset.width or 0),
                height=int(asset.height or 0),
                availability="local",
                public_url="",
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "workspace_id",
                    "task_id",
                    "product_draft_id",
                    "identity_hash",
                ]
            )
        )
        row = session.scalar(
            select(PreviewImageAssetRow).where(
                PreviewImageAssetRow.workspace_id == workspace_id,
                PreviewImageAssetRow.task_id == item.task_id,
                PreviewImageAssetRow.product_draft_id == item.product_draft_id,
                PreviewImageAssetRow.identity_hash == identity_hash,
            )
        )
        if row is None:
            raise CanvasStateConflict("dimension preview asset registration failed")
        return row

    @staticmethod
    def _register_legacy_preview_asset(
        session,
        item: DimensionCanvasItemRow,
        value: str,
        origin: str,
        workspace_id: str,
    ) -> PreviewImageAssetRow | None:
        normalized = str(value or "").strip()
        if not normalized:
            return None
        remote = normalized.lower().startswith(("http://", "https://"))
        # A browser-only /pp-media URL is not a storage authority. It should have
        # been projected by PreviewImageService already; do not invent a local path.
        if normalized.startswith("/pp-media/"):
            return None
        identity_hash = hashlib.sha256(
            (("remote:" if remote else "managed:") + normalized).encode("utf-8")
        ).hexdigest()
        asset_id = str(
            uuid5(
                NAMESPACE_URL,
                f"preview-asset:{workspace_id}:{item.task_id}:{item.product_draft_id}:{identity_hash}",
            )
        )
        suffix = normalized.lower().split("?", 1)[0].rsplit(".", 1)[-1]
        content_type = {
            "png": "image/png",
            "webp": "image/webp",
        }.get(suffix, "image/jpeg")
        session.execute(
            sqlite_insert(PreviewImageAssetRow)
            .values(
                id=asset_id,
                workspace_id=workspace_id,
                task_id=item.task_id,
                product_draft_id=item.product_draft_id,
                origin=origin,
                source_asset_id="",
                identity_hash=identity_hash,
                managed_path="" if remote else normalized,
                source_url=normalized if remote else "",
                content_hash="",
                content_type=content_type,
                byte_size=0,
                width=0,
                height=0,
                availability="materializing" if remote else "local",
                public_url="",
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "workspace_id",
                    "task_id",
                    "product_draft_id",
                    "identity_hash",
                ]
            )
        )
        return session.scalar(
            select(PreviewImageAssetRow).where(
                PreviewImageAssetRow.workspace_id == workspace_id,
                PreviewImageAssetRow.task_id == item.task_id,
                PreviewImageAssetRow.product_draft_id == item.product_draft_id,
                PreviewImageAssetRow.identity_hash == identity_hash,
            )
        )

    def _manifest_for_acceptance(
        self,
        session,
        item: DimensionCanvasItemRow,
        draft: ProductDraftRow,
        overrides: dict[str, Any],
        workspace_id: str,
    ) -> PreviewImageManifest:
        if MANIFEST_KEY in overrides:
            return PreviewImageManifest.from_value(overrides.get(MANIFEST_KEY))

        result = self._task_result(session, item)
        source_values = {
            str(value or "").strip()
            for value in result.get("source_image_urls") or []
            if str(value or "").strip()
        }
        semantic_values: list[str] = []
        patches = overrides.get("image_slot_overrides") or {}
        for entry in result.get("image_manifest") or []:
            if not isinstance(entry, dict):
                continue
            slot_id = str(entry.get("slot_id") or "")
            patched = patches.get(slot_id) if isinstance(patches, dict) else None
            value = str((patched or {}).get("url") or entry.get("value") or "").strip()
            if value:
                semantic_values.append(value)
        if "carousel_images" in overrides:
            carousel_values = [str(value or "").strip() for value in overrides.get("carousel_images") or []]
        else:
            carousel_values = semantic_values or [
                str(value or "").strip() for value in result.get("carousel_image_paths") or []
            ]
            summary = str(result.get("grid_image_summary_path") or "").strip()
            if summary:
                carousel_values.append(summary)
        carousel_values = list(dict.fromkeys(value for value in carousel_values if value))
        main_value = str(overrides.get("main_image") or "").strip()
        if not main_value and carousel_values:
            main_value = carousel_values[0]
        if main_value and main_value not in carousel_values:
            carousel_values.insert(0, main_value)
        detail_values = (
            [str(value or "").strip() for value in overrides.get("detail_images") or []]
            if "detail_images" in overrides
            else [str(value or "").strip() for value in result.get("detail_image_paths") or []]
        )

        def register(value: str) -> str:
            row = self._register_legacy_preview_asset(
                session,
                item,
                value,
                "source" if value in source_values else "generated",
                workspace_id,
            )
            return str(row.id) if row is not None else ""

        carousel_ids = [asset_id for value in carousel_values if (asset_id := register(value))]
        detail_ids = [asset_id for value in detail_values if (asset_id := register(value))]
        main_id = register(main_value) if main_value else ""
        return PreviewImageManifest.from_value(
            {
                "main_asset_id": main_id,
                "carousel_asset_ids": carousel_ids,
                "detail_asset_ids": detail_ids,
                "semantic_asset_ids": {
                    slot_id: carousel_ids[index]
                    for slot_id, index in SLOT_INDEX.items()
                    if index < len(carousel_ids)
                },
            }
        )

    def _current_review_inputs(
        self,
        session,
        item: DimensionCanvasItemRow,
        draft: ProductDraftRow,
        *,
        target_slot_id: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        slot_id = str(target_slot_id or item.target_slot_id)
        result = self._task_result(session, item)
        overrides = _loads(draft.preview_overrides_json, {})
        if MANIFEST_KEY in overrides:
            manifest = PreviewImageManifest.from_value(overrides.get(MANIFEST_KEY))
            index = SLOT_INDEX.get(slot_id)
            asset_id = (
                manifest.carousel_asset_ids[index]
                if index is not None and index < len(manifest.carousel_asset_ids)
                else ""
            )
            dimensions = overrides.get("physical_dimensions") or result.get("physical_dimensions") or {}
            return ({"asset_id": asset_id} if asset_id else {}), dimensions
        slot_patch = dict((overrides.get("image_slot_overrides") or {}).get(slot_id) or {})
        if slot_patch:
            slot = slot_patch
        else:
            slot = {}
            for entry in result.get("image_manifest") or []:
                if isinstance(entry, dict) and str(entry.get("slot_id") or "") == slot_id:
                    slot = {"url": str(entry.get("value") or "")}
                    break
            if not slot and slot_id == "carousel.dimension_background":
                values = result.get("carousel_image_paths") or []
                if len(values) > 3:
                    slot = {"url": str(values[3] or "")}
        dimensions = overrides.get("physical_dimensions") or result.get("physical_dimensions") or {}
        return slot, dimensions

    @staticmethod
    def _batch_counts(session, batch_id: str, workspace_id: str) -> dict[str, int]:
        rows = session.scalars(
            select(DimensionCanvasItemRow).where(
                DimensionCanvasItemRow.batch_id == batch_id,
                DimensionCanvasItemRow.workspace_id == workspace_id,
            )
        ).all()
        counts: dict[str, int] = {"total_count": len(rows)}
        for row in rows:
            key = f"{row.state}_count"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _change_set_with_items(self, session, row: DimensionCanvasChangeSetRow) -> dict[str, Any]:
        items = session.scalars(
            select(DimensionCanvasChangeItemRow)
            .where(
                DimensionCanvasChangeItemRow.change_set_id == row.id,
                DimensionCanvasChangeItemRow.workspace_id == row.workspace_id,
            )
            .order_by(DimensionCanvasChangeItemRow.created_at, DimensionCanvasChangeItemRow.id)
        ).all()
        return {**self._change_set(row), "items": [self._change_item(item) for item in items]}

    def _refresh_change_set_counts(self, session, change_set_id: str, workspace_id: str) -> None:
        change_set = session.scalar(
            select(DimensionCanvasChangeSetRow).where(
                DimensionCanvasChangeSetRow.id == change_set_id,
                DimensionCanvasChangeSetRow.workspace_id == workspace_id,
            )
        )
        if change_set is None:
            return
        rows = session.scalars(
            select(DimensionCanvasChangeItemRow).where(
                DimensionCanvasChangeItemRow.change_set_id == change_set_id,
                DimensionCanvasChangeItemRow.workspace_id == workspace_id,
            )
        ).all()
        counts = {
            "item_count": len(rows),
            "accepted_count": sum(row.status == "accepted" for row in rows),
            "conflict_count": sum(row.status == "conflict" for row in rows),
            "rejected_count": sum(row.status == "rejected" for row in rows),
            "pending_count": sum(row.status == "pending" for row in rows),
        }
        change_set.counts_json = _dumps(counts)
        if counts["pending_count"] == 0:
            change_set.status = "resolved"
            change_set.accepted_at = utc_now()

    @staticmethod
    def _batch(row: DimensionCanvasBatchRow) -> dict[str, Any]:
        counts = _loads(row.counts_json, {})
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "source_task_id": row.source_task_id,
            "status": row.status,
            "counts": counts,
            **counts,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _item(row: DimensionCanvasItemRow) -> dict[str, Any]:
        settings = _loads(row.canvas_settings_json, {})
        public_settings = {key: value for key, value in settings.items() if not str(key).startswith("_render")}
        return {
            "id": row.id,
            "batch_id": row.batch_id,
            "workspace_id": row.workspace_id,
            "task_id": row.task_id,
            "task_item_id": row.task_item_id,
            "product_draft_id": row.product_draft_id,
            "skc": row.skc,
            "source_preview_revision": row.source_preview_revision,
            "selected_source_asset_id": row.selected_source_asset_id,
            "target_slot_id": row.target_slot_id,
            "physical_dimensions": _loads(row.physical_dimensions_json, {}),
            "annotations": _loads(row.annotations_json, []),
            "canvas_settings": public_settings,
            "state": row.state,
            "item_revision": row.item_revision,
            "render_revision": row.render_revision,
            "render_asset_id": row.render_asset_id,
            "render_input_hash": str(row.render_input_hash or ""),
            "rendered_input_hash": str(row.rendered_input_hash or ""),
            "error_code": row.error_code,
            "error_message": row.error_message,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _asset(row: DimensionCanvasAssetRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "item_id": row.item_id,
            "role": row.role,
            "source_url": row.source_url,
            "managed_path": row.managed_path,
            "source_media_asset_id": row.source_media_asset_id,
            "content_hash": row.content_hash,
            "width": row.width,
            "height": row.height,
            "content_type": row.content_type,
            "availability": row.availability,
            "created_at": row.created_at,
        }

    @classmethod
    def _item_with_result(
        cls,
        row: DimensionCanvasItemRow,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        item = cls._item(row)
        merged = prefill_physical_dimensions(
            {**dict(result or {}), "physical_dimensions": item.get("physical_dimensions") or {}}
        )
        item["physical_dimensions"] = merged.model_dump(mode="json")
        return item

    @staticmethod
    def _change_set(row: DimensionCanvasChangeSetRow) -> dict[str, Any]:
        counts = _loads(row.counts_json, {})
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "batch_id": row.batch_id,
            "source_task_id": row.source_task_id,
            "status": row.status,
            "idempotency_key": row.idempotency_key,
            **counts,
            "created_at": row.created_at,
            "accepted_at": row.accepted_at,
        }

    @staticmethod
    def _change_item(row: DimensionCanvasChangeItemRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "change_set_id": row.change_set_id,
            "dimension_item_id": row.dimension_item_id,
            "product_draft_id": row.product_draft_id,
            "base_preview_revision": row.base_preview_revision,
            "target_slot_id": row.target_slot_id,
            "base_asset": _loads(row.base_asset_json, {}),
            "replacement_asset": _loads(row.replacement_asset_json, {}),
            "physical_dimensions": _loads(row.physical_dimensions_json, {}),
            "status": row.status,
            "conflict": _loads(row.conflict_json, {}),
            "created_at": row.created_at,
            "resolved_at": row.resolved_at,
        }

    @staticmethod
    def _notification(row: DimensionCanvasNotificationRow) -> dict[str, Any]:
        payload = _loads(row.payload_json, {})
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "change_set_id": row.change_set_id,
            "kind": row.kind,
            "payload": payload,
            **payload,
            "created_at": row.created_at,
            "read_at": row.read_at,
        }
