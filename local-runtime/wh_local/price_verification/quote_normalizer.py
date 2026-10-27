"""Pure, read-only normalization of Temu price-quote discovery evidence.

The browser plugin supplies captured JSON and DOM snapshots; this module never
opens a connection, writes to a platform, or trusts a page row as an adjusted
price unless the captured batch-price popup was explicitly confirmed.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, urlencode, urlparse, urlunsplit

from .contracts import PriceVerificationContractError, redact_sensitive, safe_json_value


class ForbiddenPlatformWriteError(PriceVerificationContractError):
    """Raised when evidence contains an action outside read-only discovery."""


@dataclass
class QuoteItem:
    quote_key: str = ""
    skc_id: str = ""
    # 平台侧商品货号（Temu 的 productSkcExtCode），即我们生成的 PP-xxxxxx。
    # 它是核价采集与商品主档之间唯一可靠的对应键，标题只作兜底。
    market_skc_code: str = ""
    sku_id: str = ""
    sku_true_id: str = ""
    sku_identifier_kind: str = ""
    sku_merchant_code: str = ""
    sku_attribute_set: str = ""
    sku_attribute_text: str = ""
    sku_quantity: str = ""
    skc_attribute_text: str = ""
    product_attribute_summary: str = ""
    spu_or_goods_id: str = ""
    site: str = ""
    status: str = ""
    original_declared_price_cny: Decimal | None = None
    adjusted_declared_price_cny: Decimal | None = None
    new_declared_price_cny: Decimal | None = None
    product_title: str = ""
    main_image_url: str = ""
    official_link_url: str = ""
    extra_image_urls: list[str] = field(default_factory=list)
    source_endpoint: str = ""
    capture_method: str = ""
    captured_at: str = ""
    evidence_sources: str = ""
    source_confidence: str = ""
    authenticity_status: str = ""
    completeness_score: int = 0
    missing_fields: str = ""
    conflict_fields: str = ""
    network_evidence_count: int = 0
    dom_evidence_count: int = 0
    source_http_statuses: str = ""


@dataclass(frozen=True)
class QuoteCounts:
    quotes: int
    complete_quotes: int
    review_quotes: int
    network_records: int
    raw_network_records: int
    ignored_network_records: int
    dom_rows: int
    raw_dom_rows: int
    ignored_dom_rows: int
    dom_rows_ignored_by_popup_state: int
    platform_item_quotes: int
    skc_quotes: int
    complete_skc_quotes: int


@dataclass(frozen=True)
class QuotePreview:
    quotes: list[QuoteItem]
    counts: QuoteCounts
    confidence_counts: dict[str, int]
    authenticity_status_counts: dict[str, int]


_COMPLETE_FIELDS = (
    ("skc_id", "SKC ID"), ("sku_id", "SKU ID"), ("site", "site"),
    ("original_declared_price_cny", "original_declared_price_cny"),
    ("adjusted_declared_price_cny", "adjusted_declared_price_cny"),
    ("product_title", "product_title"), ("main_image_url", "main_image_url"),
    ("source_endpoint", "source_endpoint"), ("captured_at", "captured_at"),
)
_CRITICAL_FIELDS = frozenset({
    "skc_id", "sku_id", "spu_or_goods_id", "original_declared_price_cny",
    "adjusted_declared_price_cny", "new_declared_price_cny",
})
_MONEY_KEYS = {
    "original": ("supplyPrice", "originalSupplyPrice", "originalDeclaredPrice", "originalDeclarePrice", "priceBeforeExchange", "supplierPrice", "supplierPriceValue", "supplierDeclarePrice", "oldSupplyPrice", "declarePrice", "declaredPrice", "原申报价格", "申报价格"),
    "adjusted": ("suggestSupplyPrice", "suggestSupplierPrice", "suggestPrice", "adjustedSupplyPrice", "adjustedSupplierPrice", "adjustedDeclaredPrice", "platformSuggestPrice", "recommendedSupplyPrice", "targetSupplyPrice", "targetSupplierPrice", "调整后申报价格", "建议价格", "建议供货价"),
    "new": ("newDeclaredPrice", "newSupplyPrice", "merchantPrice", "negotiatedPrice", "新申报价格"),
}
_CENT_MONEY_KEYS = frozenset(
    re.sub(r"[\s_\-()（）]+", "", key).casefold()
    for keys in _MONEY_KEYS.values()
    for key in keys
    if key.isascii()
)


def assert_read_only_evidence(payload: Mapping[str, Any]) -> None:
    """Validate evidence at the trusted input boundary without any I/O."""
    if not isinstance(payload, Mapping):
        raise PriceVerificationContractError("price quote evidence must be a mapping")
    try:
        safe_json_value(payload)
    except PriceVerificationContractError as error:
        if "platform write" in str(error):
            raise ForbiddenPlatformWriteError(str(error)) from error
        raise
    for record in network_records(payload):
        method = clean_text(record.get("method")).upper()
        if method and method not in {"GET", "POST"}:
            raise ForbiddenPlatformWriteError("price quote discovery captured unexpected HTTP method")


def normalize_price_quote_discovery(payload: Mapping[str, Any]) -> QuotePreview:
    """Return normalized quote evidence from a saved browser capture.

    Network evidence is authoritative.  Confirmed popup DOM rows may fill a
    missing adjusted/new price or visual field, but can never replace a network
    value without recording a conflict for review.
    """
    assert_read_only_evidence(payload)
    safe_payload = safe_json_value(payload)
    records = list(network_records(safe_payload))
    primary = [record for record in records if is_primary_price_quote_record(record)]
    selected = select_current_price_records(primary) or records
    raw_rows = list(dom_rows(safe_payload))
    popup_confirmed = confirmed_batch_popup(safe_payload)
    popup_rejected = primary and popup_was_not_confirmed(safe_payload)
    selected_rows = [] if popup_rejected else raw_rows

    network_items = [item for record in selected for item in quote_items_from_network_record(record)]
    dom_items: list[QuoteItem] = []
    parent_dom_item: QuoteItem | None = None
    for row in selected_rows:
        item = quote_item_from_dom_row(row, popup_confirmed=popup_confirmed)
        if not has_quote_signal(item):
            continue
        inherited_parent_skc = stringify_id(row.get("parent_skc_id"))
        # 弹窗里 SKU 行只显示「SKU货号」而不显示 SKU ID，所以货号也算变种身份。
        has_sku = bool(item.sku_id or item.sku_merchant_code)
        if inherited_parent_skc and has_sku and parent_dom_item is not None:
            inherit_parent_dom_item(item, parent_dom_item)
        elif item.skc_id:
            parent_dom_item = item
        elif has_sku and parent_dom_item is not None:
            inherit_parent_dom_item(item, parent_dom_item)
        dom_items.append(item)
    network_items = attach_sku_only_network_items(network_items, dom_items)
    items = [*align_network_to_dom_page(network_items, dom_items), *dom_items]
    quotes = dedupe_quotes(items)
    complete = [item for item in quotes if is_complete_quote(item)]
    return QuotePreview(
        quotes=quotes,
        counts=QuoteCounts(
            quotes=len(quotes), complete_quotes=len(complete),
            review_quotes=sum(needs_review(item) for item in quotes),
            network_records=len(selected), raw_network_records=len(records),
            ignored_network_records=len(records) - len(selected),
            dom_rows=len(selected_rows), raw_dom_rows=len(raw_rows),
            ignored_dom_rows=len(raw_rows) - len(selected_rows),
            dom_rows_ignored_by_popup_state=len(raw_rows) if popup_rejected else 0,
            platform_item_quotes=parent_item_count(selected),
            skc_quotes=quote_product_count(quotes),
            complete_skc_quotes=quote_product_count(complete),
        ),
        confidence_counts=dict(Counter(item.source_confidence for item in quotes)),
        authenticity_status_counts=dict(Counter(item.authenticity_status for item in quotes)),
    )


def network_records(payload: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    for container in (payload, payload.get("network")):
        if not isinstance(container, Mapping):
            continue
        records = container.get("records")
        if isinstance(records, list):
            yield from (record for record in records if isinstance(record, dict))
    captures = payload.get("captures")
    if isinstance(captures, Mapping):
        for capture in captures.values():
            if isinstance(capture, Mapping) and isinstance(capture.get("records"), list):
                yield from (record for record in capture["records"] if isinstance(record, dict))


def dom_rows(payload: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    dom = payload.get("dom")
    sources = (dom, payload)
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        rows = source.get("rows") if source is dom else source.get("dom_rows")
        if isinstance(rows, list):
            yield from (row for row in rows if isinstance(row, dict))


def select_current_price_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the latest capture per requested page; fallback to fullest capture."""
    if not records:
        return []
    page_records: dict[str, tuple[str, int, dict[str, Any]]] = {}
    for index, record in enumerate(records):
        page = request_page_key(record)
        if page:
            candidate = (clean_text(record.get("capturedAt")), index, record)
            if page not in page_records or candidate[:2] >= page_records[page][:2]:
                page_records[page] = candidate
    if page_records:
        return [entry[2] for entry in sorted(page_records.values(), key=lambda entry: entry[:2])]
    ranked = sorted(records, key=lambda record: (parent_item_count([record]), clean_text(record.get("capturedAt"))))
    return [ranked[-1]]


