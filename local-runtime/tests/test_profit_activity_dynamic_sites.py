from __future__ import annotations

from decimal import Decimal

import pytest

from wh_local.modules.profit_activity.domain.engine import ProfitValidationError, calculate_profit
from wh_local.modules.profit_activity.domain.models import ProfitSettings, ProfitSiteProfile
from wh_local.modules.profit_activity.infrastructure.database import create_database
from wh_local.modules.profit_activity.infrastructure.repository import ProfitActivityRepository
from wh_local.modules.profit_activity.service import ProfitActivityService


def test_custom_site_profile_defaults_to_zero_and_calculates_with_generic_formula() -> None:
    site = ProfitSiteProfile(site_code="BR", display_name="巴西")

    assert site.first_mile_rate == Decimal("0")
    assert site.first_mile_fixed == Decimal("0")
    assert site.domestic_fee == Decimal("0")
    assert site.shipping_subsidy == Decimal("0")
    assert site.end_fee == Decimal("0")
    assert site.refund_rate == Decimal("0")

    configured = ProfitSiteProfile(
        site_code="BR",
        display_name="巴西",
        first_mile_rate=Decimal("10"),
        first_mile_fixed=Decimal("2"),
        domestic_fee=Decimal("3"),
        shipping_subsidy=Decimal("4"),
        end_fee=Decimal("5"),
        refund_rate=Decimal("0.1"),
    )
    preview = calculate_profit(
        site_code="BR",
        selling_price=Decimal("100"),
        cost_price=Decimal("20"),
        weight_kg=Decimal("2"),
        settings=ProfitSettings(),
        custom_site=configured,
    )

    assert preview.total_cost == Decimal("50.0000")
    assert preview.gross_profit == Decimal("54.0000")
    assert preview.net_profit == Decimal("43.6000")


def test_custom_site_code_rejects_invalid_identifier() -> None:
    with pytest.raises(ValueError, match="site_code_invalid"):
        ProfitSiteProfile(site_code="Brazil!", display_name="巴西")


def test_unknown_site_cannot_be_calculated() -> None:
    with pytest.raises(ProfitValidationError, match="site_code_invalid"):
        calculate_profit(
            site_code="BR",
            selling_price=Decimal("100"),
            cost_price=Decimal("20"),
            weight_kg=Decimal("2"),
            settings=ProfitSettings(),
        )


def test_custom_sites_are_persisted_per_workspace(tmp_path) -> None:
    database = create_database(tmp_path / "profit.sqlite3")
    repository = ProfitActivityRepository(database.sessions)
    try:
        saved = repository.create_site(
            "workspace-a",
            ProfitSiteProfile(site_code="BR", display_name="巴西"),
        )

        assert saved.site_code == "BR"
        assert [site.site_code for site in repository.list_sites("workspace-a")] == ["BR"]
        assert repository.list_sites("workspace-b") == []
    finally:
        database.dispose()


def test_created_site_is_used_by_calculation_and_product_archive(tmp_path) -> None:
    database = create_database(tmp_path / "profit.sqlite3")
    service = ProfitActivityService(ProfitActivityRepository(database.sessions), database)
    profile = ProfitSiteProfile(
        site_code="BR",
        display_name="巴西",
        first_mile_rate=Decimal("10"),
        first_mile_fixed=Decimal("2"),
        domestic_fee=Decimal("3"),
        shipping_subsidy=Decimal("4"),
        end_fee=Decimal("5"),
    )
    try:
        service.create_site(profile)
        calculated = service.calculate("BR", Decimal("100"), Decimal("20"), Decimal("2"))
        product = service.upsert_product({
            "site": "BR", "skc": "BR-SKC-1", "selling_price": "100", "cost_price": "20", "weight_kg": "2",
        })
        run, decisions = service.run_filter("BR", None)

        assert calculated["preview"].total_cost == Decimal("50.0000")
        assert product["site"] == "BR"
        assert product["net_profit"] == 54.0
        assert run.site_code == "BR"
        assert len(decisions) == 1
        assert decisions[0]["decision"] == "eligible"
    finally:
        service.close()
