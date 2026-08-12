from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettingsPayload(_StrictModel):
    domestic_fee: Decimal = Field(ge=0)
    shipping_subsidy: Decimal = Field(ge=0)
    refund_rate: Decimal = Field(ge=0, le=1)
    us_first_mile_rate: Decimal = Field(ge=0)
    us_first_mile_fixed: Decimal = Field(ge=0)
    co_first_mile_rate: Decimal = Field(ge=0)
    co_first_mile_fixed: Decimal = Field(ge=0)
    ec_domestic_fee: Decimal = Field(ge=0)
    ec_shipping_subsidy: Decimal = Field(ge=0)
    ec_shipping_subsidy_price_limit: Decimal = Field(gt=0)
    ec_first_mile_rate: Decimal = Field(ge=0)
    ec_first_mile_fixed: Decimal = Field(ge=0)
    ec_end_fee: Decimal = Field(ge=0)
    ec_refund_rate: Decimal = Field(ge=0, le=1)
    activity_min_net_profit: Decimal = Field(ge=0)
    activity_profit_rate_threshold: Decimal = Field(ge=0, le=1)
    rule_version: int = Field(ge=1)


class SettingsUpdateRequest(_StrictModel):
    expected_revision: int = Field(ge=0)
    settings: SettingsPayload


class CalculateRequest(_StrictModel):
    site_code: Literal["US", "CO", "EC"]
    selling_price: Decimal = Field(gt=0)
    cost_price: Decimal = Field(gt=0)
    weight_kg: Decimal = Field(gt=0)


class ArchiveRequest(CalculateRequest):
    skc: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=4_000)
    calculation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    settings_revision: int = Field(ge=0)
    confirm_negative_profit: bool = False

    @field_validator("skc")
    @classmethod
    def skc_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("skc must not be blank")
        return value


class FilterRequest(_StrictModel):
    site_code: Literal["US", "CO", "EC"] | None = None
    record_ids: list[int] | None = Field(default=None, min_length=1)

    @field_validator("record_ids")
    @classmethod
    def record_ids_are_unique(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("record_ids must be unique")
        return value
