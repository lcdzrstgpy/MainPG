from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, or_, select

from .database import ProductProcessingDatabase
from .media_asset_orm import MediaAssetRow, MediaBindingRow
from .orm import utc_now


def media_binding_key(
    product_draft_id: int,
    role: str,
    slot_id: str,
    sku_id: str,
    variant_label: str,
    source_identity: str,
    sort_order: int,
) -> str:
    """Stable workspace-scoped identity for a business image binding."""
    payload = {
        "draft": int(product_draft_id or 0),
        "role": str(role or ""),
        "slot": str(slot_id or ""),
        "sku": str(sku_id or ""),
        "variant": str(variant_label or ""),
        "source": str(source_identity or ""),
        "order": int(sort_order or 0),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class MediaMaterializationConflict(RuntimeError):
    pass


class MediaAssetRepository:
    """Workspace-scoped persistence for the unified media asset registry."""

    def __init__(self, database: ProductProcessingDatabase):
        self.database = database

    def register_remote_asset(
        self,
        workspace_id: str,
        source_url: str,
        source_identity_hash: str,
    ) -> dict[str, Any]:
        """Register or reuse a pending remote asset by canonical source identity."""
        with self.database.sessions.begin() as session:
            existing = session.scalar(
                select(MediaAssetRow).where(
                    MediaAssetRow.workspace_id == workspace_id,
                    MediaAssetRow.source_identity_hash == source_identity_hash,
                )
            )
            if existing is not None:
                return self._asset(existing)
            row = MediaAssetRow(
                workspace_id=workspace_id,
                origin="remote_source",
                source_url=str(source_url or ""),
                source_identity_hash=source_identity_hash,
                status="pending",
            )
            session.add(row)
            session.flush()
            return self._asset(row)

    def register_local_asset(
        self,
        workspace_id: str,
        origin: str,
        *,
        content_hash: str,
        managed_path: str,
        content_type: str,
        byte_size: int,
        width: int,
        height: int,
    ) -> dict[str, Any]:
        """Register or reuse a ready local asset by verified content hash."""
        with self.database.sessions.begin() as session:
            existing = session.scalar(
                select(MediaAssetRow).where(
                    MediaAssetRow.workspace_id == workspace_id,
                    MediaAssetRow.source_identity_hash == "",
                    MediaAssetRow.content_hash == content_hash,
                )
            )
            if existing is not None:
                return self._asset(existing)
            row = MediaAssetRow(
                workspace_id=workspace_id,
                origin=str(origin or "preview_upload"),
                source_identity_hash="",
                content_hash=content_hash,
                managed_path=str(managed_path or ""),
                content_type=str(content_type or ""),
                byte_size=int(byte_size or 0),
                width=int(width or 0),
                height=int(height or 0),
                status="ready",
            )
            session.add(row)
            session.flush()
            return self._asset(row)

    def get_asset(self, asset_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(MediaAssetRow).where(
                    MediaAssetRow.id == asset_id,
                    MediaAssetRow.workspace_id == workspace_id,
                )
            )
            return self._asset(row) if row else None

    def create_binding(
        self,
        workspace_id: str,
        asset_id: str,
        product_draft_id: int,
        *,
        task_id: int,
        task_item_id: int,
        role: str,
        slot_id: str,
        sku_id: str,
        variant_label: str,
        sort_order: int,
        binding_key: str,
    ) -> dict[str, Any]:
        """Create or reuse a business binding by workspace-scoped binding_key."""
        with self.database.sessions.begin() as session:
            existing = session.scalar(
                select(MediaBindingRow).where(
                    MediaBindingRow.workspace_id == workspace_id,
                    MediaBindingRow.binding_key == binding_key,
                )
            )
            if existing is not None:
                return self._binding(existing)
            row = MediaBindingRow(
                workspace_id=workspace_id,
                asset_id=asset_id,
                product_draft_id=int(product_draft_id),
                task_id=int(task_id or 0),
                task_item_id=int(task_item_id or 0),
                role=str(role or "gallery"),
                slot_id=str(slot_id or ""),
                sku_id=str(sku_id or ""),
                variant_label=str(variant_label or ""),
                sort_order=int(sort_order or 0),
                binding_key=binding_key,
                active=1,
            )
            session.add(row)
            session.flush()
            return self._binding(row)

    def list_bindings(
        self,
        workspace_id: str,
        *,
        product_draft_id: int | None = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            stmt = select(MediaBindingRow).where(
                MediaBindingRow.workspace_id == workspace_id
            )
            if product_draft_id is not None:
                stmt = stmt.where(MediaBindingRow.product_draft_id == int(product_draft_id))
            if active_only:
                stmt = stmt.where(MediaBindingRow.active == 1)
            stmt = stmt.order_by(MediaBindingRow.sort_order, MediaBindingRow.created_at, MediaBindingRow.id)
            rows = session.scalars(stmt).all()
            return [self._binding(row) for row in rows]

    def claim_materialization(
        self,
        workspace_id: str | None = None,
        limit: int = 20,
        lease_seconds: int = 180,
    ) -> list[dict[str, Any]]:
        """Claim pending/retryable/expired-materializing remote assets under a lease."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        cutoff = (now - timedelta(seconds=lease_seconds)).isoformat()
        token = uuid4().hex
        conditions = [
            MediaAssetRow.source_url != "",
            or_(
                MediaAssetRow.status == "pending",
                and_(
                    MediaAssetRow.status == "retryable",
                    or_(
                        MediaAssetRow.next_retry_at == "",
                        MediaAssetRow.next_retry_at <= now_iso,
                    ),
                ),
                and_(
                    MediaAssetRow.status == "materializing",
                    MediaAssetRow.claimed_at < cutoff,
                ),
            ),
        ]
        if workspace_id is not None:
            conditions.append(MediaAssetRow.workspace_id == workspace_id)
        with self.database.sessions.begin() as session:
            rows = session.scalars(
                select(MediaAssetRow)
                .where(*conditions)
                .order_by(MediaAssetRow.created_at, MediaAssetRow.id)
                .limit(max(1, int(limit)))
            ).all()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                row.status = "materializing"
                row.claim_token = token
                row.claimed_at = now_iso
                row.attempt_count = int(row.attempt_count or 0) + 1
                row.updated_at = utc_now()
                claimed.append(self._asset(row))
            session.flush()
            return claimed

    def mark_materialization_succeeded(
        self,
        asset_id: str,
        workspace_id: str,
        claim_token: str,
        *,
        managed_path: str,
        content_hash: str,
        content_type: str,
        byte_size: int,
        width: int,
        height: int,
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = self._require_claim(session, asset_id, workspace_id, claim_token)
            row.status = "ready"
            row.managed_path = str(managed_path or "")
            row.content_hash = str(content_hash or "")
            row.content_type = str(content_type or "")
            row.byte_size = int(byte_size or 0)
            row.width = int(width or 0)
            row.height = int(height or 0)
            row.claim_token = ""
            row.claimed_at = ""
            row.next_retry_at = ""
            row.error_code = ""
            row.error_message = ""
            row.updated_at = utc_now()
            session.flush()
            return self._asset(row)

    def mark_materialization_retryable(
        self,
        asset_id: str,
        workspace_id: str,
        claim_token: str,
        code: str,
        message: str,
        retry_at: str,
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = self._require_claim(session, asset_id, workspace_id, claim_token)
            row.status = "retryable"
            row.claim_token = ""
            row.claimed_at = ""
            row.next_retry_at = str(retry_at or "")
            row.error_code = str(code or "")
            row.error_message = str(message or "")[:240]
            row.updated_at = utc_now()
            session.flush()
            return self._asset(row)

    def mark_materialization_failed(
        self,
        asset_id: str,
        workspace_id: str,
        claim_token: str,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = self._require_claim(session, asset_id, workspace_id, claim_token)
            row.status = "failed"
            row.claim_token = ""
            row.claimed_at = ""
            row.next_retry_at = ""
            row.error_code = str(code or "")
            row.error_message = str(message or "")[:240]
            row.updated_at = utc_now()
            session.flush()
            return self._asset(row)

    def reset_asset_for_retry(self, asset_id: str, workspace_id: str) -> dict[str, Any]:
        """Allow a retryable/failed asset to be materialized again."""
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(MediaAssetRow).where(
                    MediaAssetRow.id == asset_id,
                    MediaAssetRow.workspace_id == workspace_id,
                )
            )
            if row is None:
                raise LookupError("media asset not found")
            if row.status not in {"retryable", "failed"}:
                raise MediaMaterializationConflict("media asset is not retryable")
            row.status = "pending"
            row.claim_token = ""
            row.claimed_at = ""
            row.next_retry_at = ""
            row.error_code = ""
            row.error_message = ""
            row.updated_at = utc_now()
            session.flush()
            return self._asset(row)

    @staticmethod
    def _require_claim(session, asset_id: str, workspace_id: str, claim_token: str) -> MediaAssetRow:
        row = session.scalar(
            select(MediaAssetRow).where(
                MediaAssetRow.id == asset_id,
                MediaAssetRow.workspace_id == workspace_id,
            )
        )
        if row is None:
            raise LookupError("media asset not found")
        if row.status != "materializing" or row.claim_token != str(claim_token or ""):
            raise MediaMaterializationConflict("media materialization claim changed")
        return row

    @staticmethod
    def _asset(row: MediaAssetRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "origin": row.origin,
            "source_url": row.source_url,
            "source_identity_hash": row.source_identity_hash,
            "content_hash": row.content_hash,
            "access_token": row.access_token,
            "managed_path": row.managed_path,
            "content_type": row.content_type,
            "byte_size": row.byte_size,
            "width": row.width,
            "height": row.height,
            "status": row.status,
            "attempt_count": row.attempt_count,
            "claim_token": row.claim_token,
            "claimed_at": row.claimed_at,
            "next_retry_at": row.next_retry_at,
            "error_code": row.error_code,
            "error_message": row.error_message,
        }

    @staticmethod
    def _binding(row: MediaBindingRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "asset_id": row.asset_id,
            "product_draft_id": row.product_draft_id,
            "task_id": row.task_id,
            "task_item_id": row.task_item_id,
            "role": row.role,
            "slot_id": row.slot_id,
            "sku_id": row.sku_id,
            "variant_label": row.variant_label,
            "sort_order": row.sort_order,
            "binding_key": row.binding_key,
            "active": row.active,
        }
