"""Profit preview for the top-ranked source candidate against the Temu SKC price."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from wh_local.modules.profit_activity.domain.engine import activity_decision
from wh_local.modules.profit_activity.domain.models import ProfitSettings

from .contracts import CandidateProfitInputs
from .profit_adapter import preview_profit

DEFAULT_WEIGHT_KG = Decimal("0.5")
DEFAULT_CANDIDATE_LIMIT = 5


def build_candidate_profit(
    candidate: Mapping[str, Any],
    *,
    site: str,
    selling_price: Any,
    weight_kg: Any = DEFAULT_WEIGHT_KG,
    settings: ProfitSettings | None = None,
) -> dict[str, Any]:
    """Compute the profit preview of one 1688 candidate against the Temu declared price.

    ``cost_price`` is the candidate unit price plus the allocated domestic
    freight over its MOQ, mirroring the established landed-cost basis
    ``1688_price_moq_freight`` used by the delivery build.
    """
    price = _decimal(candidate.get("promotion_price") or candidate.get("price"))
    if price is None or price <= 0:
        return {"available": False, "reason": "missing_source_price"}
    moq = _decimal(candidate.get("moq")) or Decimal("1")
    if moq < 1:
        moq = Decimal("1")
    freight = _decimal(candidate.get("domestic_freight"))
    cost_price = price + (freight / moq if freight is not None else Decimal("0"))
    selling = _decimal(selling_price)
    if selling is None or selling <= 0:
        return {"available": False, "reason": "missing_selling_price"}
    weight = _decimal(weight_kg) or DEFAULT_WEIGHT_KG
    if weight <= 0:
        weight = DEFAULT_WEIGHT_KG
    try:
        preview = preview_profit(
            CandidateProfitInputs(
                site_code=site,
                selling_price=str(selling),
                cost_price=str(cost_price),
                weight_kg=str(weight),
            ),
            settings or ProfitSettings(),
        )
    except Exception:
        return {"available": False, "reason": "profit_calculation_failed"}
    eligibility = activity_decision(
        _profit_preview_for_decision(preview),
        settings or ProfitSettings(),
    )
    qualified, qualification = eligibility
    return {
        "available": True,
        "site": site,
        "selling_price": preview["selling_price"],
        "cost_price": preview["cost_price"],
        "weight_kg": preview["weight_kg"],
        "source_price": round(float(price), 4),
        "moq": round(float(moq), 4),
        "domestic_freight": float(freight) if freight is not None else None,
        "domestic_fee": preview["domestic_fee"],
        "shipping_subsidy": preview["shipping_subsidy"],
        "shipping_cost": preview["shipping_cost"],
        "end_fee": preview["end_fee"],
        "total_cost": preview["total_cost"],
        "gross_profit": preview["gross_profit"],
        "net_profit": preview["net_profit"],
        "profit_rate": preview["profit_rate"],
        "qualified": qualified == "eligible",
        "qualification": qualification,
    }


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip().replace("¥", "").replace(",", ""))
    except Exception:
        return None
    return number if number.is_finite() else None


def _profit_preview_for_decision(preview: Mapping[str, Any]) -> Any:
    """Rebuild the domain ProfitPreview from the JSON-safe preview dict."""
    from wh_local.modules.profit_activity.domain.models import ProfitPreview

    return ProfitPreview(
        site_code=preview["site"],
        selling_price=_decimal(preview["selling_price"]) or Decimal("0"),
        cost_price=_decimal(preview["cost_price"]) or Decimal("0"),
        weight_kg=_decimal(preview["weight_kg"]) or Decimal("0"),
        domestic_fee=_decimal(preview["domestic_fee"]) or Decimal("0"),
        shipping_subsidy=_decimal(preview["shipping_subsidy"]) or Decimal("0"),
        shipping_cost=_decimal(preview["shipping_cost"]) or Decimal("0"),
        end_fee=_decimal(preview["end_fee"]) or Decimal("0"),
        total_cost=_decimal(preview["total_cost"]) or Decimal("0"),
        gross_profit=_decimal(preview["gross_profit"]) or Decimal("0"),
        net_profit=_decimal(preview["net_profit"]) or Decimal("0"),
        profit_rate=_decimal(preview["profit_rate"]) or Decimal("0"),
    )
