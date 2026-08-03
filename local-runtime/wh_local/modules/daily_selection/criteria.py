"""Validated collection criteria for the daily-selection workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence
from urllib.parse import urlparse


class DailySelectionCriteriaError(ValueError):
    """Raised when a collection request is incomplete or internally inconsistent."""


def _normalized_keywords(keywords: Sequence[str]) -> tuple[str, ...]:
    if isinstance(keywords, str):
        keywords = (keywords,)
    normalized: list[str] = []
    for keyword in keywords:
        if not isinstance(keyword, str):
            raise DailySelectionCriteriaError("keywords must contain strings")
        value = " ".join(keyword.split())
        if value and value not in normalized:
            normalized.append(value)
    if not 1 <= len(normalized) <= 5:
        raise DailySelectionCriteriaError("keyword mode requires one to five normalized keywords")
    return tuple(normalized)


def _reference_image_url(value: str | None) -> str:
    if not isinstance(value, str):
        raise DailySelectionCriteriaError("image mode requires reference_image_url")
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DailySelectionCriteriaError("reference_image_url must be a valid http or https URL")
    return normalized


def _number(value: float | int | Decimal | None, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise DailySelectionCriteriaError(f"{field_name} must be a number")
    result = float(value)
    if result < 0:
        raise DailySelectionCriteriaError(f"{field_name} cannot be negative")
    return result


@dataclass(frozen=True)
class DailySelectionCriteria:
    keywords: Sequence[str] = field(default_factory=tuple)
    collection_mode: str = "keyword"
    collection_platform: str = "1688"
    selection_scope: str = "exact"
    reference_image_url: str | None = None
    category: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    min_moq: int | None = None
    target_count: int = 20
    max_api_calls: int = 60
    detail_count: int = 20
    exclude_risks: Sequence[str] = field(default_factory=tuple)
    site: str | None = None

    def __post_init__(self) -> None:
        mode_aliases = {"keyword": "keyword", "keywords": "keyword", "image": "image"}
        mode = mode_aliases.get(self.collection_mode)
        if mode is None:
            raise DailySelectionCriteriaError("collection_mode must be keyword or image")
        if self.collection_platform != "1688":
            raise DailySelectionCriteriaError("collection_platform must be 1688")
        if self.selection_scope not in {"exact", "divergent"}:
            raise DailySelectionCriteriaError("selection_scope must be exact or divergent")

        normalized_keywords = _normalized_keywords(self.keywords) if mode == "keyword" else tuple(
            " ".join(keyword.split()) for keyword in self.keywords if isinstance(keyword, str) and keyword.strip()
        )
        if mode == "image" and any(not isinstance(keyword, str) for keyword in self.keywords):
            raise DailySelectionCriteriaError("keywords must contain strings")
        object.__setattr__(self, "collection_mode", mode)
        object.__setattr__(self, "keywords", normalized_keywords)
        if mode == "image":
            object.__setattr__(self, "reference_image_url", _reference_image_url(self.reference_image_url))
        elif self.reference_image_url is not None:
            object.__setattr__(self, "reference_image_url", _reference_image_url(self.reference_image_url))

        min_price = _number(self.min_price, "min_price")
        max_price = _number(self.max_price, "max_price")
        if min_price is not None and max_price is not None and min_price > max_price:
            raise DailySelectionCriteriaError("min_price cannot be greater than max_price")
        object.__setattr__(self, "min_price", min_price)
        object.__setattr__(self, "max_price", max_price)
        if self.min_moq is not None and (isinstance(self.min_moq, bool) or not isinstance(self.min_moq, int) or self.min_moq < 1):
            raise DailySelectionCriteriaError("min_moq must be a positive integer")
        for field_name in ("target_count", "detail_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise DailySelectionCriteriaError(f"{field_name} must be a positive integer")
        if isinstance(self.max_api_calls, bool) or not isinstance(self.max_api_calls, int) or not 1 <= self.max_api_calls <= 60:
            raise DailySelectionCriteriaError("max_api_calls must be between 1 and 60")
        object.__setattr__(self, "exclude_risks", tuple(" ".join(value.split()) for value in self.exclude_risks if value.strip()))

    @property
    def keyword_tags(self) -> tuple[str, ...]:
        """Keywords act as descriptive tags in image mode, never as a second query."""
        return tuple(self.keywords) if self.collection_mode == "image" else tuple()
