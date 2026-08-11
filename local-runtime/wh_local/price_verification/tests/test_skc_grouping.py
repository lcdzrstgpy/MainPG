from decimal import Decimal

from wh_local.price_verification.quote_normalizer import QuoteItem, dedupe_quotes
from wh_local.price_verification.quote_service import _preferred_price, _preferred_sku
from wh_local.price_verification.sourcing.task_builder import _selected_price


def test_dedupe_quotes_discards_sku_only_record_without_a_skc() -> None:
    rows = dedupe_quotes((
        QuoteItem(sku_id="6129805571537", product_title="Blue Speechless Cloud SKU货号", adjusted_declared_price_cny=Decimal("14.31")),
        QuoteItem(skc_id="880130955398", sku_id="sku-blue", product_title="Valid SKC", adjusted_declared_price_cny=Decimal("9.90")),
    ))

    assert [row.skc_id for row in rows] == ["880130955398"]


def test_batch_confirmation_uses_lowest_adjusted_sku_price() -> None:
    sku_prices = (
        {"sku_id": "large", "adjusted_declared_price_cny": Decimal("48.09"), "original_declared_price_cny": Decimal("50.00")},
        {"sku_id": "small", "adjusted_declared_price_cny": Decimal("12.42"), "original_declared_price_cny": Decimal("20.00")},
    )

    assert _preferred_price(sku_prices) == Decimal("12.42")
    assert _preferred_sku(sku_prices)["sku_id"] == "small"
    assert _selected_price({"sku_prices": sku_prices}) == Decimal("12.42")


def test_price_selection_uses_lowest_original_price_when_adjusted_prices_are_missing() -> None:
    sku_prices = (
        {"sku_id": "large", "adjusted_declared_price_cny": None, "original_declared_price_cny": Decimal("50.00")},
        {"sku_id": "small", "adjusted_declared_price_cny": None, "original_declared_price_cny": Decimal("20.00")},
    )

    assert _preferred_price(sku_prices) == Decimal("20.00")
    assert _selected_price({"sku_prices": sku_prices}) == Decimal("20.00")
