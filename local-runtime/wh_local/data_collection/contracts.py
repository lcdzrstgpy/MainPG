"""Stable Pydantic contracts shared by the daily-selection pipeline."""

from __future__ import annotations

import re
import math
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError, field_validator


SENSITIVE_FIELD_NAMES = frozenset(
    {
        "key",
        "api_key",
        "apikey",
        "api_secret",
        "secret",
        "access_token",
        "token",
        "cookie",
        "session",
        "authorization",
        "remote_token",
        "ark_api_key",
        "wuyin_api_key",
    }
)
_FIELD_NAME_SEPARATOR = re.compile(r"[\s._-]+")
_SENSITIVE_FIELD_IDENTIFIERS = frozenset(
    _FIELD_NAME_SEPARATOR.sub("", name).casefold() for name in SENSITIVE_FIELD_NAMES
)
_BEARER_CREDENTIAL = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_INLINE_CREDENTIAL = re.compile(
    r"(?i)(\b(?:key|api[_-]?key|api[_-]?secret|access[_-]?token|secret|token|cookie|session|authorization)\b\s*[=:]\s*)"
    r"(?:bearer\s+)?[^\s,;]+"
)
_HTTP_URL = TypeAdapter(HttpUrl)


class DailySelectionContractError(ValueError):
    """Raised when a value cannot safely be represented in a contract."""


def is_sensitive_field(name: object) -> bool:
    """Match exact credential identifiers across common field-name styles."""
    normalized = _FIELD_NAME_SEPARATOR.sub("", str(name).strip()).casefold()
    return normalized in _SENSITIVE_FIELD_IDENTIFIERS


