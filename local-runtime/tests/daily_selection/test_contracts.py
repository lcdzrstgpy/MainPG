from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.daily_selection.contracts import (  # noqa: E402
    ApiEvidence,
    DailySelectionCandidate,
    DailySelectionContractError,
    DailySelectionError,
    SourceVariantRecord,
)


def test_candidate_keeps_images_sku_source_data_and_selection_evidence_separate() -> None:
    candidate = DailySelectionCandidate(
        candidate_id="candidate-1",
        offer_id="offer-9",
        source_platform="1688",
        source_url="https://detail.1688.com/offer/9.html",
        source_title="露营灯",
        main_image_url="https://img.example.com/main.jpg",
        source_image_urls=["https://img.example.com/gallery.jpg"],
        source_detail_image_urls=["https://img.example.com/detail.jpg"],
        source_variant_records=[
            SourceVariantRecord(
                sku_id="sku-red",
                attributes={"颜色": "红色"},
                image_url="https://img.example.com/red.jpg",
            )
        ],
        source_attributes={"材质": "铝"},
        price_cny=19.9,
        min_order_quantity=2,
        selection_score=88.5,
        selection_reasons=["价格符合"],
        risk_tags=["需核验库存"],
        status="selected",
        evidence=[ApiEvidence(provider="1688", operation="offer.detail", request_id="r-1")],
        shop_name="灯具工厂",
        location="义乌",
        sales_text="月销 100",
        weight_text="0.5kg",
        package_info_text="24x12x8cm",
        freight_cny=8,
        captured_fields=["price_cny"],
        missing_capture_fields=["brand"],
        score_components={"price": 40},
        raw_payload={"offer": {"id": "offer-9"}, "token": "redacted"},
    )

    assert candidate.main_image_url.endswith("main.jpg")
    assert candidate.source_image_urls == ("https://img.example.com/gallery.jpg",)
    assert candidate.source_variant_records[0].attributes == {"颜色": "红色"}
    assert candidate.evidence[0].operation == "offer.detail"
    assert "token" not in candidate.raw_payload
    assert candidate.score_components == {"price": 40}


def test_error_contract_removes_sensitive_context() -> None:
    error = DailySelectionError(
        code="upstream_failed",
        message="The source request failed",
        context={"request_id": "r-1", "Authorization": "not-retained"},
    )

    assert error.context == {"request_id": "r-1"}


def test_candidate_parses_nested_records_before_filtering_sensitive_fields() -> None:
    candidate = DailySelectionCandidate(
        candidate_id="candidate-2",
        offer_id="offer-10",
        source_platform="1688",
        source_url="https://detail.1688.com/offer/10.html",
        source_title="折叠椅",
        main_image_url=None,
        source_variant_records=[
            {
                "sku_id": "sku-blue",
                "attributes": {"颜色": "蓝色", "token": "not-retained"},
                "image_url": "https://img.example.com/blue.jpg",
            }
        ],
        evidence=[
            {
                "provider": "1688",
                "operation": "offer.detail",
                "request_summary": {"Authorization": "not-retained", "offer_id": "offer-10"},
            }
        ],
    )

    assert isinstance(candidate.source_variant_records[0], SourceVariantRecord)
    assert candidate.source_variant_records[0].attributes == {"颜色": "蓝色"}
    assert isinstance(candidate.evidence[0], ApiEvidence)
    assert candidate.evidence[0].request_summary == {"offer_id": "offer-10"}


def test_candidate_rejects_binary_sku_image_in_raw_variant_record() -> None:
    with pytest.raises(DailySelectionContractError, match="URL string"):
        DailySelectionCandidate(
            candidate_id="candidate-3",
            offer_id="offer-11",
            source_platform="1688",
            source_url="https://detail.1688.com/offer/11.html",
            source_title="收纳箱",
            main_image_url=None,
            source_variant_records=[{"sku_id": "sku-1", "image_url": b"not-an-image-url"}],
        )
