"""JSON-safe adapter around the existing profit-activity engine."""

from __future__ import annotations

from typing import Any

from wh_local.modules.profit_activity.domain.engine import calculate_profit
from wh_local.modules.profit_activity.domain.models import ProfitSettings

from .contracts import CandidateProfitInputs


def preview_profit(inputs: CandidateProfitInputs, settings: ProfitSettings) -> dict[str, Any]:
    """Calculate a candidate preview through the single established formula."""
    if not isinstance(inputs, CandidateProfitInputs):
        raise TypeError("inputs must be CandidateProfitInputs")
    if not isinstance(settings, ProfitSettings):
        raise TypeError("settings must be ProfitSettings")
    preview = calculate_profit(
        site_code=inputs.site_code,
        selling_price=inputs.selling_price,
        cost_price=inputs.cost_price,
        weight_kg=inputs.weight_kg,
        settings=settings,
    )
    return {
        "site": preview.site_code,
        "selling_price": float(preview.selling_price),
        "cost_price": float(preview.cost_price),
        "weight_kg": float(preview.weight_kg),
        "domestic_fee": float(preview.domestic_fee),
        "shipping_subsidy": float(preview.shipping_subsidy),
        "shipping_cost": float(preview.shipping_cost),
        "end_fee": float(preview.end_fee),
        "total_cost": float(preview.total_cost),
        "gross_profit": float(preview.gross_profit),
        "net_profit": float(preview.net_profit),
        "profit_rate": float(preview.profit_rate),
    }