_STALE_NETWORK_AGE_SECONDS = 300


def align_network_to_dom_page(
    network_items: Sequence[QuoteItem], dom_items: Sequence[QuoteItem]
) -> list[QuoteItem]:
    """Drop network quotes captured on an older page state.

    The DOM snapshot is always taken at capture time, so it describes the
    current page.  A network record can be much older (e.g. the batch dialog
    was opened minutes before the capture click); merging it unchanged would
    reintroduce products that are no longer visible ("旧数据没被覆盖").  When
    the network capture is clearly older than the DOM rows, keep only network
    quotes whose SKC still appears on the current page.
    """
    if not network_items or not dom_items:
        return list(network_items)
    dom_skcs = {item.skc_id for item in dom_items if item.skc_id}
    if not dom_skcs:
        return list(network_items)
    dom_times = [timestamp for item in dom_items if (timestamp := _parse_iso(item.captured_at))]
    net_times = [timestamp for item in network_items if (timestamp := _parse_iso(item.captured_at))]
    if not dom_times or not net_times:
        return list(network_items)
    if (min(dom_times) - max(net_times)).total_seconds() <= _STALE_NETWORK_AGE_SECONDS:
        return list(network_items)
    return [item for item in network_items if item.skc_id in dom_skcs]


def attach_sku_only_network_items(
    network_items: Sequence[QuoteItem], dom_items: Sequence[QuoteItem]
) -> list[QuoteItem]:
    """Attach SKU-only network rows to the parent SKC shown in the popup DOM."""
    parent_by_sku: dict[str, QuoteItem] = {}
    for item in dom_items:
        if not item.skc_id:
            continue
        for identifier in (item.sku_id, item.sku_true_id, item.sku_merchant_code):
            normalized = stringify_id(identifier)
            if normalized:
                parent_by_sku.setdefault(normalized, item)

    attached: list[QuoteItem] = []
    for item in network_items:
        if item.skc_id:
            attached.append(item)
            continue
        parent = next(
            (
                parent_by_sku.get(stringify_id(identifier))
                for identifier in (item.sku_id, item.sku_true_id, item.sku_merchant_code)
                if stringify_id(identifier) in parent_by_sku
            ),
            None,
        )
        if parent is not None:
            item.skc_id = parent.skc_id
            item.market_skc_code = item.market_skc_code or parent.market_skc_code
            item.product_title = item.product_title or parent.product_title
            item.main_image_url = item.main_image_url or parent.main_image_url
            item.official_link_url = item.official_link_url or parent.official_link_url
            item.site = item.site or parent.site
        attached.append(item)
    return attached


