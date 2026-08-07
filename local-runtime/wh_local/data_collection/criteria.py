"""Validated Pydantic collection criteria for the daily-selection workflow."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError, field_validator, model_validator


class DailySelectionCriteriaError(ValueError):
    """Raised when a collection request is incomplete or inconsistent."""


_HTTP_URL = TypeAdapter(HttpUrl)


def _normalized_keywords(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        raise DailySelectionCriteriaError("keywords must be a sequence of strings")
    normalized: list[str] = []
    for keyword in value:
        if not isinstance(keyword, str):
            raise DailySelectionCriteriaError("keywords must contain strings")
        candidate = " ".join(keyword.split())
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


def _decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DailySelectionCriteriaError(f"{field_name} must be a decimal number")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise DailySelectionCriteriaError(f"{field_name} must be a decimal number") from error
    if not result.is_finite():
        raise DailySelectionCriteriaError(f"{field_name} must be finite")
    if result < 0:
        raise DailySelectionCriteriaError(f"{field_name} cannot be negative")
    return result


class DailySelectionCriteria(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    keywords: tuple[str, ...] = ()
    collection_mode: Literal["keyword", "image"] = "keyword"
    collection_platform: Literal["1688"] = "1688"
    selection_scope: Literal["exact", "divergent"] = "divergent"
    reference_image_url: str | None = None
    category: str = ""
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    min_moq: int | None = None
    min_sku_count: int | None = None
    max_sku_count: int | None = None
    min_sku_price: Decimal | None = None
    max_sku_price: Decimal | None = None
    min_sku_stock: int | None = None
    max_sku_stock: int | None = None
    target_count: int = Field(default=30, ge=1, le=100)
    max_api_calls: int = Field(default=200, ge=1, le=300)
    detail_count: int = Field(default=50, ge=1, le=190, description="详情拉取的最低覆盖数；采集器会在 API 预算内尽量全量拉取候选详情，保证 SKU/发源地/属性完整")
    exclude_risks: bool = True
    site: Literal["US", "CO", "EC"] = "US"
    max_parallel_collect: int = Field(default=6, ge=1, le=10, description="采集并行数，1=串行")

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as error:
            raise DailySelectionCriteriaError(str(error)) from error

    @field_validator("keywords", mode="before")
    @classmethod
    def _normalize_keywords(cls, value: object) -> tuple[str, ...]:
        return _normalized_keywords(value)

    @field_validator("reference_image_url", mode="before")
    @classmethod
    def _valid_reference_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise DailySelectionCriteriaError("reference_image_url must be a URL string")
        try:
            return str(_HTTP_URL.validate_python(value.strip()))
        except ValidationError as error:
            raise DailySelectionCriteriaError(
                "reference_image_url must be a valid http or https URL"
            ) from error
        raise AssertionError("unreachable")

    @field_validator("min_price", "max_price", "min_sku_price", "max_sku_price", mode="before")
    @classmethod
    def _decimal_price(cls, value: object, info: Any) -> Decimal | None:
        return _decimal(value, info.field_name)

    @field_validator(
        "min_moq", "target_count", "max_api_calls", "detail_count",
        "min_sku_count", "max_sku_count", "min_sku_stock", "max_sku_stock",
        mode="before",
    )
    @classmethod
    def _strict_integers(cls, value: object, info: Any) -> object:
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise DailySelectionCriteriaError(f"{info.field_name} must be an integer")
        return value

    @field_validator("exclude_risks", mode="before")
    @classmethod
    def _strict_boolean(cls, value: object) -> bool:
        if not isinstance(value, bool):
            raise DailySelectionCriteriaError("exclude_risks must be a boolean")
        return value

    @model_validator(mode="after")
    def _consistent_request(self) -> "DailySelectionCriteria":
        if self.collection_mode == "keyword":
            if not 1 <= len(self.keywords) <= 5:
                raise DailySelectionCriteriaError(
                    "keyword mode requires one to five normalized keywords"
                )
            if self.reference_image_url is not None:
                raise DailySelectionCriteriaError(
                    "keyword mode cannot include reference_image_url"
                )
        elif self.reference_image_url is None:
            raise DailySelectionCriteriaError("image mode requires reference_image_url")
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise DailySelectionCriteriaError("min_price cannot be greater than max_price")
        if self.min_moq is not None and self.min_moq < 1:
            raise DailySelectionCriteriaError("min_moq must be a positive integer")
        if self.min_sku_count is not None and self.max_sku_count is not None and self.min_sku_count > self.max_sku_count:
            raise DailySelectionCriteriaError("min_sku_count cannot be greater than max_sku_count")
        if self.min_sku_count is not None and self.min_sku_count < 1:
            raise DailySelectionCriteriaError("min_sku_count must be a positive integer")
        if self.min_sku_price is not None and self.max_sku_price is not None and self.min_sku_price > self.max_sku_price:
            raise DailySelectionCriteriaError("min_sku_price cannot be greater than max_sku_price")
        if self.min_sku_stock is not None and self.max_sku_stock is not None and self.min_sku_stock > self.max_sku_stock:
            raise DailySelectionCriteriaError("min_sku_stock cannot be greater than max_sku_stock")
        if self.min_sku_stock is not None and self.min_sku_stock < 1:
            raise DailySelectionCriteriaError("min_sku_stock must be a positive integer")
        # An image search always consumes download, upload, and search slots
        # before any item detail can be fetched.  Reject impossible requests
        # up front instead of silently returning a partial batch.
        reserved_search_calls = 3 if self.collection_mode == "image" else len(self.keywords)
        if self.collection_mode == "keyword" and self.selection_scope == "divergent":
            # Divergent mode may issue one locally expanded query per source
            # keyword.  Reserve for the worst allowed request, rather than
            # quietly leaving fewer detail slots than the client requested.
            reserved_search_calls *= 2
        if self.detail_count > self.max_api_calls - reserved_search_calls:
            raise DailySelectionCriteriaError(
                "detail_count exceeds the remaining API-call budget for this request"
            )
        return self

    @property
    def keyword_tags(self) -> tuple[str, ...]:
        """Keywords are descriptive tags in image mode, never a second query."""
        return self.keywords if self.collection_mode == "image" else ()
