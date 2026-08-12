"""Pure contracts shared by the read-only sourcing adapters."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class SourcingContractError(ValueError):
    """Raised when a sourcing value cannot be safely evaluated locally."""


def decimal_value(value: object, field_name: str, *, positive: bool = True) -> Decimal:
    """Parse a finite decimal without allowing binary floating-point arithmetic."""
    if isinstance(value, bool) or value is None:
        raise SourcingContractError(f"{field_name} is required")
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise SourcingContractError(f"{field_name} must be a decimal") from error
    if not parsed.is_finite():
        raise SourcingContractError(f"{field_name} must be finite")
    if positive and parsed <= 0:
        raise SourcingContractError(f"{field_name} must be positive")
    if not positive and parsed < 0:
        raise SourcingContractError(f"{field_name} must be nonnegative")
    return parsed


@dataclass(frozen=True)
class SourceSearchTask:
    """One image-search task representing all valid SKUs of one SKC."""

    task_key: str
    skc_id: str
    main_image_url: str
    source_quote_keys: tuple[str, ...]
    quote_key: str = ""
    official_link_url: str = ""
    selected_price_cny: str = ""
    sku_id: str = ""
    spu_or_goods_id: str = ""
    product_title: str = ""
    max_candidates: int = 10

    def to_payload(self) -> dict[str, object]:
        return {
            "task_key": self.task_key,
            "skc_id": self.skc_id,
            "main_image_url": self.main_image_url,
            "source_quote_keys": list(self.source_quote_keys),
            "quote_key": self.quote_key or self.task_key,
            "official_link_url": self.official_link_url,
            "selected_price_cny": self.selected_price_cny,
            "sku_id": self.sku_id,
            "spu_or_goods_id": self.spu_or_goods_id,
            "product_title": self.product_title,
            "max_candidates": self.max_candidates,
        }


@dataclass(frozen=True)
class SourceBrowserImageSearchPayload:
    """Read-only source-browser command body with locally explained skips."""

    tasks: tuple[SourceSearchTask, ...]
    skipped_quote_keys: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        return {
            "tasks": [task.to_payload() for task in self.tasks],
            "skipped_quote_keys": list(self.skipped_quote_keys),
        }


@dataclass(frozen=True)
class CandidateCostInputs:
    """A candidate's unit source price and order-level domestic freight."""

    price: Decimal | str | int | float
    moq: Decimal | str | int | float = Decimal("1")
    domestic_freight: Decimal | str | int | float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", decimal_value(self.price, "price"))
        object.__setattr__(self, "moq", decimal_value(self.moq, "moq"))
        if self.domestic_freight is not None:
            object.__setattr__(
                self,
                "domestic_freight",
                decimal_value(self.domestic_freight, "domestic_freight", positive=False),
            )


@dataclass(frozen=True)
class CandidateProfitInputs:
    """Inputs handed to the established profit-activity calculation engine."""

    site_code: str
    selling_price: Decimal | str | int | float
    cost_price: Decimal | str | int | float
    weight_kg: Decimal | str | int | float

    def __post_init__(self) -> None:
        site_code = self.site_code.strip().upper() if isinstance(self.site_code, str) else ""
        if site_code not in {"US", "CO", "EC"}:
            raise SourcingContractError("site_code must be US, CO, or EC")
        object.__setattr__(self, "site_code", site_code)
        object.__setattr__(self, "selling_price", decimal_value(self.selling_price, "selling_price"))
        object.__setattr__(self, "cost_price", decimal_value(self.cost_price, "cost_price"))
        object.__setattr__(self, "weight_kg", decimal_value(self.weight_kg, "weight_kg"))
