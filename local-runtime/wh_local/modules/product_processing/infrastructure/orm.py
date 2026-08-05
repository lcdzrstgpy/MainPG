from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class ProductDraftRow(Base):
    __tablename__ = "product_processing_drafts"
    __table_args__ = (
        UniqueConstraint("workspace_id", "candidate_id", name="uq_product_processing_workspace_candidate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(255), default="local", index=True)
    source_type: Mapped[str] = mapped_column(String(64), default="manual", index=True)
    source_ref: Mapped[str] = mapped_column(Text, default="")
    candidate_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    selection_run_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    handoff_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    handoff_idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    skc: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_name: Mapped[str] = mapped_column(Text, default="")
    title: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(Text, default="")
    image_path: Mapped[str] = mapped_column(Text, default="")
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    declared_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    raw_payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class ProcessingTaskRow(Base):
    __tablename__ = "product_processing_tasks"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_product_processing_workspace_task_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(255), default="local", index=True)
    title: Mapped[str] = mapped_column(Text, default="产品处理任务")
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    preflight_only: Mapped[bool] = mapped_column(Boolean, default=False)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_file: Mapped[str] = mapped_column(Text, default="")
    error_report_file: Mapped[str] = mapped_column(Text, default="")
    video_manifest_file: Mapped[str] = mapped_column(Text, default="")
    cleared_from_product_processing: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)

    items: Mapped[list["ProcessingTaskItemRow"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="ProcessingTaskItemRow.id"
    )


class ProcessingTaskItemRow(Base):
    __tablename__ = "product_processing_task_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("product_processing_tasks.id", ondelete="CASCADE"), index=True)
    product_draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_processing_drafts.id", ondelete="SET NULL"), nullable=True
    )
    skc: Mapped[str] = mapped_column(String(255), default="")
    spu: Mapped[str] = mapped_column(String(255), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    image_url: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)

    task: Mapped[ProcessingTaskRow] = relationship(back_populates="items")


class DailySelectionIntakeRow(Base):
    __tablename__ = "product_processing_daily_selection_intakes"
    __table_args__ = (
        UniqueConstraint("workspace_id", "run_id", name="uq_product_processing_workspace_run"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(255), default="local", index=True)
    run_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed")
    criteria_json: Mapped[str] = mapped_column(Text, default="{}")
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    errors_json: Mapped[str] = mapped_column(Text, default="[]")
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class EnginePromptRow(Base):
    __tablename__ = "product_processing_prompts"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    custom: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class SourceImageAssetRow(Base):
    __tablename__ = "product_processing_source_images"
    __table_args__ = (UniqueConstraint("product_draft_id", "url", name="uq_product_processing_source_image"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_draft_id: Mapped[int] = mapped_column(
        ForeignKey("product_processing_drafts.id", ondelete="CASCADE"), index=True
    )
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_processing_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), default="source")
    url: Mapped[str] = mapped_column(Text)
    local_path: Mapped[str] = mapped_column(Text, default="")
    sync_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    sync_error: Mapped[str] = mapped_column(Text, default="")
    sync_claimed_at: Mapped[str] = mapped_column(String(64), default="")
    sync_claim_token: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)


class DailySelectionHandoffReceiptRow(Base):
    __tablename__ = "product_processing_handoff_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    handoff_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    run_id: Mapped[str] = mapped_column(String(255), index=True)
    candidate_id: Mapped[str] = mapped_column(String(255), index=True)
    product_draft_id: Mapped[int] = mapped_column(
        ForeignKey("product_processing_drafts.id", ondelete="CASCADE"), index=True
    )
    source_status: Mapped[str] = mapped_column(String(32), default="pending")
    consumer_status: Mapped[str] = mapped_column(String(32), default="consumed")
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
