from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.modules.daily_selection.contracts import (  # noqa: E402
    ApiEvidence,
    DailySelectionCandidate,
    SourceVariantRecord,
)
from wh_local.modules.daily_selection.criteria import DailySelectionCriteria  # noqa: E402
from wh_local.modules.daily_selection.filtering import (  # noqa: E402
    filter_and_score_candidates,
    filter_candidates,
)
from wh_local.modules.daily_selection.scoring import score_candidate  # noqa: E402


def candidate(**overrides: object) -> DailySelectionCandidate:
    data: dict[str, object] = {
        "candidate_id": "1688:offer-1",
        "offer_id": "offer-1",
        "source_platform": "1688",
        "source_url": "https://detail.1688.com/offer-1.html",
        "source_title": "折叠露营灯",
        "main_image_url": "https://img.example.test/main.jpg",
        "source_image_urls": ("https://img.example.test/gallery.jpg",),
        "source_detail_image_urls": ("https://img.example.test/detail.jpg",),
        "source_variant_records": (
            SourceVariantRecord(
                sku_id="sku-red",
                attributes={"颜色": "红色"},
                image_url="https://img.example.test/red.jpg",
                price_cny=Decimal("9.90"),
                min_order_quantity=2,
            ),
        ),
        "source_attributes": {"材质": "铝"},
        "price_cny": Decimal("9.90"),
        "min_order_quantity": 2,
        "shop_name": "露营工厂",
        "evidence": (ApiEvidence(provider="1688", operation="item_get", captured_at="2026-08-04T09:00:00+08:00"),),
    }
    data.update(overrides)
    if "offer_id" in overrides and "source_url" not in overrides:
        data["source_url"] = f"https://detail.1688.com/{data['offer_id']}.html"
    return DailySelectionCandidate(**data)


def test_filter_marks_duplicate_offer_as_filtered_without_losing_source_evidence() -> None:
    original = candidate()
    duplicate = candidate(
        candidate_id="1688:offer-1-copy",
        source_url="https://detail.1688.com/offer-1.html?from=search",
    )

    result = filter_candidates((original, duplicate), DailySelectionCriteria(keywords=["露营灯"]))

    assert result.candidates == (original,)
    assert result.filtered[0].status == "filtered"
    assert "duplicate_source_offer" in result.filtered[0].selection_reasons
    assert result.filtered[0].source_image_urls == duplicate.source_image_urls
    assert result.filtered[0].source_variant_records == duplicate.source_variant_records
    assert result.filtered[0].evidence == duplicate.evidence


def test_filter_uses_canonical_source_url_when_offer_id_is_normalized_from_url() -> None:
    first = candidate(
        candidate_id="1688:url-one",
        offer_id="https://detail.1688.com/unknown.html",
        source_url="https://detail.1688.com/unknown.html?spm=a",
    )
    duplicate = candidate(
        candidate_id="1688:url-two",
        offer_id="https://detail.1688.com/unknown.html",
        source_url="https://DETAIL.1688.COM/unknown.html#details",
    )

    result = filter_candidates((first, duplicate), DailySelectionCriteria(keywords=["露营灯"]))

    assert result.candidates == (first,)
    assert result.filtered[0].selection_reasons == ("duplicate_source_url",)


def test_filter_deduplicates_real_offer_id_against_url_fallback_identity() -> None:
    identified = candidate(
        candidate_id="1688:identified",
        offer_id="known-offer-9",
        source_url="https://detail.1688.com/shared-offer.html?spm=search",
    )
    idless_fallback = candidate(
        candidate_id="1688:idless",
        offer_id="https://detail.1688.com/shared-offer.html",
        source_url="https://DETAIL.1688.COM/shared-offer.html#details",
    )

    result = filter_candidates((identified, idless_fallback), DailySelectionCriteria(keywords=["露营灯"]))

    assert result.candidates == (identified,)
    assert result.filtered[0].selection_reasons == ("duplicate_source_url",)


def test_filter_applies_price_and_moq_limits_with_specific_reasons() -> None:
    too_cheap = candidate(candidate_id="1688:cheap", offer_id="cheap", price_cny=Decimal("4.99"))
    too_expensive = candidate(candidate_id="1688:expensive", offer_id="expensive", price_cny=Decimal("20.01"))
    moq_too_low = candidate(candidate_id="1688:moq", offer_id="moq", min_order_quantity=1)
    criteria = DailySelectionCriteria(
        keywords=["露营灯"], min_price=Decimal("5"), max_price=Decimal("20"), min_moq=2
    )

    result = filter_candidates((too_cheap, too_expensive, moq_too_low), criteria)

    assert result.candidates == ()
    assert [item.selection_reasons for item in result.filtered] == [
        ("price_below_min",),
        ("price_above_max",),
        ("moq_below_min",),
    ]


