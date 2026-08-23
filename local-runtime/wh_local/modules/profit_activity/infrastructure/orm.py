from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProfitSettingsRow(Base):
    __tablename__ = "profit_activity_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, default="default", index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    save_root: Mapped[str] = mapped_column(Text, nullable=False, default="")
    domestic_fee: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("2.5"))
    shipping_subsidy: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("21"))
    refund_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0.05"))
    us_first_mile_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("72"))
    us_first_mile_fixed: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("5"))
    us_domestic_fee: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("2.5"))
    us_shipping_subsidy: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("21"))
    us_refund_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0.05"))
    co_first_mile_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("80"))
    co_first_mile_fixed: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    co_domestic_fee: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("2.5"))
    co_shipping_subsidy: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("21"))
    co_refund_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0.05"))
    ec_domestic_fee: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("2.5"))
    ec_shipping_subsidy: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("15"))
    ec_shipping_subsidy_price_limit: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("120"))
    ec_first_mile_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("108"))
    ec_first_mile_fixed: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    ec_end_fee: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("27"))
    ec_refund_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0.05"))
    activity_min_net_profit: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    activity_profit_rate_threshold: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    activity_threshold_configured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProfitSiteRow(Base):
    __tablename__ = "profit_activity_sites"
    __table_args__ = (UniqueConstraint("workspace_id", "site_code", name="uq_profit_activity_site_workspace_code"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    site_code: Mapped[str] = mapped_column(String(12), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    first_mile_rate: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    first_mile_fixed: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    domestic_fee: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    shipping_subsidy: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    end_fee: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("0"))
    refund_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProfitRecordRow(Base):
    __tablename__ = "profit_activity_records"
    __table_args__ = (UniqueConstraint("workspace_id", "site_code", "skc", name="uq_profit_activity_workspace_site_skc"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, default="default", index=True)
    site_code: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    skc: Mapped[str] = mapped_column(String(128), nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, default="shared")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="manual", index=True)
    created_by: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by_username: Mapped[str] = mapped_column(String(128), nullable=False, default="local")
    image_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attachment_image_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_main_image_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_image_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_groups_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    source_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    selling_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    cost_price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    domestic_fee: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    shipping_subsidy: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    refund_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=Decimal("0"))
    shipping_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    end_fee: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    gross_profit: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    net_profit: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    profit_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    calculation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    settings_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ActivityRunRow(Base):
    __tablename__ = "profit_activity_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, default="default", index=True)
    site_code: Mapped[str | None] = mapped_column(String(12), index=True)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_net_profit: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    minimum_profit_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    retained_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ActivityDecisionRow(Base):
    __tablename__ = "profit_activity_decisions"
    __table_args__ = (UniqueConstraint("run_id", "record_id", name="uq_profit_activity_run_record"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, default="default", index=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("profit_activity_runs.id", ondelete="CASCADE"), nullable=False)
    record_id: Mapped[int] = mapped_column(ForeignKey("profit_activity_records.id", ondelete="CASCADE"), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)


class ImportSessionRow(Base):
    __tablename__ = "profit_activity_import_sessions"
    import_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, default="default", index=True)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    site: Mapped[str] = mapped_column(String(12), nullable=False)
    rows_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ImportTaskRow(Base):
    __tablename__ = "profit_activity_import_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, default="default", index=True)
    import_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class FilterTaskRow(Base):
    __tablename__ = "profit_activity_filter_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False, default="default", index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
