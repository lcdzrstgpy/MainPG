from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.quote_normalizer import QuoteItem  # noqa: E402
from wh_local.price_verification.sourcing.costs import (  # noqa: E402
    CandidateCostInputs,
    calculate_candidate_costs,
)
from wh_local.price_verification.sourcing.task_builder import (  # noqa: E402
    build_source_browser_image_search_payload,
)


def complete_quote(skc_id: str, sku_id: str) -> QuoteItem:
    return QuoteItem(
        skc_id=skc_id,
        sku_id=sku_id,
        original_declared_price_cny=Decimal("20"),
        main_image_url="https://images.example/product.jpg",
    )


def test_complete_skus_of_one_skc_become_one_source_task() -> None:
    batch = build_source_browser_image_search_payload(
        [complete_quote("SKC-1", "SKU-A"), complete_quote("SKC-1", "SKU-B")]
    )

    assert len(batch.tasks) == 1
    assert batch.tasks[0].source_quote_keys == ("SKC-1:SKU-A", "SKC-1:SKU-B")


def test_quotes_missing_source_evidence_do_not_create_tasks() -> None:
    missing_image = complete_quote("SKC-1", "SKU-A")
    missing_image.main_image_url = ""
    missing_price = complete_quote("SKC-2", "SKU-B")
    missing_price.original_declared_price_cny = None

    batch = build_source_browser_image_search_payload([missing_image, missing_price])

    assert batch.tasks == ()
    assert batch.skipped_quote_keys == ("SKC-1:SKU-A", "SKC-2:SKU-B")


def test_landed_cost_allocates_domestic_freight_across_moq() -> None:
    costs = calculate_candidate_costs(
        CandidateCostInputs(price="10", moq="5", domestic_freight="4")
    )

    assert costs.landed_cost == 10.8
    assert costs.cost_status == "closed"
    assert costs.review_required is False


def test_missing_domestic_freight_requires_review_not_closed_cost() -> None:
    costs = calculate_candidate_costs(CandidateCostInputs(price="10", moq="5"))

    assert costs.landed_cost is None
    assert costs.cost_status == "review_required"
    assert costs.review_required is True
