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

# 仅在“产品本身是风险品”时才硬过滤；卖食品/药品/婴儿用品的**包装与容器**
# （袋/盒/箱/瓶/罐/收纳架等）不是风险品本身，不应因标题带“食品/药品/儿童”字样被过滤。
# 该豁免只作用于消费类风险（food/medical/infant），危险品与 IP 不受影响。
_CONSUMER_RISK_TAGS: frozenset[str] = frozenset({"food", "medical", "infant"})

# 材质/等级限定词：如“食品级硅胶”“医用级不锈钢”只是材料等级，不代表商品是食品/药品。
_GRADE_QUALIFIERS: tuple[str, ...] = (
    "食品级", "食用级", "医用级", "医疗级", "药用级", "药品级", "婴儿级",
    "food-grade", "food grade", "medical-grade", "medical grade",
)

# 售卖状态词：描述“风险品本身的包装形态”（罐装奶粉/盒装零食/瓶装饮料），
# 不代表卖家卖的是容器。判定容器语境前先剔除，避免“婴儿奶粉罐装”被误豁免。
_STATE_SUFFIXES: tuple[str, ...] = (
    "罐装", "盒装", "袋装", "瓶装", "桶装", "箱装", "包装", "打包",
)

# 容器/包装/收纳语境词：命中即认为商品是“盒子/袋子/架子/收纳”类载体而非风险品本身。
# 保留单字（袋/盒/箱…）以覆盖“药品盒”“食品袋”等复合词未收录的写法。
_PACKAGING_CONTEXT: tuple[str, ...] = (
    "包装", "打包", "袋子", "盒子", "纸箱", "箱子", "彩盒", "礼盒", "卡盒",
    "飞机盒", "瓦楞盒", "药盒", "收纳盒", "储物盒", "保鲜盒", "分装盒",
    "礼品袋", "手提袋", "购物袋", "外卖袋", "自封袋", "密封袋", "真空袋",
    "快递袋", "打包袋", "背心袋", "马甲袋", "马夹袋", "密实袋", "保鲜袋",
    "分装袋", "收纳袋", "封口袋", "垃圾袋", "食品袋", "铝箔袋", "保温袋",
    "纸袋", "帆布袋", "收纳箱", "储物箱", "周转箱", "整理箱",
    "托盘", "吸塑", "泡壳", "内托", "瓶盖", "封口", "贴纸", "标签",
    "收纳", "储物", "架子", "收纳架", "置物架", "展示架", "货架", "帽架",
    "挂架", "支架", "书架", "层架", "收纳柜", "储物柜", "展示柜", "矮柜",
    "收纳篮", "收纳筐", "储物篮", "密封罐", "储物罐", "收纳罐", "玻璃罐",
    "密封瓶", "收纳瓶", "分装瓶", "喷瓶", "瓶子",
    "袋", "盒", "箱", "瓶", "罐", "桶", "架", "柜", "篮", "篓", "筐",
    "packaging", "package", "packing", "wrapper", "box", "bag", "bottle",
    "container", "jar", "tube", "carton", "case", "foil", "storage", "rack",
    "tray", "dispenser",
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
    # 按字段逐个判断：只有同一字段同时出现“风险词 + 容器/包装语境”才豁免，
    # 避免店铺名含“包装”而标题是“婴儿奶粉”时被误豁免。
    detected: list[str] = []
    for field in (candidate.source_title, candidate.source_url, candidate.shop_name):
        if not field:
            continue
        for tag in _risk_tags_in_text(field):
            if tag not in detected:
                detected.append(tag)
    return _unique((*candidate.risk_tags, *detected))


def _risk_tags_in_text(text: str) -> tuple[str, ...]:
    """Return risk tags detected in a single field, applying packaging exemptions.

    - 等级限定词（食品级/医用级…）只描述材料等级，先移除再匹配。
    - food/medical/infant 若与容器/包装语境词同现，说明商品是包装/容器
      （如“食品包装袋”“药品包装盒”“儿童置物架”），不是风险品本身，豁免。
    - dangerous_goods / ip 不豁免。
    """
    scanned = text.casefold()
    for token in (*_GRADE_QUALIFIERS, *_STATE_SUFFIXES):
        scanned = scanned.replace(token, "")
    has_packaging_context = any(term in scanned for term in _PACKAGING_CONTEXT)
    detected: list[str] = []
    for tag, terms in _RISK_TERMS:
        if not any(term in scanned for term in terms):
            continue
        if tag in _CONSUMER_RISK_TAGS and has_packaging_context:
            continue
        detected.append(tag)
    return tuple(detected)


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
