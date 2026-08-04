from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.daily_selection.contracts import (  # noqa: E402
    ApiEvidence,
    DailySelectionCandidate,
    DailySelectionContractError,
    DailySelectionError,
    ImageReference,
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
        price_cny="19.90",
        min_order_quantity=2,
        selection_score="88.50",
        selection_reasons=["价格符合"],
        risk_tags=["需核验库存"],
        status="confirmed",
        evidence=[ApiEvidence(provider="1688", operation="offer.detail", request_id="r-1")],
        shop_name="灯具工厂",
        location="义乌",
        sales_text="月销 100",
        weight_text="0.5kg",
        package_info_text="24x12x8cm",
        freight_cny="8.00",
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
    assert isinstance(candidate, BaseModel)
    assert candidate.price_cny == Decimal("19.90")
    assert candidate.selection_score == Decimal("88.50")
    assert candidate.freight_cny == Decimal("8.00")
    assert candidate.model_dump(mode="json")["price_cny"] == "19.90"


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


def test_decimal_contracts_preserve_exact_arithmetic_for_candidate_and_sku_money() -> None:
    candidate = DailySelectionCandidate(
        candidate_id="candidate-decimal",
        offer_id="offer-decimal",
        source_platform="1688",
        source_url="https://detail.1688.com/offer/decimal.html",
        source_title="精确金额商品",
        main_image_url=None,
        price_cny=Decimal("0.1") + Decimal("0.2"),
        freight_cny="0.30",
        selection_score="99.90",
        source_variant_records=[{"sku_id": "sku-decimal", "price_cny": "0.30"}],
    )

    assert candidate.price_cny == Decimal("0.3")
    assert candidate.freight_cny == Decimal("0.30")
    assert candidate.selection_score == Decimal("99.90")
    assert candidate.source_variant_records[0].price_cny == Decimal("0.30")
    assert candidate.model_dump(mode="json")["source_variant_records"][0]["price_cny"] == "0.30"


def test_contract_sanitization_preserves_ordinary_english_and_redacts_explicit_credentials() -> None:
    evidence = ApiEvidence(
        provider="fake-1688",
        operation="item_search",
        response_summary={
            "cookie jar": "kept",
            "tokenizer": "kept",
            "sessional": "kept",
            "secretary": "kept",
            "notes": [
                "cookie jar tokenizer sessional",
                "key=must-not-escape",
                "api_key=must-not-escape",
                "Authorization: Bearer must-not-escape",
            ],
            "api_secret": "must-not-escape",
        },
    )

    assert evidence.response_summary["cookie jar"] == "kept"
    assert evidence.response_summary["tokenizer"] == "kept"
    assert evidence.response_summary["sessional"] == "kept"
    assert evidence.response_summary["secretary"] == "kept"
    assert evidence.response_summary["notes"][0] == "cookie jar tokenizer sessional"
    assert "must-not-escape" not in repr(evidence.response_summary)
    assert "api_secret" not in evidence.response_summary


@pytest.mark.parametrize("unsafe", [object(), {"not", "json"}])
def test_contract_mapping_fields_reject_values_without_stable_json_serialization(unsafe: object) -> None:
    with pytest.raises(DailySelectionContractError, match="JSON"):
        ApiEvidence(
            provider="fake-1688",
            operation="item_search",
            request_summary={"unsafe": unsafe},
        )


@pytest.mark.parametrize(
    "unsafe_url",
    ["https://bad host.example/image.jpg", "https://images.example.test:99999/image.jpg"],
)
def test_contract_urls_reject_malformed_http_hosts_and_ports(unsafe_url: str) -> None:
    with pytest.raises(DailySelectionContractError, match="URL"):
        ImageReference(url=unsafe_url)
