from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.contracts import PriceVerificationActor  # noqa: E402
from wh_local.price_verification.quote_normalizer import QuoteItem  # noqa: E402
from wh_local.price_verification.sourcing.service import build_source_preview  # noqa: E402


def complete_quote(skc_id: str, *, sku_attributes: str = "红色") -> QuoteItem:
    return QuoteItem(
        skc_id=skc_id,
        sku_id=f"SKU-{skc_id}",
        product_title="同款收纳盒",
        sku_attribute_text=sku_attributes,
        original_declared_price_cny=Decimal("20"),
        main_image_url="https://images.example/product.jpg",
    )


def source_result_with_candidate(**candidate: object) -> dict[str, object]:
    return {"items": [{"skc_id": "SKC-1", "status": "succeeded", "candidates": [candidate]}]}


def test_same_product_with_closed_cost_is_recommended() -> None:
    preview = build_source_preview(
        [complete_quote("SKC-1")],
        source_result_with_candidate(price=10, freight=2, title="同款收纳盒 红色", variants=["红色"]),
    )

    assert preview["items"][0]["candidates"][0]["source_decision"] == "recommended"


def test_variant_conflict_requires_review() -> None:
    preview = build_source_preview(
        [complete_quote("SKC-1", sku_attributes="红色")],
        source_result_with_candidate(title="蓝色款", price=10, freight=2, variants=["蓝色"]),
    )

    assert preview["items"][0]["source_review_candidates"]
    assert preview["items"][0]["source_decision"] == "review"


def test_missing_sku_evidence_creates_validation_target() -> None:
    preview = build_source_preview(
        [complete_quote("SKC-1")],
        source_result_with_candidate(title="同款收纳盒", price=10, freight=2),
    )

    assert preview["items"][0]["source_decision"] == "sku_validation"
    assert preview["source_sku_validation_targets"][0]["skc_id"] == "SKC-1"


def test_preview_keeps_partial_successes_and_only_failed_items_retryable() -> None:
    preview = build_source_preview(
        [complete_quote("SKC-1"), complete_quote("SKC-2")],
        {
            "items": [
                {"skc_id": "SKC-1", "status": "succeeded", "candidates": [{"title": "同款收纳盒 红色", "price": 10, "freight": 2, "variants": ["红色"]}]},
                {"skc_id": "SKC-2", "status": "failed", "error": "page unavailable"},
            ]
        },
    )

    assert preview["counts"]["recommended_quotes"] == 1
    assert preview["counts"]["failed_quotes"] == 1
    assert preview["retry_quote_keys"] == ["SKC-2:SKU-SKC-2"]
