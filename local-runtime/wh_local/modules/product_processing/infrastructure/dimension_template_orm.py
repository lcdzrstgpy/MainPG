from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .orm import Base, utc_now


class DimensionTemplateRow(Base):
    __tablename__ = "product_dimension_templates"
    __table_args__ = (
        UniqueConstraint("workspace_id", "category_key", "package_profile", name="uq_dimension_template_identity"),
        Index("idx_dimension_templates_profile", "workspace_id", "package_profile"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(255), default="__global__", index=True)
    category_key: Mapped[str] = mapped_column(Text)
    package_profile: Mapped[str] = mapped_column(String(64))
    known_len_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_len_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_len_default: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_wid_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_wid_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_wid_default: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_hei_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_hei_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_hei_default: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_wgt_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_wgt_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    known_wgt_default: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_len_p10: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_len_p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_len_p90: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_wid_p10: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_wid_p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_wid_p90: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_hei_p10: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_hei_p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_hei_p90: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_wgt_p10: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_wgt_p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    stat_wgt_p90: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    len_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    wid_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    hei_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    wgt_sample_count: Mapped[int] = mapped_column(Integer, default=0)
    source_confirmed_n: Mapped[int] = mapped_column(Integer, default=0)
    manual_confirmed_n: Mapped[int] = mapped_column(Integer, default=0)
    quarantined_axis_count: Mapped[int] = mapped_column(Integer, default=0)
    accuracy_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)


class DimensionObservationRow(Base):
    __tablename__ = "product_dimension_observations"
    __table_args__ = (
        UniqueConstraint("workspace_id", "observation_key", name="uq_dimension_observation_identity"),
        Index("idx_dimension_observations_template", "workspace_id", "category_key", "package_profile"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    observation_key: Mapped[str] = mapped_column(String(255))
    category_key: Mapped[str] = mapped_column(Text)
    package_profile: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(32))
    task_id: Mapped[int] = mapped_column(Integer, default=0)
    product_draft_id: Mapped[int] = mapped_column(Integer, default=0)
    variant_key: Mapped[str] = mapped_column(Text, default="")
    length_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    width_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    provenance_json: Mapped[str] = mapped_column(Text, default="{}")
    quality_json: Mapped[str] = mapped_column(Text, default="{}")
    raw_estimate_json: Mapped[str] = mapped_column(Text, default="{}")
    resolved_estimate_json: Mapped[str] = mapped_column(Text, default="{}")
    error_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String(64), default=utc_now)


class DimensionTemplateRefreshRow(Base):
    __tablename__ = "product_dimension_template_refresh_queue"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "category_key",
            "package_profile",
            name="uq_dimension_template_refresh_identity",
        ),
        Index("idx_dimension_template_refresh_due", "not_before_epoch"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(255), index=True)
    category_key: Mapped[str] = mapped_column(Text)
    package_profile: Mapped[str] = mapped_column(String(64))
    pending_changes: Mapped[int] = mapped_column(Integer, default=1)
    not_before_epoch: Mapped[float] = mapped_column(Float, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String(64), default=utc_now, onupdate=utc_now)