def inherit_parent_dom_item(item: QuoteItem, parent: QuoteItem) -> QuoteItem:
    """Attach a visual child SKU row to the nearest parent SKC DOM row."""
    item.skc_id = parent.skc_id
    item.market_skc_code = item.market_skc_code or parent.market_skc_code
    item.product_title = item.product_title or parent.product_title
    item.main_image_url = item.main_image_url or parent.main_image_url
    item.official_link_url = item.official_link_url or parent.official_link_url
    item.site = item.site or parent.site
    return item


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def quote_items_from_network_record(record: Mapping[str, Any]) -> list[QuoteItem]:
    response = response_json(record)
    if not isinstance(response, (dict, list)):
        return []
    endpoint = endpoint_for(record)
    captured_at = clean_text(record.get("capturedAt"))
    status = clean_text(record.get("status"))
    items: list[QuoteItem] = []
    for mapping in walk_mappings(response):
        sku_infos = mapping.get("skuInfoList")
        if isinstance(sku_infos, list):
            parent = {key: value for key, value in mapping.items() if key != "skuInfoList"}
            for sku_info in sku_infos:
                if isinstance(sku_info, Mapping):
                    items.append(quote_from_mapping({**parent, **sku_info}, endpoint, captured_at, status))
            continue
        item = quote_from_mapping(mapping, endpoint, captured_at, status)
        if has_quote_signal(item) and not looks_like_nested_sku_only(mapping):
            items.append(item)
    return items


# Temu 价格弹窗的 SKU 单元格形如「L-Royal Gown Dress - Blue SKU货号：6297074883160」：
# 货号是商家 SKU 货号（productSkuExtCode），不是 SKU ID；SKU ID 列在弹窗里并不展示。
# 有的商家把变体号也编进货号（866344969878 variant-4），弹窗会折行显示成
# 「866344969878v / ariant-4」，整串才是完整货号，不能截断。
_SKU_CODE_RE = re.compile(
    r"\bSKU\s*货号\s*[:：]\s*([A-Za-z0-9_-]{3,}(?:\s*variant[-_]?\d+)?)",
    re.IGNORECASE,
)
_SKU_ID_RE = re.compile(r"\bSKU\s*(?:ID|编号)\s*[:：]\s*([A-Za-z0-9_-]{3,})")
_SKC_ID_RE = re.compile(r"\bSKC\s*[:：]\s*([A-Za-z0-9_-]{3,})")
_SKC_CODE_RE = re.compile(r"\bSKC\s*货号\s*[:：]\s*([A-Za-z0-9_-]{3,})")

# 有些商家的 SKU 货号/规格里带内部变体码（如 866344969878variant-4、variant-3）。
# 它只是系统序号，不是可读属性；当成「SKU 属性」展示会变成 variant-4。
_VARIANT_TOKEN_RE = re.compile(r"variant[-_\s]?\d+", re.IGNORECASE)


def _is_variant_token(value: str) -> bool:
    return bool(re.fullmatch(r"variant[-_\s]?\d+", clean_text(value), flags=re.IGNORECASE))


def _merchant_code(value: Any) -> str:
    """商家 SKU 货号：带内部变体码时要保留整串（866344969878 variant-4）。"""
    text = clean_text(value)
    if not text:
        return ""
    if _VARIANT_TOKEN_RE.search(text):
        text = re.sub(r"\s+", "", text)
    return stringify_id(text)


def _clean_attribute_text(value: Any, *, code: str = "") -> str:
    """把「货号 + 规格」串清洗成可读的 SKU 属性。

    弹窗的 SKU 单元格可能同时包含属性文案、``SKU货号：xxx`` 和内部变体码，
    甚至把三者拆到不同单元格；货号和变体码都不能当成属性。
    """
    text = clean_text(value)
    if not text:
        return ""
    text = clean_text(_SKU_CODE_RE.sub(" ", text))
    text = clean_text(_SKU_ID_RE.sub(" ", text))
    if code:
        if text.casefold().startswith(code.casefold()):
            text = text[len(code):]
        else:
            text = re.sub(
                rf"(?<![A-Za-z0-9]){re.escape(code)}(?![A-Za-z0-9])", " ", text
            )
    text = clean_text(_VARIANT_TOKEN_RE.sub(" ", text))
    if not text or _is_variant_token(text):
        return ""
    return "" if text in {"-", "—", "无"} else text


def _dom_sku_block(row: Mapping[str, Any]) -> str:
    """价格弹窗里 SKU 单元格的可见文本（含规格描述与「SKU货号：xxx」）。"""
    for cell in row.get("cells") or []:
        if not isinstance(cell, Mapping):
            continue
        cell_text = clean_text(cell.get("text"))
        if _SKU_CODE_RE.search(cell_text) or _SKU_ID_RE.search(cell_text):
            return cell_text
    return ""


