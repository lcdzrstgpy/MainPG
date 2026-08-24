from wh_local.data_collection.contracts import SourceVariantRecord
from wh_local.data_collection.routes import _plugin_variant_records


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
