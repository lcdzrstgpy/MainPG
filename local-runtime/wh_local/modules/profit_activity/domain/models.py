from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re


SiteCode = str


@dataclass(frozen=True)
class ProfitSiteProfile:
    """管理员新增站点使用的通用利润费率。"""

    site_code: str
    display_name: str
    first_mile_rate: Decimal = Decimal("0")
    first_mile_fixed: Decimal = Decimal("0")
    domestic_fee: Decimal = Decimal("0")
    shipping_subsidy: Decimal = Decimal("0")
    end_fee: Decimal = Decimal("0")
    refund_rate: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        code = self.site_code.strip().upper()
        name = self.display_name.strip()
        if not re.fullmatch(r"[A-Z0-9_]{2,12}", code):
            raise ValueError("site_code_invalid")
        if not name:
            raise ValueError("site_display_name_required")
        object.__setattr__(self, "site_code", code)
        object.__setattr__(self, "display_name", name)
        for field in ("first_mile_rate", "first_mile_fixed", "domestic_fee", "shipping_subsidy", "end_fee"):
            if getattr(self, field) < 0:
                raise ValueError(f"{field}_must_be_nonnegative")
        if not Decimal("0") <= self.refund_rate <= Decimal("1"):
            raise ValueError("refund_rate_must_be_between_zero_and_one")


@dataclass(frozen=True)
class ProfitSettings:
    """与原工作台一致的利润计算配置快照。"""

    save_root: str = ""
    domestic_fee: Decimal = Decimal("0")
    shipping_subsidy: Decimal = Decimal("0")
    refund_rate: Decimal = Decimal("0")
    us_first_mile_rate: Decimal = Decimal("0")
    us_first_mile_fixed: Decimal = Decimal("0")
    co_first_mile_rate: Decimal = Decimal("0")
    co_first_mile_fixed: Decimal = Decimal("0")
    ec_domestic_fee: Decimal = Decimal("0")
    ec_shipping_subsidy: Decimal = Decimal("0")
    ec_shipping_subsidy_price_limit: Decimal = Decimal("0")
    ec_first_mile_rate: Decimal = Decimal("0")
    ec_first_mile_fixed: Decimal = Decimal("0")
    ec_end_fee: Decimal = Decimal("0")
    ec_refund_rate: Decimal = Decimal("0")
    activity_min_net_profit: Decimal = Decimal("8")
    activity_profit_rate_threshold: Decimal = Decimal("0.20")
    rule_version: int = 2

    @property
    def activity_filter_rule_version(self) -> int:
        return self.rule_version


@dataclass(frozen=True)
class ProfitPreview:
    site_code: str
    selling_price: Decimal
    cost_price: Decimal
    weight_kg: Decimal
    domestic_fee: Decimal
    shipping_subsidy: Decimal
    shipping_cost: Decimal
    end_fee: Decimal
    total_cost: Decimal
    gross_profit: Decimal
    net_profit: Decimal
    profit_rate: Decimal