def quote_item_from_dom_row(row: Mapping[str, Any], *, popup_confirmed: bool = False) -> QuoteItem:
    cells = dom_cell_map(row)
    text = " ".join(filter(None, [clean_text(row.get("text")), *map(clean_text, cells.values())]))
    source = clean_text(row.get("source")) or "dom_table"
    text_prices = money_values_in_text(text)
    original = money_for(cells, ("原申报价格(CNY)", "原申报价格", "申报价格(CNY)", "申报价格"))
    if original is None and text_prices:
        # Some Temu virtual grids do not expose column headers to the DOM.
        # The visible first currency amount remains the original declared price.
        original = text_prices[0]
    adjusted = money_for(cells, ("调整后申报价格(CNY)", "调整后申报价格", "建议价格", "建议供货价")) if popup_confirmed else None
    if adjusted is None and original is not None and len(text_prices) >= 2:
        # Header-less virtual grids: the second visible currency amount is the
        # adjusted (recommended) declared price on the Temu price confirmation
        # page, so capture it even when the popup confirmation was not used.
        adjusted = text_prices[1]
    new = money_for(cells, ("新申报价格(CNY)", "新申报价格")) if popup_confirmed else None
    sku_block = _dom_sku_block(row)
    merchant_match = _SKU_CODE_RE.search(sku_block)
    sku_id_match = _SKU_ID_RE.search(sku_block)
    merchant_code = _merchant_code(merchant_match.group(1)) if merchant_match else ""
    sku_id = stringify_id(sku_id_match.group(1)) if sku_id_match else ""
    # 属性优先取独立的属性列：部分商品把「SKU货号：xxx variant-N」单独放在一列，
    # 直接从该列切出来的剩余部分只是内部变体码，会把真正的属性覆盖掉。
    sku_attribute = _clean_attribute_text(
        text_for(cells, ("SKU属性值", "SKU属性集", "SKU属性", "属性集", "SKU规格", "规格", "属性")),
        code=merchant_code,
    ) or _clean_attribute_text(sku_block, code=merchant_code)
    if not sku_id and not merchant_code:
        # 老式无结构行：沿用原来的兜底正则，且只认 SKU ID/编号，绝不把货号当 SKU ID。
        sku_id = id_from(cells, ("SKU ID", "sku_id", "SKU编号"), text, r"\bSKU\s*(?:ID|编号)[:：\s]*([A-Za-z0-9_-]{4,})\b")
    skc_code_match = _SKC_CODE_RE.search(text)
    market_skc_code = clean_text(skc_code_match.group(1)) if skc_code_match else ""
    goods_id = id_from(cells, ("SPU", "SPU ID", "商品ID", "goods_id"), text, r"\b(?:SPU|Goods)[:：\s]*([A-Za-z0-9_-]{4,})\b")
    sku_quantity = text_for(cells, ("库存", "库存数量", "可售库存", "数量", "quantity", "stock"))
    row_links = dom_row_links(row)
    link = row_links[0] if row_links else ""
    if not goods_id and link:
        query_goods = parse_qs(urlparse(link).query).get("goods_id", [""])[0]
        goods_id = stringify_id(query_goods)
    product_title = text_for(cells, ("商品标题", "商品名称", "title")) or title_before_skc(text)
    return QuoteItem(
        skc_id=skc_id_from_dom_row(row, cells, text),
        market_skc_code=market_skc_code,
        sku_id=sku_id,
        sku_true_id=sku_id,
        sku_identifier_kind="sku_id" if sku_id else ("merchant_sku_code" if merchant_code else ""),
        sku_merchant_code=merchant_code,
        sku_attribute_text=sku_attribute,
        sku_quantity=sku_quantity,
        spu_or_goods_id=goods_id,
        site=text_for(cells, ("站点", "site")) or site_text(text), status=text_for(cells, ("状态", "status")),
        original_declared_price_cny=original, adjusted_declared_price_cny=adjusted,
        new_declared_price_cny=new, product_title=product_title,
        main_image_url=first_url(row), official_link_url=official_temu_link({**row, "href": link} if link else row, goods_id),
        source_endpoint=source, capture_method=source,
        captured_at=clean_text(row.get("capturedAt")), evidence_sources=source, dom_evidence_count=1,
    )


def dom_row_links(row: Mapping[str, Any]) -> list[str]:
    """Collect product links from a DOM row (row-level ``link`` plus cell URLs)."""
    links: list[str] = []
    for key in ("link", "href", "url"):
        value = clean_text(row.get(key))
        if value and value not in links:
            links.append(value)
    for cell in row.get("cells") or []:
        if not isinstance(cell, Mapping):
            continue
        value = clean_text(cell.get("url") or cell.get("href") or cell.get("link"))
        if value and value not in links:
            links.append(value)
    return links


def dedupe_quotes(items: Iterable[QuoteItem]) -> list[QuoteItem]:
    merged: dict[tuple[str, str, str, str], QuoteItem] = {}
    for item in items:
        key = dedupe_key(item)
        if key not in merged:
            key = compatible_dedupe_key(item, merged) or key
        if key not in merged:
            merged[key] = item
        elif quote_score(item) > quote_score(merged[key]):
            winner, loser = item, merged[key]
            merge_quote_evidence(winner, loser)
            merged[key] = winner
        else:
            merge_quote_evidence(merged[key], item)
    result = list(merged.values())
    for item in result:
        annotate_quote_integrity(item)
    return result