def redact_sensitive_text(value: str, sensitive_values: tuple[str, ...] = ()) -> str:
    """Redact explicit credential syntax and configured secret values in text."""
    redacted = value
    for credential in sorted(
        {item for item in sensitive_values if isinstance(item, str) and item},
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(credential, "[redacted]")
    redacted = _BEARER_CREDENTIAL.sub("Bearer [redacted]", redacted)
    return _INLINE_CREDENTIAL.sub(lambda match: f"{match.group(1)}[redacted]", redacted)


def _safe_value(value: Any) -> Any:
    """Return JSON-like data with exact credentials removed and binaries rejected."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise DailySelectionContractError("image and evidence payloads cannot contain binary data")
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if not is_sensitive_field(key)
        }
    if isinstance(value, (list, tuple)):
        return tuple(_safe_value(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise DailySelectionContractError("contract mappings must contain finite JSON numbers")
    if isinstance(value, Decimal):
        if value.is_finite():
            return value
        raise DailySelectionContractError("contract mappings must contain finite JSON numbers")
    raise DailySelectionContractError("contract mappings must contain JSON-serializable values")


def _url(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise DailySelectionContractError(f"{field_name} must be a URL string")
    candidate = value.strip()
    try:
        return str(_HTTP_URL.validate_python(candidate))
    except ValidationError as error:
        raise DailySelectionContractError(
            f"{field_name} must be a valid http or https URL"
        ) from error
    raise AssertionError("unreachable")


def _decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DailySelectionContractError(f"{field_name} must be a decimal number")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise DailySelectionContractError(f"{field_name} must be a decimal number") from error
    if not result.is_finite():
        raise DailySelectionContractError(f"{field_name} must be finite")
    return result


class _ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as error:
            raise DailySelectionContractError(str(error)) from error


class DailySelectionError(_ContractModel):
    """A safe, structured error that can cross module boundaries."""

    code: str
    message: str
    context: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("code", "message", mode="before")
    @classmethod
    def _required_text(cls, value: object, info: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DailySelectionContractError(f"{info.field_name} is required")
        return redact_sensitive_text(value.strip())

    @field_validator("context", mode="before")
    @classmethod
    def _safe_context(cls, value: object) -> Any:
        if not isinstance(value, Mapping):
            raise DailySelectionContractError("context must be a mapping")
        return _safe_value(value)


class ImageReference(_ContractModel):
    """An image URL plus source-provided metadata; bytes are never retained."""

    url: str
    metadata: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("url", mode="before")
    @classmethod
    def _valid_url(cls, value: object) -> str:
        return _url(value, "url")

    @field_validator("metadata", mode="before")
    @classmethod
    def _safe_metadata(cls, value: object) -> Any:
        if not isinstance(value, Mapping):
            raise DailySelectionContractError("metadata must be a mapping")
        return _safe_value(value)


class SourceVariantRecord(_ContractModel):
    """One source SKU/variant, independent from product-level imagery."""

    sku_id: str
    source_sku_id: str | None = None
    attributes: Mapping[str, Any] = Field(default_factory=dict)
    spec_text: str | None = None
    image_url: str | None = None
    price_cny: Decimal | None = None
    source_price: Decimal | None = None
    source_currency: str | None = None
    min_order_quantity: int | None = None
    quantity: int | None = None
    sales: int | None = None

    @field_validator("sku_id", mode="before")
    @classmethod
    def _required_sku_id(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DailySelectionContractError("sku_id is required")
        return value.strip()

    @field_validator("source_sku_id", "source_currency", mode="before")
    @classmethod
    def _optional_source_text(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("attributes", mode="before")
    @classmethod
    def _safe_attributes(cls, value: object) -> Any:
        if not isinstance(value, Mapping):
            raise DailySelectionContractError("attributes must be a mapping")
        return _safe_value(value)

    @field_validator("spec_text", mode="before")
    @classmethod
    def _optional_spec_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise DailySelectionContractError("spec_text must be a string")
        return _safe_value(value)

    @field_validator("image_url", mode="before")
    @classmethod
    def _valid_image_url(cls, value: object) -> str | None:
        return None if value is None else _url(value, "image_url")

    @field_validator("price_cny", "source_price", mode="before")
    @classmethod
    def _decimal_price(cls, value: object) -> Decimal | None:
        return _decimal(value, "price_cny")

    @field_validator("min_order_quantity", mode="before")
    @classmethod
    def _positive_moq(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise DailySelectionContractError("min_order_quantity must be a positive integer")
        return value

    @field_validator("quantity", "sales", mode="before")
    @classmethod
    def _non_negative_counts(cls, value: object, info: Any) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise DailySelectionContractError(f"{info.field_name} must be a non-negative integer")
        return value


class SourceTierPrice(_ContractModel):
    """A source-provided price at a minimum purchase quantity."""

    min_order_quantity: int
    price_cny: Decimal

    @field_validator("min_order_quantity", mode="before")
    @classmethod
    def _positive_moq(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise DailySelectionContractError("min_order_quantity must be a positive integer")
        return value

    @field_validator("price_cny", mode="before")
    @classmethod
    def _decimal_price(cls, value: object) -> Decimal:
        result = _decimal(value, "price_cny")
        if result is None:
            raise DailySelectionContractError("price_cny is required")
        return result


class ApiEvidence(_ContractModel):
    """Traceable API evidence with credential material removed."""

    provider: str
    operation: str
    request_id: str | None = None
    captured_at: str | None = None
    request_summary: Mapping[str, Any] = Field(default_factory=dict)
    response_summary: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("provider", "operation", mode="before")
    @classmethod
    def _required_text(cls, value: object, info: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DailySelectionContractError(f"{info.field_name} is required")
        return value.strip()

    @field_validator("request_id", "captured_at", mode="before")
    @classmethod
    def _safe_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise DailySelectionContractError("evidence text fields must be strings")
        return redact_sensitive_text(value)

    @field_validator("request_summary", "response_summary", mode="before")
    @classmethod
    def _safe_summary(cls, value: object, info: Any) -> Any:
        if not isinstance(value, Mapping):
            raise DailySelectionContractError(f"{info.field_name} must be a mapping")
        return _safe_value(value)


class DailySelectionCandidate(_ContractModel):
    """A candidate offer and the source, scoring, and evidence behind it."""

    candidate_id: str
    offer_id: str
    source_platform: Literal["1688"]
    source_url: str
    source_title: str
    query_keyword: str | None = None
    selection_result_label: str | None = None
    listed_at: str | None = None
    main_image_url: str | None
    source_image_urls: tuple[str, ...] = ()
    source_detail_image_urls: tuple[str, ...] = ()
    source_variant_records: tuple[SourceVariantRecord, ...] = ()
    source_attributes: Mapping[str, Any] = Field(default_factory=dict)
    category_path: str | None = None
    category_id: str | None = None
    price_cny: Decimal | None = None
    min_order_quantity: int | None = None
    selection_score: Decimal = Decimal("0")
    selection_reasons: tuple[str, ...] = ()
    risk_tags: tuple[str, ...] = ()
    status: Literal["candidate", "filtered", "confirmed", "rejected"] = "candidate"
    evidence: tuple[ApiEvidence, ...] = ()
    shop_name: str | None = None
    location: str | None = None
    sales_text: str | None = None
    weight_text: str | None = None
    package_info_text: str | None = None
    freight_cny: Decimal | None = None
    original_price_cny: Decimal | None = None
    stock_quantity: int | None = None
    unit: str | None = None
    brand: str | None = None
    video_url: str | None = None
    tiered_prices: tuple[SourceTierPrice, ...] = ()
    captured_fields: tuple[str, ...] = ()
    missing_capture_fields: tuple[str, ...] = ()
    score_components: Mapping[str, Any] = Field(default_factory=dict)
    raw_payload: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("candidate_id", "offer_id", "source_title", mode="before")
    @classmethod
    def _required_text(cls, value: object, info: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DailySelectionContractError(f"{info.field_name} is required")
        return value.strip()

    @field_validator(
        "query_keyword",
        "selection_result_label",
        "listed_at",
        "unit",
        "brand",
        mode="before",
    )
    @classmethod
    def _optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise DailySelectionContractError("optional text fields must be strings")
        stripped = value.strip()
        return stripped or None

    @field_validator("source_url", mode="before")
    @classmethod
    def _valid_source_url(cls, value: object) -> str:
        return _url(value, "source_url")

    @field_validator("main_image_url", "video_url", mode="before")
    @classmethod
    def _valid_optional_url(cls, value: object, info: Any) -> str | None:
        return None if value is None else _url(value, info.field_name)

    @field_validator("source_image_urls", "source_detail_image_urls", mode="before")
    @classmethod
    def _valid_image_urls(cls, value: object, info: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise DailySelectionContractError(f"{info.field_name} must be a sequence")
        return tuple(_url(item, info.field_name) for item in value)

    @field_validator("price_cny", "selection_score", "freight_cny", "original_price_cny", mode="before")
    @classmethod
    def _decimal_fields(cls, value: object, info: Any) -> Decimal | None:
        return _decimal(value, info.field_name)

    @field_validator("min_order_quantity", mode="before")
    @classmethod
    def _positive_moq(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise DailySelectionContractError("min_order_quantity must be a positive integer")
        return value

    @field_validator("stock_quantity", mode="before")
    @classmethod
    def _non_negative_stock(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise DailySelectionContractError("stock_quantity must be a non-negative integer")
        return value

    @field_validator("source_attributes", "score_components", "raw_payload", mode="before")
    @classmethod
    def _safe_mapping(cls, value: object, info: Any) -> Any:
        if not isinstance(value, Mapping):
            raise DailySelectionContractError(f"{info.field_name} must be a mapping")
        return _safe_value(value)


class ShopPage(_ContractModel):
    """One sanitized page of offer identifiers returned by a 1688 shop search."""

    offer_ids: tuple[str, ...]
    missing_offer_count: int = 0
    has_next: bool
    total_pages: int | None = None
    evidence: ApiEvidence

    @field_validator("offer_ids", mode="before")
    @classmethod
    def _offer_ids(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise DailySelectionContractError("offer_ids must be a sequence")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise DailySelectionContractError("offer_ids must contain non-empty strings")
            result.append(item.strip())
        return tuple(result)

    @field_validator("missing_offer_count", mode="before")
    @classmethod
    def _missing_count(cls, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DailySelectionContractError("missing_offer_count must be a non-negative integer")
        return value

    @field_validator("has_next", mode="before")
    @classmethod
    def _has_next(cls, value: object) -> bool:
        if not isinstance(value, bool):
            raise DailySelectionContractError("has_next must be a boolean")
        return value

    @field_validator("total_pages", mode="before")
    @classmethod
    def _total_pages(cls, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise DailySelectionContractError("total_pages must be between 0 and 100")
        return value
