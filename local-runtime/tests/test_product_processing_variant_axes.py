"""变种规格轴拆分：颜色里混入的尺码要拆成独立的 Size 轴。

1688 供货商常把尺码写进颜色选项（"蓝色 S" / "蓝色M码"），不拆的话草稿池显示成
一个属性、导出也只有一个 Color 列，店小秘导入后无法按尺码区分。
"""

from __future__ import annotations

from wh_local.modules.product_processing.domain.variant_axes import (
    normalize_variant_attributes,
    split_color_size,
)
from wh_local.modules.product_processing.domain.workbooks import clean_variant_attributes
from wh_local.modules.product_processing.service import ProductProcessingService


def test_split_color_size_handles_common_forms() -> None:
    assert split_color_size("蓝色 S") == ("蓝色", "S")
    assert split_color_size("蓝色S") == ("蓝色", "S")
    assert split_color_size("蓝色-S") == ("蓝色", "S")
    assert split_color_size("蓝色M码") == ("蓝色", "M")
    assert split_color_size("红色 2XL") == ("红色", "2XL")
    assert split_color_size("绿色-90cm") == ("绿色", "90CM")
    assert split_color_size("蓝色 均码") == ("蓝色", "均码")


def test_split_color_size_rejects_plain_values() -> None:
    # 尾部不是尺码，或英文单词被误切成 "Flora"+"L"
    assert split_color_size("蓝色") is None
    assert split_color_size("Floral") is None
    assert split_color_size("圣诞小熊马甲") is None
    assert split_color_size("") is None


def test_normalize_variant_attributes_splits_color_only_once() -> None:
    merged = normalize_variant_attributes({"颜色": "蓝色 S"})
    assert merged == {"颜色": "蓝色", "尺码": "S"}

    # 已经有尺码轴：不再拆，避免出现两个尺码
    existing = normalize_variant_attributes({"颜色": "蓝色 S", "尺码": "M"})
    assert existing == {"颜色": "蓝色 S", "尺码": "M"}

    # 非颜色轴不动
    assert normalize_variant_attributes({"款式": "蓝色 S"}) == {"款式": "蓝色 S"}


def test_export_cleaning_splits_color_and_size_axes() -> None:
    cleaned = clean_variant_attributes({"颜色": "蓝色 M", "材质": "棉"})
    assert cleaned == [("Color", "蓝色"), ("Size", "M"), ("Material", "棉")]


def test_sku_changes_accept_legacy_and_split_labels() -> None:
    raw = {
        "source_variant_records": [
            {"sku_id": "1", "attributes": {"颜色": "蓝色 S"}},
            {"sku_id": "2", "attributes": {"颜色": "蓝色 M"}},
        ]
    }
    # 旧删除记录用的是拆分前的 key（"蓝色 S"），仍要能命中
    ProductProcessingService._apply_sku_changes(raw, {}, ["蓝色 S"])
    kept = raw["source_variant_records"]
    assert [record["sku_id"] for record in kept] == ["2"]
    assert kept[0]["attributes"] == {"颜色": "蓝色", "尺码": "M"}


def test_sku_changes_apply_edit_with_split_label() -> None:
    raw = {"source_variant_records": [{"sku_id": "1", "attributes": {"颜色": "蓝色 L"}}]}
    ProductProcessingService._apply_sku_changes(raw, {"蓝色/L": "Lake Blue / L"}, [])
    record = raw["source_variant_records"][0]
    assert record["display_name"] == "Lake Blue / L"
    assert record["attributes"] == {"颜色": "蓝色", "尺码": "L"}
