"""Local, evidence-preserving hard filters for daily-selection candidates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .contracts import DailySelectionCandidate
from .criteria import DailySelectionCriteria


_RISK_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("medical", ("medical", "healthcare", "医用", "医疗", "医疗器械", "药品", "药物")),
    ("food", ("food", "edible", "食品", "食用")),
    ("infant", ("infant", "baby", "toddler", "kids", "婴儿", "婴童", "儿童")),
    ("dangerous_goods", ("dangerous", "hazardous", "flammable", "explosive", "危险品", "易燃", "爆炸", "腐蚀", "烟花", "锂电池")),
    ("ip", ("trademark", "copyright", "replica", "侵权", "仿牌", "盗版", "迪士尼", "漫威", "宝可梦", "皮卡丘")),
)


@dataclass(frozen=True)
class FilteringResult:
    """Candidates that pass filtering plus audit-preserving rejected records."""

    candidates: tuple[DailySelectionCandidate, ...]
    filtered: tuple[DailySelectionCandidate, ...]
    confirmable: tuple[DailySelectionCandidate, ...]


def filter_candidates(
    candidates: Sequence[DailySelectionCandidate], criteria: DailySelectionCriteria
) -> FilteringResult:
    """Deduplicate then hard-filter candidates without discarding source evidence.

    Every rejected input is returned as a ``filtered`` candidate with additive,
    machine-readable reasons.  This keeps source URLs, images, SKU records and
    API evidence available for audit and later reviewer inspection.
    """
    accepted: list[DailySelectionCandidate] = []
    filtered: list[DailySelectionCandidate] = []
    seen_offer_ids: set[tuple[str, str]] = set()
    seen_source_urls: set[tuple[str, str]] = set()

    for candidate in candidates:
        risks = _risk_tags(candidate)
        risk_reasons = tuple(f"risk_{tag}" for tag in risks) if criteria.exclude_risks else ()
        duplicate_reason = _duplicate_reason(candidate, seen_offer_ids, seen_source_urls)
        if duplicate_reason is not None:
            filtered.append(_filtered(candidate, (duplicate_reason, *risk_reasons), risks))
            continue
        if risk_reasons:
            filtered.append(_filtered(candidate, risk_reasons, risks))
            continue
        sku_reason = _sku_filter_reason(candidate, criteria)
        if sku_reason is not None:
            filtered.append(_filtered(candidate, (sku_reason, *risk_reasons), risks))
            continue
        # 对齐参考版本：价格/起订量/主图缺失等只是软性提示，不硬过滤，
        # 由用户在人工复核时决定保留或排除。
        accepted.append(_with_risks(_with_filter_notes(candidate, criteria), risks))

    result_candidates = tuple(accepted)
    return FilteringResult(
        candidates=result_candidates,
        filtered=tuple(filtered),
        confirmable=tuple(candidate for candidate in result_candidates if not candidate.risk_tags),
    )


def filter_and_score_candidates(
    candidates: Sequence[DailySelectionCandidate], criteria: DailySelectionCriteria
) -> FilteringResult:
    """Apply hard filters and return the eligible candidates in score order."""
    from .scoring import score_candidates

    filtered_result = filter_candidates(candidates, criteria)
    ranked = score_candidates(filtered_result.candidates, criteria)
    return FilteringResult(
        candidates=ranked,
        filtered=filtered_result.filtered,
        confirmable=tuple(candidate for candidate in ranked if not candidate.risk_tags),
    )


def _duplicate_reason(
    candidate: DailySelectionCandidate,
    seen_offer_ids: set[tuple[str, str]],
    seen_source_urls: set[tuple[str, str]],
) -> str | None:
    offer_id = _real_offer_identity(candidate)
    source_url = (candidate.source_platform, canonical_source_url(candidate.source_url))
    if offer_id is not None and offer_id in seen_offer_ids:
        return "duplicate_source_offer"
    if source_url in seen_source_urls:
        return "duplicate_source_url"
    if offer_id is not None:
        seen_offer_ids.add(offer_id)
    seen_source_urls.add(source_url)
    return None


def _real_offer_identity(candidate: DailySelectionCandidate) -> tuple[str, str] | None:
    source_url = canonical_source_url(candidate.source_url)
    offer_id = candidate.offer_id.strip()
    # The normalizer uses the source URL as ``offer_id`` when an upstream item
    # has no product ID.  Keep that fallback distinct from a genuine offer ID.
    if _canonical_url_or_none(offer_id) == source_url:
        return None
    return candidate.source_platform, offer_id


def canonical_source_url(value: str) -> str:
    """Return the stable source-link identity used only for ID-less offers."""
    parsed = urlsplit(value.strip())
    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold()
    port = parsed.port
    netloc = hostname
    if parsed.username:
        netloc = f"{parsed.username}@{netloc}"
    if port is not None and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        netloc = f"{netloc}:{port}"
    return urlunsplit((scheme, netloc, parsed.path.rstrip("/") or "/", "", ""))


def _canonical_url_or_none(value: str) -> str | None:
    parsed = urlsplit(value)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        return None
    return canonical_source_url(value)


def _sku_filter_reason(
    candidate: DailySelectionCandidate, criteria: DailySelectionCriteria
) -> str | None:
    """Return a machine-readable reason when a candidate fails SKU filtering.

    SKU filters are hard filters: when any of min/max SKU count, SKU price, or
    SKU stock is configured, candidates that cannot satisfy it (including those
    without SKU data) are rejected instead of being softly noted.
    """
    variants = candidate.source_variant_records
    if criteria.min_sku_count is not None or criteria.max_sku_count is not None:
        count = len(variants)
        if criteria.min_sku_count is not None and count < criteria.min_sku_count:
            return "sku_count_below_min"
        if criteria.max_sku_count is not None and count > criteria.max_sku_count:
            return "sku_count_above_max"
    need_sku = (
        criteria.min_sku_price is not None
        or criteria.max_sku_price is not None
        or criteria.min_sku_stock is not None
        or criteria.max_sku_stock is not None
    )
    if need_sku and not variants:
        return "missing_sku"
    if criteria.min_sku_price is not None or criteria.max_sku_price is not None:
        prices = [variant.price_cny for variant in variants if variant.price_cny is not None]
        if not prices:
            return "missing_sku_price"
        if criteria.min_sku_price is not None and min(prices) < criteria.min_sku_price:
            return "sku_price_below_min"
        if criteria.max_sku_price is not None and max(prices) > criteria.max_sku_price:
            return "sku_price_above_max"
    if criteria.min_sku_stock is not None or criteria.max_sku_stock is not None:
        stocks = [variant.quantity for variant in variants if variant.quantity is not None]
        if not stocks:
            return "missing_sku_stock"
        if criteria.min_sku_stock is not None and min(stocks) < criteria.min_sku_stock:
            return "sku_stock_below_min"
        if criteria.max_sku_stock is not None and max(stocks) > criteria.max_sku_stock:
            return "sku_stock_above_max"
    return None


def _with_filter_notes(
    candidate: DailySelectionCandidate, criteria: DailySelectionCriteria
) -> DailySelectionCandidate:
    """Append soft quality notes instead of hard-filtering on them.

    Aligned with the reference workbench: price / MOQ / missing image only
    affect the selection score and appear as machine-readable reasons, while
    the reviewer decides whether to keep the candidate.
    """
    notes: list[str] = []
    if candidate.main_image_url is None:
        notes.append("missing_main_image")
    if criteria.min_price is not None and (
        candidate.price_cny is None or candidate.price_cny < criteria.min_price
    ):
        notes.append("missing_price" if candidate.price_cny is None else "price_below_min")
    if criteria.max_price is not None and (
        candidate.price_cny is None or candidate.price_cny > criteria.max_price
    ):
        reason = "missing_price" if candidate.price_cny is None else "price_above_max"
        if reason not in notes:
            notes.append(reason)
    if criteria.min_moq is not None and (
        candidate.min_order_quantity is not None and candidate.min_order_quantity > criteria.min_moq
    ):
        # ``min_moq`` 是起订量上限：高于上限提示“起订量偏高”，不硬过滤。
        notes.append("moq_above_limit")
    if candidate.min_order_quantity is None:
        notes.append("missing_moq")
    if not notes:
        return candidate
    return candidate.model_copy(
        update={"selection_reasons": _unique((*candidate.selection_reasons, *notes))}
    )


def _risk_tags(candidate: DailySelectionCandidate) -> tuple[str, ...]:
    # 对齐模板：风险词只扫描标题/链接/店铺，避免属性里的否定语境
    # （如“不适宜人群：少年儿童”“本品不能代替药物”）被误判为风险。
    text = " ".join(
        value for value in (candidate.source_title, candidate.source_url, candidate.shop_name) if value
    ).casefold()
    detected = [tag for tag, terms in _RISK_TERMS if any(term in text for term in terms)]
    return _unique((*candidate.risk_tags, *detected))


def _with_risks(candidate: DailySelectionCandidate, risks: Sequence[str]) -> DailySelectionCandidate:
    risk_tags = _unique((*candidate.risk_tags, *risks))
    if risk_tags and candidate.status == "confirmed":
        return candidate.model_copy(
            update={
                "status": "candidate",
                "risk_tags": risk_tags,
                "selection_reasons": _unique((*candidate.selection_reasons, "risk_not_confirmable")),
            }
        )
    return candidate.model_copy(update={"risk_tags": risk_tags})


def _filtered(
    candidate: DailySelectionCandidate, reasons: Sequence[str], risks: Sequence[str]
) -> DailySelectionCandidate:
    return candidate.model_copy(
        update={
            "status": "filtered",
            "selection_reasons": _unique((*candidate.selection_reasons, *reasons)),
            "risk_tags": _unique((*candidate.risk_tags, *risks)),
        }
    )


def _unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


# A clear alias for callers that prefer an action-oriented verb.
apply_filters = filter_candidates