def test_filter_tags_medical_food_infant_dangerous_and_ip_risks() -> None:
    candidates = (
        candidate(candidate_id="1688:medical", offer_id="medical", source_title="医用护理灯"),
        candidate(candidate_id="1688:food", offer_id="food", source_title="食品级保鲜盒"),
        candidate(candidate_id="1688:infant", offer_id="infant", source_title="婴童安抚玩具"),
        candidate(candidate_id="1688:danger", offer_id="danger", source_title="危险品运输箱"),
        candidate(candidate_id="1688:ip", offer_id="ip", source_title="迪士尼授权同款"),
    )

    result = filter_candidates(candidates, DailySelectionCriteria(keywords=["露营灯"]))

    assert result.candidates == ()
    assert [item.risk_tags for item in result.filtered] == [
        ("medical",),
        ("food",),
        ("infant",),
        ("dangerous_goods",),
        ("ip",),
    ]
    assert all(item.status == "filtered" for item in result.filtered)


def test_duplicate_risk_candidate_keeps_its_risk_audit_reason() -> None:
    risky = candidate(candidate_id="1688:risk-one", offer_id="risk-one", source_title="medical organizer")
    duplicate = candidate(candidate_id="1688:risk-two", offer_id="risk-one", source_title="medical organizer")

    result = filter_candidates((risky, duplicate), DailySelectionCriteria(keywords=["organizer"]))

    assert result.filtered[1].risk_tags == ("medical",)
    assert result.filtered[1].selection_reasons == ("duplicate_source_offer", "risk_medical")


def test_filter_requires_main_image_and_risk_candidate_never_becomes_confirmable() -> None:
    no_image = candidate(candidate_id="1688:no-image", offer_id="no-image", main_image_url=None)
    risky = candidate(candidate_id="1688:risky", offer_id="risky", source_title="医疗器械收纳包")
    criteria = DailySelectionCriteria(keywords=["露营灯"], exclude_risks=False)

    result = filter_candidates((no_image, risky), criteria)

    assert result.filtered[0].selection_reasons == ("missing_main_image",)
    assert tuple(item.candidate_id for item in result.candidates) == ("1688:risky",)
    assert result.confirmable == ()
    assert result.candidates[0].risk_tags == ("medical",)


def test_retained_risk_candidate_is_downgraded_from_confirmed_status() -> None:
    risky = candidate(
        candidate_id="1688:confirmed-risk", offer_id="confirmed-risk", source_title="medical organizer", status="confirmed"
    )

    result = filter_candidates((risky,), DailySelectionCriteria(keywords=["organizer"], exclude_risks=False))

    assert result.candidates[0].status == "candidate"
    assert "risk_not_confirmable" in result.candidates[0].selection_reasons
    assert result.confirmable == ()


def test_scoring_is_stable_and_explains_all_components_and_evidence_bonuses() -> None:
    item = candidate()

    first = score_candidate(item)
    second = score_candidate(item)

    assert first == second
    assert first.selection_score == Decimal("100")
    assert first.score_components == {
        "supply": Decimal("25"),
        "match": Decimal("25"),
        "evidence": Decimal("40"),
        "freshness": Decimal("10"),
        "main_image": Decimal("8"),
        "image_gallery": Decimal("8"),
        "detail_images": Decimal("8"),
        "attributes": Decimal("8"),
        "complete_sku": Decimal("8"),
    }


def test_filter_and_score_returns_eligible_candidates_in_descending_stable_score_order() -> None:
    lower_evidence = candidate(
        candidate_id="1688:lower", offer_id="lower", source_image_urls=(), source_detail_image_urls=()
    )
    richer_evidence = candidate(candidate_id="1688:richer", offer_id="richer")

    result = filter_and_score_candidates(
        (lower_evidence, richer_evidence), DailySelectionCriteria(keywords=["露营灯"])
    )

    assert [item.candidate_id for item in result.candidates] == ["1688:richer", "1688:lower"]
    assert result.confirmable == result.candidates
    assert all(isinstance(item.selection_score, Decimal) for item in result.candidates)
