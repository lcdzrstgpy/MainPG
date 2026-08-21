"""Pure parsers for 1688 links and OneBound shop-search pages."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qs, urlparse

from .contracts import ApiEvidence, ShopPage
from .normalizer import sanitize_raw_payload


_OFFER_ID = re.compile(r"^[0-9]+$")
_OFFER_PATH = re.compile(r"/(?:offer/)?([0-9]+)(?:\.html?)?/?$", re.IGNORECASE)
_SHOP_OFFER_FRAGMENT = re.compile(r"(?:^|&)\s*offerid-([0-9]+)(?:\s*(?:&|$))", re.IGNORECASE)
_MAX_SHOP_PAGES = 100


def extract_1688_offer_id(value: str) -> str:
    """Extract a numeric offer ID from a plain ID or an authenticated-free 1688 URL."""
    if not isinstance(value, str):
        raise ValueError("1688 offer input must be a string")
    candidate = value.strip()
    if _OFFER_ID.fullmatch(candidate):
        return candidate
    parsed = urlparse(candidate)
    hostname = (parsed.hostname or "").rstrip(".").casefold()
    if parsed.scheme not in {"http", "https"} or not (hostname == "1688.com" or hostname.endswith(".1688.com")):
        raise ValueError("1688 offer input must be a numeric ID or an 1688 URL")
    match = _OFFER_PATH.fullmatch(parsed.path)
    if match:
        return match.group(1)
    for key in ("offerId", "offer_id", "num_iid", "id"):
        values = parse_qs(parsed.query).get(key)
        if values and _OFFER_ID.fullmatch(values[0]):
            return values[0]
    fragment = _SHOP_OFFER_FRAGMENT.search(parsed.query)
    if fragment:
        return fragment.group(1)
    raise ValueError("1688 offer URL did not include an offer ID")


def validate_shop_sid(value: object) -> str:
    """Return a non-empty upstream shop identifier without coercing booleans."""
    if isinstance(value, bool) or value is None:
        raise ValueError("shop sid is required")
    if isinstance(value, int):
        candidate = str(value)
    elif isinstance(value, str):
        candidate = value.strip()
    else:
        raise ValueError("shop sid must be a string or integer")
    if not candidate:
        raise ValueError("shop sid is required")
    return candidate


def normalize_shop_page(payload: Mapping[str, Any], evidence: ApiEvidence | None) -> ShopPage:
    """Keep real offer identities and enough sanitized pagination to stop safely."""
    cleaned = sanitize_raw_payload(payload)
    offer_ids: list[str] = []
    missing_offer_count = 0
    items, metadata = _items_and_metadata(cleaned)
    for item in items:
        offer_id = _offer_id_from_item(item)
        if offer_id is None:
            missing_offer_count += 1
        else:
            offer_ids.append(offer_id)
    total_pages, current_page, page_size = _pagination(metadata, cleaned)
    return ShopPage(
        offer_ids=tuple(offer_ids),
        missing_offer_count=missing_offer_count,
        has_next=_has_next(items, total_pages, current_page, page_size),
        total_pages=total_pages,
        evidence=evidence or ApiEvidence(provider="onebound-1688", operation="item_search_shop"),
    )


def _items_and_metadata(payload: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], ...], Mapping[str, Any]]:
    data = payload.get("data")
    container = data.get("items") if isinstance(data, Mapping) else None
    if container is None:
        container = payload.get("items")
    metadata = container if isinstance(container, Mapping) else {}
    collection = container.get("item") if isinstance(container, Mapping) and "item" in container else container
    if isinstance(collection, Mapping):
        return (collection,), metadata
    if not isinstance(collection, Sequence) or isinstance(collection, (str, bytes, bytearray)):
        return (), metadata
    return tuple(item for item in collection if isinstance(item, Mapping)), metadata


def _pagination(
    metadata: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[int | None, int | None, int | None]:
    data = payload.get("data")
    data_mapping = data if isinstance(data, Mapping) else {}
    total_results = _positive_or_zero(_first_value(metadata, data_mapping, payload, ("total_results", "total")))
    page_size = _positive(_first_value(metadata, data_mapping, payload, ("page_size", "pagesize", "pageSize")))
    current_page = _positive(_first_value(metadata, data_mapping, payload, ("current_page", "page", "page_no", "pageNo")))
    if total_results is None or page_size is None:
        return None, current_page, page_size
    total_pages = min(_MAX_SHOP_PAGES, (total_results + page_size - 1) // page_size)
    return total_pages, current_page, page_size


def _first_value(
    primary: Mapping[str, Any], secondary: Mapping[str, Any], tertiary: Mapping[str, Any], names: tuple[str, ...]
) -> object:
    for source in (primary, secondary, tertiary):
        for name in names:
            if name in source:
                return source[name]
    return None


def _positive(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _positive_or_zero(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _has_next(
    items: Sequence[Mapping[str, Any]], total_pages: int | None, current_page: int | None, page_size: int | None
) -> bool:
    if not items:
        return False
    if total_pages is not None:
        if total_pages == 0:
            return False
        if current_page is not None:
            return current_page < total_pages and current_page < _MAX_SHOP_PAGES
        if page_size is not None and len(items) < page_size:
            return False
        return total_pages > 1
    if current_page is not None and current_page >= _MAX_SHOP_PAGES:
        return False
    return True


def _offer_id_from_item(item: Mapping[str, Any]) -> str | None:
    for key in ("num_iid", "offer_id", "item_id", "id"):
        value = item.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, str) and _OFFER_ID.fullmatch(value.strip()):
            return value.strip()
    for key in ("detail_url", "url", "item_url", "offer_url"):
        value = item.get(key)
        if isinstance(value, str):
            try:
                return extract_1688_offer_id(value)
            except ValueError:
                continue
    return None
