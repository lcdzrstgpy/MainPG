from __future__ import annotations

from decimal import Decimal

from wh_local.data_collection.contracts import ApiEvidence
from wh_local.data_collection.normalizer import normalize_detail_response


def _evidence() -> ApiEvidence:
    return ApiEvidence(provider="onebound-1688", operation="item_get", request_id="request-1")


def test_normalize_detail_response_returns_a_complete_stable_canonical_candidate() -> None:
    candidate = normalize_detail_response(
        {
            "item": {
                "num_iid": "1001",
                "title": "  保温杯  ",
                "detail_url": "http://detail.1688.com/offer/1001.html?spm=ignored",
                "pic_url": "https://images.example/main.jpg",
                "item_imgs": ["https://images.example/one.jpg", "https://images.example/two.jpg"],
                "detail_images": ["https://images.example/detail.jpg"],
                "price": "8.80",
                "original_price": "12.00",
                "begin_amount": "2",
                "quantity": "45",
                "sales": "12",
                "unit": "个",
                "brand": "示例品牌",
                "cat_name": "家居/杯子",
                "seller_info": {"nick": "示例店铺"},
                "location": "浙江 金华",
                "weight": "300g",
                "package_info": "20x10x10cm",
                "video": "https://videos.example/product.mp4",
                "price_range": [{"begin_amount": 2, "price": "8.8"}, {"begin_amount": 10, "price": "7.5"}],
                "props": [{"name": "材质", "value": "不锈钢"}],
                "skus": [
                    {
                        "sku_id": "sku-red",
                        "properties_name": "0:0:颜色:红色",
                        "price": "9.1",
                        "quantity": "6",
                        "image": "https://images.example/red.jpg",
                    }
                ],
            }
        },
        evidence=_evidence(),
    )

    assert candidate.candidate_id == "1688:1001"
    assert candidate.offer_id == "1001"
    assert candidate.source_url == "https://detail.1688.com/offer/1001.html"
    assert candidate.source_title == "保温杯"
    assert candidate.price_cny == Decimal("8.80")
    assert candidate.original_price_cny == Decimal("12.00")
    assert candidate.min_order_quantity == 2
    assert candidate.stock_quantity == 45
    assert candidate.sales_text == "12"
    assert candidate.unit == "个"
    assert candidate.brand == "示例品牌"
    assert candidate.category_path == "家居/杯子"
    assert candidate.shop_name == "示例店铺"
    assert candidate.video_url == "https://videos.example/product.mp4"
    assert [(tier.min_order_quantity, tier.price_cny) for tier in candidate.tiered_prices] == [
        (2, Decimal("8.8")),
        (10, Decimal("7.5")),
    ]
    assert candidate.source_attributes == {"材质": "不锈钢"}
    assert candidate.source_variant_records[0].attributes == {"颜色": "红色"}


def test_normalize_detail_response_handles_mixed_onebound_fields_and_normalizes_protocol_relative_urls() -> None:
    candidate = normalize_detail_response(
        {
            "data": {
                "offer_id": 1002,
                "name": "混合字段商品",
                "url": "//detail.1688.com/offer/1002.html?foo=bar",
                "main_image": "//images.example/main.jpg",
                "images": [{"url": "//images.example/gallery.jpg"}],
                "desc": '<p>ignore me <img src="//images.example/description.jpg"></p>',
                "sku": {
                    "sku": [
                        {
                            "skuId": "blue",
                            "property_alias": "颜色:蓝色;尺寸:L",
                            "sku_image": "//images.example/blue.jpg",
                            "stock": 3,
                        }
                    ]
                },
            }
        },
        evidence=_evidence(),
    )

    assert candidate.source_url == "https://detail.1688.com/offer/1002.html"
    assert candidate.main_image_url == "https://images.example/main.jpg"
    assert candidate.source_image_urls == (
        "https://images.example/main.jpg",
        "https://images.example/gallery.jpg",
    )
    assert candidate.source_detail_image_urls == ("https://images.example/description.jpg",)
    sku = candidate.source_variant_records[0]
    assert sku.image_url == "https://images.example/blue.jpg"
    assert sku.attributes == {"颜色": "蓝色", "尺寸": "L"}


def test_normalize_detail_response_drops_raw_description_html_and_credentials_from_the_contract() -> None:
    html = '<section>secret product text<img src="https://images.example/description.jpg"></section>'
    candidate = normalize_detail_response(
        {
            "item": {
                "num_iid": "1003",
                "title": "安全商品",
                "desc": html,
                "api_key": "leaked-key",
                "nested": {"authorization": "Bearer leaked-token", "safe": "present"},
            }
        },
        evidence=_evidence(),
    )

    payload = candidate.raw_payload
    assert "desc" not in str(payload)
    assert html not in str(payload)
    assert "leaked-key" not in str(payload)
    assert "leaked-token" not in str(payload)
    assert candidate.source_detail_image_urls == ("https://images.example/description.jpg",)
