from __future__ import annotations

from uuid import uuid4

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .orm import Base, utc_now


class PreviewImageAssetRow(Base):
    __tablename__ = "product_processing_preview_image_assets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "task_id",
            "product_draft_id",
            "identity_hash",
            name="uq_preview_asset_workspace_task_draft_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("product_processing_tasks.id", ondelete="CASCADE"), index=True
    )
    product_draft_id: Mapped[int] = mapped_column(
        ForeignKey("product_processing_drafts.id", ondelete="CASCADE"), index=True
    )
    origin: Mapped[str] = mapped_column(String(32), index=True)
    source_asset_id: Mapped[str] = mapped_column(String(64), default="")
    identity_hash: Mapped[str] = mapped_column(String(64))
    access_token: Mapped[str] = mapped_column(String(64), default=lambda: uuid4().hex)
    managed_path: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    content_type: Mapped[str] = mapped_column(String(64), default="image/jpeg")
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    availability: Mapped[str] = mapped_column(String(32), default="local", index=True)
    public_url: Mapped[str] = mapped_column(Text, default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    materialize_claim_token: Mapped[str] = mapped_column(String(64), default="")
    materialize_claimed_at: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class PreviewImagePublicationRow(Base):
    __tablename__ = "product_processing_preview_publications"

    workspace_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    public_url: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(String(64), default="")
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    claim_token: Mapped[str] = mapped_column(String(64), default="")
    claimed_at: Mapped[str] = mapped_column(String(64), default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class PreviewFinalizeRunRow(Base):
    __tablename__ = "product_processing_preview_finalize_runs"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "task_id",
            "snapshot_hash",
            name="uq_preview_finalize_snapshot",
        ),
        UniqueConstraint(
            "workspace_id",
            "task_id",
            "idempotency_key",
            name="uq_preview_finalize_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("product_processing_tasks.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    snapshot_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    published_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    claim_token: Mapped[str] = mapped_column(String(64), default="")
    claimed_at: Mapped[str] = mapped_column(String(64), default="")
    workbook_path: Mapped[str] = mapped_column(Text, default="")
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    product_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)