def compatible_dedupe_key(
    item: QuoteItem, merged: Mapping[tuple[str, str, str, str], QuoteItem]
) -> tuple[str, str, str, str] | None:
    """Join a network quote with its DOM snapshot row for the same SKC.

    One batch page is captured twice in a single pass: the network JSON
    (authoritative, carries the official link) and the visible DOM rows
    (carries the on-page prices).  They rarely share SKU identifiers, so a
    plain key match misses them and the review panel shows the same SKC
    twice with inconsistent columns.  Merge when the SKC matches and one
    capture is network JSON while the other is a DOM row.
    """
    # SKU 货号 + 兼容站点是同一变体的强标识：弹窗行经常解析不到 SKC（首列只有货号），
    # 先按货号合并，避免网络行与 DOM 行各留一条、DOM 里的 SKU 属性集丢失。
    for key, existing in merged.items():
        if (
            existing.sku_merchant_code
            and existing.sku_merchant_code == item.sku_merchant_code
            and _attribute_compatible(existing, item)
            and (
                not existing.site
                or not item.site
                or existing.site == item.site
            )
        ):
            return key
    if not item.skc_id:
        return None
    for key, existing in merged.items():
        if existing.skc_id != item.skc_id:
            continue
        if (
            existing.sku_id
            and existing.sku_id == item.sku_id
            and (not existing.site or not item.site)
        ):
            return key
        # 同一个 SKU 可能一边用官方 SKU ID、另一边只有商家 SKU 货号（Temu 价格弹窗只展示
        # 货号）。按货号相等也要合并，否则同一变体会被算成两行（468618588060 的 10 个
        # SKU 曾因此显示成 20 行）。
        if (
            existing.sku_merchant_code
            and existing.sku_merchant_code == item.sku_merchant_code
            and _attribute_compatible(existing, item)
            and (
                not existing.site
                or not item.site
                or existing.site == item.site
            )
        ):
            return key
        # Network/DOM evidence may be paired at SKC level only when at least
        # one side lacks a SKU identifier.  Two explicit, different SKUs are
        # distinct variants and must never be coalesced.
        if existing.sku_id and item.sku_id and existing.sku_id != item.sku_id:
            continue
        if _network_dom_pair(existing, item):
            return key
    return None


def _network_dom_pair(left: QuoteItem, right: QuoteItem) -> bool:
    """Whether two captures describe the same SKC from the two evidence paths."""
    return (
        left.capture_method == "network_json" and right.capture_method.startswith("dom_")
    ) or (
        right.capture_method == "network_json" and left.capture_method.startswith("dom_")
    )


def annotate_quote_integrity(item: QuoteItem) -> QuoteItem:
    missing = [label for field_name, label in _COMPLETE_FIELDS if getattr(item, field_name) in (None, "")]
    item.missing_fields = ", ".join(missing)
    item.completeness_score = round(100 * (len(_COMPLETE_FIELDS) - len(missing)) / len(_COMPLETE_FIELDS))
    bad_status = any(not 200 <= int(value) < 300 for value in item.source_http_statuses.split(" | ") if value.isdigit())
    if item.conflict_fields or bad_status:
        item.source_confidence, item.authenticity_status = "review", "source_conflict_needs_review" if item.conflict_fields else "network_http_status_needs_review"
    elif item.network_evidence_count and item.dom_evidence_count:
        item.source_confidence, item.authenticity_status = ("high", "network_dom_confirmed") if item.completeness_score >= 80 else ("medium", "network_dom_partial")
    elif item.network_evidence_count:
        item.source_confidence, item.authenticity_status = ("high", "network_primary") if item.completeness_score >= 80 else ("medium", "network_partial")
    else:
        item.source_confidence, item.authenticity_status = ("medium", "price_popup_dom_primary") if item.adjusted_declared_price_cny is not None and item.completeness_score >= 80 else ("low", "dom_only_needs_network_confirmation")
    if item.completeness_score < 60 and item.source_confidence != "review":
        item.source_confidence, item.authenticity_status = "low", "incomplete_needs_review"
    return item


def _sku_attribute_from_spec(*texts: str, code: str) -> str:
    """从 Temu 的 ``productSkuExtCode`` / ``spec`` 里剥出规格描述。

    Temu 的价格审核接口**没有独立的 SKU 属性字段**：``productSkuExtCode`` 与 ``spec``
    都是「货号 + 规格」拼成的一串（如 ``XL-486B Light Yellow Small Floral Ruffled``、
    ``Sky Blue-L``）。不拆开的话，货号只能截到 ``XL-486B``，规格被整段丢掉，
    页面「SKU 属性」就永远是「—」。这里把开头的货号剥掉，剩下的即规格。
    """
    for raw in texts:
        text = clean_text(raw)
        if not text:
            continue
        # 带内部变体码的货号可能是空格分隔的（866344969878 variant-4），比较前先压掉空白。
        compare_text, compare_code = text, clean_text(code)
        if _VARIANT_TOKEN_RE.search(text) or _VARIANT_TOKEN_RE.search(compare_code):
            compare_text = re.sub(r"\s+", "", compare_text)
            compare_code = re.sub(r"\s+", "", compare_code)
        if compare_code and compare_text.casefold().startswith(compare_code.casefold()):
            rest = compare_text[len(compare_code):].lstrip(" \t-_/|,，:：")
            if rest and not _is_variant_token(rest):
                return rest
            continue
        if compare_code and compare_text.casefold() == compare_code.casefold():
            continue
        if _is_variant_token(text):
            # 内部变体码（variant-4）不是可读规格，宁可留空交给页面 DOM 补。
            continue
        return text
    return ""


