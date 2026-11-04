# -*- coding: utf-8 -*-
"""选品评分的回归测试：排序要看「值不值得做」，不能退化成「字段填得全」。"""
from __future__ import annotations

from decimal import Decimal

from wh_local.data_collection.contracts import (
    DailySelectionCandidate,
    SourceVariantRecord,
)
from wh_local.data_collection.criteria import DailySelectionCriteria
from wh_local.data_collection.scoring import sales_count, score_candidate, score_candidates


def _candidate(
    offer_id: str,
    *,
    sales_text: str | None = None,
    price: Decimal | int | None = Decimal("20"),
    moq: int | None = 5,
    shop_name: str | None = None,
    with_detail: bool = False,
    variants: tuple[SourceVariantRecord, ...] = (),
) -> DailySelectionCandidate:
    return DailySelectionCandidate(
        candidate_id=f"cand:{offer_id}",
        offer_id=offer_id,
        source_platform="1688",
        source_url=f"https://detail.1688.com/offer/{offer_id}.html",
        source_title=f"收纳盒 {offer_id}",
        main_image_url=f"https://cbu01.alicdn.com/img/{offer_id}.jpg",
        source_image_urls=(f"https://cbu01.alicdn.com/img/{offer_id}-2.jpg",) if with_detail else (),
        source_attributes={"材质": "PP"} if with_detail else {},
        price_cny=price,
        min_order_quantity=moq,
        shop_name=shop_name,
        sales_text=sales_text,
        source_variant_records=variants,
    )


def _criteria() -> DailySelectionCriteria:
    return DailySelectionCriteria(keywords=("收纳盒",), min_price=3, max_price=35, target_count=10)


def _complete_variants() -> tuple[SourceVariantRecord, ...]:
    return (
        SourceVariantRecord(
            sku_id="sku-1",
            attributes={"颜色": "白"},
            image_url="https://cbu01.alicdn.com/img/sku-1.jpg",
            price_cny=Decimal("18"),
            min_order_quantity=2,
        ),
    )


def test_sales_outweighs_detail_completeness() -> None:
    best_seller = _candidate("111", sales_text="已售 5万")
    complete_but_unsold = _candidate(
        "222",
        sales_text=None,
        shop_name="完整详情店",
        with_detail=True,
        variants=_complete_variants(),
    )

    ranked = score_candidates((complete_but_unsold, best_seller), _criteria())

    assert [candidate.offer_id for candidate in ranked] == ["111", "222"]
    assert ranked[0].selection_score > ranked[1].selection_score
    # 销量项是拉开差距的主因，资料完整度只做小幅加分。
    assert ranked[0].score_components["sales"] > ranked[1].score_components["sales"]
    assert ranked[0].score_components["complete_sku"] == Decimal("0")
    assert ranked[1].score_components["complete_sku"] > 0


def test_equal_scores_tie_break_on_sales() -> None:
    quiet = _candidate("111", sales_text="100")
    hot = _candidate("222", sales_text="8000")

    ranked = score_candidates((quiet, hot), _criteria())

    assert [candidate.offer_id for candidate in ranked] == ["222", "111"]


def test_cheaper_price_inside_band_scores_higher() -> None:
    cheap = score_candidate(_candidate("111", price=Decimal("5")), _criteria())
    pricey = score_candidate(_candidate("222", price=Decimal("30")), _criteria())

    assert cheap.score_components["price_fit"] > pricey.score_components["price_fit"]
    # 超出筛选区间只保留很低的基础分，不会被误当成「价格合适」。
    out_of_band = score_candidate(_candidate("333", price=Decimal("90")), _criteria())
    assert out_of_band.score_components["price_fit"] == Decimal("3")


def test_low_moq_scores_higher_than_bulk_order() -> None:
    sample = score_candidate(_candidate("111", moq=2), _criteria())
    bulk = score_candidate(_candidate("222", moq=500), _criteria())

    assert sample.score_components["moq_fit"] > bulk.score_components["moq_fit"]


def test_sales_count_falls_back_to_summed_sku_sales() -> None:
    candidate = _candidate(
        "111",
        sales_text=None,
        variants=(
            SourceVariantRecord(sku_id="a", sales=30),
            SourceVariantRecord(sku_id="b", sales=70),
        ),
    )

    assert sales_count(candidate) == Decimal("100")


def test_components_keep_keys_used_by_the_results_ui() -> None:
    scored = score_candidate(
        _candidate("111", shop_name="示例店铺", with_detail=True, variants=_complete_variants()),
        _criteria(),
    )

    for key in ("sales", "match", "price_fit", "moq_fit", "shop", "main_image", "image_gallery", "attributes", "complete_sku"):
        assert key in scored.score_components
