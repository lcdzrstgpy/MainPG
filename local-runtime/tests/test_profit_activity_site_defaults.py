from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from wh_local.modules.profit_activity.infrastructure.database import create_database
from wh_local.modules.profit_activity.infrastructure.repository import ProfitActivityRepository


def test_new_settings_use_the_three_builtin_site_defaults(tmp_path: Path) -> None:
    database = create_database(tmp_path / "fresh.sqlite3")
    try:
        settings = ProfitActivityRepository(database.sessions).get_settings().settings
        assert (settings.us_first_mile_rate, settings.us_first_mile_fixed) == (Decimal("72"), Decimal("5"))
        assert (settings.us_domestic_fee, settings.us_shipping_subsidy, settings.us_refund_rate) == (Decimal("2.5"), Decimal("21"), Decimal("0.05"))
        assert (settings.co_first_mile_rate, settings.co_first_mile_fixed) == (Decimal("80"), Decimal("0"))
        assert (settings.co_domestic_fee, settings.co_shipping_subsidy, settings.co_refund_rate) == (Decimal("2.5"), Decimal("21"), Decimal("0.05"))
        assert (settings.ec_first_mile_rate, settings.ec_first_mile_fixed, settings.ec_domestic_fee, settings.ec_shipping_subsidy, settings.ec_refund_rate) == (Decimal("108"), Decimal("0"), Decimal("2.5"), Decimal("15"), Decimal("0.05"))
    finally:
        database.dispose()
