"""Convert sanitized OneBound 1688 responses into daily-selection contracts.

The normalizer is deliberately network-free: it accepts response mappings and
optional provider audit records, retaining only URL-based image references.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse, urlunparse

from .contracts import (
    ApiEvidence,
    DailySelectionCandidate,
    SourceVariantRecord,
    is_sensitive_field,
    redact_sensitive_text,
)


MAX_PRODUCT_IMAGES = 8
MAX_DETAIL_IMAGES = 12

_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)")


def sanitize_raw_payload(value: Any) -> Any:
    """Drop credential-like fields and binary values from an untrusted payload."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    if isinstance(value, Mapping):
        return {
            str(key): sanitize_raw_payload(item)
            for key, item in value.items()
            if not is_sensitive_field(key)
        }
    if isinstance(value, (list, tuple)):
        return tuple(sanitize_raw_payload(item) for item in value)
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def normalize_search_response(
    payload: Mapping[str, Any], *, evidence: ApiEvidence | None = None
) -> tuple[DailySelectionCandidate, ...]:
    """Normalize every usable offer in a 1688 keyword or image-search response."""
    cleaned = sanitize_raw_payload(payload)
    items = _items_from_payload(cleaned)
    audit = evidence or _response_evidence(cleaned, "item_search")
    candidates: list[DailySelectionCandidate] = []
    for item in items:
        candidate = _candidate_from_search_item(
            item,
            {"search_payload": cleaned, "detail_payload": None},
            audit,
        )
        if candidate is not None:
            candidates.append(candidate)
    return tuple(candidates)


def enrich_candidate_with_detail(
    candidate: DailySelectionCandidate,
    payload: Mapping[str, Any],
    *,
    evidence: ApiEvidence | None = None,
) -> DailySelectionCandidate:
    """Return a candidate completed with one item-detail response.

    Search values remain fallbacks and prior API evidence is retained verbatim.
    """
    cleaned = sanitize_raw_payload(payload)
    detail = _detail_from_payload(cleaned)
    product_images = _limited_urls(
        _urls_from(detail, ("item_imgs", "images", "image_urls", "item_images")),
        MAX_PRODUCT_IMAGES,
        prefix=tuple(candidate.source_image_urls) + ((candidate.main_image_url,) if candidate.main_image_url else ()),
    )
    detail_images = _limited_urls(
        _urls_from(detail, ("detail_images", "desc_imgs", "desc_img", "detail_img", "description_images")),
        MAX_DETAIL_IMAGES,
    )
    attributes = _mapping_value(detail, ("props", "item_props", "attributes", "properties"))
    variants = _variants_from(detail)
    package_info = _text_value(detail, ("package_info", "package_info_text", "package", "packing"))
    weight = _text_value(detail, ("weight", "weight_text", "item_weight"))
    freight = _number_value(detail, ("freight", "freight_cny", "post_fee", "shipping_fee"))
    detail_price = _number_value(detail, ("price", "price_cny", "promotion_price"))
    price = detail_price if detail_price is not None else candidate.price_cny
    moq = _integer_value(detail, ("moq", "min_order_quantity", "begin_num", "start_quantity")) or candidate.min_order_quantity
    main_image = _url_value(detail, ("main_image_url", "main_image", "pic_url", "image_url")) or candidate.main_image_url
    evidence_records = candidate.evidence + ((evidence or _response_evidence(cleaned, "item_get")),)
    missing = _missing_fields(
        candidate.missing_capture_fields,
        {
            "main_image_url": main_image,
            "price_cny": price,
            "min_order_quantity": moq,
            "source_image_urls": product_images,
            "source_detail_image_urls": detail_images,
            "source_attributes": attributes,
            "source_variant_records": variants,
            "package_info_text": package_info,
            "weight_text": weight,
            "freight_cny": freight,
        },
    )
    return DailySelectionCandidate(
        candidate_id=candidate.candidate_id,
        offer_id=candidate.offer_id,
        source_platform="1688",
        source_url=candidate.source_url,
        source_title=_text_value(detail, ("title", "name")) or candidate.source_title,
        main_image_url=main_image,
        source_image_urls=product_images,
        source_detail_image_urls=detail_images,
        source_variant_records=variants or candidate.source_variant_records,
        source_attributes=attributes or candidate.source_attributes,
        price_cny=price,
        min_order_quantity=moq,
        selection_score=candidate.selection_score,
        selection_reasons=candidate.selection_reasons,
        risk_tags=candidate.risk_tags,
        status=candidate.status,
        evidence=evidence_records,
        shop_name=candidate.shop_name,
        location=candidate.location,
        sales_text=candidate.sales_text,
        weight_text=weight or candidate.weight_text,
        package_info_text=package_info or candidate.package_info_text,
        freight_cny=freight if freight is not None else candidate.freight_cny,
        captured_fields=_captured_fields(missing),
        missing_capture_fields=missing,
        score_components=candidate.score_components,
        raw_payload={
            "search_payload": candidate.raw_payload.get(
                "search_payload", candidate.raw_payload
            ),
            "detail_payload": cleaned,
        },
    )


