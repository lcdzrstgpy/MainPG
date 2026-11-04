"""Deterministic, local scoring for daily-selection candidates.

评分回答的是「这个货源值不值得做」，而不是「这条数据填得全不全」：
销量、价格区间匹配、起订量门槛占大头，资料完整度只作为少量加分。
所有输入都来自候选本身与筛选条件，不读时钟、不访问网络，同输入必得同输出。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, ROUND_HALF_UP
import math
import re

from .contracts import DailySelectionCandidate, SourceVariantRecord
from .criteria import DailySelectionCriteria


# 权重合计 100 分。销量 35 / 关键词匹配 20 / 价格区间 15 / 起订量 10 / 店铺 5 / 资料完整 15。
_SALES_WEIGHT = Decimal("35")
_SALES_SATURATION = Decimal("100000")  # 10 万销量视为满分，避免头部爆款无限拉开差距
_MATCH_WEIGHT = Decimal("20")
_PRICE_FIT_WEIGHT = Decimal("15")
_MOQ_FIT_WEIGHT = Decimal("10")
_SHOP_WEIGHT = Decimal("5")
_EVIDENCE_MAIN_IMAGE = Decimal("4")
_EVIDENCE_GALLERY = Decimal("3")
_EVIDENCE_ATTRIBUTES = Decimal("3")
_EVIDENCE_SKU = Decimal("5")

_SALES_UNITS = {
    "万": Decimal("10000"),
    "w": Decimal("10000"),
    "千": Decimal("1000"),
    "k": Decimal("1000"),
}
_SALES_NUMBER = re.compile(r"(\d+(?:\.\d+)?)\s*(万|w|W|千|k|K)?")
_CENT = Decimal("0.01")


def score_candidate(
    candidate: DailySelectionCandidate, criteria: DailySelectionCriteria | None = None
) -> DailySelectionCandidate:
    """Return a candidate with a stable Decimal score and explainable components."""
    components = {
        "sales": _sales_score(candidate),
        "match": _match_score(candidate, criteria),
        "price_fit": _price_fit_score(candidate, criteria),
        "moq_fit": _moq_fit_score(candidate),
        "shop": _SHOP_WEIGHT if candidate.shop_name else Decimal("0"),
        **_evidence_components(candidate),
    }
    total = sum(components.values(), Decimal("0")).quantize(_CENT, rounding=ROUND_HALF_UP)
    return candidate.model_copy(update={"selection_score": total, "score_components": components})


def score_candidates(
    candidates: Sequence[DailySelectionCandidate], criteria: DailySelectionCriteria | None = None
) -> tuple[DailySelectionCandidate, ...]:
    """Score and sort candidates deterministically, highest score first.

    Ties fall back to sales, then lower price and lower MOQ so two candidates
    with identical scores still present in a commercially sensible order.
    """
    scored = tuple(score_candidate(candidate, criteria) for candidate in candidates)
    return tuple(sorted(scored, key=_rank_key))


def _rank_key(candidate: DailySelectionCandidate) -> tuple[Decimal, Decimal, bool, Decimal, bool, int, str]:
    price = candidate.price_cny
    moq = candidate.min_order_quantity
    return (
        -candidate.selection_score,
        -sales_count(candidate),
        price is None,
        price if price is not None else Decimal("0"),
        moq is None,
        moq if moq is not None else 0,
        candidate.candidate_id,
    )


def sales_count(candidate: DailySelectionCandidate) -> Decimal:
    """Best-effort sales volume: product-level text first, then summed SKU sales."""
    parsed = _parse_sales_text(candidate.sales_text)
    if parsed is not None:
        return parsed
    variant_total = sum(
        (Decimal(record.sales) for record in candidate.source_variant_records if record.sales),
        Decimal("0"),
    )
    return variant_total


def _parse_sales_text(value: str | None) -> Decimal | None:
    if not value:
        return None
    match = _SALES_NUMBER.search(value.replace(",", "").replace("，", ""))
    if match is None:
        return None
    amount = Decimal(match.group(1))
    unit = match.group(2)
    return amount * _SALES_UNITS.get(unit.casefold(), Decimal("1")) if unit else amount


def _sales_score(candidate: DailySelectionCandidate) -> Decimal:
    count = sales_count(candidate)
    if count <= 0:
        return Decimal("0")
    # 对数归一：1 万和 10 万之间的差距，比 0 和 1 千之间的差距更值得被区分。
    ratio = math.log10(float(count) + 1) / math.log10(float(_SALES_SATURATION) + 1)
    return (min(Decimal("1"), Decimal(str(ratio))) * _SALES_WEIGHT).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )


def _match_score(candidate: DailySelectionCandidate, criteria: DailySelectionCriteria | None) -> Decimal:
    title = candidate.source_title.casefold()
    if not title:
        return Decimal("0")
    if criteria is None or not criteria.keywords:
        return Decimal("12")
    matched = sum(1 for keyword in criteria.keywords if keyword.casefold() in title)
    if matched == 0:
        return Decimal("0")
    return min(_MATCH_WEIGHT, Decimal("12") + Decimal("4") * matched)


def _price_fit_score(candidate: DailySelectionCandidate, criteria: DailySelectionCriteria | None) -> Decimal:
    """价格越贴合筛选区间、且在区间内越便宜，得分越高。"""
    price = candidate.price_cny
    if price is None:
        return Decimal("0")
    if criteria is None:
        return Decimal("10")
    low, high = criteria.min_price, criteria.max_price
    if low is None and high is None:
        return Decimal("10")
    if (low is not None and price < low) or (high is not None and price > high):
        return Decimal("3")
    if low is not None and high is not None and high > low:
        position = (high - price) / (high - low)
        return (Decimal("10") + Decimal("5") * position).quantize(_CENT, rounding=ROUND_HALF_UP)
    return Decimal("12")


def _moq_fit_score(candidate: DailySelectionCandidate) -> Decimal:
    """起订量越低越容易测款，按门槛给分而不是「有就加分」。"""
    moq = candidate.min_order_quantity
    if moq is None:
        return Decimal("0")
    if moq <= 5:
        return _MOQ_FIT_WEIGHT
    if moq <= 10:
        return Decimal("8")
    if moq <= 20:
        return Decimal("6")
    if moq <= 50:
        return Decimal("4")
    if moq <= 100:
        return Decimal("2")
    return Decimal("0")


def _evidence_components(candidate: DailySelectionCandidate) -> dict[str, Decimal]:
    """资料完整度只做小幅加分，避免「详情拉得全」压过「销量好」。"""
    return {
        "main_image": _EVIDENCE_MAIN_IMAGE if candidate.main_image_url else Decimal("0"),
        "image_gallery": _EVIDENCE_GALLERY if candidate.source_image_urls else Decimal("0"),
        "attributes": _EVIDENCE_ATTRIBUTES if candidate.source_attributes else Decimal("0"),
        "complete_sku": _EVIDENCE_SKU if _has_complete_sku_evidence(candidate.source_variant_records) else Decimal("0"),
    }


def _has_complete_sku_evidence(records: Sequence[SourceVariantRecord]) -> bool:
    return bool(records) and all(
        record.sku_id
        and record.attributes
        and record.image_url is not None
        and record.price_cny is not None
        and record.min_order_quantity is not None
        for record in records
    )


# Alias kept for pipeline callers that describe this operation as ranking.
rank_candidates = score_candidates
