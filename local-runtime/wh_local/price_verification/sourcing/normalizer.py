"""Normalize read-only browser source results into decision-ready candidates."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..quote_normalizer import QuoteItem
from .costs import calculate_candidate_costs
from .contracts import CandidateCostInputs
from .identity import evaluate_product_evidence, evaluate_sku_evidence


def normalize_source_candidate(
    quote: QuoteItem | Mapping[str, Any], raw_candidate: Mapping[str, Any], *, quote_key: str = ""
) -> dict[str, Any]:
    """Make one browser candidate JSON-safe and decide its local disposition."""
    if not isinstance(raw_candidate, Mapping):
        raise TypeError("raw_candidate must be a mapping")
    offer_id = _offer_id(raw_candidate)
    source_url = canonical_source_url(
        _first_text(raw_candidate, "source_url", "url", "product_url", "detail_url", "item_url", "offer_url"),
        offer_id,
    )
    offer_id = offer_id or offer_id_from_url(source_url)
    title = _first_text(raw_candidate, "source_title", "title", "product_title", "name")
    variants = _variants(raw_candidate)
    price = _number(raw_candidate, "price", "price_cny", "unit_price", "unit_price_cny", "sku_price")
    promotion_price = _number(raw_candidate, "promotion_price", "promotionPrice", "sale_price", "activity_price")
    image_search_rank = _positive_int(raw_candidate.get("image_search_rank"))
    source_channel = _first_text(raw_candidate, "source_channel") or ""
    moq, moq_status = _moq(raw_candidate)
    freight = _number(raw_candidate, "freight", "freight_cny", "domestic_freight", "domestic_freight_cny")
    weight = _number(raw_candidate, "weight", "weight_kg")
    product_status, product_evidence = evaluate_product_evidence(quote, raw_candidate)
    sku_status, sku_evidence = evaluate_sku_evidence(quote, raw_candidate)
    costs = None
    if price is not None and price > 0 and moq is not None:
        try:
            costs = calculate_candidate_costs(
                CandidateCostInputs(price=price, moq=moq, domestic_freight=freight)
            ).to_payload()
        except ValueError:
            costs = None
    decision, reason = _decision(product_status, sku_status, price, costs, moq_status)
    candidate_key = _first_text(raw_candidate, "candidate_key", "id", "candidate_id") or offer_id or source_url or title
    return {
        "quote_key": quote_key,
        "candidate_key": candidate_key[:240],
        "offer_id": offer_id,
        "source_url": source_url,
        "source_title": title,
        "main_image_url": _first_text(raw_candidate, "main_image_url", "image", "image_url", "pic_url"),
        "variants": list(variants),
        "sku_attributes": _first_text(raw_candidate, "sku_attributes", "sku_attribute_text", "source_sku_attributes"),
        "price": price,
        "promotion_price": promotion_price,
        "image_search_rank": image_search_rank,
        "sales": _number(raw_candidate, "sales", "sold", "volume"),
        "moq": moq,
        "moq_status": moq_status,
        "domestic_freight": freight,
        "weight_kg": weight,
        "source_channel": source_channel,
        "product_evidence_status": product_status,
        "product_evidence": list(product_evidence),
        "sku_evidence_status": sku_status,
        "sku_evidence": list(sku_evidence),
        "source_decision": decision,
        "source_decision_reason": reason,
        **(costs or {"unit_price": price, "landed_cost": None, "cost_status": "review_required", "review_required": True}),
    }


def normalize_source_candidates(
    quote: QuoteItem | Mapping[str, Any], candidates: Iterable[Mapping[str, Any]], *, quote_key: str = ""
) -> list[dict[str, Any]]:
    """Normalize and deduplicate by stable offer URL before any ranking."""
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in candidates:
        if not isinstance(raw, Mapping):
            continue
        candidate = normalize_source_candidate(quote, raw, quote_key=quote_key)
        key = candidate["offer_id"] or candidate["source_url"] or candidate["candidate_key"]
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(candidate)
    return output


def canonical_source_url(value: object, offer_id: str = "") -> str:
    """Strip fragments/secrets and canonicalize recognized 1688 offers."""
    url = value.strip() if isinstance(value, str) else ""
    product_id = offer_id.strip() if isinstance(offer_id, str) else ""
    if not url:
        return f"https://detail.1688.com/offer/{product_id}.html" if product_id else ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    product_id = product_id or offer_id_from_url(url)
    if _is_1688_host(parts.hostname) and product_id:
        return f"https://detail.1688.com/offer/{product_id}.html"
    blocked = {"token", "access_token", "session", "sessionid", "sid", "authorization", "auth", "password", "secret", "key"}
    query = [(key, item) for key, item in parse_qsl(parts.query, keep_blank_values=True) if key.casefold() not in blocked]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def offer_id_from_url(value: str) -> str:
    try:
        host = urlsplit(value).hostname
    except ValueError:
        return ""
    if not _is_1688_host(host):
        return ""
    match = re.search(r"(?:offer/|offerId=|offer_id=)(\d{3,})", value, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _is_1688_host(host: str | None) -> bool:
    normalized = host.casefold().rstrip(".") if isinstance(host, str) else ""
    return normalized == "1688.com" or normalized.endswith(".1688.com")


def _decision(product_status: str, sku_status: str, price: float | None, costs: Mapping[str, Any] | None, moq_status: str) -> tuple[str, str]:
    # A detected SKU contradiction remains useful review evidence even when
    # the short result title cannot independently prove the product identity.
    # Image search intentionally starts from the quote image, so withholding it
    # as "no reliable source" would hide the exact variant the employee needs
    # to reject or correct.
    if sku_status == "conflict":
        return "review", "sku_attribute_conflict"
    if product_status == "conflict":
        return "no_reliable_source", "incompatible_product_evidence"
    if product_status != "compatible":
        return "review", "missing_compatible_product_evidence"
    if sku_status != "compatible":
        return "sku_validation", "sku_attributes_need_validation"
    if moq_status == "invalid":
        return "review", "invalid_moq"
    if price is None or price <= 0:
        return "review", "missing_sku_price"
    if not costs or costs.get("cost_status") != "closed":
        return "review", "cost_not_closed"
    return "recommended", "compatible_product_sku_and_closed_cost"


def _offer_id(raw: Mapping[str, Any]) -> str:
    value = _first_text(raw, "offer_id", "offerId", "product_id", "productId", "num_iid", "item_id", "id")
    return value or offer_id_from_url(
        _first_text(raw, "source_url", "url", "product_url", "detail_url", "item_url", "offer_url")
    )


def _variants(raw: Mapping[str, Any]) -> tuple[str, ...]:
    values = raw.get("variants") or raw.get("source_variant_records") or raw.get("variant_records") or ()
    output: list[str] = []
    if isinstance(values, (list, tuple)):
        for entry in values:
            if isinstance(entry, Mapping):
                value = _first_text(entry, "name", "text", "label", "value", "title")
            else:
                value = entry.strip() if isinstance(entry, str) else ""
            if value and value not in output:
                output.append(value)
    return tuple(output)


def _number(raw: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = raw.get(key)
        if value is None or isinstance(value, bool):
            continue
        number = _parsed_number(value)
        if number is not None and number >= 0:
            return number
    return None


def _moq(raw: Mapping[str, Any]) -> tuple[float | None, str]:
    for key in ("moq", "minimum_order_quantity", "min_order_quantity"):
        if key not in raw or raw[key] is None:
            continue
        number = _parsed_number(raw[key])
        return (number, "provided") if number is not None and number > 0 else (None, "invalid")
    return 1.0, "defaulted"


def _parsed_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        match = re.search(r"-?\d+(?:\.\d+)?", value.replace(",", ""))
        if not match:
            return None
        value = match.group(0)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return float(number) if number.is_finite() else None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _first_text(raw: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
