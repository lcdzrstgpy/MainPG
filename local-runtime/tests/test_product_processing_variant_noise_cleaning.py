from __future__ import annotations

from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.modules.product_processing.domain.workbooks import (
    clean_variant_attributes,
    is_variant_value_noise,
    _dxm_single_export_row,
)


# ===== clean_variant_attributes / is_variant_value_noise =====


def test_clean_variant_attributes_keeps_real_variants() -> None:
    cases = [
        {"Size": "Small, 7.87 x 6.29 x 5.11 inches"},
        {"Size": "Large, 11.1 x 8.3 x 6.8 inches"},
        {"Color": "Monstera Leaf"},
        {"Style": "3.5CM"},
        {"规格型号": "30x90 mm"},
        {"Model": "UK-B1 Small Label Holder"},
        {"Color": "semi-transparent", "Style": "1000mm"},
        {"Color": "As Shown"},
        {"Color": "Multicolor"},
        {"Color": "17CM Bright Red"},
    ]
    for attrs in cases:
        out = clean_variant_attributes(attrs)
        assert out, f"expected non-empty for {attrs!r}: {out!r}"


def test_clean_variant_attributes_drops_page_metadata_noise() -> None:
    noise_values = [
        "No import fees",
        "Brand: JODIMACH",
        "JODIMACH brand",
        "4.8 out of 5 stars",
        "5.0 Star Rating",
        "5.0 stars out of 5",
        "Afterpay 211",
        "afterpay211",
        "Klarna",
        "common_arrows",
        "common arrows",
        "Sold by",
        "From",
        "Pre-Discount Price",
        "No Additional Variants",
        "70.08 inch artificial monstera leaf table runner for tropical themed table decor",
        "1pc glossy light beige base tote bag, Western-style cactus, boot, sun and floral "
        "print, stylish handbag for shopping, commuting and outdoor picnics",
        "High Quality, 10 Pack in OPP Bag, One Push Damage Free Binding",
    ]
    for value in noise_values:
        assert clean_variant_attributes({"Color": value}) == [], f"{value!r} should be dropped"


def test_clean_variant_attributes_drops_name_value_misalignment() -> None:
    # 重量错位到视觉/型号轴：Color → 100 grams 应剔除；容量轴则保留。
    assert clean_variant_attributes({"Color": "100 grams"}) == []
    assert clean_variant_attributes({"Size": "50 grams"}) == []
    # 容量/数量轴允许纯重量值
    assert clean_variant_attributes({"Capacity": "100 grams"}) == [("Capacity", "100 grams")]


def test_clean_variant_attributes_drops_business_fields() -> None:
    for attrs in [
        {"17020": "品类"},
        {"18012": "风格"},
        {"73426263": "2 盒"},
        {"1599855373426263": "2 盒"},
        {"isAegisTrade": "true"},
        {"isAlipaySupport": "true"},
    ]:
        assert clean_variant_attributes(attrs) == [], f"{attrs!r} should be dropped"


def test_is_variant_value_noise_public_predicate() -> None:
    assert is_variant_value_noise("Color", "No import fees") is True
    assert is_variant_value_noise("Color", "Brand: JODIMACH") is True
    assert is_variant_value_noise("Color", "Monstera Leaf") is False
    assert is_variant_value_noise("Size", "Small, 7.87 x 6.29 x 5.11 inches") is False
    # 空白/缺失不应判定为噪音（空值在别处单独跳过）
    assert is_variant_value_noise("", "") is False


# ===== DXM 导出行的变种列（第 4~7 列）=====


def test_dxm_export_variant_columns_keep_real_and_drop_noise() -> None:
    row = {
        "skc": "SKC-1",
        "sku": "SKU-1",
        "image_url": "http://cdn.example/1.jpg",
        "source_attributes": [],
        "target_language": "en",
        "source_variant_records": [
            {
                "sku_id": "V-1",
                "attributes": [
                    {"name": "Color", "value": "No import fees"},  # 噪音
                    {"name": "Size", "value": "Small"},
                ],
            }
        ],
    }
    line = _dxm_single_export_row(row, row["source_variant_records"][0])
    assert line[4] == "Size"
    assert line[5] == "Small"
    # 清洗后只剩一个变种；噪音值绝不应出现在变种列（第 4~7 列）
    assert "No import fees" not in line[4:8]


def test_dxm_export_variant_columns_all_noise_falls_back_to_standard() -> None:
    row = {
        "skc": "SKC-1",
        "sku": "SKU-1",
        "image_url": "http://cdn.example/1.jpg",
        "source_attributes": [],
        "target_language": "en",
        "source_variant_records": [
            {
                "sku_id": "V-1",
                "attributes": [
                    {"name": "Color", "value": "Brand: JODIMACH"},
                    {"name": "Color", "value": "Afterpay 211"},
                ],
            }
        ],
    }
    line = _dxm_single_export_row(row, row["source_variant_records"][0])
    assert line[4] == "规格"
    assert line[5] == "Standard"


# ===== service 层翻译前收集也过滤噪音 =====


def test_unique_variant_values_filters_noise() -> None:
    raw = {
        "source_variant_records": [
            {"attributes": {"Size": "Small, 7.87 x 6.29 x 5.11 inches"}},
            {"attributes": {"Color": "Monstera Leaf"}},
            {"attributes": {"Color": "100 grams"}},
            {"attributes": {"Size": "No import fees"}},
            {"attributes": {"Color": "Brand: JODIMACH"}},
            {"attributes": {"17020": "品类"}},
        ]
    }
    values = ProductProcessingService._unique_variant_values(raw)
    assert values == ["Small, 7.87 x 6.29 x 5.11 inches", "Monstera Leaf"]
