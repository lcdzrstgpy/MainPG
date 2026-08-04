from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.quote_normalizer import QuoteItem  # noqa: E402
from wh_local.price_verification.sourcing.normalizer import (  # noqa: E402
    canonical_source_url,
    normalize_source_candidate,
    normalize_source_candidates,
)


def quote(*, sku_attributes: str = "红色") -> QuoteItem:
    return QuoteItem(
        skc_id="SKC-1",
        sku_id="SKU-1",
        product_title="同款收纳盒",
        sku_attribute_text=sku_attributes,
        original_declared_price_cny=Decimal("20"),
        main_image_url="https://images.example/box.jpg",
    )


def test_normalizer_canonicalizes_offer_and_closes_cost() -> None:
    candidate = normalize_source_candidate(
        quote(),
        {
            "url": "https://detail.1688.com/offer/12345.html?token=secret#item",
            "title": "同款收纳盒 红色",
            "image": "https://images.example/source-box.jpg",
            "price": "10",
            "freight": "2",
            "moq": "2",
            "variants": [{"name": "红色"}],
            "weight": "0.3",
        },
    )

    assert candidate["offer_id"] == "12345"
    assert candidate["source_url"] == "https://detail.1688.com/offer/12345.html"
    assert candidate["landed_cost"] == 11.0
    assert candidate["cost_status"] == "closed"
    assert candidate["source_decision"] == "recommended"


def test_explicit_variant_conflict_is_review_not_recommendation() -> None:
    candidate = normalize_source_candidate(
        quote(),
        {"title": "同款收纳盒 蓝色款", "price": 10, "freight": 2, "variants": ["蓝色"]},
    )

    assert candidate["product_evidence_status"] == "compatible"
    assert candidate["sku_evidence_status"] == "conflict"
    assert candidate["source_decision"] == "review"


def test_lookalike_1688_host_is_not_canonicalized_or_deduplicated() -> None:
    candidates = normalize_source_candidates(
        quote(),
        [
            {"url": "https://detail.1688.com/offer/12345.html", "title": "同款收纳盒 红色", "variants": ["红色"], "price": 10, "freight": 2},
            {"url": "https://not1688.com/offer/12345.html", "title": "同款收纳盒 红色", "variants": ["红色"], "price": 10, "freight": 2},
        ],
    )

    assert canonical_source_url("https://not1688.com/offer/12345.html") == "https://not1688.com/offer/12345.html"
    assert len(candidates) == 2


def test_tokenized_urls_for_same_canonical_1688_offer_are_deduplicated() -> None:
    candidates = normalize_source_candidates(
        quote(),
        [
            {"url": "https://detail.1688.com/offer/12345.html?token=one", "title": "同款收纳盒 红色", "variants": ["红色"], "price": 10, "freight": 2},
            {"url": "https://m.1688.com/offer/12345.html?access_token=two", "title": "同款收纳盒 红色", "variants": ["红色"], "price": 11, "freight": 2},
        ],
    )

    assert len(candidates) == 1
    assert candidates[0]["source_url"] == "https://detail.1688.com/offer/12345.html"


def test_explicit_nonpositive_moq_requires_review_but_missing_moq_defaults() -> None:
    absent_moq = normalize_source_candidate(
        quote(), {"title": "同款收纳盒 红色", "variants": ["红色"], "price": 10, "freight": 2}
    )
    zero_moq = normalize_source_candidate(
        quote(), {"title": "同款收纳盒 红色", "variants": ["红色"], "price": 10, "freight": 2, "moq": 0}
    )
    negative_moq = normalize_source_candidate(
        quote(), {"title": "同款收纳盒 红色", "variants": ["红色"], "price": 10, "freight": 2, "moq": -2}
    )

    assert absent_moq["moq"] == 1.0
    assert absent_moq["source_decision"] == "recommended"
    assert zero_moq["moq"] is None
    assert zero_moq["source_decision"] == "review"
    assert negative_moq["moq"] is None
    assert negative_moq["source_decision_reason"] == "invalid_moq"
