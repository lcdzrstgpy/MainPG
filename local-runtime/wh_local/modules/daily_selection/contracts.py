"""Stable, serialisable contracts shared by the daily-selection pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


SENSITIVE_FIELD_NAMES = frozenset(
    {"api_key", "apikey", "secret", "token", "cookie", "session", "authorization"}
)


class DailySelectionContractError(ValueError):
    """Raised when a value cannot safely be represented in a selection contract."""


@dataclass(frozen=True)
class DailySelectionError:
    """A safe, structured error that can cross module boundaries."""

    code: str
    message: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise DailySelectionContractError("error code and message are required")
        object.__setattr__(self, "context", _safe_value(dict(self.context)))


def _is_sensitive_field(name: object) -> bool:
    normalized = str(name).replace("-", "_").casefold()
    return any(marker in normalized for marker in SENSITIVE_FIELD_NAMES)


def _safe_value(value: Any) -> Any:
    """Return JSON-like data with credentials removed and binaries rejected."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise DailySelectionContractError("image and evidence payloads cannot contain binary data")
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if not _is_sensitive_field(key)
        }
    if isinstance(value, (list, tuple)):
        return tuple(_safe_value(item) for item in value)
    return value


def _url(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise DailySelectionContractError(f"{field_name} must be a URL string")
    candidate = value.strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DailySelectionContractError(f"{field_name} must be a valid http or https URL")
    return candidate


@dataclass(frozen=True)
class ImageReference:
    """An image URL plus source-provided metadata; image bytes are never retained."""

    url: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", _url(self.url, "url"))
        object.__setattr__(self, "metadata", _safe_value(dict(self.metadata)))


@dataclass(frozen=True)
class SourceVariantRecord:
    """One source SKU/variant, independent from product-level imagery."""

    sku_id: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    image_url: str | None = None
    price_cny: float | None = None
    min_order_quantity: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sku_id, str) or not self.sku_id.strip():
            raise DailySelectionContractError("sku_id is required")
        object.__setattr__(self, "sku_id", self.sku_id.strip())
        object.__setattr__(self, "attributes", _safe_value(dict(self.attributes)))
        if self.image_url is not None:
            object.__setattr__(self, "image_url", _url(self.image_url, "image_url"))


@dataclass(frozen=True)
class ApiEvidence:
    """Traceable API evidence with any credential-like fields removed."""

    provider: str
    operation: str
    request_id: str | None = None
    captured_at: str | None = None
    request_summary: Mapping[str, Any] = field(default_factory=dict)
    response_summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider or not self.operation:
            raise DailySelectionContractError("provider and operation are required")
        object.__setattr__(self, "request_summary", _safe_value(dict(self.request_summary)))
        object.__setattr__(self, "response_summary", _safe_value(dict(self.response_summary)))


@dataclass(frozen=True)
class DailySelectionCandidate:
    """A candidate offer and the distinct source, scoring, and evidence records behind it."""

    candidate_id: str
    offer_id: str
    source_platform: str
    source_url: str
    source_title: str
    main_image_url: str | None
    source_image_urls: Sequence[str] = field(default_factory=tuple)
    source_detail_image_urls: Sequence[str] = field(default_factory=tuple)
    source_variant_records: Sequence[SourceVariantRecord] = field(default_factory=tuple)
    source_attributes: Mapping[str, Any] = field(default_factory=dict)
    price_cny: float | None = None
    min_order_quantity: int | None = None
    selection_score: float | None = None
    selection_reasons: Sequence[str] = field(default_factory=tuple)
    risk_tags: Sequence[str] = field(default_factory=tuple)
    status: str = "collected"
    evidence: Sequence[ApiEvidence] = field(default_factory=tuple)
    shop_name: str | None = None
    location: str | None = None
    sales_text: str | None = None
    weight_text: str | None = None
    package_info_text: str | None = None
    freight_cny: float | None = None
    captured_fields: Sequence[str] = field(default_factory=tuple)
    missing_capture_fields: Sequence[str] = field(default_factory=tuple)
    score_components: Mapping[str, Any] = field(default_factory=dict)
    raw_payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("candidate_id", "offer_id", "source_platform", "source_title", "status"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise DailySelectionContractError(f"{field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        object.__setattr__(self, "source_url", _url(self.source_url, "source_url"))
        if self.main_image_url is not None:
            object.__setattr__(self, "main_image_url", _url(self.main_image_url, "main_image_url"))
        object.__setattr__(self, "source_image_urls", tuple(_url(url, "source_image_urls") for url in self.source_image_urls))
        object.__setattr__(self, "source_detail_image_urls", tuple(_url(url, "source_detail_image_urls") for url in self.source_detail_image_urls))
        object.__setattr__(self, "source_variant_records", tuple(self.source_variant_records))
        object.__setattr__(self, "selection_reasons", tuple(self.selection_reasons))
        object.__setattr__(self, "risk_tags", tuple(self.risk_tags))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "captured_fields", tuple(self.captured_fields))
        object.__setattr__(self, "missing_capture_fields", tuple(self.missing_capture_fields))
        for field_name in ("source_attributes", "score_components", "raw_payload"):
            object.__setattr__(self, field_name, _safe_value(dict(getattr(self, field_name))))
