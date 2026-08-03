from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


SiteCode = Literal["US", "CO", "EC"]


@dataclass(frozen=True)
class ProfitSettings:
    """与原工作台一致的利润计算配置快照。"""

    domestic_fee: Decimal = Decimal("2.5")
    shipping_subsidy: Decimal = Decimal("21")
    refund_rate: Decimal = Decimal("0.05")
    us_first_mile_rate: Decimal = Decimal("72")
    us_first_mile_fixed: Decimal = Decimal("5")
    co_first_mile_rate: Decimal = Decimal("80")
    co_first_mile_fixed: Decimal = Decimal("0")
    ec_domestic_fee: Decimal = Decimal("2.5")
    ec_shipping_subsidy: Decimal = Decimal("15")
    ec_shipping_subsidy_price_limit: Decimal = Decimal("120")
    ec_first_mile_rate: Decimal = Decimal("108")
    ec_first_mile_fixed: Decimal = Decimal("0")
    ec_end_fee: Decimal = Decimal("27")
    ec_refund_rate: Decimal = Decimal("0.05")
    activity_min_net_profit: Decimal = Decimal("8")
    activity_profit_rate_threshold: Decimal = Decimal("0.20")
    rule_version: int = 2


@dataclass(frozen=True)
class ProfitPreview:
    site_code: SiteCode
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
