"""Temu 发布 payload 形状回归。

样例来自一次**真实发布成功**的请求体（已脱敏：货号/仓库号/运费模板号/图片地址/
站外链接全部替换，数值与嵌套结构保持原样）。见 ``fixtures/temu_goods_add_sample.json``。

这层测试的意义：之前所有 temu_publish 测试都是「读源码做正则断言」，能保证代码写法
没被改乱，但保证不了「生成的 payload 结构对不对」。改了组装逻辑却漏掉某个字段时，
源码断言照样会绿，要等真发品被 Temu 拒绝才发现。这里直接比字段骨架。

判定方式：样例里出现的每个字段，我们要么发出来，要么在 ``ALLOWED_MISSING_*``
里写明理由。**新增豁免必须先想清楚为什么可以不发**，否则会出现「悄悄少一个字段」。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wh_local.modules.temu_publish.schemas import (
    GoodsDraftInput,
    GoodsPropertyInput,
    SkcInput,
    SkuInput,
    SkuSpecInput,
)

FIXTURE = Path(__file__).with_name("fixtures") / "temu_goods_add_sample.json"

# 样例里有、但我们刻意不发（或按类目条件才发）的字段。每条都要有理由。
ALLOWED_MISSING_TOP: dict[str, str] = {
    # 这些是「类目/商品类型决定」的字段，本样例的类目需要、我们默认不发。
    "productCustomReq": (
        "商品自定义容器：样例带 isRecommendedTag/cat10Id，我们只在用户填了商品标签名时"
        "才带 goodsLabelName。结构差异见 service.py 里 productCustomReq 分支。"
    ),
    "goodsLayerDecorationReqs": "详情图楼层，只有填了详情图才发（本用例的 draft 没填）。",
    # ↓ 已知缺口：样例明确带了主图宽高，我们没维护
    "width": "已知缺口：顶层主图宽度。样例固定 800，我们不传、由 Temu 按默认处理。要补得先让前端存图宽高。",
    "height": "已知缺口：顶层主图高度。同上。",
}
ALLOWED_MISSING_SKC: dict[str, str] = {
    "isBasePlate": "底板标记，默认 0；POD/定制底板场景还没接管。",
    "width": "已知缺口：SKC 图宽度，样例 800。同顶层 width。",
    "height": "已知缺口：SKC 图高度，样例 800。同顶层 height。",
}
ALLOWED_MISSING_SKU: dict[str, str] = {
    "productSkuBarCodeReqs": "条形码，本类目为空数组；要求条形码的类目我们确实发不出去。",
    "width": "已知缺口：SKU 图宽度，样例 800。同顶层 width。",
    "height": "已知缺口：SKU 图高度，样例 800。同顶层 height。",
}


def _sample() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["request"]


def _draft() -> GoodsDraftInput:
    """尽量按样例的结构造一份 draft：1 个 SKC / 1 个 SKU / 1 组销售属性。"""
    spec = SkuSpecInput(
        parent_spec_id=15998553, parent_spec_name="数量", spec_id=18191285, spec_name="4件"
    )
    return GoodsDraftInput(
        category_ids=[9711, 12015, 12256, 12262],
        title="Redacted sample product title",
        carousel_image_urls=["https://img.example.com/main.jpg"],
        material_image_url="https://img.example.com/main.jpg",
        freight_template_id="HFT-REDACTED",
        properties=[GoodsPropertyInput(pid=100001, vid=300001)],
        site_ids=[100],
        outer_goods_url="https://example.com/product",
        skcs=[
            SkcInput(
                ext_code="SKC-1",
                preview_image_urls=["https://img.example.com/main.jpg"],
                color_image_url="https://img.example.com/main.jpg",
                main_specs=[spec],
                skus=[
                    SkuInput(
                        ext_code="SKU-1",
                        supplier_price=8500,
                        suggested_price=8500,
                        stock=100,
                        site_id=100,
                        warehouse_id="WH-REDACTED",
                        weight_mg=800000,
                        length_mm=500,
                        width_mm=500,
                        height_mm=300,
                        thumb_url="https://img.example.com/main.jpg",
                        pieces_per_pack=12,
                        specs=[spec],
                    )
                ],
            )
        ],
    )


def _build() -> dict[str, Any]:
    from wh_local.modules.temu_publish.service import TemuPublishService

    # _assemble_payload 是纯函数式的组装入口，不需要 client/凭据
    from wh_local.modules.temu_publish.service import _assemble_payload

    payload = _assemble_payload(_draft(), [], None)
    assert isinstance(payload, dict)
    return payload


def test_top_level_fields_cover_the_real_sample() -> None:
    """样例的顶层字段，除了白名单，全都必须发出去。"""

    sample = _sample()
    payload = _build()

    missing = {
        key: ALLOWED_MISSING_TOP[key]
        for key in sample
        if key not in payload and key in ALLOWED_MISSING_TOP
    }
    unexpected = [key for key in sample if key not in payload and key not in ALLOWED_MISSING_TOP]
    assert not unexpected, f"样例里有、我们没发、也没进白名单的顶层字段: {unexpected}"
    # 白名单只声明不校验数量，但要确保白名单本身不是过期噪声
    for key in ALLOWED_MISSING_TOP:
        assert key in sample, f"白名单里的 {key} 样例里已经没有了，白名单该删"


def test_category_chain_is_fully_written() -> None:
    """类目链 cat1Id~cat10Id 必须 10 个都在（不足补 0），别只写前 4 个。"""

    payload = _build()
    for index in range(1, 11):
        assert f"cat{index}Id" in payload, f"缺少 cat{index}Id"
    assert payload["cat1Id"] == 9711
    assert payload["cat4Id"] == 12262
    assert payload["cat5Id"] == 0


def test_skc_and_sku_field_skeleton_matches_the_sample() -> None:
    """SKC/SKU 两层的字段骨架对齐样例（白名单外不得缺失）。"""

    sample = _sample()
    payload = _build()

    sample_skc = sample["productSkcReqs"][0]
    our_skc = payload["productSkcReqs"][0]
    missing_skc = [
        key
        for key in sample_skc
        if key not in our_skc and key not in ALLOWED_MISSING_SKC
    ]
    assert not missing_skc, f"SKC 层缺字段: {missing_skc}"

    sample_sku = sample_skc["productSkuReqs"][0]
    our_sku = our_skc["productSkuReqs"][0]
    missing_sku = [
        key
        for key in sample_sku
        if key not in our_sku and key not in ALLOWED_MISSING_SKU
    ]
    assert not missing_sku, f"SKU 层缺字段: {missing_sku}"


def test_units_match_the_sample_scale() -> None:
    """单位量纲必须跟样例一致，字段名一个不能错。

    样例实测形状（别按文档注释猜）：
    - ``productSkuVolumeReq = {"len": 500, "width": 500, "height": 300}``
      → 键是 ``len`` 不是 ``length``；500 对应 50cm，也就是**毫米**
    - ``productSkuWeightReq = {"value": 800000}`` → 是个对象，不是裸整数；800000mg = 800g
    - ``siteSupplierPrices[].supplierPrice = 8500`` → 分
    """

    payload = _build()
    sku = payload["productSkcReqs"][0]["productSkuReqs"][0]
    ext = sku["productSkuWhExtAttrReq"]

    # 字段名必须跟样例一致：len（不是 length），weight 是对象
    assert set(ext["productSkuVolumeReq"]) == {"len", "width", "height"}
    assert ext["productSkuVolumeReq"] == {"len": 500, "width": 500, "height": 300}
    assert ext["productSkuWeightReq"] == {"value": 800000}
    assert sku["siteSupplierPrices"][0]["supplierPrice"] == 8500

    sample_sku = _sample()["productSkcReqs"][0]["productSkuReqs"][0]
    sample_ext = sample_sku["productSkuWhExtAttrReq"]
    assert set(ext["productSkuVolumeReq"]) == set(sample_ext["productSkuVolumeReq"])
    assert set(ext["productSkuWeightReq"]) == set(sample_ext["productSkuWeightReq"])
    # 敏感货的声明容器也要在（样例里有，缺了会被要求补类型）
    assert "productSkuSensitiveAttrReq" in ext
    assert "productSkuSensitiveLimitReq" in ext
