from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


SiteCode = Literal["US", "CO", "EC"]
TargetLanguage = Literal["en", "es"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SourceVariantRecord(BaseModel):
    """Canonical data_collection SKU contract with a legacy ID alias."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    sku_id: str = Field(validation_alias=AliasChoices("sku_id", "source_sku_id"))
    attributes: dict[str, Any] = Field(default_factory=dict)
    image_url: str | None = None
    price_cny: Decimal | None = None
    min_order_quantity: int | None = None

    @field_validator("sku_id", mode="before")
    @classmethod
    def require_sku_id(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("sku_id is required")
        return normalized


class ApiEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")

    provider: str
    operation: str
    request_id: str | None = None
    captured_at: str | None = None
    request_summary: dict[str, Any] = Field(default_factory=dict)
    response_summary: dict[str, Any] = Field(default_factory=dict)


class DailySelectionCandidate(BaseModel):
    """Input-compatible copy of the formal data_collection candidate contract."""

    model_config = ConfigDict(extra="allow")

    candidate_id: str
    offer_id: str
    source_platform: str = "1688"
    source_url: str
    source_title: str
    main_image_url: str | None = None
    source_image_urls: list[str] = Field(default_factory=list)
    source_detail_image_urls: list[str] = Field(default_factory=list)
    source_variant_records: list[SourceVariantRecord] = Field(default_factory=list)
    source_attributes: dict[str, Any] = Field(default_factory=dict)
    price_cny: Decimal | None = None
    min_order_quantity: int | None = None
    selection_score: Decimal = Decimal("0")
    selection_reasons: list[str] = Field(default_factory=list)
    risk_tags: list[str] = Field(default_factory=list)
    status: Literal["candidate", "filtered", "confirmed", "rejected"] = "candidate"
    evidence: list[ApiEvidence] = Field(default_factory=list)
    shop_name: str | None = None
    location: str | None = None
    sales_text: str | None = None
    weight_text: str | None = None
    package_info_text: str | None = None
    freight_cny: Decimal | None = None
    captured_fields: list[str] = Field(default_factory=list)
    missing_capture_fields: list[str] = Field(default_factory=list)
    score_components: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_example(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        attributes = normalized.get("source_attributes")
        if isinstance(attributes, list):
            canonical: dict[str, Any] = {}
            attribute_sources: dict[str, str] = {}
            for item in attributes:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name:
                    canonical[name] = item.get("value")
                    if item.get("source"):
                        attribute_sources[name] = str(item["source"])
            normalized["source_attributes"] = canonical
            raw = dict(normalized.get("raw_payload") or {})
            if attribute_sources:
                raw["legacy_attribute_sources"] = attribute_sources
            normalized["raw_payload"] = raw
        captured = normalized.get("captured_fields")
        if isinstance(captured, dict):
            raw = dict(normalized.get("raw_payload") or {})
            raw["legacy_captured_fields"] = captured
            normalized["raw_payload"] = raw
            normalized["captured_fields"] = [str(key) for key in captured]
        if normalized.get("selection_filtered") is True and normalized.get("status") == "candidate":
            normalized["status"] = "filtered"
        return normalized

    @field_validator("candidate_id", "offer_id", "source_url", "source_title", mode="before")
    @classmethod
    def required_text(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("field must not be empty")
        return normalized

    @field_validator("min_order_quantity", mode="before")
    @classmethod
    def normalize_moq(cls, value: Any) -> int | None:
        if value in (None, ""):
            return None
        if isinstance(value, bool):
            raise ValueError("min_order_quantity must be a positive integer")
        number = int(value)
        if number < 1:
            raise ValueError("min_order_quantity must be a positive integer")
        return number


class DailySelectionCriteria(BaseModel):
    """Formal criteria fields; extras remain accepted for forward compatibility."""

    model_config = ConfigDict(extra="allow")

    keywords: list[str] = Field(default_factory=list)
    collection_mode: Literal["keyword", "image"] = "keyword"
    collection_platform: str = "1688"
    selection_scope: Literal["exact", "divergent"] = "divergent"
    reference_image_url: str | None = None
    category: str = ""
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    min_moq: int | None = None
    target_count: int = 30
    max_api_calls: int = 50
    detail_count: int = 10
    exclude_risks: bool = True
    site: SiteCode = "US"


class DailySelectionRun(BaseModel):
    """Formal run shape plus aliases for the earlier provisional example."""

    model_config = ConfigDict(extra="allow")

    run_id: str
    workspace_id: str = "local"
    status: str
    criteria: DailySelectionCriteria
    metadata: dict[str, Any] = Field(default_factory=dict)
    candidate_count: int | None = None
    candidates: list[DailySelectionCandidate] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    errors: list[Any] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_run(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        normalized["run_id"] = str(normalized.get("run_id") or "").strip()
        metadata = dict(normalized.get("metadata") or {})
        legacy_counts = normalized.get("counts")
        legacy_errors = normalized.get("errors")
        if isinstance(legacy_counts, dict):
            metadata.setdefault("legacy_counts", legacy_counts)
            for key in ("api_calls", "search_calls", "image_search_calls", "detail_calls"):
                if key in legacy_counts:
                    metadata.setdefault(key, legacy_counts[key])
        if isinstance(legacy_errors, list):
            metadata.setdefault("errors", legacy_errors)
        normalized["metadata"] = metadata
        normalized.setdefault("candidate_count", len(normalized.get("candidates") or []))
        return normalized

    @field_validator("run_id", "workspace_id", mode="before")
    @classmethod
    def require_identity(cls, value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("run_id and workspace_id must not be empty")
        return normalized


class DailySelectionHandoffEnvelope(BaseModel):
    """Exact envelope returned by data_collection confirm routes."""

    model_config = ConfigDict(extra="forbid")

    handoff_id: str
    run_id: str
    candidate_id: str
    workspace_id: str
    payload_json: str
    status: Literal["pending", "consumed", "failed"] = "pending"
    idempotency_key: str
    created_at: str


DEFAULT_PROMPTS: dict[str, str] = {
    "title": "保留商品核心属性，生成简洁、准确且符合目标站点语言的标题。",
    "desc": "根据来源事实生成商品描述，不虚构材质、尺寸、认证或功效。",
    "size": "只依据来源重量和包装信息整理尺寸字段；缺失时明确标记待确认。",
    "grid_image": "使用来源商品图生成干净的四宫格主图，不改变商品本体。",
    "detail_image": "根据来源图片和属性生成详情信息图，不添加未经证实的卖点。",
    "combined_text": "一次性整理标题、描述、规格与合规提示，保持字段之间一致。",
}
