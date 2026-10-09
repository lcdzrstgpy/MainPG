from wh_local.data_collection.contracts import SourceVariantRecord
from wh_local.data_collection.routes import (
    _plugin_physical_evidence,
    _plugin_product_to_draft,
    _plugin_variant_records,
)


def test_plugin_variant_records_keep_distinct_attribute_combinations_with_reused_sku_id() -> None:
    records = _plugin_variant_records(
        {
            "product_id": "temu-1",
            "platform": "temu",
            "currency": "USD",
            "variant_combinations": [
                {"source_sku_id": "color-gray", "attributes": {"颜色": "灰色", "尺码": "单人"}, "price": "$10.49"},
                {"source_sku_id": "color-gray", "attributes": {"颜色": "灰色", "尺码": "双人"}, "price": "$10.49"},
                {"source_sku_id": "color-white", "attributes": {"颜色": "米白", "尺码": "单人"}, "price": "$10.49"},
                {"source_sku_id": "color-white", "attributes": {"颜色": "米白", "尺码": "双人"}, "price": "$10.49"},
            ],
        }
    )

    assert len(records) == 4
    assert {tuple(record["attributes"].values()) for record in records} == {
        ("灰色", "单人"), ("灰色", "双人"), ("米白", "单人"), ("米白", "双人")
    }


def test_plugin_variant_records_preserve_foreign_source_price_without_calling_it_cny() -> None:
    [record] = _plugin_variant_records(
        {
            "product_id": "temu-2",
            "platform": "temu",
            "currency": "USD",
            "variant_combinations": [
                {"attributes": {"颜色": "深蓝"}, "price": "$17.44", "currency": "USD"}
            ],
        }
    )

    assert record["source_price"] == 17.44
    assert record["source_currency"] == "USD"
    assert record["price_cny"] is None
    assert SourceVariantRecord(**record).source_currency == "USD"


def test_plugin_variant_records_use_the_product_main_image_when_a_text_only_sku_has_no_image() -> None:
    [record] = _plugin_variant_records(
        {
            "product_id": "temu-size-only",
            "platform": "temu",
            "variant_combinations": [
                {"attributes": {"尺码": "双人沙发 90*160cm"}, "price": "$10.34"}
            ],
        },
        fallback_image_url="https://img.kwcdn.com/product/fancy/sofa-main.jpg",
    )

    assert record["image_url"] == "https://img.kwcdn.com/product/fancy/sofa-main.jpg"


def test_plugin_selected_sku_weight_beats_generic_page_weight() -> None:
    weight_text, _package = _plugin_physical_evidence(
        {
            "weight_text": "重量 50g",
            "employee_action_weight_kg": 0.32,
        }
    )

    assert weight_text == "重量 0.32kg"


def test_plugin_draft_preserves_visible_parameter_table_for_processing() -> None:
    draft = _plugin_product_to_draft(
        {
            "platform": "1688",
            "product_id": "offer-1",
            "product_link": "https://detail.1688.com/offer/1.html",
            "title": "Pumpkin decoration",
            "source_attributes": {"商品重量": "275g", "产品尺寸": "12*8*10cm"},
            "source_attribute_table": [{"key": "商品重量", "value": "275g"}],
        }
    )

    assert draft["source_attributes"]["商品重量"] == "275g"
    assert draft["weight_text"] == "重量 275g"


def test_plugin_selected_canonical_variant_weight_beats_other_variants() -> None:
    weight_text, _package = _plugin_physical_evidence(
        {
            "source_variant_records": [
                {"selected": False, "weight_text": "重量 800g"},
                {"selected": True, "weight_text": "重量 300g"},
            ]
        }
    )

    assert weight_text == "重量 300g"
