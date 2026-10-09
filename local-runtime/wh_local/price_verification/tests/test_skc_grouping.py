from decimal import Decimal

from wh_local.price_verification.quote_normalizer import QuoteItem, dedupe_quotes
from wh_local.price_verification.quote_service import _preferred_price
from wh_local.price_verification.sourcing.task_builder import _selected_price


def test_dedupe_quotes_keeps_sku_only_record_separate_from_skc() -> None:
    rows = dedupe_quotes((
        QuoteItem(sku_id="6129805571537", product_title="Blue Speechless Cloud SKU货号", adjusted_declared_price_cny=Decimal("14.31")),
        QuoteItem(skc_id="880130955398", sku_id="sku-blue", product_title="Valid SKC", adjusted_declared_price_cny=Decimal("9.90")),
    ))

    # 无 SKC 的报价（仅 SKU）保留为独立行，不与 SKC 记录合并（quote_service 按
    # skc_id or spu_or_goods_id or sku_id 分组，SKU-only 行后续可被 SPU/SKU 兼容合并）。
    assert [row.skc_id for row in rows] == ["", "880130955398"]
    assert [row.sku_id for row in rows] == ["6129805571537", "sku-blue"]


def test_batch_confirmation_uses_first_adjusted_sku_price() -> None:
    sku_prices = (
        {"sku_id": "large", "adjusted_declared_price_cny": Decimal("48.09"), "original_declared_price_cny": Decimal("50.00")},
        {"sku_id": "small", "adjusted_declared_price_cny": Decimal("12.42"), "original_declared_price_cny": Decimal("20.00")},
    )

    # _preferred_price 与 task_builder._selected_price 均取首个非空价格（adjusted 优先）。
    assert _preferred_price(sku_prices) == Decimal("48.09")
    assert _selected_price({"sku_prices": sku_prices}) == Decimal("48.09")


def test_price_selection_uses_first_original_price_when_adjusted_prices_are_missing() -> None:
    sku_prices = (
        {"sku_id": "large", "adjusted_declared_price_cny": None, "original_declared_price_cny": Decimal("50.00")},
        {"sku_id": "small", "adjusted_declared_price_cny": None, "original_declared_price_cny": Decimal("20.00")},
    )

    assert _preferred_price(sku_prices) == Decimal("50.00")
    assert _selected_price({"sku_prices": sku_prices}) == Decimal("50.00")
