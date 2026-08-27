from __future__ import annotations

import json
import time
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import Select, and_, delete, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from .database import ProductProcessingDatabase
from .orm import (
    AiStageCacheRow,
    ComboSourceRow,
    DailySelectionHandoffReceiptRow,
    DailySelectionIntakeRow,
    EnginePromptRow,
    EnginePromptTemplateRow,
    ProcessingStageReceiptRow,
    ProcessingTaskItemRow,
    ProcessingTaskRow,
    ProductProcessingBillingAttemptRow,
    ProductDraftRow,
    SourceImageAssetRow,
    utc_now,
)
from .media_asset_orm import MediaAssetRow, MediaBindingRow
from .media_asset_repository import media_binding_key


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


class StalePreviewRevision(RuntimeError):
    """Raised when a canvas result targets an older precheck revision."""


class PreviewSlotConflict(StalePreviewRevision):
    """Raised when the target image slot changed after canvas import."""


class StaleShopIntakeFence(RuntimeError):
    """Raised when a shop batch or item no longer owns its intake lease."""


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

    # ---- 商品自定义组合：来源图暂存区（服务端持久化）----

    def list_combo_sources(self, workspace_id: str) -> list[dict[str, Any]]:
        with self.database.sessions.begin() as session:
            rows = session.scalars(
                select(ComboSourceRow)
                .where(ComboSourceRow.workspace_id == workspace_id)
                .order_by(ComboSourceRow.id.asc())
            ).all()
            return [self._combo_source(row) for row in rows]

    def add_combo_source(self, values: dict[str, Any]) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = ComboSourceRow(**values)
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                # 幂等：同 workspace + source_key 已存在时，重读胜者记录返回。
                session.rollback()
                existing = session.scalar(
                    select(ComboSourceRow).where(
                        ComboSourceRow.workspace_id == values["workspace_id"],
                        ComboSourceRow.source_key == values["source_key"],
                    )
                )
                if existing is None:
                    raise
                return self._combo_source(existing)
            return self._combo_source(row)

    def remove_combo_source(self, source_id: int, workspace_id: str) -> bool:
        with self.database.sessions.begin() as session:
            result = session.execute(
                delete(ComboSourceRow).where(
                    ComboSourceRow.id == source_id,
                    ComboSourceRow.workspace_id == workspace_id,
                )
            )
            return bool(result.rowcount) if result.rowcount is not None else False

    def clear_combo_sources(self, workspace_id: str) -> int:
        with self.database.sessions.begin() as session:
            result = session.execute(
                delete(ComboSourceRow).where(ComboSourceRow.workspace_id == workspace_id)
            )
            return int(result.rowcount or 0)

    def begin_product_billing_attempt(
        self,
        *,
        task_id: int,
        item_id: int,
        workspace_id: str,
        kind: str,
        feature_key: str,
        account_id: str,
    ) -> dict[str, Any]:
        """Return the unfinished attempt or create the next durable ordinal.

        并发安全：同一 item/kind 的 ordinal 与 idempotency_key 唯一；若多个
        调用方在事务提交前同时看到同一 latest，后提交者会撞 UNIQUE 约束，
        这里捕获后重读胜者记录返回（幂等），而不是向上抛冲突。
        """
        try:
            with self.database.sessions.begin() as session:
                task = session.get(ProcessingTaskRow, task_id)
                item = session.get(ProcessingTaskItemRow, item_id)
                if (
                    task is None
                    or item is None
                    or task.workspace_id != workspace_id
                    or item.task_id != task_id
                ):
                    raise LookupError("product processing task item not found")
                latest = session.scalar(
                    select(ProductProcessingBillingAttemptRow)
                    .where(
                        ProductProcessingBillingAttemptRow.task_id == task_id,
                        ProductProcessingBillingAttemptRow.item_id == item_id,
                        ProductProcessingBillingAttemptRow.kind == kind,
                    )
                    .order_by(ProductProcessingBillingAttemptRow.attempt_ordinal.desc())
                    .limit(1)
                )
                if latest is not None and not latest.settlement_state.startswith("settled_"):
                    if latest.account_id != account_id or latest.feature_key != feature_key:
                        raise PermissionError("billing attempt ownership or feature mismatch")
                    return self._billing_attempt(latest)
                ordinal = int(latest.attempt_ordinal if latest is not None else 0) + 1
                row = ProductProcessingBillingAttemptRow(
                    workspace_id=workspace_id,
                    task_id=task_id,
                    item_id=item_id,
                    kind=kind,
                    feature_key=feature_key,
                    account_id=account_id,
                    attempt_ordinal=ordinal,
                    idempotency_key=(
                        f"product_processing:{task_id}:{item_id}:{kind}:attempt:{ordinal}"
                    ),
                )
                session.add(row)
                session.flush()
                return self._billing_attempt(row)
        except IntegrityError:
            # 并发撞车：另一调用方已提交相同 ordinal，重读其最新记录并返回。
            with self.database.sessions.begin() as session:
                latest = session.scalar(
                    select(ProductProcessingBillingAttemptRow)
                    .where(
                        ProductProcessingBillingAttemptRow.task_id == task_id,
                        ProductProcessingBillingAttemptRow.item_id == item_id,
                        ProductProcessingBillingAttemptRow.kind == kind,
                    )
                    .order_by(ProductProcessingBillingAttemptRow.attempt_ordinal.desc())
                    .limit(1)
                )
                if latest is not None and not latest.settlement_state.startswith("settled_"):
                    return self._billing_attempt(latest)
                raise

    def record_product_billing_reservation(
        self,
        attempt_id: int,
        *,
        usage_id: str,
        remote_status: str,
    ) -> dict[str, Any]:
        if remote_status != "reserved" or not str(usage_id).strip():
            raise ValueError("remote billing did not return a reserved usage")
        with self.database.sessions.begin() as session:
            row = session.get(ProductProcessingBillingAttemptRow, attempt_id)
            if row is None:
                raise LookupError("product billing attempt not found")
            if row.settlement_state.startswith("settled_"):
                return self._billing_attempt(row)
            if row.usage_id and row.usage_id != usage_id:
                raise ValueError("remote billing usage changed for durable attempt")
            row.usage_id = str(usage_id)
            row.remote_status = "reserved"
            row.settlement_state = "settlement_pending" if row.desired_outcome else "reserved"
            row.last_error = ""
            row.updated_at = utc_now()
            session.flush()
            return self._billing_attempt(row)

    def mark_product_billing_desired_outcome(
        self,
        attempt_id: int,
        *,
        desired_outcome: str,
        error_message: str,
    ) -> dict[str, Any]:
        if desired_outcome not in {"succeeded", "failed"}:
            raise ValueError("unsupported billing desired outcome")
        with self.database.sessions.begin() as session:
            row = session.get(ProductProcessingBillingAttemptRow, attempt_id)
            if row is None:
                raise LookupError("product billing attempt not found")
            if not row.settlement_state.startswith("settled_"):
                row.desired_outcome = desired_outcome
                row.last_error = str(error_message)[:500]
                if row.usage_id:
                    row.settlement_state = "settlement_pending"
                row.updated_at = utc_now()
            session.flush()
            return self._billing_attempt(row)

    def mark_product_billing_settlement_pending(
        self, attempt_id: int, *, error_message: str
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = session.get(ProductProcessingBillingAttemptRow, attempt_id)
            if row is None:
                raise LookupError("product billing attempt not found")
            if not row.settlement_state.startswith("settled_"):
                row.settlement_state = "settlement_pending" if row.usage_id else "reserving"
                row.last_error = str(error_message)[:500]
                row.updated_at = utc_now()
            session.flush()
            return self._billing_attempt(row)

    def mark_product_billing_settled(
        self, attempt_id: int, *, remote_status: str
    ) -> dict[str, Any]:
        if remote_status not in {"succeeded", "failed"}:
            raise ValueError("remote billing returned a non-terminal settlement")
        with self.database.sessions.begin() as session:
            row = session.get(ProductProcessingBillingAttemptRow, attempt_id)
            if row is None:
                raise LookupError("product billing attempt not found")
            row.remote_status = remote_status
            row.settlement_state = f"settled_{remote_status}"
            row.last_error = ""
            row.updated_at = utc_now()
            session.flush()
            return self._billing_attempt(row)

    def product_billing_attempts(
        self,
        *,
        task_id: int | None = None,
        item_id: int | None = None,
        pending_only: bool = False,
    ) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            statement = select(ProductProcessingBillingAttemptRow)
            if task_id is not None:
                statement = statement.where(ProductProcessingBillingAttemptRow.task_id == task_id)
            if item_id is not None:
                statement = statement.where(ProductProcessingBillingAttemptRow.item_id == item_id)
            if pending_only:
                statement = statement.where(
                    ~ProductProcessingBillingAttemptRow.settlement_state.like("settled_%")
                )
            rows = session.scalars(
                statement.order_by(
                    ProductProcessingBillingAttemptRow.task_id,
                    ProductProcessingBillingAttemptRow.item_id,
                    ProductProcessingBillingAttemptRow.kind,
                    ProductProcessingBillingAttemptRow.attempt_ordinal,
                )
            ).all()
            return [self._billing_attempt(row) for row in rows]

    def create_draft_with_media(
        self,
        *,
        draft_values: dict[str, Any],
        media_entries: list[dict[str, Any]],
        handoff_id: str,
        idempotency_key: str,
        workspace_id: str,
        run_id: str,
        candidate_id: str,
        source_status: str,
        payload_sha256: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create a V2 draft plus its media assets, bindings, and receipt atomically."""
        with self.database.sessions.begin() as session:
            draft_row = ProductDraftRow(media_contract_version=2, **draft_values)
            session.add(draft_row)
            session.flush()
            draft_id = int(draft_row.id)
            asset_by_identity: dict[str, MediaAssetRow] = {}
            for entry in media_entries:
                source_identity_hash = str(entry.get("source_identity_hash") or "")
                asset_row = asset_by_identity.get(source_identity_hash)
                if asset_row is None:
                    asset_row = session.scalar(
                        select(MediaAssetRow).where(
                            MediaAssetRow.workspace_id == workspace_id,
                            MediaAssetRow.source_identity_hash == source_identity_hash,
                        )
                    )
                    if asset_row is None:
                        asset_row = MediaAssetRow(
                            workspace_id=workspace_id,
                            origin="remote_source",
                            source_url=str(entry.get("source_url") or ""),
                            source_identity_hash=source_identity_hash,
                            status="pending",
                        )
                        session.add(asset_row)
                        session.flush()
                    asset_by_identity[source_identity_hash] = asset_row
                role = str(entry.get("role") or "gallery")
                slot_id = str(entry.get("slot_id") or "")
                sku_id = str(entry.get("sku_id") or "")
                variant_label = str(entry.get("variant_label") or "")
                sort_order = int(entry.get("sort_order") or 0)
                session.add(
                    MediaBindingRow(
                        workspace_id=workspace_id,
                        asset_id=asset_row.id,
                        product_draft_id=draft_id,
                        task_id=int(entry.get("task_id") or 0),
                        task_item_id=int(entry.get("task_item_id") or 0),
                        role=role,
                        slot_id=slot_id,
                        sku_id=sku_id,
                        variant_label=variant_label,
                        sort_order=sort_order,
                        binding_key=media_binding_key(
                            draft_id,
                            role,
                            slot_id,
                            sku_id,
                            variant_label,
                            source_identity_hash,
                            sort_order,
                        ),
                        active=1,
                    )
                )
            receipt_row = session.scalar(
                select(DailySelectionHandoffReceiptRow).where(
                    DailySelectionHandoffReceiptRow.handoff_id == handoff_id
                )
            )
            if receipt_row is None:
                receipt_row = DailySelectionHandoffReceiptRow(
                    handoff_id=handoff_id,
                    idempotency_key=idempotency_key,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    candidate_id=candidate_id,
                    product_draft_id=draft_id,
                    source_status=source_status,
                    consumer_status="consumed",
                    payload_sha256=payload_sha256,
                )
                session.add(receipt_row)
                session.flush()
            return self._draft(draft_row), self._handoff_receipt(receipt_row)

    def intake_shop_candidate_with_media(
        self,
        *,
        draft_values: dict[str, Any],
        media_entries: list[dict[str, Any]],
        workspace_id: str,
        candidate_id: str,
        shop_fence: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Create or refresh one shop draft and its source-media bindings atomically."""
        for attempt in range(3):
            try:
                return self._intake_shop_candidate_with_media_once(
                    draft_values=draft_values,
                    media_entries=media_entries,
                    workspace_id=workspace_id,
                    candidate_id=candidate_id,
                    shop_fence=shop_fence,
                )
            except IntegrityError:
                if attempt == 2:
                    raise
            except OperationalError as error:
                if attempt == 2 or "locked" not in str(error).casefold():
                    raise
            time.sleep(0.01 * (attempt + 1))
        raise AssertionError("unreachable")

    def _intake_shop_candidate_with_media_once(
        self,
        *,
        draft_values: dict[str, Any],
        media_entries: list[dict[str, Any]],
        workspace_id: str,
        candidate_id: str,
        shop_fence: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        with self._shop_intake_transaction() as session:
            if shop_fence is not None:
                self._assert_shop_intake_fence(
                    session, workspace_id=workspace_id, fence=shop_fence
                )
            draft_row = session.scalar(
                select(ProductDraftRow)
                .where(
                    ProductDraftRow.workspace_id == workspace_id,
                    ProductDraftRow.candidate_id == candidate_id,
                    ProductDraftRow.source_type == "onebound_api",
                    ProductDraftRow.handoff_id.is_(None),
                )
                .order_by(ProductDraftRow.id.desc())
                .limit(1)
            )
            if draft_row is not None and draft_row.status in {"processing", "processed"}:
                return "skipped", self._draft(draft_row)

            if draft_row is None:
                draft_row = ProductDraftRow(media_contract_version=2, **draft_values)
                session.add(draft_row)
                action = "created"
            else:
                for key, value in draft_values.items():
                    setattr(draft_row, key, value)
                draft_row.status = "draft"
                draft_row.media_contract_version = 2
                draft_row.updated_at = utc_now()
                action = "refreshed"
            session.flush()

            draft_id = int(draft_row.id)
            session.execute(
                delete(MediaBindingRow).where(
                    MediaBindingRow.workspace_id == workspace_id,
                    MediaBindingRow.product_draft_id == draft_id,
                    MediaBindingRow.role.in_(("main", "gallery", "detail", "sku")),
                )
            )
            asset_by_identity: dict[str, MediaAssetRow] = {}
            for entry in media_entries:
                source_identity_hash = str(entry.get("source_identity_hash") or "")
                asset_row = asset_by_identity.get(source_identity_hash)
                if asset_row is None:
                    asset_row = session.scalar(
                        select(MediaAssetRow).where(
                            MediaAssetRow.workspace_id == workspace_id,
                            MediaAssetRow.source_identity_hash == source_identity_hash,
                        )
                    )
                    if asset_row is None:
                        asset_row = MediaAssetRow(
                            workspace_id=workspace_id,
                            origin="remote_source",
                            source_url=str(entry.get("source_url") or ""),
                            source_identity_hash=source_identity_hash,
                            status="pending",
                        )
                        session.add(asset_row)
                        session.flush()
                    asset_by_identity[source_identity_hash] = asset_row
                role = str(entry.get("role") or "gallery")
                slot_id = str(entry.get("slot_id") or "")
                sku_id = str(entry.get("sku_id") or "")
                variant_label = str(entry.get("variant_label") or "")
                sort_order = int(entry.get("sort_order") or 0)
                session.add(
                    MediaBindingRow(
                        workspace_id=workspace_id,
                        asset_id=asset_row.id,
                        product_draft_id=draft_id,
                        task_id=int(entry.get("task_id") or 0),
                        task_item_id=int(entry.get("task_item_id") or 0),
                        role=role,
                        slot_id=slot_id,
                        sku_id=sku_id,
                        variant_label=variant_label,
                        sort_order=sort_order,
                        binding_key=media_binding_key(
                            draft_id,
                            role,
                            slot_id,
                            sku_id,
                            variant_label,
                            source_identity_hash,
                            sort_order,
                        ),
                        active=1,
                    )
                )
            session.flush()
            return action, self._draft(draft_row)

    @staticmethod
    def _assert_shop_intake_fence(session: Any, *, workspace_id: str, fence: dict[str, Any]) -> None:
        """Validate both leases while holding the same SQLite writer transaction."""
        required = (
            "batch_id", "batch_lease_owner", "batch_lease_token",
            "item_id", "item_lease_owner", "item_lease_token", "offer_id",
        )
        if any(not str(fence.get(key) or "") for key in required):
            raise StaleShopIntakeFence("shop intake fence is incomplete")
        row = session.execute(
            text(
                """SELECT 1
                FROM shop_collection_batches AS batch
                JOIN shop_collection_items AS item ON item.batch_id = batch.batch_id
                WHERE batch.batch_id = :batch_id
                  AND batch.workspace_id = :workspace_id
                  AND batch.status = 'enriching'
                  AND batch.lease_owner = :batch_lease_owner
                  AND batch.lease_token = :batch_lease_token
                  AND batch.lease_expires_at > datetime('now')
                  AND item.item_id = :item_id
                  AND item.workspace_id = :workspace_id
                  AND item.offer_id = :offer_id
                  AND item.detail_status = 'running'
                  AND item.lease_owner = :item_lease_owner
                  AND item.lease_token = :item_lease_token
                  AND item.lease_expires_at > datetime('now')"""
            ),
            {key: str(fence[key]) for key in required} | {"workspace_id": workspace_id},
        ).first()
        if row is None:
            raise StaleShopIntakeFence("shop intake lease is stale or batch was cancelled")

    @contextmanager
    def _shop_intake_transaction(self):
        """Acquire SQLite's writer lease before reading a replayable candidate."""
        if isinstance(self.database.engine.pool, StaticPool):
            with self.database.shop_intake_lock:
                with self.database.sessions.begin() as session:
                    yield session
            return
        with self.database.sessions() as session:
            if session.get_bind().dialect.name != "sqlite":
                with session.begin():
                    yield session
                return
            session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def draft_by_candidate(
        self,
        candidate_id: str,
        workspace_id: str = "local",
        *,
        source_type: str | None = None,
    ) -> dict[str, Any] | None:
        if not candidate_id:
            return None
        with self.database.sessions() as session:
            statement = select(ProductDraftRow).where(
                ProductDraftRow.workspace_id == workspace_id,
                ProductDraftRow.candidate_id == candidate_id,
            )
            if source_type is not None:
                statement = statement.where(ProductDraftRow.source_type == source_type)
            row = session.scalar(statement.order_by(ProductDraftRow.id.desc()).limit(1))
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

    def mark_drafts_status(
        self,
        draft_ids: list[int],
        status: str,
        *,
        workspace_id: str = "local",
    ) -> list[int]:
        """批量更新草稿状态：提交处理后置 processing（草稿池立即隐藏），失败后回退 draft。"""
        with self.database.sessions.begin() as session:
            rows = session.scalars(
                select(ProductDraftRow).where(
                    ProductDraftRow.id.in_(draft_ids),
                    ProductDraftRow.workspace_id == workspace_id,
                )
            ).all()
            now = utc_now()
            for row in rows:
                row.status = status
                row.updated_at = now
            return [row.id for row in rows]

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

    def save_draft_preview_overrides(
        self,
        draft_id: int,
        overrides: dict[str, Any],
        *,
        expected_revision: int | None = None,
        workspace_id: str = "local",
    ) -> dict[str, Any] | None:
        """保存预检环节的覆盖数据（标题/描述/图片/核心字段），供导出最终版表格时应用。"""
        with self.database.sessions.begin() as session:
            row = session.get(ProductDraftRow, draft_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            current_revision = int(row.preview_revision or 0)
            if expected_revision is not None and current_revision != int(expected_revision):
                raise StalePreviewRevision(
                    f"preview revision conflict: expected {expected_revision}, current {current_revision}"
                )
            serialized = dumps(overrides or {})
            if row.preview_overrides_json != serialized:
                row.preview_overrides_json = serialized
                row.preview_revision = current_revision + 1
                row.updated_at = utc_now()
            session.flush()
            return self._draft(row)

    def apply_dimension_slot_patch(
        self,
        draft_id: int,
        *,
        target_slot: str,
        patch: dict[str, Any],
        base_slot_value: str,
        workspace_id: str = "local",
    ) -> dict[str, Any] | None:
        """Atomically merge one reviewed dimension image into the latest preview overrides.

        Unrelated title/description edits are retained.  A legacy whole-carousel edit or
        a newer edit of the same semantic slot is rejected instead of being overwritten.
        """
        slot_indexes = {
            "carousel.hero": 0,
            "carousel.detail": 1,
            "carousel.lifestyle": 2,
            "carousel.dimension_background": 3,
        }
        if target_slot not in slot_indexes or not str(patch.get("url") or "").strip():
            raise ValueError("invalid dimension image slot patch")
        with self.database.sessions.begin() as session:
            row = session.get(ProductDraftRow, draft_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            overrides = loads(row.preview_overrides_json, {})
            if not isinstance(overrides, dict):
                overrides = {}
            current_slot_value = str(base_slot_value or "").strip()
            legacy = overrides.get("carousel_images") or []
            if isinstance(legacy, list) and slot_indexes[target_slot] < len(legacy):
                current_slot_value = str(legacy[slot_indexes[target_slot]] or "").strip()
            slot_overrides = overrides.get("image_slot_overrides") or {}
            if not isinstance(slot_overrides, dict):
                slot_overrides = {}
            current_patch = slot_overrides.get(target_slot) or {}
            if isinstance(current_patch, dict) and str(current_patch.get("url") or "").strip():
                current_slot_value = str(current_patch["url"]).strip()
            if current_slot_value != str(base_slot_value or "").strip():
                raise PreviewSlotConflict("target image slot changed after dimension canvas import")

            next_patch = {"url": str(patch["url"]).strip()}
            asset_id = str(patch.get("asset_id") or "").strip()
            if asset_id:
                next_patch["asset_id"] = asset_id
            next_overrides = dict(overrides)
            next_overrides["image_slot_overrides"] = {**slot_overrides, target_slot: next_patch}
            current_revision = int(row.preview_revision or 0)
            updated = session.execute(
                update(ProductDraftRow)
                .where(
                    ProductDraftRow.id == draft_id,
                    ProductDraftRow.workspace_id == workspace_id,
                    ProductDraftRow.preview_revision == current_revision,
                )
                .values(
                    preview_overrides_json=dumps(next_overrides),
                    preview_revision=current_revision + 1,
                    updated_at=utc_now(),
                )
            )
            if updated.rowcount != 1:
                raise StalePreviewRevision("preview changed while accepting dimension image")
            session.expire(row)
            session.refresh(row)
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

    def list_tasks(
        self,
        limit: int,
        workspace_id: str = "local",
        *,
        offset: int = 0,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        with self.database.sessions() as session:
            filters = [ProcessingTaskRow.workspace_id == workspace_id]
            if date_from:
                filters.append(ProcessingTaskRow.created_at >= f"{date_from}T00:00:00")
            if date_to:
                filters.append(ProcessingTaskRow.created_at <= f"{date_to}T23:59:59.999999")
            total = int(
                session.scalar(select(func.count()).select_from(ProcessingTaskRow).where(*filters)) or 0
            )
            rows = session.scalars(
                select(ProcessingTaskRow)
                .options(selectinload(ProcessingTaskRow.items))
                .where(*filters)
                .order_by(ProcessingTaskRow.created_at.desc(), ProcessingTaskRow.id.desc())
                .offset(max(0, int(offset)))
                .limit(limit)
            ).all()
            return [self._task(row) for row in rows], total

    def queued_tasks(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            statement = (
                select(ProcessingTaskRow)
                .options(selectinload(ProcessingTaskRow.items))
                .where(ProcessingTaskRow.status == "queued")
                .order_by(ProcessingTaskRow.id)
            )
            if workspace_id is not None:
                statement = statement.where(ProcessingTaskRow.workspace_id == workspace_id)
            return [self._task(row) for row in session.scalars(statement).all()]

    def recover_interrupted_tasks(self) -> list[dict[str, Any]]:
        """Turn process-lost running work into an explicit retryable terminal state."""
        recovered: list[tuple[int, str]] = []
        with self.database.sessions.begin() as session:
            tasks = session.scalars(
                select(ProcessingTaskRow)
                .options(selectinload(ProcessingTaskRow.items))
                .where(ProcessingTaskRow.status == "running")
            ).all()
            now = utc_now()
            for task in tasks:
                for item in task.items:
                    if item.status not in {"pending", "running"}:
                        continue
                    item.status = "failed"
                    item.reason = "应用重启中断处理；上次调用结果不确定，请人工重试"
                    item.result_json = dumps(
                        {
                            "failure_class": "technical_retryable",
                            "retryable": True,
                            "operator_hint": "确认服务稳定后重试；系统不会自动重复付费调用",
                            "error_type": "worker_interrupted",
                        }
                    )
                    item.updated_at = now
                    if item.product_draft_id is not None:
                        draft = session.get(ProductDraftRow, item.product_draft_id)
                        if draft is not None and draft.workspace_id == task.workspace_id and draft.status == "processing":
                            draft.status = "draft"
                            draft.updated_at = now
                statuses = [item.status for item in task.items]
                task.success_count = sum(value == "completed" for value in statuses)
                task.skipped_count = sum(value == "skipped" for value in statuses)
                task.failed_count = sum(value not in {"pending", "running", "completed", "skipped"} for value in statuses)
                task.status = "failed" if task.success_count == 0 else "partial_failure"
                task.updated_at = now
                recovered.append((task.id, task.workspace_id))
        return [task for task_id, workspace in recovered if (task := self.get_task(task_id, workspace)) is not None]

    def claim_task_execution(self, task_id: int, workspace_id: str = "local") -> bool:
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(ProcessingTaskRow)
                .where(
                    ProcessingTaskRow.id == task_id,
                    ProcessingTaskRow.workspace_id == workspace_id,
                    ProcessingTaskRow.status == "queued",
                )
                .values(status="running", updated_at=utc_now())
            )
            return changed.rowcount == 1

    def fail_task_execution(self, task_id: int, reason: str, workspace_id: str = "local") -> dict[str, Any] | None:
        """Fail only unfinished items and restore their drafts after an executor crash."""
        with self.database.sessions.begin() as session:
            task = session.scalar(
                select(ProcessingTaskRow)
                .options(selectinload(ProcessingTaskRow.items))
                .where(
                    ProcessingTaskRow.id == task_id,
                    ProcessingTaskRow.workspace_id == workspace_id,
                )
            )
            if task is None:
                return None
            now = utc_now()
            for item in task.items:
                if item.status not in {"pending", "running"}:
                    continue
                item.status = "failed"
                item.reason = str(reason)[:240]
                item.result_json = dumps(
                    {
                        "failure_class": "technical_retryable",
                        "retryable": True,
                        "operator_hint": "后台执行异常，修复原因后可重试",
                        "error_type": "executor_failed",
                    }
                )
                item.updated_at = now
                if item.product_draft_id is not None:
                    draft = session.get(ProductDraftRow, item.product_draft_id)
                    if draft is not None and draft.workspace_id == workspace_id and draft.status == "processing":
                        draft.status = "draft"
                        draft.updated_at = now
            statuses = [item.status for item in task.items]
            task.success_count = sum(value == "completed" for value in statuses)
            task.skipped_count = sum(value == "skipped" for value in statuses)
            task.failed_count = sum(value not in {"pending", "running", "completed", "skipped"} for value in statuses)
            task.status = "failed" if task.success_count == 0 else "partial_failure"
            task.updated_at = now
        return self.get_task(task_id, workspace_id)

    def set_task_status(self, task_id: int, status: str, workspace_id: str = "local") -> dict[str, Any] | None:
        with self.database.sessions.begin() as session:
            row = session.get(ProcessingTaskRow, task_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            row.status = status
            row.updated_at = utc_now()
        return self.get_task(task_id, workspace_id)

    def mark_task_cancelled(self, task_id: int, workspace_id: str = "local") -> dict[str, Any] | None:
        """取消任务：置为终态 cancelled，并把未处理（pending/running）链接标记为失败。

        取消与暂停不同：暂停保留未处理项（pending/running）供 resume 断点续跑；
        取消是不可恢复的终态，未处理项立即标记失败（原因=用户已取消任务），
        直连计费下这些链接按 no_return 全额退款。
        """
        with self.database.sessions.begin() as session:
            task = session.scalar(
                select(ProcessingTaskRow)
                .options(selectinload(ProcessingTaskRow.items))
                .where(
                    ProcessingTaskRow.id == task_id,
                    ProcessingTaskRow.workspace_id == workspace_id,
                )
            )
            if task is None:
                return None
            now = utc_now()
            for item in task.items:
                if item.status not in {"pending", "running"}:
                    continue
                item.status = "failed"
                item.reason = "用户已取消任务"
                item.result_json = dumps(
                    {
                        "failure_class": "task_control",
                        "retryable": False,
                        "operator_hint": "任务已被取消，未处理链接已释放（不会继续产生 AI 费用）",
                        "error_type": "task_cancelled",
                        "debug_hint": "用户主动取消任务；该链接未进入/未完成 AI 处理，直连计费按全额退款",
                    }
                )
                item.updated_at = now
                if item.product_draft_id is not None:
                    draft = session.get(ProductDraftRow, item.product_draft_id)
                    if draft is not None and draft.workspace_id == workspace_id and draft.status == "processing":
                        draft.status = "draft"
                        draft.updated_at = now
            statuses = [item.status for item in task.items]
            task.success_count = sum(value == "completed" for value in statuses)
            task.skipped_count = sum(value == "skipped" for value in statuses)
            task.failed_count = sum(value not in {"pending", "running", "completed", "skipped"} for value in statuses)
            task.status = "cancelled"
            task.updated_at = now
        return self.get_task(task_id, workspace_id)

    def merge_task_settings(
        self,
        task_id: int,
        workspace_id: str = "local",
        **updates: Any,
    ) -> dict[str, Any] | None:
        """Merge field updates into the task settings JSON atomically."""
        with self.database.sessions.begin() as session:
            row = session.get(ProcessingTaskRow, task_id)
            if row is None or row.workspace_id != workspace_id:
                return None
            settings = dict(loads(row.settings_json, {}) or {})
            settings.update(updates)
            row.settings_json = dumps(settings)
            row.updated_at = utc_now()
            session.flush()
            return self._task(self._load_task(session, row.id))

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
            statuses = [item.status for item in items.values()]
            success_count = sum(value == "completed" for value in statuses)
            skipped_count = sum(value == "skipped" for value in statuses)
            failed_count = sum(value not in {"pending", "running", "completed", "skipped"} for value in statuses)
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
                if current in (None, "", "pending", "running"):
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

    def reset_failed_items(
        self,
        task_id: int,
        workspace_id: str = "local",
        *,
        draft_ids: list[int] | None = None,
    ) -> bool:
        with self.database.sessions.begin() as session:
            task = session.get(ProcessingTaskRow, task_id)
            if task is None or task.workspace_id != workspace_id:
                return False
            item_query = select(ProcessingTaskItemRow).where(
                    ProcessingTaskItemRow.task_id == task_id,
                    ProcessingTaskItemRow.status.in_(["failed", "attention_required"]),
                )
            if draft_ids:
                item_query = item_query.where(ProcessingTaskItemRow.product_draft_id.in_(draft_ids))
            for item in session.scalars(item_query):
                item.status = "pending"
                item.reason = ""
                item.result_json = "{}"
                item.updated_at = utc_now()
            statuses = session.scalars(
                select(ProcessingTaskItemRow.status).where(ProcessingTaskItemRow.task_id == task_id)
            ).all()
            task.success_count = sum(value == "completed" for value in statuses)
            task.skipped_count = sum(value == "skipped" for value in statuses)
            task.failed_count = sum(value not in {"pending", "running", "completed", "skipped"} for value in statuses)
            task.status = "queued"
            task.updated_at = utc_now()
            return True

    def load_stage_receipt(
        self,
        task_id: int,
        item_id: int,
        stage: str,
        *,
        workspace_id: str = "local",
    ) -> dict[str, Any] | None:
        stage_name = str(stage).strip()
        if not stage_name:
            return None
        with self.database.sessions() as session:
            try:
                self._require_workspace_task_item(
                    session,
                    task_id=task_id,
                    item_id=item_id,
                    workspace_id=workspace_id,
                )
            except LookupError:
                return None
            row = session.scalar(
                select(ProcessingStageReceiptRow).where(
                    ProcessingStageReceiptRow.workspace_id == workspace_id,
                    ProcessingStageReceiptRow.task_item_id == item_id,
                    ProcessingStageReceiptRow.stage == stage_name,
                )
            )
            return self._stage_receipt(row) if row is not None else None

    def upsert_stage_receipt(
        self,
        task_id: int,
        item_id: int,
        stage: str,
        *,
        input_hash: str,
        output_data: Any,
        workspace_id: str = "local",
    ) -> dict[str, Any]:
        stage_name = str(stage).strip()
        stable_input_hash = str(input_hash).strip()
        if not stage_name or not stable_input_hash:
            raise ValueError("stage and input_hash are required")
        with self.database.sessions.begin() as session:
            task, _item = self._require_workspace_task_item(
                session,
                task_id=task_id,
                item_id=item_id,
                workspace_id=workspace_id,
            )
            row = session.scalar(
                select(ProcessingStageReceiptRow).where(
                    ProcessingStageReceiptRow.workspace_id == task.workspace_id,
                    ProcessingStageReceiptRow.task_item_id == item_id,
                    ProcessingStageReceiptRow.stage == stage_name,
                )
            )
            now = utc_now()
            if row is None:
                row = ProcessingStageReceiptRow(
                    workspace_id=task.workspace_id,
                    task_item_id=item_id,
                    stage=stage_name,
                    input_hash=stable_input_hash,
                    output_json=dumps(output_data),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
            else:
                row.input_hash = stable_input_hash
                row.output_json = dumps(output_data)
                row.updated_at = now
            return self._stage_receipt(row)

    def delete_invalid_stage_receipt(
        self,
        task_id: int,
        item_id: int,
        stage: str,
        *,
        expected_input_hash: str,
        workspace_id: str = "local",
    ) -> bool:
        stage_name = str(stage).strip()
        with self.database.sessions.begin() as session:
            self._require_workspace_task_item(
                session,
                task_id=task_id,
                item_id=item_id,
                workspace_id=workspace_id,
            )
            result = session.execute(
                delete(ProcessingStageReceiptRow).where(
                    ProcessingStageReceiptRow.workspace_id == workspace_id,
                    ProcessingStageReceiptRow.task_item_id == item_id,
                    ProcessingStageReceiptRow.stage == stage_name,
                    ProcessingStageReceiptRow.input_hash != str(expected_input_hash).strip(),
                )
            )
            return bool(result.rowcount)

    def delete_downstream_stage_receipts(
        self,
        task_id: int,
        item_id: int,
        downstream_stages: Iterable[str],
        *,
        workspace_id: str = "local",
    ) -> int:
        stage_names = list(
            dict.fromkeys(str(stage).strip() for stage in downstream_stages if str(stage).strip())
        )
        if not stage_names:
            return 0
        with self.database.sessions.begin() as session:
            self._require_workspace_task_item(
                session,
                task_id=task_id,
                item_id=item_id,
                workspace_id=workspace_id,
            )
            result = session.execute(
                delete(ProcessingStageReceiptRow).where(
                    ProcessingStageReceiptRow.workspace_id == workspace_id,
                    ProcessingStageReceiptRow.task_item_id == item_id,
                    ProcessingStageReceiptRow.stage.in_(stage_names),
                )
            )
            return int(result.rowcount or 0)

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

    # ------------------------------------------------------------------
    # 预设提示词模板（账号级多命名模板，追加指令模式）
    # ------------------------------------------------------------------

    def prompt_templates(self) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            rows = session.scalars(
                select(EnginePromptTemplateRow).order_by(EnginePromptTemplateRow.updated_at.desc())
            ).all()
            return [self._prompt_template(row) for row in rows]

    def active_prompt_template(self) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(EnginePromptTemplateRow).where(EnginePromptTemplateRow.is_active.is_(True))
            )
            return self._prompt_template(row) if row is not None else None

    def save_prompt_template(
        self,
        *,
        template_id: int | None,
        name: str,
        prompts: dict[str, str],
        activate: bool,
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            if template_id is not None:
                row = session.get(EnginePromptTemplateRow, template_id)
                if row is None:
                    raise ValueError("prompt template not found")
            else:
                row = EnginePromptTemplateRow()
                session.add(row)
            row.name = str(name or "").strip() or "未命名模板"
            row.prompts_json = json.dumps(prompts, ensure_ascii=False, separators=(",", ":"))
            row.updated_at = utc_now()
            if activate:
                for other in session.scalars(select(EnginePromptTemplateRow)).all():
                    other.is_active = other.id == row.id
                row.is_active = True
            session.flush()
            return self._prompt_template(row)

    def activate_prompt_template(self, template_id: int) -> dict[str, Any] | None:
        with self.database.sessions.begin() as session:
            target = session.get(EnginePromptTemplateRow, template_id)
            if target is None:
                return None
            for row in session.scalars(select(EnginePromptTemplateRow)).all():
                row.is_active = row.id == template_id
            return self._prompt_template(target)

    def delete_prompt_template(self, template_id: int) -> bool:
        with self.database.sessions.begin() as session:
            row = session.get(EnginePromptTemplateRow, template_id)
            if row is None:
                return False
            session.delete(row)
            return True

    @staticmethod
    def _prompt_template(row: EnginePromptTemplateRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "name": row.name,
            "prompts": loads(row.prompts_json, {}),
            "is_active": bool(row.is_active),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

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
    def _require_workspace_task_item(
        session,
        *,
        task_id: int,
        item_id: int,
        workspace_id: str,
    ) -> tuple[ProcessingTaskRow, ProcessingTaskItemRow]:
        task = session.get(ProcessingTaskRow, task_id)
        if task is None or task.workspace_id != workspace_id:
            raise LookupError("product processing task not found")
        item = session.get(ProcessingTaskItemRow, item_id)
        if item is None or item.task_id != task_id:
            raise LookupError("product processing task item not found")
        return task, item

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
            "preview_overrides": loads(row.preview_overrides_json, {}),
            "preview_revision": int(row.preview_revision or 0),
            "media_contract_version": int(row.media_contract_version or 1),
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

    def set_combo_main(self, source_id: int, workspace_id: str) -> dict[str, Any]:
        """把某张来源图设为组合主图：同 workspace 先取消其他主图标记，再设置该张。

        ``source_id`` 不存在时抛 KeyError，由 service 层转成 404。
        """
        with self.database.sessions.begin() as session:
            target = session.get(ComboSourceRow, source_id)
            if target is None or target.workspace_id != workspace_id:
                raise KeyError(source_id)
            session.execute(
                update(ComboSourceRow)
                .where(ComboSourceRow.workspace_id == workspace_id)
                .values(is_main=False)
            )
            target.is_main = True
            session.flush()
            return self._combo_source(target)

    @staticmethod
    def _combo_source(row: ComboSourceRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "source_key": row.source_key,
            "source_type": row.source_type,
            "draft_id": row.draft_id,
            "title": row.title,
            "url": row.url,
            "is_main": bool(row.is_main),
            "local_path": row.local_path,
            "created_at": row.created_at,
        }


    @staticmethod
    def _billing_attempt(row: ProductProcessingBillingAttemptRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "task_id": row.task_id,
            "item_id": row.item_id,
            "kind": row.kind,
            "feature_key": row.feature_key,
            "account_id": row.account_id,
            "attempt_ordinal": row.attempt_ordinal,
            "idempotency_key": row.idempotency_key,
            "usage_id": row.usage_id,
            "remote_status": row.remote_status,
            "desired_outcome": row.desired_outcome,
            "settlement_state": row.settlement_state,
            "last_error": row.last_error,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _stage_receipt(row: ProcessingStageReceiptRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "task_item_id": row.task_item_id,
            "stage": row.stage,
            "input_hash": row.input_hash,
            "output": loads(row.output_json, None),
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
