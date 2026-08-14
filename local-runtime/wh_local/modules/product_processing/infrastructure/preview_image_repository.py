from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..domain.preview_images import (
    MANIFEST_KEY,
    PreviewImageManifest,
    snapshot_hash as calculate_snapshot_hash,
    task_item_result_version,
)
from .database import ProductProcessingDatabase
from .orm import (
    ProcessingTaskItemRow,
    ProcessingTaskRow,
    ProductDraftRow,
    utc_now,
)
from .preview_image_orm import (
    PreviewFinalizeRunRow,
    PreviewImageAssetRow,
    PreviewImagePublicationRow,
)


class PreviewPublicationConflict(RuntimeError):
    """Raised when a publication or finalization lease is not owned."""


class PreviewRevisionConflict(RuntimeError):
    """Raised when a revision-safe manifest mutation loses its compare-and-swap."""


class PreviewIdempotencyConflict(RuntimeError):
    """Raised when one finalization idempotency key is reused for another request."""


class PreviewSourceNotInLibrary(ValueError):
    """Raised when a source proxy is selected for export before joining the library."""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _lease_times(lease_seconds: int) -> tuple[str, str]:
    if int(lease_seconds) <= 0:
        raise ValueError("preview lease must be positive")
    now = datetime.now(timezone.utc)
    return now.isoformat(), (now - timedelta(seconds=int(lease_seconds))).isoformat()


