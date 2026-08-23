from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .models import ProfitPreview, ProfitSettings, ProfitSiteProfile, SiteCode


_MONEY = Decimal("0.0001")
_RATE = Decimal("0.000001")
_US_END_FEES: tuple[tuple[Decimal, Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("0.1"), Decimal("24")), (Decimal("0.1"), Decimal("0.2"), Decimal("26")),
    (Decimal("0.2"), Decimal("0.3"), Decimal("27")), (Decimal("0.3"), Decimal("0.4"), Decimal("29")),
    (Decimal("0.4"), Decimal("0.5"), Decimal("30")), (Decimal("0.5"), Decimal("0.6"), Decimal("31")),
    (Decimal("0.6"), Decimal("0.7"), Decimal("32")), (Decimal("0.7"), Decimal("0.8"), Decimal("33")),
    (Decimal("0.8"), Decimal("0.9"), Decimal("34")), (Decimal("0.9"), Decimal("1.0"), Decimal("35")),
    (Decimal("1.0"), Decimal("1.1"), Decimal("36")), (Decimal("1.1"), Decimal("1.2"), Decimal("39")),
    (Decimal("1.2"), Decimal("1.3"), Decimal("42")), (Decimal("1.3"), Decimal("1.4"), Decimal("45")),
    (Decimal("1.4"), Decimal("1.5"), Decimal("48")), (Decimal("1.5"), Decimal("1.6"), Decimal("51")),
    (Decimal("1.6"), Decimal("1.7"), Decimal("54")), (Decimal("1.7"), Decimal("1.8"), Decimal("57")),
    (Decimal("1.8"), Decimal("1.9"), Decimal("60")), (Decimal("1.9"), Decimal("2.0"), Decimal("63")),
)


class ProfitValidationError(ValueError):
    pass


def calculate_profit(
    *, site_code: SiteCode, selling_price: Decimal, cost_price: Decimal,
    weight_kg: Decimal, settings: ProfitSettings, custom_site: ProfitSiteProfile | None = None,
) -> ProfitPreview:
    """计算利润；金额保留 4 位小数，利润率保留 6 位小数。"""
    selling, cost, weight = (_positive(selling_price, "selling_price"), _positive(cost_price, "cost_price"), _positive(weight_kg, "weight_kg"))
    validate_settings(settings)
    if site_code == "US":
        domestic, subsidy, shipping, end_fee, refund = (
            settings.us_domestic_fee, settings.us_shipping_subsidy if selling <= Decimal("171") else Decimal("0"),
            weight * settings.us_first_mile_rate + settings.us_first_mile_fixed, _us_end_fee(weight), settings.us_refund_rate,
        )
    elif site_code == "CO":
        domestic, subsidy, shipping, end_fee, refund = (
            settings.co_domestic_fee, settings.co_shipping_subsidy if selling <= Decimal("171") else Decimal("0"),
            weight * settings.co_first_mile_rate + settings.co_first_mile_fixed, Decimal("24"), settings.co_refund_rate,
        )
    elif site_code == "EC":
        domestic, subsidy, shipping, end_fee, refund = (
            settings.ec_domestic_fee,
            settings.ec_shipping_subsidy if selling <= settings.ec_shipping_subsidy_price_limit else Decimal("0"),
            weight * settings.ec_first_mile_rate + settings.ec_first_mile_fixed, settings.ec_end_fee, settings.ec_refund_rate,
        )
    elif custom_site is not None and custom_site.site_code == site_code:
        domestic, subsidy, shipping, end_fee, refund = (
            custom_site.domestic_fee,
            custom_site.shipping_subsidy,
            weight * custom_site.first_mile_rate + custom_site.first_mile_fixed,
            custom_site.end_fee,
            custom_site.refund_rate,
        )
    else:
        raise ProfitValidationError("site_code_invalid")
    total = cost + domestic + shipping + end_fee
    gross = selling + subsidy - total
    net = gross * (Decimal("1") - refund) - total * refund
    return ProfitPreview(
        site_code=site_code, selling_price=_money(selling), cost_price=_money(cost), weight_kg=_money(weight),
        domestic_fee=_money(domestic), shipping_subsidy=_money(subsidy), shipping_cost=_money(shipping),
        end_fee=_money(end_fee), total_cost=_money(total), gross_profit=_money(gross), net_profit=_money(net),
        profit_rate=_rate(net / total),
    )


def activity_decision(preview: ProfitPreview, settings: ProfitSettings) -> tuple[str, str]:
    net_passed = preview.net_profit >= settings.activity_min_net_profit
    rate_passed = preview.profit_rate >= settings.activity_profit_rate_threshold
    if net_passed and rate_passed:
        return "eligible", "net_profit_and_profit_rate_passed"
    if net_passed:
        return "eligible", "net_profit_passed"
    if rate_passed:
        return "eligible", "profit_rate_passed"
    return "excluded", "net_profit_and_profit_rate_below_threshold"


def validate_settings(settings: ProfitSettings) -> None:
    for field in ("domestic_fee", "shipping_subsidy", "us_first_mile_rate", "us_first_mile_fixed", "us_domestic_fee", "us_shipping_subsidy", "co_first_mile_rate", "co_first_mile_fixed", "co_domestic_fee", "co_shipping_subsidy", "ec_domestic_fee", "ec_shipping_subsidy", "ec_first_mile_rate", "ec_first_mile_fixed", "ec_end_fee", "activity_min_net_profit"):
        if getattr(settings, field) < 0:
            raise ProfitValidationError(f"{field}_must_be_nonnegative")
    if settings.ec_shipping_subsidy_price_limit < 0:
        raise ProfitValidationError("ec_shipping_subsidy_price_limit_must_be_nonnegative")
    for field in ("refund_rate", "us_refund_rate", "co_refund_rate", "ec_refund_rate", "activity_profit_rate_threshold"):
        if not Decimal("0") <= getattr(settings, field) <= Decimal("1"):
            raise ProfitValidationError(f"{field}_must_be_between_zero_and_one")
    if settings.rule_version < 1:
        raise ProfitValidationError("rule_version_must_be_positive")


def _positive(value: Decimal, field: str) -> Decimal:
    if value <= 0:
        raise ProfitValidationError(f"{field}_must_be_positive")
    return value


def _us_end_fee(weight: Decimal) -> Decimal:
    if weight >= Decimal("2"):
        return Decimal("63")
    return next((fee for lower, upper, fee in _US_END_FEES if lower < weight <= upper), Decimal("24"))


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return value.quantize(_RATE, rounding=ROUND_HALF_UP)
