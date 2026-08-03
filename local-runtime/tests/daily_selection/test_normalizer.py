from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.daily_selection.contracts import ApiEvidence  # noqa: E402
from wh_local.modules.daily_selection.normalizer import (  # noqa: E402
    MAX_DETAIL_IMAGES,
    MAX_PRODUCT_IMAGES,
    enrich_candidate_with_detail,
    normalize_search_response,
    sanitize_raw_payload,
)


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def search_payload() -> dict[str, Any]:
    payload = fixture("1688_keyword_search_success.json")
    item = payload["data"]["items"][0]
    item.update(
        {
            "detail_url": "https://detail.1688.com/offer-100.html?source=search",
            "pic_url": "https://images.example.test/products/main.jpg",
            "price": "19.90",
            "moq": "2",
            "sales": "123件成交",
            "shop_name": "露营用品旗舰店",
            "location": "浙江 金华",
        }
    )
    return payload


def search_evidence() -> ApiEvidence:
    return ApiEvidence(provider="onebound-1688", operation="item_search", request_id="search-001")


def detail_evidence() -> ApiEvidence:
    return ApiEvidence(provider="onebound-1688", operation="item_get", request_id="detail-001")


def test_normalize_search_extracts_a_traceable_1688_candidate() -> None:
    candidates = normalize_search_response(search_payload(), evidence=search_evidence())

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.candidate_id == "1688:offer-100"
    assert candidate.offer_id == "offer-100"
    assert candidate.source_platform == "1688"
    assert candidate.source_url == "https://detail.1688.com/offer-100.html"
    assert candidate.source_title == "便携露营灯"
    assert candidate.main_image_url == "https://images.example.test/products/main.jpg"
    assert candidate.price_cny == 19.9
    assert candidate.min_order_quantity == 2
    assert candidate.sales_text == "123件成交"
    assert candidate.shop_name == "露营用品旗舰店"
    assert candidate.location == "浙江 金华"
    assert candidate.evidence == (search_evidence(),)
    assert candidate.raw_payload["data"]["items"][0].get("token") is None
    assert candidate.raw_payload["data"].get("api_secret") is None


def test_detail_enrichment_preserves_search_evidence_and_captures_source_fields() -> None:
    candidate = normalize_search_response(search_payload(), evidence=search_evidence())[0]
    payload = fixture("1688_item_get_success.json")
    payload["data"].update(
        {
            "item_imgs": [
                {"url": "https://images.example.test/products/main.jpg"},
                {"url": "https://images.example.test/products/side.jpg"},
                {"url": "ftp://images.example.test/reject.jpg"},
                {"url": "https://images.example.test/products/side.jpg"},
            ],
            "detail_images": [
                "https://images.example.test/details/1.jpg",
                "https://images.example.test/details/1.jpg",
                "javascript:alert(1)",
            ],
            "props": {"材质": "铝合金", "颜色": "黑色"},
            "skus": [
                {
                    "sku_id": "sku-black",
                    "attributes": {"颜色": "黑色"},
                    "image_url": "https://images.example.test/sku/black.jpg",
                    "price": "21.50",
                    "moq": "3",
                }
            ],
            "package_info": "24 x 12 x 12 cm",
            "weight": "0.55 kg",
            "freight": "5.50",
            "access_token": "must-not-escape",
        }
    )

    enriched = enrich_candidate_with_detail(candidate, payload, evidence=detail_evidence())

    assert enriched.source_title == "便携露营灯"
    assert enriched.main_image_url == "https://images.example.test/products/main.jpg"
    assert enriched.source_image_urls == (
        "https://images.example.test/products/main.jpg",
        "https://images.example.test/products/side.jpg",
    )
    assert enriched.source_detail_image_urls == ("https://images.example.test/details/1.jpg",)
    assert enriched.source_attributes == {"材质": "铝合金", "颜色": "黑色"}
    assert len(enriched.source_variant_records) == 1
    assert enriched.source_variant_records[0].sku_id == "sku-black"
    assert enriched.source_variant_records[0].image_url == "https://images.example.test/sku/black.jpg"
    assert enriched.source_variant_records[0].price_cny == 21.5
    assert enriched.source_variant_records[0].min_order_quantity == 3
    assert enriched.package_info_text == "24 x 12 x 12 cm"
    assert enriched.weight_text == "0.55 kg"
    assert enriched.freight_cny == 5.5
    assert enriched.evidence == (search_evidence(), detail_evidence())
    assert enriched.raw_payload["data"].get("access_token") is None
    assert all(isinstance(url, str) for url in enriched.source_image_urls + enriched.source_detail_image_urls)


def test_normalizer_records_absences_and_uses_canonical_url_when_offer_id_is_missing() -> None:
    payload = fixture("1688_keyword_search_success.json")
    payload["data"]["items"] = [
        {
            "detail_url": "https://detail.1688.com/offer/fallback.html?foo=bar",
            "title": "缺失字段商品",
            "pic_url": "data:image/png;base64,not-allowed",
        }
    ]

    candidate = normalize_search_response(payload)[0]

    assert candidate.candidate_id == "1688:https://detail.1688.com/offer/fallback.html"
    assert candidate.offer_id == "https://detail.1688.com/offer/fallback.html"
    assert candidate.source_url == "https://detail.1688.com/offer/fallback.html"
    assert candidate.main_image_url is None
    assert {"main_image_url", "price_cny", "min_order_quantity", "sales_text", "shop_name", "location"} <= set(
        candidate.missing_capture_fields
    )


def test_search_rejects_lookalike_non_1688_source_domain() -> None:
    payload = fixture("1688_keyword_search_success.json")
    payload["data"]["items"] = [
        {
            "detail_url": "https://not1688.com/offer/fallback.html",
            "title": "伪装来源商品",
        }
    ]

    assert normalize_search_response(payload) == ()


def test_detail_price_and_moq_clear_search_missing_capture_fields() -> None:
    payload = search_payload()
    item = payload["data"]["items"][0]
    del item["price"]
    del item["moq"]
    candidate = normalize_search_response(payload)[0]
    detail = fixture("1688_item_get_success.json")
    detail["data"]["moq"] = "4"

    enriched = enrich_candidate_with_detail(candidate, detail, evidence=detail_evidence())

    assert {"price_cny", "min_order_quantity"} <= set(candidate.missing_capture_fields)
    assert enriched.price_cny == 19.9
    assert enriched.min_order_quantity == 4
    assert "price_cny" not in enriched.missing_capture_fields
    assert "min_order_quantity" not in enriched.missing_capture_fields


def test_image_caps_and_recursive_sanitization_never_retain_binary_data() -> None:
    candidate = normalize_search_response(search_payload())[0]
    payload = fixture("1688_item_get_success.json")
    payload["data"].update(
        {
            "item_imgs": [f"https://images.example.test/products/{index}.jpg" for index in range(MAX_PRODUCT_IMAGES + 3)],
            "detail_images": [f"https://images.example.test/details/{index}.jpg" for index in range(MAX_DETAIL_IMAGES + 3)],
        }
    )
    payload["data"]["session"] = {"cookie": "nope", "safe": [b"bytes", {"token": "nope"}]}

    cleaned = sanitize_raw_payload(payload)
    enriched = enrich_candidate_with_detail(candidate, payload)

    assert "session" not in cleaned["data"]
    assert len(enriched.source_image_urls) == MAX_PRODUCT_IMAGES
    assert len(enriched.source_detail_image_urls) == MAX_DETAIL_IMAGES
    assert b"bytes" not in repr(enriched.raw_payload).encode()
