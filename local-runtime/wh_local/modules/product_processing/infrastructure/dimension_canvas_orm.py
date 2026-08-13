from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .orm import Base, utc_now


class DimensionCanvasBatchRow(Base):
    __tablename__ = "product_processing_dimension_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    source_task_id: Mapped[int] = mapped_column(
        ForeignKey("product_processing_tasks.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(
        String(64), default=utc_now, onupdate=utc_now
    )


class DimensionCanvasItemRow(Base):
    __tablename__ = "product_processing_dimension_items"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "task_id",
            "task_item_id",
            "product_draft_id",
            name="uq_dimension_item_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("product_processing_dimension_batches.id", ondelete="CASCADE"),
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    task_id: Mapped[int] = mapped_column(Integer, index=True)
    task_item_id: Mapped[int] = mapped_column(Integer, index=True)
    product_draft_id: Mapped[int] = mapped_column(Integer, index=True)
    skc: Mapped[str] = mapped_column(String(255), default="")
    source_preview_revision: Mapped[int] = mapped_column(Integer, default=0)
    selected_source_asset_id: Mapped[str] = mapped_column(String(36), default="")
    target_slot_id: Mapped[str] = mapped_column(
        String(128), default="carousel.dimension_background"
    )
    physical_dimensions_json: Mapped[str] = mapped_column(Text, default="{}")
    annotations_json: Mapped[str] = mapped_column(Text, default="[]")
    canvas_settings_json: Mapped[str] = mapped_column(Text, default="{}")
    state: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    item_revision: Mapped[int] = mapped_column(Integer, default=0)
    render_revision: Mapped[int] = mapped_column(Integer, default=0)
    render_input_hash: Mapped[str] = mapped_column(String(64), default="")
    rendered_input_hash: Mapped[str] = mapped_column(String(64), default="")
    render_asset_id: Mapped[str] = mapped_column(String(36), default="")
    publish_claim_token: Mapped[str] = mapped_column(String(64), default="")
    publish_claimed_at: Mapped[str] = mapped_column(String(64), default="")
    error_code: Mapped[str] = mapped_column(String(64), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(
        String(64), default=utc_now, onupdate=utc_now
    )


class DimensionCanvasAssetRow(Base):
    __tablename__ = "product_processing_dimension_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("product_processing_dimension_items.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    managed_path: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(128), default="")
    availability: Mapped[str] = mapped_column(
        String(32), default="metadata", index=True
    )
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)


class DimensionCanvasChangeSetRow(Base):
    __tablename__ = "product_processing_dimension_change_sets"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_dimension_change_set_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("product_processing_dimension_batches.id", ondelete="CASCADE"),
        index=True,
    )
    source_task_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default="pending_review", index=True
    )
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    accepted_at: Mapped[str] = mapped_column(String(64), default="")


class DimensionCanvasChangeItemRow(Base):
    __tablename__ = "product_processing_dimension_change_items"
    __table_args__ = (
        UniqueConstraint(
            "change_set_id",
            "dimension_item_id",
            name="uq_dimension_change_item",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    change_set_id: Mapped[str] = mapped_column(
        ForeignKey(
            "product_processing_dimension_change_sets.id", ondelete="CASCADE"
        ),
        index=True,
    )
    dimension_item_id: Mapped[str] = mapped_column(
        ForeignKey("product_processing_dimension_items.id", ondelete="CASCADE"),
        index=True,
    )
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
    change_set_id: Mapped[str] = mapped_column(
        ForeignKey(
            "product_processing_dimension_change_sets.id", ondelete="CASCADE"
        ),
        index=True,
    )
    kind: Mapped[str] = mapped_column(
        String(64), default="dimension_change_set"
    )
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    read_at: Mapped[str] = mapped_column(String(64), default="")
