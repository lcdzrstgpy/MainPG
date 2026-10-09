from __future__ import annotations

from uuid import uuid4

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .orm import Base, utc_now


def _new_id() -> str:
    return uuid4().hex


class MediaAssetRow(Base):
    """Canonical image asset. Identity is asset_id; URL/path are never authorities."""

    __tablename__ = "product_processing_media_assets"
    __table_args__ = (
        Index(
            "uq_media_asset_workspace_source_identity",
            "workspace_id",
            "source_identity_hash",
            unique=True,
            sqlite_where=text("source_identity_hash <> ''"),
        ),
        Index(
            "uq_media_asset_workspace_local_content",
            "workspace_id",
            "content_hash",
            unique=True,
            sqlite_where=text("source_identity_hash = '' AND content_hash <> ''"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    origin: Mapped[str] = mapped_column(String(32), default="remote_source", index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    source_identity_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    access_token: Mapped[str] = mapped_column(String(64), default=lambda: uuid4().hex)
    managed_path: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(String(64), default="")
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    claim_token: Mapped[str] = mapped_column(String(64), default="")
    claimed_at: Mapped[str] = mapped_column(String(64), default="")
    next_retry_at: Mapped[str] = mapped_column(String(64), default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class MediaBindingRow(Base):
    """Business binding: which draft/task/SKU/carousel slot uses an asset."""

    __tablename__ = "product_processing_media_bindings"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "binding_key",
            name="uq_media_binding_workspace_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("product_processing_media_assets.id", ondelete="CASCADE"),
        index=True,
    )
    product_draft_id: Mapped[int] = mapped_column(
        ForeignKey("product_processing_drafts.id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    task_item_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    role: Mapped[str] = mapped_column(String(32), default="gallery", index=True)
    slot_id: Mapped[str] = mapped_column(String(128), default="")
    sku_id: Mapped[str] = mapped_column(String(255), default="")
    variant_label: Mapped[str] = mapped_column(String(255), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    binding_key: Mapped[str] = mapped_column(String(64))
    active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)