# Friendly aliases for pipeline callers that speak in terms of results rather than responses.
normalize_search_results = normalize_search_response
merge_detail_response = enrich_candidate_with_detail


def _candidate_from_search_item(
    item: Mapping[str, Any], raw_payload: Mapping[str, Any], evidence: ApiEvidence
) -> DailySelectionCandidate | None:
    offer_id = _text_value(item, ("num_iid", "offer_id", "item_id", "id"))
    source_url = _canonical_1688_url(_text_value(item, ("detail_url", "url", "item_url", "offer_url")), offer_id)
    if source_url is None:
        return None
    stable_offer_id = offer_id or source_url
    title = _text_value(item, ("title", "name"))
    if title is None:
        return None
    main_image = _url_value(item, ("pic_url", "main_image_url", "image_url", "image", "pic"))
    price = _number_value(item, ("price", "price_cny", "promotion_price"))
    moq = _integer_value(item, ("moq", "min_order_quantity", "begin_num", "start_quantity"))
    fields = {
        "main_image_url": main_image,
        "price_cny": price,
        "min_order_quantity": moq,
        "sales_text": _text_value(item, ("sales", "sales_text", "sold", "volume")),
        "shop_name": _text_value(item, ("shop_name", "shop", "seller_name", "company")),
        "location": _text_value(item, ("location", "area", "province")),
    }
    missing = _missing_fields((), fields)
    return DailySelectionCandidate(
        candidate_id=f"1688:{stable_offer_id}",
        offer_id=stable_offer_id,
        source_platform="1688",
        source_url=source_url,
        source_title=title,
        main_image_url=main_image,
        price_cny=price,
        min_order_quantity=moq,
        evidence=(evidence,),
        shop_name=fields["shop_name"],
        location=fields["location"],
        sales_text=fields["sales_text"],
        captured_fields=_captured_fields(missing),
        missing_capture_fields=missing,
        raw_payload=raw_payload,
    )


