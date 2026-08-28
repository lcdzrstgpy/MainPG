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
    preview_overrides_json: Mapped[str] = mapped_column(Text, default="{}")
    preview_revision: Mapped[int] = mapped_column(Integer, default=0)
    media_contract_version: Mapped[int] = mapped_column(Integer, default=1)
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


class ProductProcessingBillingAttemptRow(Base):
    """Credential-free local coordinator for one remote billing attempt."""

    __tablename__ = "product_processing_billing_attempts"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "item_id",
            "kind",
            "attempt_ordinal",
            name="uq_product_processing_billing_attempt",
        ),
        UniqueConstraint("idempotency_key", name="uq_product_processing_billing_idempotency"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(255), default="local", index=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("product_processing_tasks.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("product_processing_task_items.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    feature_key: Mapped[str] = mapped_column(String(128))
    account_id: Mapped[str] = mapped_column(String(255), index=True)
    attempt_ordinal: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    usage_id: Mapped[str] = mapped_column(String(255), default="", index=True)
    remote_status: Mapped[str] = mapped_column(String(32), default="")
    desired_outcome: Mapped[str] = mapped_column(String(32), default="")
    settlement_state: Mapped[str] = mapped_column(String(32), default="reserving", index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class ProcessingStageReceiptRow(Base):
    """Durable evidence for one completed processing stage of one task item."""

    __tablename__ = "product_processing_stage_receipts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "task_item_id",
            "stage",
            name="uq_product_processing_workspace_item_stage_receipt",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    task_item_id: Mapped[int] = mapped_column(
        ForeignKey("product_processing_task_items.id", ondelete="CASCADE"),
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(64), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


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


class EnginePromptTemplateRow(Base):
    """用户预设提示词模板：按账户（本机全局）保存，支持多个命名模板，可激活一个用于处理任务。

    用户提示词为「追加指令」模式，不覆盖系统默认提示词；生图板块仅允许附加宫内规划，
    四宫格结构、分界线、拆分逻辑与产品保真约束由系统固定（见 service._apply_user_image_additions）。
    """

    __tablename__ = "product_processing_prompt_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), default="未命名模板")
    prompts_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
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


class ComboSourceRow(Base):
    """商品自定义组合的「来源图暂存区」（服务端持久化）。

    每条记录对应一张将来可加入组合的来源图：来源可以是草稿池某条草稿的图片
    （source_type=draft_pool，保留 draft_id），也可以是用户手动上传的本地图片
    （source_type=upload，保存 local_path 用于本地取图）。同一 workspace 内按
    source_key 唯一，保证重复「加入」操作幂等。
    """

    __tablename__ = "product_processing_combo_sources"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_key",
            name="uq_product_processing_combo_source_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(255), default="local", index=True)
    source_key: Mapped[str] = mapped_column(String(255), index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="upload", index=True)
    draft_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_processing_drafts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    # 组合主图标记：同 workspace 内最多一张主图（"设为主图"时服务端取消其他）。
    is_main: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    local_path: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)


class AiStageCacheRow(Base):
    """阶段级 AI 调用结果缓存（对齐原项目 ai_stage_cache）。

    按 ``(workspace_id, cache_key)`` 唯一；cache_key = stage + prompt 哈希 + 输入哈希 +
    站点/语言契约的稳定摘要。命中时原样复用输出，避免同款商品/同语言重复消费 AI 调用。
    """

    __tablename__ = "product_processing_ai_stage_cache"

    workspace_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    stage: Mapped[str] = mapped_column(String(64), default="", index=True)
    model_signature: Mapped[str] = mapped_column(String(255), default="")
    prompt_hash: Mapped[str] = mapped_column(String(64), default="")
    input_hash: Mapped[str] = mapped_column(String(64), default="")
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    last_used_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
