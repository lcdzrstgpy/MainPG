from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.profit_activity.domain.engine import calculate_profit  # noqa: E402
from wh_local.modules.profit_activity.domain.models import ProfitSettings  # noqa: E402
from wh_local.price_verification.sourcing.profit_adapter import (  # noqa: E402
    CandidateProfitInputs,
    preview_profit,
)


def test_profit_adapter_matches_existing_engine() -> None:
    actual = preview_profit(
        CandidateProfitInputs(
            site_code="US", selling_price="100", cost_price="20", weight_kg="0.5"
        ),
        ProfitSettings(),
    )
    expected = calculate_profit(
        site_code="US",
        selling_price=Decimal("100"),
        cost_price=Decimal("20"),
        weight_kg=Decimal("0.5"),
        settings=ProfitSettings(),
    )

    assert actual["net_profit"] == float(expected.net_profit)
    assert actual["profit_rate"] == float(expected.profit_rate)
    assert actual["total_cost"] == float(expected.total_cost)
    assert actual["site"] == "US"


def test_profit_preview_is_json_safe() -> None:
    actual = preview_profit(
        CandidateProfitInputs(
            site_code="CO", selling_price="100", cost_price="20", weight_kg="0.5"
        ),
        ProfitSettings(),
    )

    assert all(not isinstance(value, Decimal) for value in actual.values())