def _items_from_payload(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    data = payload.get("data")
    collection = data.get("items") if isinstance(data, Mapping) else None
    if collection is None:
        items = payload.get("items")
        collection = items.get("item") if isinstance(items, Mapping) else items
    if isinstance(collection, Mapping):
        return (collection,)
    if not isinstance(collection, Sequence) or isinstance(collection, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in collection if isinstance(item, Mapping))


def _detail_from_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    data = payload.get("data")
    return data if isinstance(data, Mapping) else payload


def _canonical_1688_url(value: str | None, offer_id: str | None) -> str | None:
    candidate = value.strip() if isinstance(value, str) else ""
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if candidate:
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").casefold()
        is_1688_host = hostname == "1688.com" or hostname.endswith(".1688.com")
        if parsed.scheme in {"http", "https"} and is_1688_host and parsed.path:
            return urlunparse(("https", parsed.netloc.casefold(), parsed.path, "", "", ""))
    if offer_id:
        return f"https://detail.1688.com/{offer_id}.html"
    return None


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _url_value(source: Mapping[str, Any], names: Sequence[str]) -> str | None:
    for name in names:
        value = source.get(name)
        if _is_http_url(value):
            return value.strip()
    return None


def _urls_from(source: Mapping[str, Any], names: Sequence[str]) -> tuple[str, ...]:
    values: list[Any] = []
    for name in names:
        field = source.get(name)
        if isinstance(field, Sequence) and not isinstance(field, (str, bytes, bytearray)):
            values.extend(field)
        elif field is not None:
            values.append(field)
    urls: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            value = _url_value(value, ("url", "image_url", "pic_url", "image"))
        if _is_http_url(value):
            urls.append(value.strip())
    return tuple(urls)


def _limited_urls(urls: Sequence[str], limit: int, *, prefix: Sequence[str] = ()) -> tuple[str, ...]:
    selected: list[str] = []
    for url in tuple(prefix) + tuple(urls):
        if _is_http_url(url) and url not in selected:
            selected.append(url)
        if len(selected) == limit:
            break
    return tuple(selected)


def _text_value(source: Mapping[str, Any], names: Sequence[str]) -> str | None:
    for name in names:
        value = source.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return None


def _number_value(source: Mapping[str, Any], names: Sequence[str]) -> Decimal | None:
    value = _text_value(source, names)
    if value is None:
        return None
    match = _NUMBER.search(value.replace(",", ""))
    if match is None:
        return None
    try:
        value = Decimal(match.group())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _integer_value(source: Mapping[str, Any], names: Sequence[str]) -> int | None:
    number = _number_value(source, names)
    return int(number) if number is not None else None


def _mapping_value(source: Mapping[str, Any], names: Sequence[str]) -> Mapping[str, Any]:
    for name in names:
        value = source.get(name)
        if isinstance(value, Mapping):
            return sanitize_raw_payload(value)
    return {}


def _variants_from(source: Mapping[str, Any]) -> tuple[SourceVariantRecord, ...]:
    records: list[SourceVariantRecord] = []
    entries: Any = None
    for name in ("skus", "sku", "sku_list", "variants"):
        if name in source:
            entries = source[name]
            break
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
        return ()
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        sku_id = _text_value(entry, ("sku_id", "skuId", "id", "spec_id"))
        if sku_id is None:
            continue
        records.append(
            SourceVariantRecord(
                sku_id=sku_id,
                attributes=_mapping_value(entry, ("attributes", "props", "properties", "spec")),
                image_url=_url_value(entry, ("image_url", "pic_url", "image", "sku_image")),
                price_cny=_number_value(entry, ("price", "price_cny", "promotion_price")),
                min_order_quantity=_integer_value(entry, ("moq", "min_order_quantity", "begin_num")),
            )
        )
    return tuple(records)


def _missing_fields(existing: Sequence[str], values: Mapping[str, Any]) -> tuple[str, ...]:
    missing = [field for field in existing if field not in values or values[field] in (None, (), {}, "")]
    for field, value in values.items():
        if value in (None, (), {}, "") and field not in missing:
            missing.append(field)
    return tuple(missing)


def _captured_fields(missing: Sequence[str]) -> tuple[str, ...]:
    all_fields = (
        "main_image_url",
        "price_cny",
        "min_order_quantity",
        "sales_text",
        "shop_name",
        "location",
        "source_image_urls",
        "source_detail_image_urls",
        "source_attributes",
        "source_variant_records",
        "package_info_text",
        "weight_text",
        "freight_cny",
    )
    return tuple(field for field in all_fields if field not in missing)


def _response_evidence(payload: Mapping[str, Any], operation: str) -> ApiEvidence:
    request_id = payload.get("request_id")
    return ApiEvidence(
        provider="onebound-1688",
        operation=operation,
        request_id=request_id if isinstance(request_id, str) else None,
    )