def quote_from_mapping(mapping: Mapping[str, Any], endpoint: str, captured_at: str, http_status: str) -> QuoteItem:
    sku_true_id = stringify_id(first_value(mapping, ("productSkuId", "skuId", "sku_id", "SKU ID")))
    raw_code_text = clean_text(first_value(mapping, ("productSkuExtCode", "skuExtCode", "productSkuCode", "skuCode", "sellerSku", "merchantSku")))
    spec_text = clean_text(first_value(mapping, ("spec", "skuSpec", "skuSpecText", "specDesc")))
    merchant_code = _merchant_code(raw_code_text) or stringify_id(spec_text)
    # 独立属性字段优先；Temu 价格审核接口只给拼串，退回到从 spec 里拆。
    sku_attribute = (
        clean_text(
            first_value(
                mapping,
                (
                    "skuAttributeText",
                    "skuAttributes",
                    "skuPropsText",
                    "skuAttributeSet",
                    "skuAttributeGroup",
                    "skuName",
                ),
            )
        )
        or _sku_attribute_from_spec(raw_code_text, spec_text, code=merchant_code)
    )
    # spec 只是变体序号时不是属性，而是页面「SKU货号」的一部分（866344969878variant-4）；
    # 并回货号才能让同一货号前缀下的多个变体保持独立，而不是被折叠成一行。
    if (
        spec_text
        and _is_variant_token(spec_text)
        and merchant_code
        and spec_text.casefold() not in merchant_code.casefold()
    ):
        merchant_code = f"{merchant_code}{spec_text}"
    images = image_urls(mapping)
    goods_id = stringify_id(first_value(mapping, ("productId", "goodsId", "spuId", "goods_id")))
    return QuoteItem(
        skc_id=stringify_id(first_value(mapping, ("skcId", "productSkcId", "skc_id", "SKC ID"))),
        market_skc_code=clean_text(
            first_value(mapping, ("productSkcExtCode", "skcExtCode", "productSkcCode"))
        ),
        sku_id=sku_true_id, sku_true_id=sku_true_id,
        sku_identifier_kind="sku_id" if sku_true_id else ("merchant_sku_code" if merchant_code else ""), sku_merchant_code=merchant_code,
        sku_attribute_set=clean_text(first_value(mapping, ("skuAttributeSet", "skuAttributeGroup", "skuName", "skuSpec"))),
        sku_attribute_text=sku_attribute,
        sku_quantity=clean_text(first_value(mapping, ("skuQuantity", "stockQuantity", "availableQuantity", "inventory", "stock", "quantity", "库存", "数量"))),
        skc_attribute_text=clean_text(first_value(mapping, ("skcAttributeText", "skcAttributes", "productAttributesText"))),
        product_attribute_summary=attribute_summary(first_value(mapping, ("productPropertyList", "productAttributeList", "attributes"))),
        spu_or_goods_id=goods_id,
        site=site_from_mapping(mapping),
        status=clean_text(first_value(mapping, ("orderStatus", "priceStatus", "reviewStatus", "status", "状态"))),
        original_declared_price_cny=money_for(mapping, _MONEY_KEYS["original"]),
        adjusted_declared_price_cny=money_for(mapping, _MONEY_KEYS["adjusted"]),
        new_declared_price_cny=money_for(mapping, _MONEY_KEYS["new"]),
        product_title=clean_text(first_value(mapping, ("productName", "goodsName", "title", "商品标题", "商品名称"))),
        main_image_url=images[0] if images else "", extra_image_urls=images[1:],
        official_link_url=official_temu_link(mapping, goods_id), source_endpoint=endpoint,
        capture_method="network_json", captured_at=captured_at, evidence_sources="network_json",
        network_evidence_count=1, source_http_statuses=http_status,
    )


def is_primary_price_quote_record(record: Mapping[str, Any]) -> bool:
    if "bargain-no-bom/batch/info/query" in endpoint_for(record).lower():
        return True
    return any(isinstance(mapping.get("priceReviewItemList"), list) for mapping in walk_mappings(response_json(record)))


def confirmed_batch_popup(payload: Mapping[str, Any]) -> bool:
    actions, dom = payload.get("actions"), payload.get("dom")
    state = actions.get("batch_price_popup") if isinstance(actions, Mapping) else None
    return bool(isinstance(state, Mapping) and state.get("ok") is True and isinstance(dom, Mapping) and dom.get("dialog_present") is True)


def popup_was_not_confirmed(payload: Mapping[str, Any]) -> bool:
    actions, dom = payload.get("actions"), payload.get("dom")
    state = actions.get("batch_price_popup") if isinstance(actions, Mapping) else None
    return bool(isinstance(state, Mapping) and state.get("ok") is False and not state.get("skipped") and not (isinstance(dom, Mapping) and dom.get("dialog_present") is True))


def response_json(record: Mapping[str, Any]) -> Any:
    response = record.get("responseJson")
    if isinstance(response, str):
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return None
    return response


def endpoint_for(record: Mapping[str, Any]) -> str:
    endpoint = clean_text(record.get("endpoint"))
    if endpoint:
        return endpoint
    url = clean_text(record.get("url"))
    return urlparse(url).path if url else ""


def request_page_key(record: Mapping[str, Any]) -> str:
    request = record.get("requestJson")
    if isinstance(request, str):
        try:
            request = json.loads(request)
        except json.JSONDecodeError:
            request = None
    for mapping in walk_mappings(request):
        for key in ("page", "pageNo", "pageNum", "pageNumber", "currentPage", "cursor", "pageCursor"):
            if clean_text(mapping.get(key)):
                return f"{key}={clean_text(mapping[key])}"
    return ""


def parent_item_count(records: Iterable[Mapping[str, Any]]) -> int:
    identities: set[str] = set()
    for record in records:
        for mapping in walk_mappings(response_json(record)):
            values = mapping.get("priceReviewItemList")
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, Mapping):
                        identity = stringify_id(first_value(value, ("skcId", "productSkcId", "productId", "goodsId")))
                        if identity:
                            identities.add(identity)
    return len(identities)


def quote_product_count(items: Iterable[QuoteItem]) -> int:
    return len({item.skc_id or f"{item.spu_or_goods_id}:{item.site}" for item in items if item.skc_id or item.spu_or_goods_id})


def dedupe_key(item: QuoteItem) -> tuple[str, str, str, str]:
    if item.skc_id and item.sku_id:
        return ("skc_sku", item.skc_id, item.sku_id, item.site)
    # 弹窗的 SKU 行只显示商家货号：缺 SKU ID 时也必须按货号区分变种，
    # 否则同一 SKC 下的多个 SKU 会被折叠成一行。同一货号挂多个属性时，
    # 属性同样参与身份，否则只会剩下第一个变体。
    if item.skc_id and item.sku_merchant_code:
        identity = item.sku_merchant_code
        if item.sku_attribute_text:
            identity = f"{identity}\u0000{normalize_key(item.sku_attribute_text)}"
        return ("skc_sku_code", item.skc_id, identity, item.site)
    if item.skc_id:
        return ("skc", item.skc_id, item.spu_or_goods_id, item.site)
    if item.sku_id:
        return ("sku", item.sku_id, item.spu_or_goods_id, item.site)
    return ("fallback", item.spu_or_goods_id, item.product_title, str(item.original_declared_price_cny or item.adjusted_declared_price_cny or ""))