class PreviewImageRepository:
    """Workspace-scoped persistence for precheck image identity and leases."""

    def __init__(self, database: ProductProcessingDatabase):
        self.database = database

    def register_asset(
        self,
        *,
        workspace_id: str,
        task_id: int,
        product_draft_id: int,
        origin: str,
        identity_hash: str,
        managed_path: str,
        source_url: str,
        content_hash: str,
        content_type: str,
        byte_size: int,
        width: int,
        height: int,
        source_asset_id: str = "",
        media_asset_id: str = "",
        source_kind: str = "",
    ) -> dict[str, Any]:
        workspace = str(workspace_id or "").strip()
        identity = str(identity_hash or "").strip().casefold()
        if not workspace or not identity:
            raise ValueError("preview asset workspace and identity are required")
        asset_id = str(
            uuid5(
                NAMESPACE_URL,
                f"preview-asset:{workspace}:{int(task_id)}:{int(product_draft_id)}:{identity}",
            )
        )
        values = {
            "id": asset_id,
            "workspace_id": workspace,
            "task_id": int(task_id),
            "product_draft_id": int(product_draft_id),
            "origin": str(origin or "source")[:32],
            "source_asset_id": str(source_asset_id or "")[:64],
            "media_asset_id": str(media_asset_id or "")[:36],
            "source_kind": str(source_kind or "")[:32],
            "identity_hash": identity[:64],
            "access_token": uuid4().hex,
            "managed_path": str(managed_path or ""),
            "source_url": str(source_url or ""),
            "content_hash": str(content_hash or "").strip().casefold()[:64],
            "content_type": str(content_type or "")[:64],
            "byte_size": max(0, int(byte_size or 0)),
            "width": max(0, int(width or 0)),
            "height": max(0, int(height or 0)),
            "availability": "local" if managed_path else "materializing",
        }
        with self.database.sessions.begin() as session:
            session.execute(
                sqlite_insert(PreviewImageAssetRow)
                .values(**values)
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
                    PreviewImageAssetRow.workspace_id == workspace,
                    PreviewImageAssetRow.task_id == int(task_id),
                    PreviewImageAssetRow.product_draft_id == int(product_draft_id),
                    PreviewImageAssetRow.identity_hash == identity[:64],
                )
            )
            if row is None:
                raise PreviewPublicationConflict(
                    "preview asset registration could not be loaded"
                )
            if not row.access_token:
                row.access_token = uuid4().hex
                session.flush()
            return self._asset(row)

    def get_asset(self, asset_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                )
            )
            return self._asset(row) if row else None

    def get_assets(
        self,
        asset_ids: Sequence[str],
        workspace_id: str,
        *,
        task_id: int | None = None,
        product_draft_id: int | None = None,
    ) -> list[dict[str, Any]]:
        ordered = list(dict.fromkeys(str(value or "").strip() for value in asset_ids))
        ordered = [value for value in ordered if value]
        if not ordered:
            return []
        with self.database.sessions() as session:
            statement = select(PreviewImageAssetRow).where(
                PreviewImageAssetRow.id.in_(ordered),
                PreviewImageAssetRow.workspace_id == str(workspace_id),
            )
            if task_id is not None:
                statement = statement.where(PreviewImageAssetRow.task_id == int(task_id))
            if product_draft_id is not None:
                statement = statement.where(
                    PreviewImageAssetRow.product_draft_id == int(product_draft_id)
                )
            rows = session.scalars(statement).all()
            indexed = {row.id: self._asset(row) for row in rows}
            return [indexed[value] for value in ordered if value in indexed]

    def list_assets(
        self,
        product_draft_id: int,
        workspace_id: str,
        *,
        task_id: int,
    ) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            statement = select(PreviewImageAssetRow).where(
                PreviewImageAssetRow.product_draft_id == int(product_draft_id),
                PreviewImageAssetRow.workspace_id == str(workspace_id),
                PreviewImageAssetRow.task_id == int(task_id),
            )
            rows = session.scalars(
                statement.order_by(PreviewImageAssetRow.created_at, PreviewImageAssetRow.id)
            ).all()
            return [self._asset(row) for row in rows]

    def list_media_proxies(
        self,
        product_draft_id: int,
        workspace_id: str,
        *,
        task_id: int,
    ) -> list[dict[str, Any]]:
        with self.database.sessions() as session:
            rows = session.scalars(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.product_draft_id == int(product_draft_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                    PreviewImageAssetRow.task_id == int(task_id),
                    PreviewImageAssetRow.media_asset_id != "",
                ).order_by(PreviewImageAssetRow.created_at, PreviewImageAssetRow.id)
            ).all()
            return [self._asset(row) for row in rows]

    def claim_materialization(
        self,
        asset_id: str,
        workspace_id: str,
        lease_seconds: int = 180,
    ) -> dict[str, Any]:
        now, cutoff = _lease_times(lease_seconds)
        token = uuid4().hex
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                )
            )
            if row is None:
                raise LookupError("preview image asset not found")
            if row.managed_path and row.content_hash:
                return self._asset(row)
            changed = session.execute(
                update(PreviewImageAssetRow)
                .where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                    or_(
                        PreviewImageAssetRow.materialize_claim_token == "",
                        PreviewImageAssetRow.materialize_claimed_at < cutoff,
                    ),
                )
                .values(
                    availability="materializing",
                    materialize_claim_token=token,
                    materialize_claimed_at=now,
                    error_code="",
                    error_message="",
                    updated_at=utc_now(),
                )
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict(
                    "preview image materialization is already active"
                )
            refreshed = session.scalar(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                )
            )
            value = self._asset(refreshed)
            value["materialize_claim_token"] = token
            return value

    def renew_materialization_claim(
        self,
        asset_id: str,
        workspace_id: str,
        claim_token: str,
    ) -> None:
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewImageAssetRow)
                .where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                    PreviewImageAssetRow.availability == "materializing",
                    PreviewImageAssetRow.materialize_claim_token == str(claim_token),
                )
                .values(materialize_claimed_at=utc_now(), updated_at=utc_now())
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict(
                    "preview image materialization claim changed"
                )

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
            changed = session.execute(
                update(PreviewImageAssetRow)
                .where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                    PreviewImageAssetRow.availability == "materializing",
                    PreviewImageAssetRow.materialize_claim_token == str(claim_token),
                )
                .values(
                    managed_path=str(managed_path),
                    content_hash=str(content_hash).casefold(),
                    content_type=str(content_type),
                    byte_size=max(0, int(byte_size)),
                    width=max(0, int(width)),
                    height=max(0, int(height)),
                    availability="local",
                    materialize_claim_token="",
                    materialize_claimed_at="",
                    error_code="",
                    error_message="",
                    updated_at=utc_now(),
                )
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict(
                    "preview image materialization claim changed"
                )
            row = session.scalar(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                )
            )
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
            changed = session.execute(
                update(PreviewImageAssetRow)
                .where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                    PreviewImageAssetRow.availability == "materializing",
                    PreviewImageAssetRow.materialize_claim_token == str(claim_token),
                )
                .values(
                    availability="materialize_failed",
                    materialize_claim_token="",
                    materialize_claimed_at="",
                    error_code=str(code or "materialization_failed")[:64],
                    error_message=str(message or "")[:240],
                    updated_at=utc_now(),
                )
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict(
                    "preview image materialization claim changed"
                )
            row = session.scalar(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                )
            )
            return self._asset(row)

    def update_asset_materialized(
        self,
        asset_id: str,
        workspace_id: str,
        *,
        managed_path: str,
        content_hash: str,
        content_type: str,
        byte_size: int,
        width: int,
        height: int,
    ) -> dict[str, Any]:
        """Persist already-owned local bytes (generated/upload/dimension paths)."""

        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewImageAssetRow)
                .where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                    PreviewImageAssetRow.materialize_claim_token == "",
                )
                .values(
                    managed_path=str(managed_path),
                    content_hash=str(content_hash).casefold(),
                    content_type=str(content_type),
                    byte_size=max(0, int(byte_size)),
                    width=max(0, int(width)),
                    height=max(0, int(height)),
                    availability="local",
                    error_code="",
                    error_message="",
                    updated_at=utc_now(),
                )
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict(
                    "preview image asset is missing or materialization is active"
                )
            row = session.scalar(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                )
            )
            return self._asset(row)

    def mark_asset_reused_public_url(
        self,
        asset_id: str,
        workspace_id: str,
        public_url: str,
        *,
        content_hash: str = "",
    ) -> dict[str, Any]:
        digest = str(content_hash or "").strip().casefold()
        with self.database.sessions.begin() as session:
            values: dict[str, Any] = {
                "availability": "published",
                "public_url": str(public_url),
                "error_code": "",
                "error_message": "",
                "updated_at": utc_now(),
            }
            if digest:
                values["content_hash"] = digest
            changed = session.execute(
                update(PreviewImageAssetRow)
                .where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                )
                .values(**values)
            )
            if changed.rowcount != 1:
                raise LookupError("preview image asset not found")
            if digest:
                session.execute(
                    sqlite_insert(PreviewImagePublicationRow)
                    .values(
                        workspace_id=str(workspace_id),
                        content_hash=digest,
                        status="published",
                        public_url=str(public_url),
                    )
                    .on_conflict_do_update(
                        index_elements=["workspace_id", "content_hash"],
                        set_={
                            "status": "published",
                            "public_url": str(public_url),
                            "claim_token": "",
                            "claimed_at": "",
                            "error_code": "",
                            "error_message": "",
                            "updated_at": utc_now(),
                        },
                    )
                )
                session.execute(
                    update(PreviewImageAssetRow)
                    .where(
                        PreviewImageAssetRow.workspace_id == str(workspace_id),
                        PreviewImageAssetRow.source_url == str(public_url),
                    )
                    .values(**values)
                )
            row = session.scalar(
                select(PreviewImageAssetRow).where(
                    PreviewImageAssetRow.id == str(asset_id),
                    PreviewImageAssetRow.workspace_id == str(workspace_id),
                )
            )
            return self._asset(row)

    def get_publication(
        self,
        workspace_id: str,
        content_hash: str,
    ) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.get(
                PreviewImagePublicationRow,
                (str(workspace_id), str(content_hash).casefold()),
            )
            return self._publication(row) if row else None

    def claim_publication(
        self,
        workspace_id: str,
        content_hash: str,
        lease_seconds: int = 180,
        *,
        content_type: str = "",
        byte_size: int = 0,
    ) -> dict[str, Any]:
        now, cutoff = _lease_times(lease_seconds)
        workspace = str(workspace_id)
        digest = str(content_hash).casefold()
        token = uuid4().hex
        with self.database.sessions.begin() as session:
            session.execute(
                sqlite_insert(PreviewImagePublicationRow)
                .values(
                    workspace_id=workspace,
                    content_hash=digest,
                    status="pending",
                    content_type=str(content_type or "")[:64],
                    byte_size=max(0, int(byte_size or 0)),
                )
                .on_conflict_do_nothing(
                    index_elements=["workspace_id", "content_hash"]
                )
            )
            existing = session.get(PreviewImagePublicationRow, (workspace, digest))
            if existing is None:
                raise PreviewPublicationConflict("publication row is missing")
            normalized_type = str(content_type or "")[:64]
            normalized_size = max(0, int(byte_size or 0))
            if (
                normalized_type
                and existing.content_type
                and existing.content_type != normalized_type
            ) or (
                normalized_size
                and int(existing.byte_size or 0)
                and int(existing.byte_size) != normalized_size
            ):
                raise PreviewPublicationConflict(
                    "preview publication metadata does not match its content hash"
                )
            claim_values: dict[str, Any] = {
                "status": "publishing",
                "claim_token": token,
                "claimed_at": now,
                "error_code": "",
                "error_message": "",
                "updated_at": utc_now(),
            }
            if normalized_type:
                claim_values["content_type"] = normalized_type
            if normalized_size:
                claim_values["byte_size"] = normalized_size
            claimed = session.execute(
                update(PreviewImagePublicationRow)
                .where(
                    PreviewImagePublicationRow.workspace_id == workspace,
                    PreviewImagePublicationRow.content_hash == digest,
                    PreviewImagePublicationRow.status != "published",
                    or_(
                        PreviewImagePublicationRow.status != "publishing",
                        PreviewImagePublicationRow.claimed_at < cutoff,
                    ),
                )
                .values(**claim_values)
            )
            row = session.get(PreviewImagePublicationRow, (workspace, digest))
            if row is None:
                raise PreviewPublicationConflict("publication row is missing")
            if row.status == "published":
                return self._publication(row)
            if claimed.rowcount != 1:
                raise PreviewPublicationConflict(
                    "preview image publication is already active"
                )
            session.execute(
                update(PreviewImageAssetRow)
                .where(
                    PreviewImageAssetRow.workspace_id == workspace,
                    PreviewImageAssetRow.content_hash == digest,
                    PreviewImageAssetRow.availability != "published",
                )
                .values(availability="publishing", updated_at=utc_now())
            )
            value = self._publication(row)
            value["claim_token"] = token
            return value

    def invalidate_publication(
        self,
        workspace_id: str,
        content_hash: str,
        public_url: str,
    ) -> None:
        """Reopen a receipt only after the caller proved its exact URL invalid."""
        workspace = str(workspace_id)
        digest = str(content_hash).casefold()
        with self.database.sessions.begin() as session:
            session.execute(
                update(PreviewImagePublicationRow)
                .where(
                    PreviewImagePublicationRow.workspace_id == workspace,
                    PreviewImagePublicationRow.content_hash == digest,
                    PreviewImagePublicationRow.status == "published",
                    PreviewImagePublicationRow.public_url == str(public_url),
                )
                .values(
                    status="pending",
                    public_url="",
                    error_code="published_object_invalid",
                    error_message="configured COS object is no longer publicly readable",
                    updated_at=utc_now(),
                )
            )
            session.execute(
                update(PreviewImageAssetRow)
                .where(
                    PreviewImageAssetRow.workspace_id == workspace,
                    PreviewImageAssetRow.content_hash == digest,
                    PreviewImageAssetRow.public_url == str(public_url),
                )
                .values(
                    availability="local",
                    public_url="",
                    updated_at=utc_now(),
                )
            )

    def renew_publication_claim(
        self,
        workspace_id: str,
        content_hash: str,
        claim_token: str,
    ) -> None:
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewImagePublicationRow)
                .where(
                    PreviewImagePublicationRow.workspace_id == str(workspace_id),
                    PreviewImagePublicationRow.content_hash
                    == str(content_hash).casefold(),
                    PreviewImagePublicationRow.status == "publishing",
                    PreviewImagePublicationRow.claim_token == str(claim_token),
                )
                .values(claimed_at=utc_now(), updated_at=utc_now())
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict("preview publication claim changed")

    def mark_publication_succeeded(
        self,
        workspace_id: str,
        content_hash: str,
        claim_token: str,
        public_url: str,
    ) -> dict[str, Any]:
        workspace = str(workspace_id)
        digest = str(content_hash).casefold()
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewImagePublicationRow)
                .where(
                    PreviewImagePublicationRow.workspace_id == workspace,
                    PreviewImagePublicationRow.content_hash == digest,
                    PreviewImagePublicationRow.status == "publishing",
                    PreviewImagePublicationRow.claim_token == str(claim_token),
                )
                .values(
                    status="published",
                    public_url=str(public_url),
                    claim_token="",
                    claimed_at="",
                    error_code="",
                    error_message="",
                    updated_at=utc_now(),
                )
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict("preview publication claim changed")
            session.execute(
                update(PreviewImageAssetRow)
                .where(
                    PreviewImageAssetRow.workspace_id == workspace,
                    PreviewImageAssetRow.content_hash == digest,
                )
                .values(
                    availability="published",
                    public_url=str(public_url),
                    error_code="",
                    error_message="",
                    updated_at=utc_now(),
                )
            )
            row = session.get(PreviewImagePublicationRow, (workspace, digest))
            return self._publication(row)

    def mark_publication_failed(
        self,
        workspace_id: str,
        content_hash: str,
        claim_token: str,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        workspace = str(workspace_id)
        digest = str(content_hash).casefold()
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewImagePublicationRow)
                .where(
                    PreviewImagePublicationRow.workspace_id == workspace,
                    PreviewImagePublicationRow.content_hash == digest,
                    PreviewImagePublicationRow.status == "publishing",
                    PreviewImagePublicationRow.claim_token == str(claim_token),
                )
                .values(
                    status="publish_failed",
                    claim_token="",
                    claimed_at="",
                    error_code=str(code or "publication_failed")[:64],
                    error_message=str(message or "")[:240],
                    updated_at=utc_now(),
                )
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict("preview publication claim changed")
            session.execute(
                update(PreviewImageAssetRow)
                .where(
                    PreviewImageAssetRow.workspace_id == workspace,
                    PreviewImageAssetRow.content_hash == digest,
                    PreviewImageAssetRow.availability != "published",
                )
                .values(
                    availability="publish_failed",
                    error_code=str(code or "publication_failed")[:64],
                    error_message=str(message or "")[:240],
                    updated_at=utc_now(),
                )
            )
            row = session.get(PreviewImagePublicationRow, (workspace, digest))
            return self._publication(row)

    def save_preview_manifests(
        self,
        task_id: int,
        items: Sequence[Mapping[str, Any]],
        *,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        """Save a complete preview batch with one SQL transaction and CAS per draft."""

        with self.database.sessions.begin() as session:
            return self._save_preview_items(
                session,
                int(task_id),
                items,
                workspace_id=str(workspace_id),
                require_complete_finalize=False,
            )

    def create_finalize_run(
        self,
        *,
        workspace_id: str,
        task_id: int,
        snapshot: Sequence[Mapping[str, Any]] = (),
        items: Sequence[Mapping[str, Any]] | None = None,
        snapshot_hash: str = "",
        idempotency_key: str = "",
        request_hash: str = "",
        total_count: int | None = None,
    ) -> dict[str, Any]:
        raw_request = [dict(entry) for entry in (items if items is not None else snapshot)]
        request_digest = str(
            request_hash or calculate_snapshot_hash(raw_request)
        ).casefold()
        if len(request_digest) != 64:
            raise ValueError("preview finalization request hash must be a SHA-256 value")
        workspace = str(workspace_id or "").strip()
        if not workspace:
            raise ValueError("preview finalization workspace is required")
        supplied_key = str(idempotency_key or "").strip()
        # Request identity is available before any draft CAS. This lets a
        # repeated request return its persisted run even after revisions moved.
        stored_key = (supplied_key or f"request:{request_digest}")[:255]
        run_id = str(
            uuid5(
                NAMESPACE_URL,
                f"preview-finalize:{workspace}:{int(task_id)}:{stored_key}",
            )
        )
        with self.database.sessions.begin() as session:
            existing = session.scalar(
                select(PreviewFinalizeRunRow).where(
                    PreviewFinalizeRunRow.workspace_id == workspace,
                    PreviewFinalizeRunRow.task_id == int(task_id),
                    PreviewFinalizeRunRow.idempotency_key == stored_key,
                )
            )
            if existing is not None:
                if existing.request_hash and existing.request_hash != request_digest:
                    raise PreviewIdempotencyConflict(
                        "preview finalization idempotency key was reused with another request"
                    )
                if not existing.request_hash:
                    existing.request_hash = request_digest
                return self._run(existing)

            if items is not None:
                entries = self._save_preview_items(
                    session,
                    int(task_id),
                    items,
                    workspace_id=workspace,
                    require_complete_finalize=True,
                )
            else:
                entries = [dict(entry) for entry in snapshot]
            calculated_digest = calculate_snapshot_hash(entries)
            supplied_digest = str(snapshot_hash or "").casefold()
            if supplied_digest and supplied_digest != calculated_digest:
                raise ValueError(
                    "preview finalization snapshot hash does not match its snapshot"
                )
            digest = supplied_digest or calculated_digest

            session.execute(
                sqlite_insert(PreviewFinalizeRunRow)
                .values(
                    id=run_id,
                    workspace_id=workspace,
                    task_id=int(task_id),
                    idempotency_key=stored_key,
                    request_hash=request_digest,
                    snapshot_hash=digest,
                    snapshot_json=_dumps(entries),
                    status="queued",
                    total_count=(
                        max(0, int(total_count))
                        if total_count is not None
                        else len(
                            {
                                str(asset_id)
                                for entry in entries
                                for asset_id in entry.get("live_asset_ids", ())
                                if str(asset_id or "").strip()
                            }
                        )
                    ),
                )
                .on_conflict_do_nothing()
            )
            row = session.scalar(
                select(PreviewFinalizeRunRow).where(
                    PreviewFinalizeRunRow.workspace_id == workspace,
                    PreviewFinalizeRunRow.task_id == int(task_id),
                    PreviewFinalizeRunRow.idempotency_key == stored_key,
                )
            )
            if row is None:
                # A semantically identical snapshot may have won through the
                # separate snapshot uniqueness boundary with another key.
                row = session.scalar(
                    select(PreviewFinalizeRunRow).where(
                        PreviewFinalizeRunRow.workspace_id == workspace,
                        PreviewFinalizeRunRow.task_id == int(task_id),
                        PreviewFinalizeRunRow.snapshot_hash == digest,
                    )
                )
            if row is None:
                raise PreviewPublicationConflict(
                    "preview finalization run could not be loaded"
                )
            if row.request_hash and row.request_hash != request_digest:
                raise PreviewIdempotencyConflict(
                    "preview finalization request conflicts with the stored run"
                )
            return self._run(row)

    def _save_preview_items(
        self,
        session: Any,
        task_id: int,
        items: Sequence[Mapping[str, Any]],
        *,
        workspace_id: str,
        require_complete_finalize: bool,
    ) -> list[dict[str, Any]]:
        task = session.scalar(
            select(ProcessingTaskRow).where(
                ProcessingTaskRow.id == int(task_id),
                ProcessingTaskRow.workspace_id == workspace_id,
            )
        )
        if task is None:
            raise LookupError("preview image task not found")
        task_items = session.scalars(
            select(ProcessingTaskItemRow).where(
                ProcessingTaskItemRow.task_id == int(task_id)
            )
        ).all()
        task_items_by_draft = {
            int(row.product_draft_id): row
            for row in task_items
            if row.product_draft_id is not None
        }
        owned_draft_ids = {
            int(row.product_draft_id)
            for row in task_items
            if row.product_draft_id is not None
        }
        normalized_entries = [dict(entry) for entry in items]
        provided_ids = [
            int(entry.get("product_draft_id") or 0) for entry in normalized_entries
        ]
        if any(value <= 0 for value in provided_ids) or len(provided_ids) != len(
            set(provided_ids)
        ):
            raise ValueError("preview save must contain each positive draft id once")
        if not set(provided_ids).issubset(owned_draft_ids):
            raise LookupError("preview image target does not belong to this task")

        if require_complete_finalize:
            exportable_ids = {
                int(row.product_draft_id)
                for row in task_items
                if row.product_draft_id is not None
                and bool(
                    str(
                        (_loads(row.result_json, {}) or {}).get("optimized_title")
                        or ""
                    ).strip()
                )
            }
            if set(provided_ids) != exportable_ids:
                raise ValueError(
                    "preview finalization must contain every exportable draft exactly once"
                )

        snapshots: list[dict[str, Any]] = []
        for entry, draft_id in sorted(
            zip(normalized_entries, provided_ids, strict=True),
            key=lambda pair: pair[1],
        ):
            if "expected_preview_revision" not in entry:
                raise PreviewRevisionConflict(
                    "expected preview revision is required"
                )
            expected_revision = int(entry["expected_preview_revision"])
            draft = session.scalar(
                select(ProductDraftRow).where(
                    ProductDraftRow.id == draft_id,
                    ProductDraftRow.workspace_id == workspace_id,
                )
            )
            if draft is None:
                raise LookupError("preview image draft not found")
            current_revision = int(draft.preview_revision or 0)
            if current_revision != expected_revision:
                raise PreviewRevisionConflict(
                    f"preview revision conflict: expected {expected_revision}, "
                    f"current {current_revision}"
                )
            item_row = task_items_by_draft.get(draft_id)
            current_result_version = task_item_result_version(
                _loads(item_row.result_json, {}) if item_row is not None else {}
            )
            if require_complete_finalize:
                expected_result_version = str(entry.get("expected_result_version") or "").casefold()
                if expected_result_version != current_result_version:
                    raise PreviewRevisionConflict(
                        "task item result version changed before finalization"
                    )
            raw_overrides = entry.get("overrides")
            overrides = dict(raw_overrides) if isinstance(raw_overrides, Mapping) else {}
            if MANIFEST_KEY in overrides:
                manifest = PreviewImageManifest.from_value(overrides.get(MANIFEST_KEY))
                overrides[MANIFEST_KEY] = manifest.as_dict()
            else:
                manifest = PreviewImageManifest()

            live_ids = manifest.live_asset_ids()
            library_ids = tuple(manifest.library_asset_ids)
            referenced_ids = tuple(dict.fromkeys((*live_ids, *library_ids)))
            rows_by_id: dict[str, PreviewImageAssetRow] = {}
            if referenced_ids:
                rows = session.scalars(
                    select(PreviewImageAssetRow).where(
                        PreviewImageAssetRow.id.in_(referenced_ids),
                        PreviewImageAssetRow.workspace_id == workspace_id,
                        PreviewImageAssetRow.task_id == int(task_id),
                        PreviewImageAssetRow.product_draft_id == draft_id,
                    )
                ).all()
                rows_by_id = {row.id: row for row in rows}
                if set(rows_by_id) != set(referenced_ids):
                    raise LookupError(
                        "preview manifest references an asset outside its task or draft"
                    )
            # A source proxy must be added to the library before it may be
            # selected for export. Processed assets are always library-eligible.
            for asset_id in live_ids:
                row = rows_by_id.get(asset_id)
                if row is not None and row.source_kind and asset_id not in library_ids:
                    raise PreviewSourceNotInLibrary("请先将处理前图片加入素材库")
            if require_complete_finalize:
                if not manifest.main_asset_id:
                    raise ValueError("preview finalization requires a main image")
                if manifest.main_asset_id not in manifest.carousel_asset_ids:
                    raise ValueError(
                        "preview finalization main image must be retained in carousel"
                    )

            serialized = _dumps(overrides)
            next_revision = current_revision
            if draft.preview_overrides_json != serialized:
                changed = session.execute(
                    update(ProductDraftRow)
                    .where(
                        ProductDraftRow.id == draft_id,
                        ProductDraftRow.workspace_id == workspace_id,
                        ProductDraftRow.preview_revision == expected_revision,
                    )
                    .values(
                        preview_overrides_json=serialized,
                        preview_revision=expected_revision + 1,
                        updated_at=utc_now(),
                    )
                )
                if changed.rowcount != 1:
                    raise PreviewRevisionConflict(
                        "preview revision changed during save"
                    )
                next_revision = expected_revision + 1
            snapshots.append(
                {
                    "product_draft_id": draft_id,
                    "preview_revision": next_revision,
                    "result_version": current_result_version,
                    "overrides": overrides,
                    "manifest": manifest.as_dict(),
                    "live_asset_ids": list(live_ids),
                }
            )
        return snapshots

    def get_finalize_run(
        self,
        run_id: str,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        with self.database.sessions() as session:
            row = session.scalar(
                select(PreviewFinalizeRunRow).where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                )
            )
            return self._run(row) if row else None

    def get_finalize(
        self,
        run_id: str,
        *,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        return self.get_finalize_run(run_id, workspace_id)

    def get_finalize_by_idempotency(
        self,
        task_id: int,
        idempotency_key: str,
        *,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        key = str(idempotency_key or "").strip()
        if not key:
            return None
        with self.database.sessions() as session:
            row = session.scalar(
                select(PreviewFinalizeRunRow).where(
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                    PreviewFinalizeRunRow.task_id == int(task_id),
                    PreviewFinalizeRunRow.idempotency_key == key[:255],
                )
            )
            return self._run(row) if row else None

    def claim_finalize_run(
        self,
        run_id: str,
        workspace_id: str,
        lease_seconds: int = 180,
    ) -> dict[str, Any]:
        now, cutoff = _lease_times(lease_seconds)
        token = uuid4().hex
        with self.database.sessions.begin() as session:
            claimed = session.execute(
                update(PreviewFinalizeRunRow)
                .where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                    or_(
                        PreviewFinalizeRunRow.status.in_(["queued", "publish_failed"]),
                        and_(
                            PreviewFinalizeRunRow.status == "publishing",
                            PreviewFinalizeRunRow.claimed_at < cutoff,
                        ),
                    ),
                )
                .values(
                    status="publishing",
                    claim_token=token,
                    claimed_at=now,
                    updated_at=utc_now(),
                )
            )
            row = session.scalar(
                select(PreviewFinalizeRunRow).where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                )
            )
            if row is None:
                raise LookupError("preview finalization run not found")
            if claimed.rowcount != 1:
                if row.status in {"completed", "stale"}:
                    return self._run(row)
                raise PreviewPublicationConflict(
                    "preview finalization is already active"
                )
            value = self._run(row)
            value["claim_token"] = token
            return value

    def recover_interrupted_finalize_runs(self) -> list[dict[str, Any]]:
        """Requeue process-lost finalizations and release their local leases."""

        with self.database.sessions.begin() as session:
            now = utc_now()
            session.execute(
                update(PreviewFinalizeRunRow)
                .where(PreviewFinalizeRunRow.status == "publishing")
                .values(
                    status="queued",
                    claim_token="",
                    claimed_at="",
                    updated_at=now,
                )
            )
            session.execute(
                update(PreviewImagePublicationRow)
                .where(PreviewImagePublicationRow.status == "publishing")
                .values(
                    status="publish_failed",
                    claim_token="",
                    claimed_at="",
                    error_code="worker_interrupted",
                    error_message="application restarted during preview publication",
                    updated_at=now,
                )
            )
            session.execute(
                update(PreviewImageAssetRow)
                .where(PreviewImageAssetRow.availability == "publishing")
                .values(availability="local", updated_at=now)
            )
            session.execute(
                update(PreviewImageAssetRow)
                .where(PreviewImageAssetRow.availability == "materializing")
                .values(
                    availability="materialize_failed",
                    materialize_claim_token="",
                    materialize_claimed_at="",
                    error_code="worker_interrupted",
                    error_message="application restarted during preview materialization",
                    updated_at=now,
                )
            )
            rows = session.scalars(
                select(PreviewFinalizeRunRow)
                .where(PreviewFinalizeRunRow.status == "queued")
                .order_by(PreviewFinalizeRunRow.created_at, PreviewFinalizeRunRow.id)
            ).all()
            return [self._run(row) for row in rows]

    def renew_finalize_claim(
        self,
        run_id: str,
        workspace_id: str,
        claim_token: str,
    ) -> None:
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewFinalizeRunRow)
                .where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                    PreviewFinalizeRunRow.status == "publishing",
                    PreviewFinalizeRunRow.claim_token == str(claim_token),
                )
                .values(claimed_at=utc_now(), updated_at=utc_now())
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict(
                    "preview finalization claim changed"
                )

    def update_finalize_progress(
        self,
        run_id: str,
        workspace_id: str,
        claim_token: str,
        *,
        published_count: int,
        failed_count: int,
        errors: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "published_count": max(0, int(published_count)),
            "failed_count": max(0, int(failed_count)),
            "claimed_at": utc_now(),
            "updated_at": utc_now(),
        }
        if errors is not None:
            values["errors_json"] = _dumps([dict(error) for error in errors])
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewFinalizeRunRow)
                .where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                    PreviewFinalizeRunRow.status == "publishing",
                    PreviewFinalizeRunRow.claim_token == str(claim_token),
                )
                .values(**values)
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict(
                    "preview finalization claim changed"
                )
            row = session.scalar(
                select(PreviewFinalizeRunRow).where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                )
            )
            return self._run(row)

    def queue_finalize_retry(
        self,
        run_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewFinalizeRunRow)
                .where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                    PreviewFinalizeRunRow.status == "publish_failed",
                    PreviewFinalizeRunRow.claim_token == "",
                )
                .values(status="queued", updated_at=utc_now())
            )
            row = session.scalar(
                select(PreviewFinalizeRunRow).where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                )
            )
            if row is None:
                raise LookupError("preview finalization run not found")
            if changed.rowcount != 1 and row.status not in {"queued", "completed", "stale"}:
                raise PreviewPublicationConflict(
                    "preview finalization cannot be retried in its current state"
                )
            return self._run(row)

    def mark_finalize_failed(
        self,
        run_id: str,
        workspace_id: str,
        claim_token: str,
        errors: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        serialized = [dict(error) for error in errors]
        return self._finish_finalize_run(
            run_id,
            workspace_id,
            claim_token,
            "publish_failed",
            {
                "failed_count": len(serialized),
                "errors_json": _dumps(serialized),
                "workbook_path": "",
                "row_count": 0,
                "product_count": 0,
            },
        )

    def mark_finalize_stale(
        self,
        run_id: str,
        workspace_id: str,
        claim_token: str,
    ) -> dict[str, Any]:
        return self._finish_finalize_run(
            run_id,
            workspace_id,
            claim_token,
            "stale",
            {"workbook_path": "", "row_count": 0, "product_count": 0},
        )

    def mark_finalize_completed(
        self,
        run_id: str,
        workspace_id: str,
        claim_token: str,
        *,
        workbook_path: str,
        row_count: int,
        product_count: int,
        snapshot: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            row = session.scalar(
                select(PreviewFinalizeRunRow).where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                    PreviewFinalizeRunRow.status == "publishing",
                    PreviewFinalizeRunRow.claim_token == str(claim_token),
                )
            )
            if row is None:
                raise PreviewPublicationConflict("preview finalization claim changed")
            entries = [dict(entry) for entry in (snapshot or _loads(row.snapshot_json, []))]
            for entry in entries:
                draft = session.scalar(
                    select(ProductDraftRow).where(
                        ProductDraftRow.id == int(entry.get("product_draft_id") or 0),
                        ProductDraftRow.workspace_id == str(workspace_id),
                    )
                )
                if draft is None or int(draft.preview_revision or 0) != int(
                    entry.get("preview_revision") or -1
                ):
                    row.status = "stale"
                    row.claim_token = ""
                    row.claimed_at = ""
                    row.workbook_path = ""
                    row.updated_at = utc_now()
                    session.flush()
                    return self._run(row)
                task_item = session.scalar(
                    select(ProcessingTaskItemRow).where(
                        ProcessingTaskItemRow.task_id == int(row.task_id),
                        ProcessingTaskItemRow.product_draft_id
                        == int(entry.get("product_draft_id") or 0),
                    )
                )
                if task_item is None or task_item_result_version(
                    _loads(task_item.result_json, {})
                ) != str(entry.get("result_version") or "").casefold():
                    row.status = "stale"
                    row.claim_token = ""
                    row.claimed_at = ""
                    row.workbook_path = ""
                    row.updated_at = utc_now()
                    session.flush()
                    return self._run(row)
            row.status = "completed"
            row.published_count = int(row.total_count or 0)
            row.failed_count = 0
            row.errors_json = "[]"
            row.workbook_path = str(workbook_path)
            row.row_count = max(0, int(row_count))
            row.product_count = max(0, int(product_count))
            row.claim_token = ""
            row.claimed_at = ""
            row.updated_at = utc_now()
            session.flush()
            return self._run(row)

    def _finish_finalize_run(
        self,
        run_id: str,
        workspace_id: str,
        claim_token: str,
        status: str,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        with self.database.sessions.begin() as session:
            changed = session.execute(
                update(PreviewFinalizeRunRow)
                .where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                    PreviewFinalizeRunRow.status == "publishing",
                    PreviewFinalizeRunRow.claim_token == str(claim_token),
                )
                .values(
                    **dict(values),
                    status=str(status),
                    claim_token="",
                    claimed_at="",
                    updated_at=utc_now(),
                )
            )
            if changed.rowcount != 1:
                raise PreviewPublicationConflict(
                    "preview finalization claim changed"
                )
            row = session.scalar(
                select(PreviewFinalizeRunRow).where(
                    PreviewFinalizeRunRow.id == str(run_id),
                    PreviewFinalizeRunRow.workspace_id == str(workspace_id),
                )
            )
            return self._run(row)

    @staticmethod
    def _asset(row: PreviewImageAssetRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "task_id": row.task_id,
            "product_draft_id": row.product_draft_id,
            "origin": row.origin,
            "source_asset_id": row.source_asset_id,
            "media_asset_id": row.media_asset_id,
            "source_kind": row.source_kind,
            "identity_hash": row.identity_hash,
            "access_token": row.access_token,
            "managed_path": row.managed_path,
            "source_url": row.source_url,
            "content_hash": row.content_hash,
            "content_type": row.content_type,
            "byte_size": int(row.byte_size or 0),
            "width": int(row.width or 0),
            "height": int(row.height or 0),
            "availability": row.availability,
            "public_url": row.public_url,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "materialize_claim_token": row.materialize_claim_token,
            "materialize_claimed_at": row.materialize_claimed_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _publication(row: PreviewImagePublicationRow) -> dict[str, Any]:
        return {
            "workspace_id": row.workspace_id,
            "content_hash": row.content_hash,
            "status": row.status,
            "public_url": row.public_url,
            "content_type": row.content_type,
            "byte_size": int(row.byte_size or 0),
            "claim_token": row.claim_token,
            "claimed_at": row.claimed_at,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _run(row: PreviewFinalizeRunRow) -> dict[str, Any]:
        errors = _loads(row.errors_json, [])
        snapshot = _loads(row.snapshot_json, [])
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "task_id": row.task_id,
            "idempotency_key": row.idempotency_key,
            "request_hash": row.request_hash,
            "snapshot_hash": row.snapshot_hash,
            "snapshot": snapshot if isinstance(snapshot, list) else [],
            "status": row.status,
            "total_count": int(row.total_count or 0),
            "published_count": int(row.published_count or 0),
            "failed_count": int(row.failed_count or 0),
            "errors": errors if isinstance(errors, list) else [],
            "claim_token": row.claim_token,
            "claimed_at": row.claimed_at,
            "workbook_path": row.workbook_path,
            "workbook_ready": bool(row.status == "completed" and row.workbook_path),
            "row_count": int(row.row_count or 0),
            "product_count": int(row.product_count or 0),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
