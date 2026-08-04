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
    }
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
    """Match only an explicit credential field name, never an English substring."""
    normalized = str(name).strip().replace("-", "_").casefold()
    return normalized in SENSITIVE_FIELD_NAMES


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
    attributes: Mapping[str, Any] = Field(default_factory=dict)
    image_url: str | None = None
    price_cny: Decimal | None = None
    min_order_quantity: int | None = None

    @field_validator("sku_id", mode="before")
    @classmethod
    def _required_sku_id(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DailySelectionContractError("sku_id is required")
        return value.strip()

    @field_validator("attributes", mode="before")
    @classmethod
    def _safe_attributes(cls, value: object) -> Any:
        if not isinstance(value, Mapping):
            raise DailySelectionContractError("attributes must be a mapping")
        return _safe_value(value)

    @field_validator("image_url", mode="before")
    @classmethod
    def _valid_image_url(cls, value: object) -> str | None:
        return None if value is None else _url(value, "image_url")

    @field_validator("price_cny", mode="before")
    @classmethod
    def _decimal_price(cls, value: object) -> Decimal | None:
        return _decimal(value, "price_cny")

    @field_validator("min_order_quantity", mode="before")
    @classmethod
    def _positive_moq(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise DailySelectionContractError("min_order_quantity must be a positive integer")
        return value


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
    main_image_url: str | None
    source_image_urls: tuple[str, ...] = ()
    source_detail_image_urls: tuple[str, ...] = ()
    source_variant_records: tuple[SourceVariantRecord, ...] = ()
    source_attributes: Mapping[str, Any] = Field(default_factory=dict)
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

    @field_validator("source_url", mode="before")
    @classmethod
    def _valid_source_url(cls, value: object) -> str:
        return _url(value, "source_url")

    @field_validator("main_image_url", mode="before")
    @classmethod
    def _valid_main_image(cls, value: object) -> str | None:
        return None if value is None else _url(value, "main_image_url")

    @field_validator("source_image_urls", "source_detail_image_urls", mode="before")
    @classmethod
    def _valid_image_urls(cls, value: object, info: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise DailySelectionContractError(f"{info.field_name} must be a sequence")
        return tuple(_url(item, info.field_name) for item in value)

    @field_validator("price_cny", "selection_score", "freight_cny", mode="before")
    @classmethod
    def _decimal_fields(cls, value: object, info: Any) -> Decimal | None:
        return _decimal(value, info.field_name)

    @field_validator("min_order_quantity", mode="before")
    @classmethod
    def _positive_moq(cls, value: object) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            raise DailySelectionContractError("min_order_quantity must be a positive integer")
        return value

    @field_validator("source_attributes", "score_components", "raw_payload", mode="before")
    @classmethod
    def _safe_mapping(cls, value: object, info: Any) -> Any:
        if not isinstance(value, Mapping):
            raise DailySelectionContractError(f"{info.field_name} must be a mapping")
        return _safe_value(value)