def _attribute_compatible(left: QuoteItem, right: QuoteItem) -> bool:
    """同一货号下可能挂多个 SKU 属性；属性能区分变体时不能合并成一行。"""
    if not left.sku_attribute_text or not right.sku_attribute_text:
        return True
    return normalize_key(left.sku_attribute_text) == normalize_key(right.sku_attribute_text)


def merge_quote_evidence(target: QuoteItem, source: QuoteItem) -> None:
    fields = tuple(field_name for field_name, _ in _COMPLETE_FIELDS) + (
        "spu_or_goods_id", "status", "new_declared_price_cny", "sku_merchant_code", "sku_quantity",
        "sku_attribute_text", "sku_attribute_set", "official_link_url",
    )
    for field_name in fields:
        old, new = getattr(target, field_name), getattr(source, field_name)
        if new in (None, ""):
            continue
        if (
            field_name == "sku_attribute_text"
            and old not in (None, "")
            and old != new
            and _is_variant_token(old)
            and not _is_variant_token(new)
        ):
            # 网络行把内部变体码当成了属性；页面 DOM 的可读属性到位后应覆盖它。
            target.sku_attribute_text = new
            continue
        if old in (None, ""):
            setattr(target, field_name, new)
        elif field_name in _CRITICAL_FIELDS and values_conflict(old, new):
            target.conflict_fields = append_unique(target.conflict_fields, field_name)
    for field_name in ("evidence_sources", "source_endpoint", "capture_method", "captured_at", "source_http_statuses"):
        setattr(target, field_name, append_unique(getattr(target, field_name), getattr(source, field_name)))
    target.extra_image_urls = unique_urls([*target.extra_image_urls, source.main_image_url, *source.extra_image_urls])
    target.extra_image_urls = [url for url in target.extra_image_urls if url != target.main_image_url]
    target.network_evidence_count += source.network_evidence_count
    target.dom_evidence_count += source.dom_evidence_count


def quote_score(item: QuoteItem) -> int:
    return sum(getattr(item, field_name) not in (None, "") for field_name, _ in _COMPLETE_FIELDS) + 5 * item.network_evidence_count + item.dom_evidence_count


def has_quote_signal(item: QuoteItem) -> bool:
    return bool(item.skc_id or item.sku_id or item.sku_merchant_code or item.spu_or_goods_id) and any(value is not None for value in (item.original_declared_price_cny, item.adjusted_declared_price_cny, item.new_declared_price_cny))


def is_complete_quote(item: QuoteItem) -> bool:
    return item.completeness_score >= 80 and not needs_review(item)


def needs_review(item: QuoteItem) -> bool:
    return item.source_confidence in {"review", "low"} or "needs" in item.authenticity_status


def first_value(mapping: Mapping[str, Any], aliases: Iterable[str]) -> Any:
    normalized = {normalize_key(key): key for key in mapping}
    for alias in aliases:
        key = normalized.get(normalize_key(alias))
        if key is not None and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def money_for(mapping: Mapping[str, Any], aliases: Iterable[str]) -> Decimal | None:
    key_map = {normalize_key(key): key for key in mapping}
    source_key = next((key_map.get(normalize_key(alias)) for alias in aliases if key_map.get(normalize_key(alias)) is not None), None)
    value = mapping[source_key] if source_key is not None else None
    if value is None:
        return None
    text = clean_text(value).replace(",", "")
    matched = re.search(r"-?\d+(?:\.\d+)?", text)
    if not matched:
        return None
    try:
        amount = Decimal(matched.group(0))
        explicit_yuan = isinstance(value, str) and (bool(re.search(r"[¥￥元]", text)) or "." in text)
        if normalize_key(source_key) in _CENT_MONEY_KEYS and not explicit_yuan and amount == amount.to_integral_value():
            amount /= Decimal("100")
        return amount.quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def money_values_in_text(value: str) -> list[Decimal]:
    """Read visible currency values from header-less virtual table rows."""
    amounts: list[Decimal] = []
    for match in re.finditer(r"[¥￥]\s*(-?\d+(?:\.\d+)?)", value.replace(",", "")):
        try:
            amounts.append(Decimal(match.group(1)).quantize(Decimal("0.01")))
        except InvalidOperation:
            continue
    return amounts


def title_before_skc(value: str) -> str:
    """Keep a readable label when a virtual grid omits semantic title cells."""
    title = re.split(r"\bSKC(?:\s*(?:ID|信息))?[:：\s]", value, maxsplit=1, flags=re.IGNORECASE)[0]
    return clean_text(title)[:240]


def text_for(mapping: Mapping[str, Any], aliases: Iterable[str]) -> str:
    return clean_text(first_value(mapping, aliases))


def skc_id_from_dom_row(row: Mapping[str, Any], cells: Mapping[str, Any], text: str) -> str:
    """Read only explicit SKC fields/markers; never treat a SKU number as an SKC."""
    inherited = stringify_id(row.get("parent_skc_id"))
    if inherited:
        return inherited
    direct = stringify_id(first_value(cells, ("SKC", "SKC ID", "skc_id")))
    if direct:
        return direct
    pattern = r"\bSKC(?:\s*(?:ID|\u4fe1\u606f))?\s*[:\uFF1A]?\s*([A-Za-z0-9_-]{4,})\b"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return stringify_id(match.group(1)) if match else ""


