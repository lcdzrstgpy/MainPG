from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.orm import selectinload

from .database import ProductProcessingDatabase
from .orm import (
    AiStageCacheRow,
    DailySelectionHandoffReceiptRow,
    DailySelectionIntakeRow,
    EnginePromptRow,
    ProcessingTaskItemRow,
    ProcessingTaskRow,
    ProductDraftRow,
    SourceImageAssetRow,
    utc_now,
)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


class ProductProcessingRepository:
    SOURCE_IMAGE_SYNC_LEASE = timedelta(minutes=5)

    def __init__(self, database: ProductProcessingDatabase):
        self.database = database

    def create_draft(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = ProductDraftRow(**values)
            session.add(row)
            session.flush()
            return self._draft(row)

    def draft_by_candidate(self, candidate_id: str, workspace_id: str = "local") -> dict[str, Any] | None:
        if not candidate_id:
            return None
        with self.database.sessions() as session:
            row = session.scalar(
                select(ProductDraftRow).where(
                    ProductDraftRow.workspace_id == workspace_id,
                    ProductDraftRow.candidate_id == candidate_id,
                )
            )
            return self._draft(row) if row else None

    def get_draft(
        self, draft_id: int, *, include_deleted: bool = False, workspace_id: str = "local"
    ) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            statement = select(ProductDraftRow).where(
                ProductDraftRow.id == draft_id,
                ProductDraftRow.workspace_id == workspace_id,
            )
            if not include_deleted:
                statement = statement.where(ProductDraftRow.status != "deleted")
            row = session.scalar(statement)
            return self._draft(row) if row else None

    def get_drafts(
        self,
        draft_ids: Iterable[int],
        *,
        include_deleted: bool = False,
        workspace_id: str = "local",
    ) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(int(item) for item in draft_ids))
        if not ids:
            return []
        with self.database.sessions() as session:
            statement: Select[tuple[ProductDraftRow]] = select(ProductDraftRow).where(
                ProductDraftRow.id.in_(ids),
                ProductDraftRow.workspace_id == workspace_id,
            )
            if not include_deleted:
                statement = statement.where(ProductDraftRow.status != "deleted")
            rows = session.scalars(statement).all()
            indexed = {row.id: self._draft(row) for row in rows}
            return [indexed[item] for item in ids if item in indexed]

    def list_drafts(
        self,
        status: str | None,
        limit: int,
        offset: int,
        *,
        selection_run_id: str | None = None,
        source_type: str | None = None,
        workspace_id: str = "local",
    ) -> tuple[list[dict[str, Any]], bool]:
        with self.database.sessions() as session:
            statement = select(ProductDraftRow).where(ProductDraftRow.workspace_id == workspace_id)
            if status:
                statement = statement.where(ProductDraftRow.status == status)
            else:
                statement = statement.where(ProductDraftRow.status == "draft")
            if selection_run_id is not None:
                statement = statement.where(ProductDraftRow.selection_run_id == selection_run_id)
            if source_type is not None:
                statement = statement.where(ProductDraftRow.source_type == source_type)
            statement = statement.order_by(ProductDraftRow.created_at.desc(), ProductDraftRow.id.desc()).offset(offset).limit(limit + 1)
            rows = session.scalars(statement).all()
            return [self._draft(row) for row in rows[:limit]], len(rows) > limit

    def drafts_revision(self, workspace_id: str = "local") -> str:
        """轻量变更指纹：最近一次草稿写入/更新的时间（ISO 字符串，字典序即时间序）。

        供前端轮询检测外部采集/入池产生的新草稿，避免频繁拉全量列表。
        """
        with self.database.sessions() as session:
            value = session.execute(
                select(func.max(ProductDraftRow.updated_at)).where(ProductDraftRow.workspace_id == workspace_id)
            ).scalar_one_or_none()
            return str(value) if value else ""

    def update_draft(
        self,
        draft_id: int,
        fields: dict[str, Any],
        raw_payload: dict[str, Any],
        *,
        workspace_id: str = "local",
    ) -> dict[str, Any] | None:
        with self.database.sessions.begin() as session:
            row = session.get(ProductDraftRow, draft_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            row.raw_payload_json = dumps(raw_payload)
            row.updated_at = utc_now()
            session.flush()
            return self._draft(row)

    def delete_drafts(self, draft_ids: list[int] | None, workspace_id: str = "local") -> list[int]:
        with self.database.sessions.begin() as session:
            statement = select(ProductDraftRow).where(
                ProductDraftRow.workspace_id == workspace_id,
                ProductDraftRow.status != "deleted",
            )
            if draft_ids is not None:
                statement = statement.where(ProductDraftRow.id.in_(draft_ids))
            rows = session.scalars(statement).all()
            now = utc_now()
            for row in rows:
                row.status = "deleted"
                row.updated_at = now
            return [row.id for row in rows]

    def create_task(
        self,
        *,
        title: str,
        preflight_only: bool,
        settings: dict[str, Any],
        drafts: list[dict[str, Any]],
        idempotency_key: str | None,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            task = ProcessingTaskRow(
                workspace_id=workspace_id,
                title=title,
                status="queued",
                preflight_only=preflight_only,
                total_count=len(drafts),
                settings_json=dumps(settings),
                idempotency_key=idempotency_key or None,
            )
            session.add(task)
            session.flush()
            for draft in drafts:
                session.add(
                    ProcessingTaskItemRow(
                        task_id=task.id,
                        product_draft_id=draft["id"],
                        skc=str(draft.get("skc") or ""),
                        title=str(draft.get("title") or draft.get("product_name") or ""),
                        image_url=str(draft.get("image_url") or ""),
                    )
                )
            session.flush()
            return self._task(self._load_task(session, task.id))

    def task_by_idempotency_key(self, key: str | None, workspace_id: str = "local") -> dict[str, Any] | None:
        if not key:
            return None
        with self.database.sessions() as session:
            row = session.scalar(
                select(ProcessingTaskRow)
                .options(selectinload(ProcessingTaskRow.items))
                .where(
                    ProcessingTaskRow.workspace_id == workspace_id,
                    ProcessingTaskRow.idempotency_key == key,
                )
            )
            return self._task(row) if row else None

    def get_task(self, task_id: int, workspace_id: str = "local") -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = self._load_task(session, task_id)
            if row is not None and row.workspace_id != workspace_id:
                return None
            return self._task(row) if row else None

    def list_tasks(self, limit: int, workspace_id: str = "local") -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            rows = session.scalars(
                select(ProcessingTaskRow)
                .options(selectinload(ProcessingTaskRow.items))
                .where(ProcessingTaskRow.workspace_id == workspace_id)
                .order_by(ProcessingTaskRow.created_at.desc(), ProcessingTaskRow.id.desc())
                .limit(limit)
            ).all()
            return [self._task(row) for row in rows]

    def set_task_status(self, task_id: int, status: str, workspace_id: str = "local") -> dict[str, Any] | None:
        with self.database.sessions.begin() as session:
            row = session.get(ProcessingTaskRow, task_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            row.status = status
            row.updated_at = utc_now()
        return self.get_task(task_id, workspace_id)

    def finish_task(
        self,
        task_id: int,
        item_results: list[dict[str, Any]],
        *,
        output_file: str,
        error_report_file: str,
        video_manifest_file: str,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            task = session.get(ProcessingTaskRow, task_id)
            if task is None or task.workspace_id != workspace_id:
                raise LookupError("product processing task not found")
            items = {
                row.id: row
                for row in session.scalars(
                    select(ProcessingTaskItemRow).where(ProcessingTaskItemRow.task_id == task_id)
                ).all()
            }
            success_count = failed_count = skipped_count = 0
            now = utc_now()
            for result in item_results:
                item = items[int(result["item_id"])]
                item.status = str(result["status"])
                item.reason = str(result.get("reason") or "")
                item.skc = str(result.get("skc") or item.skc)
                item.spu = str(result.get("spu") or item.spu)
                item.title = str(result.get("title") or item.title)
                item.image_url = str(result.get("image_url") or item.image_url)
                item.result_json = dumps(result.get("result") or {})
                item.updated_at = now
                if item.status == "completed":
                    success_count += 1
                elif item.status == "skipped":
                    skipped_count += 1
                else:
                    failed_count += 1
            task.success_count = success_count
            task.failed_count = failed_count
            task.skipped_count = skipped_count
            task.status = "completed" if failed_count == 0 else ("failed" if success_count == 0 else "partial_failure")
            task.output_file = output_file
            task.error_report_file = error_report_file
            task.video_manifest_file = video_manifest_file
            task.updated_at = now
        task_result = self.get_task(task_id, workspace_id)
        if task_result is None:
            raise LookupError("product processing task not found")
        return task_result

    def update_item_progress(
        self,
        task_id: int,
        item_id: int,
        *,
        status: str,
        reason: str = "",
        skc: str | None = None,
        spu: str | None = None,
        title: str | None = None,
        image_url: str | None = None,
        result: dict[str, Any] | None = None,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        """持久化单个任务项的处理结果并实时刷新任务计数，供进度轮询读取。

        计数按终态分桶：completed→success，skipped→skipped，其余非 pending→failed；
        用新旧分桶差值做 O(1) 增量迁移，避免每次全表重算。
        """
        with self.database.sessions.begin() as session:
            task = session.get(ProcessingTaskRow, task_id)
            if task is None or task.workspace_id != workspace_id:
                raise LookupError("product processing task not found")
            item = session.get(ProcessingTaskItemRow, item_id)
            if item is None or item.task_id != task_id:
                raise LookupError("product processing task item not found")

            def bucket(current: str | None) -> str | None:
                if current == "completed":
                    return "success"
                if current == "skipped":
                    return "skipped"
                if current in (None, "", "pending"):
                    return None
                return "failed"

            old_bucket = bucket(item.status)
            item.status = str(status)
            item.reason = str(reason)
            if skc is not None:
                item.skc = str(skc)
            if spu is not None:
                item.spu = str(spu)
            if title is not None:
                item.title = str(title)
            if image_url is not None:
                item.image_url = str(image_url)
            if result is not None:
                item.result_json = dumps(result)
            item.updated_at = utc_now()
            new_bucket = bucket(item.status)
            if old_bucket != new_bucket:
                adjustment = {"success": 0, "failed": 0, "skipped": 0}
                if old_bucket is not None:
                    adjustment[old_bucket] -= 1
                if new_bucket is not None:
                    adjustment[new_bucket] += 1
                task.success_count = max(0, task.success_count + adjustment["success"])
                task.failed_count = max(0, task.failed_count + adjustment["failed"])
                task.skipped_count = max(0, task.skipped_count + adjustment["skipped"])
            task.updated_at = utc_now()
        return self.get_task(task_id, workspace_id)

    def reset_failed_items(self, task_id: int, workspace_id: str = "local") -> bool:
        with self.database.sessions.begin() as session:
            task = session.get(ProcessingTaskRow, task_id)
            if task is None or task.workspace_id != workspace_id:
                return False
            for item in session.scalars(
                select(ProcessingTaskItemRow).where(
                    ProcessingTaskItemRow.task_id == task_id,
                    ProcessingTaskItemRow.status.in_(["failed", "attention_required"]),
                )
            ):
                item.status = "pending"
                item.reason = ""
            task.status = "queued"
            task.updated_at = utc_now()
            return True

    def clear_task(self, task_id: int, workspace_id: str = "local") -> dict[str, Any] | None:
        with self.database.sessions.begin() as session:
            task = session.get(ProcessingTaskRow, task_id)
            if task is None or task.workspace_id != workspace_id:
                return None
            task.cleared_from_product_processing = True
            task.updated_at = utc_now()
        return self.get_task(task_id, workspace_id)

    def save_intake(
        self,
        *,
        run_id: str,
        workspace_id: str,
        status: str,
        criteria: dict[str, Any],
        counts: dict[str, int],
        errors: list[Any],
        candidate_count: int,
        created_count: int,
        skipped_count: int,
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(DailySelectionIntakeRow).where(
                    DailySelectionIntakeRow.workspace_id == workspace_id,
                    DailySelectionIntakeRow.run_id == run_id,
                )
            )
            if row is None:
                row = DailySelectionIntakeRow(workspace_id=workspace_id, run_id=run_id)
                session.add(row)
            row.status = status
            row.criteria_json = dumps(criteria)
            row.counts_json = dumps(counts)
            row.errors_json = dumps(errors)
            row.candidate_count = candidate_count
            row.created_count = created_count
            row.skipped_count = skipped_count
            row.updated_at = utc_now()
            session.flush()
            return self._intake(row)

    def get_intake(self, run_id: str, workspace_id: str = "local") -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(DailySelectionIntakeRow).where(
                    DailySelectionIntakeRow.workspace_id == workspace_id,
                    DailySelectionIntakeRow.run_id == run_id,
                )
            )
            return self._intake(row) if row else None

    def prompts(self) -> dict[str, str]:
        with self.database.sessions() as session:
            return {row.key: row.custom for row in session.scalars(select(EnginePromptRow)).all()}

    def save_prompts(self, prompts: dict[str, str]) -> dict[str, str]:
        with self.database.sessions.begin() as session:
            for key, custom in prompts.items():
                row = session.get(EnginePromptRow, key)
                if row is None:
                    row = EnginePromptRow(key=key)
                    session.add(row)
                row.custom = custom
                row.updated_at = utc_now()
        return self.prompts()

    def reset_prompts(self) -> None:
        with self.database.sessions.begin() as session:
            for row in session.scalars(select(EnginePromptRow)).all():
                session.delete(row)

    def get_ai_stage_cache(
        self, cache_key: str, *, workspace_id: str = "local"
    ) -> dict[str, Any] | None:
        """读取阶段级 AI 调用缓存（对齐原项目 ai_stage_cache）。命中即计一次 hit_count。"""
        if not cache_key:
            return None
        with self.database.sessions.begin() as session:
            row = session.get(AiStageCacheRow, (workspace_id, cache_key))
            if row is None:
                return None
            row.hit_count = (row.hit_count or 0) + 1
            row.last_used_at = utc_now()
            return {
                "output": loads(row.output_json, None),
                "stage": row.stage,
                "created_at": row.created_at,
                "hit_count": row.hit_count,
                "model_signature": row.model_signature,
            }

    def save_ai_stage_cache(
        self,
        cache_key: str,
        *,
        workspace_id: str = "local",
        stage: str = "",
        model_signature: str = "",
        prompt_hash: str = "",
        input_hash: str = "",
        output_data: Any,
    ) -> None:
        """写入阶段级 AI 调用缓存；同 key 已存在时保留首次结果（幂等）。"""
        if not cache_key:
            return
        with self.database.sessions.begin() as session:
            row = session.get(AiStageCacheRow, (workspace_id, cache_key))
            if row is None:
                row = AiStageCacheRow(
                    workspace_id=workspace_id,
                    cache_key=cache_key,
                    stage=stage,
                    model_signature=model_signature,
                    prompt_hash=prompt_hash,
                    input_hash=input_hash,
                    output_json=dumps(output_data),
                )
                session.add(row)

    def preserve_source_images(
        self,
        *,
        task_id: int | None,
        product_draft_id: int,
        source_urls: list[str],
        detail_urls: list[str],
    ) -> list[dict[str, Any]]:
        requested = [("source", url) for url in source_urls] + [("detail", url) for url in detail_urls]
        requested = list(dict.fromkeys((kind, str(url or "").strip()) for kind, url in requested if str(url or "").strip()))
        with self.database.sessions.begin() as session:
            existing = {
                row.url: row
                for row in session.scalars(
                    select(SourceImageAssetRow).where(SourceImageAssetRow.product_draft_id == product_draft_id)
                ).all()
            }
            rows: list[SourceImageAssetRow] = []
            for kind, url in requested:
                row = existing.get(url)
                if row is None:
                    row = SourceImageAssetRow(
                        product_draft_id=product_draft_id,
                        task_id=task_id,
                        kind=kind,
                        url=url,
                    )
                    session.add(row)
                    session.flush()
                    existing[url] = row
                elif row.task_id is None:
                    row.task_id = task_id
                rows.append(row)
            return [self._source_image(row) for row in rows]

    def claim_syncable_source_images(
        self, product_draft_id: int, workspace_id: str = "local"
    ) -> list[dict[str, Any]]:
        claimed_at = utc_now()
        lease_expires_at = (datetime.now(timezone.utc) - self.SOURCE_IMAGE_SYNC_LEASE).isoformat()
        claimable = or_(
            SourceImageAssetRow.sync_status.in_(["pending", "failed"]),
            and_(
                SourceImageAssetRow.sync_status == "syncing",
                or_(
                    SourceImageAssetRow.sync_claimed_at == "",
                    SourceImageAssetRow.sync_claimed_at <= lease_expires_at,
                ),
            ),
        )
        with self.database.sessions.begin() as session:
            rows = session.scalars(
                select(SourceImageAssetRow)
                .join(ProductDraftRow, ProductDraftRow.id == SourceImageAssetRow.product_draft_id)
                .where(
                    ProductDraftRow.workspace_id == workspace_id,
                    SourceImageAssetRow.product_draft_id == product_draft_id,
                    claimable,
                )
                .order_by(SourceImageAssetRow.id)
            ).all()
            claimed_tokens: dict[int, str] = {}
            for row in rows:
                claim_token = uuid4().hex
                result = session.execute(
                    update(SourceImageAssetRow)
                    .where(
                        SourceImageAssetRow.id == row.id,
                        claimable,
                    )
                    .values(
                        sync_status="syncing",
                        sync_error="",
                        sync_claimed_at=claimed_at,
                        sync_claim_token=claim_token,
                    )
                )
                if result.rowcount:
                    claimed_tokens[row.id] = claim_token
            if not claimed_tokens:
                return []
            claimed = session.scalars(
                select(SourceImageAssetRow)
                .where(SourceImageAssetRow.id.in_(claimed_tokens))
                .order_by(SourceImageAssetRow.id)
            ).all()
            return [
                {**self._source_image(row), "_sync_claim_token": claimed_tokens[row.id]}
                for row in claimed
            ]

    def complete_source_image(
        self, image_id: int, local_path: str, claim_token: str, workspace_id: str = "local"
    ) -> bool:
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(SourceImageAssetRow)
                .join(ProductDraftRow, ProductDraftRow.id == SourceImageAssetRow.product_draft_id)
                .where(
                    SourceImageAssetRow.id == image_id,
                    ProductDraftRow.workspace_id == workspace_id,
                    SourceImageAssetRow.sync_status == "syncing",
                    SourceImageAssetRow.sync_claim_token == claim_token,
                )
            )
            if row is None:
                return False
            row.local_path = local_path
            row.sync_status = "ready"
            row.sync_error = ""
            row.sync_claimed_at = ""
            row.sync_claim_token = ""
            return True

    def fail_source_image(self, image_id: int, error: str, claim_token: str, workspace_id: str = "local") -> bool:
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(SourceImageAssetRow)
                .join(ProductDraftRow, ProductDraftRow.id == SourceImageAssetRow.product_draft_id)
                .where(
                    SourceImageAssetRow.id == image_id,
                    ProductDraftRow.workspace_id == workspace_id,
                    SourceImageAssetRow.sync_status == "syncing",
                    SourceImageAssetRow.sync_claim_token == claim_token,
                )
            )
            if row is None:
                return False
            row.sync_status = "failed"
            row.sync_error = (str(error).strip() or "source image synchronization failed")[:500]
            row.sync_claimed_at = ""
            row.sync_claim_token = ""
            return True

    def list_source_images(
        self,
        *,
        product_draft_id: int | None = None,
        task_id: int | None = None,
        workspace_id: str = "local",
    ) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            statement = (
                select(SourceImageAssetRow)
                .join(ProductDraftRow, ProductDraftRow.id == SourceImageAssetRow.product_draft_id)
                .where(ProductDraftRow.workspace_id == workspace_id)
            )
            if product_draft_id is not None:
                statement = statement.where(SourceImageAssetRow.product_draft_id == product_draft_id)
            if task_id is not None:
                statement = statement.where(SourceImageAssetRow.task_id == task_id)
            rows = session.scalars(statement.order_by(SourceImageAssetRow.id)).all()
            return [self._source_image(row) for row in rows]

    def ready_primary_source_image_paths(
        self,
        draft_ids: Iterable[int],
        *,
        workspace_id: str = "local",
    ) -> dict[int, str]:
        ids = list(dict.fromkeys(int(item) for item in draft_ids))
        if not ids:
            return {}
        with self.database.sessions() as session:
            rows = session.execute(
                select(SourceImageAssetRow.product_draft_id, SourceImageAssetRow.local_path)
                .join(ProductDraftRow, ProductDraftRow.id == SourceImageAssetRow.product_draft_id)
                .where(
                    ProductDraftRow.workspace_id == workspace_id,
                    SourceImageAssetRow.product_draft_id.in_(ids),
                    SourceImageAssetRow.kind == "source",
                    SourceImageAssetRow.sync_status == "ready",
                    SourceImageAssetRow.local_path != "",
                )
                .order_by(SourceImageAssetRow.product_draft_id, SourceImageAssetRow.id)
            ).all()
            paths: dict[int, str] = {}
            for draft_id, local_path in rows:
                paths.setdefault(int(draft_id), str(local_path))
            return paths

    def primary_source_images(
        self,
        draft_ids: Iterable[int],
        *,
        workspace_id: str = "local",
    ) -> dict[int, dict[str, str]]:
        ids = list(dict.fromkeys(int(item) for item in draft_ids))
        if not ids:
            return {}
        with self.database.sessions() as session:
            rows = session.scalars(
                select(SourceImageAssetRow)
                .join(ProductDraftRow, ProductDraftRow.id == SourceImageAssetRow.product_draft_id)
                .where(
                    ProductDraftRow.workspace_id == workspace_id,
                    SourceImageAssetRow.product_draft_id.in_(ids),
                    SourceImageAssetRow.kind == "source",
                )
                .order_by(SourceImageAssetRow.product_draft_id, SourceImageAssetRow.id)
            ).all()
            images: dict[int, dict[str, str]] = {}
            for row in rows:
                images.setdefault(
                    int(row.product_draft_id),
                    {
                        "sync_status": row.sync_status,
                        "sync_error": row.sync_error,
                    },
                )
            return images

    def handoff_receipt(self, handoff_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(DailySelectionHandoffReceiptRow).where(
                    DailySelectionHandoffReceiptRow.workspace_id == workspace_id,
                    DailySelectionHandoffReceiptRow.handoff_id == handoff_id,
                )
            )
            return self._handoff_receipt(row) if row else None

    def save_handoff_receipt(
        self,
        *,
        handoff_id: str,
        idempotency_key: str,
        workspace_id: str,
        run_id: str,
        candidate_id: str,
        product_draft_id: int,
        source_status: str,
        payload_sha256: str,
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(DailySelectionHandoffReceiptRow).where(
                    DailySelectionHandoffReceiptRow.handoff_id == handoff_id
                )
            )
            if row is None:
                row = DailySelectionHandoffReceiptRow(
                    handoff_id=handoff_id,
                    idempotency_key=idempotency_key,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    candidate_id=candidate_id,
                    product_draft_id=product_draft_id,
                    source_status=source_status,
                    consumer_status="consumed",
                    payload_sha256=payload_sha256,
                )
                session.add(row)
                session.flush()
            return self._handoff_receipt(row)

    @staticmethod
    def _load_task(session, task_id: int) -> ProcessingTaskRow | None:
        return session.scalar(
            select(ProcessingTaskRow)
            .options(selectinload(ProcessingTaskRow.items))
            .where(ProcessingTaskRow.id == task_id)
        )

    @staticmethod
    def _draft(row: ProductDraftRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "source_type": row.source_type,
            "source_ref": row.source_ref,
            "candidate_id": row.candidate_id,
            "selection_run_id": row.selection_run_id,
            "handoff_id": row.handoff_id,
            "handoff_idempotency_key": row.handoff_idempotency_key,
            "skc": row.skc,
            "sku": row.sku,
            "product_name": row.product_name,
            "title": row.title,
            "description": row.description,
            "image_url": row.image_url,
            "image_path": row.image_path,
            "cost": row.cost,
            "declared_price": row.declared_price,
            "status": row.status,
            "raw_payload": loads(row.raw_payload_json, {}),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _item(row: ProcessingTaskItemRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "item_id": row.id,
            "task_id": row.task_id,
            "product_draft_id": row.product_draft_id,
            "skc": row.skc,
            "spu": row.spu,
            "title": row.title,
            "image_url": row.image_url,
            "status": row.status,
            "reason": row.reason,
            "result": loads(row.result_json, {}),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @classmethod
    def _task(cls, row: ProcessingTaskRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "task_id": row.id,
            "workspace_id": row.workspace_id,
            "title": row.title,
            "status": row.status,
            "preflight_only": row.preflight_only,
            "total_count": row.total_count,
            "success_count": row.success_count,
            "failed_count": row.failed_count,
            "skipped_count": row.skipped_count,
            "settings": loads(row.settings_json, {}),
            "idempotency_key": row.idempotency_key,
            "output_file": row.output_file,
            "error_report_file": row.error_report_file,
            "video_manifest_file": row.video_manifest_file,
            "cleared_from_product_processing": row.cleared_from_product_processing,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "items": [cls._item(item) for item in row.items],
        }

    @staticmethod
    def _intake(row: DailySelectionIntakeRow) -> dict[str, Any]:
        return {
            "workspace_id": row.workspace_id,
            "run_id": row.run_id,
            "status": row.status,
            "criteria": loads(row.criteria_json, {}),
            "counts": loads(row.counts_json, {}),
            "errors": loads(row.errors_json, []),
            "candidate_count": row.candidate_count,
            "created_count": row.created_count,
            "skipped_count": row.skipped_count,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _source_image(row: SourceImageAssetRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "product_draft_id": row.product_draft_id,
            "task_id": row.task_id,
            "kind": row.kind,
            "url": row.url,
            "local_path": row.local_path,
            "sync_status": row.sync_status,
            "sync_error": row.sync_error,
            "created_at": row.created_at,
        }

    @staticmethod
    def _handoff_receipt(row: DailySelectionHandoffReceiptRow) -> dict[str, Any]:
        return {
            "handoff_id": row.handoff_id,
            "idempotency_key": row.idempotency_key,
            "workspace_id": row.workspace_id,
            "run_id": row.run_id,
            "candidate_id": row.candidate_id,
            "product_draft_id": row.product_draft_id,
            "source_status": row.source_status,
            "consumer_status": row.consumer_status,
            "payload_sha256": row.payload_sha256,
            "created_at": row.created_at,
        }