def id_from(mapping: Mapping[str, Any], aliases: Iterable[str], text: str, pattern: str) -> str:
    direct = stringify_id(first_value(mapping, aliases))
    if direct:
        return direct
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return stringify_id(match.group(1)) if match else ""


def stringify_id(value: Any) -> str:
    text = clean_text(value)
    if isinstance(value, (dict, bool)) or not text:
        return ""
    match = re.search(r"[A-Za-z]*-?[A-Za-z0-9_-]*\d[A-Za-z0-9_-]*", text)
    return match.group(0) if match and len(match.group(0)) >= 4 else ""


def site_text(value: Any) -> str:
    text = clean_text(value)
    match = re.search(r"[\u4e00-\u9fffA-Za-z]{1,12}站", text)
    if match:
        return match.group(0)
    # 弹窗的 SKU 子行没有站点列：整行文本不能当成站点，否则每个 SKU 都会
    # 因为「站点不同」被拆成独立分组（同一 SKC 会碎成几十组）。
    return text if len(text) <= 12 else ""


def site_from_mapping(mapping: Mapping[str, Any]) -> str:
    """Extract the bound Temu site from a price-review network mapping.

    Temu's batch price-review JSON binds the site as a list per SKC rather
    than a plain scalar, e.g. ``semiHostedBindSiteNameList: ["美国站"]`` or
    ``semiHostedBindSiteList: [{"siteId": 100, "siteName": "美国站"}]``, so
    plain alias lookups silently miss it.
    """
    for key in ("semiHostedBindSiteNameList", "semiHostedBindSiteName", "bindSiteName"):
        text = clean_text(mapping.get(key))
        if text and site_text(text):
            return site_text(text)
    for key in ("semiHostedBindSiteList", "bindSiteList"):
        for item in walk_mappings(mapping.get(key) or []):
            if isinstance(item, Mapping):
                site = site_text(first_value(item, ("siteName", "siteNameCn", "站点")))
                if site:
                    return site
    return site_text(first_value(mapping, ("siteName", "siteNameCn", "site", "站点")))


def dom_cell_map(row: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name in ("cellMap", "cellsByHeader"):
        values = row.get(name)
        if isinstance(values, Mapping):
            output.update(values)
    cells = row.get("cells")
    if isinstance(cells, list):
        for index, cell in enumerate(cells):
            if isinstance(cell, Mapping):
                output[clean_text(cell.get("header")) or f"column_{index + 1}"] = cell.get("text", "")
    return output


def image_urls(mapping: Mapping[str, Any]) -> list[str]:
    values = [value for key, value in mapping.items() if any(marker in normalize_key(key) for marker in ("image", "img", "pic", "photo", "preview", "thumb"))]
    return unique_urls(url for value in values for url in urls_from_value(value))


def first_url(row: Mapping[str, Any]) -> str:
    return (image_urls(row) or [""])[0]


def official_temu_link(mapping: Mapping[str, Any], goods_id: str = "") -> str:
    """Return a stable public Temu product URL without tracking parameters."""
    raw = clean_text(
        first_value(
            mapping,
            (
                "officialLinkUrl", "official_link_url", "productUrl", "product_url",
                "goodsUrl", "goods_url", "productLink", "product_link",
                "linkUrl", "link_url", "href",
            ),
        )
    )
    resolved_goods_id = stringify_id(goods_id)
    if raw:
        parsed = urlparse(raw)
        hostname = (parsed.hostname or "").casefold()
        if hostname == "temu.com" or hostname.endswith(".temu.com"):
            query_goods = parse_qs(parsed.query).get("goods_id", [""])[0]
            resolved_goods_id = stringify_id(query_goods) or resolved_goods_id
            if not resolved_goods_id and parsed.path and parsed.path != "/":
                return urlunsplit(("https", "www.temu.com", parsed.path, "", ""))
    if not resolved_goods_id:
        return ""
    return "https://www.temu.com/goods.html?" + urlencode({"goods_id": resolved_goods_id})


def urls_from_value(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return [url for nested in value.values() for url in urls_from_value(nested)]
    if isinstance(value, list):
        return [url for nested in value for url in urls_from_value(nested)]
    return re.findall(r"(?:https?:)?//[^\s\"'<>),]+", clean_text(value))


def unique_urls(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        url = f"https:{value}" if value.startswith("//") else value
        if url and url not in result:
            result.append(url)
    return result


def attribute_summary(value: Any) -> str:
    if isinstance(value, Mapping):
        return " | ".join(f"{clean_text(key)}:{clean_text(item)}" for key, item in value.items() if clean_text(key) and clean_text(item))
    if isinstance(value, list):
        return " | ".join(filter(None, (attribute_summary(item) for item in value)))
    return clean_text(value)


def walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from walk_mappings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_mappings(nested)


def looks_like_nested_sku_only(mapping: Mapping[str, Any]) -> bool:
    return bool(first_value(mapping, ("productSkuId", "skuId"))) and not bool(first_value(mapping, ("skcId", "productSkcId", "productName", "goodsName", "mainImageUrl")))


def clean_text(value: Any) -> str:
    if value is None or isinstance(value, Mapping):
        return ""
    if isinstance(value, list):
        return " / ".join(filter(None, map(clean_text, value)))
    return str(value).strip()


def normalize_key(value: Any) -> str:
    return re.sub(r"[\s_\-()（）]+", "", str(value or "")).casefold()


def values_conflict(left: Any, right: Any) -> bool:
    if isinstance(left, Decimal) and isinstance(right, Decimal):
        return abs(left - right) > Decimal("0.01")
    return normalize_key(left) != normalize_key(right)


def append_unique(existing: str, new_value: Any) -> str:
    candidate = clean_text(new_value)
    parts = [part for part in existing.split(" | ") if part]
    return " | ".join(parts + ([candidate] if candidate and candidate not in parts else []))
