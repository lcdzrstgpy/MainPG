"""Convert sanitized OneBound 1688 responses into daily-selection contracts.

The normalizer is deliberately network-free: it accepts response mappings and
optional provider audit records, retaining only URL-based image references.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlparse, urlunparse

from .contracts import (
    ApiEvidence,
    DailySelectionCandidate,
    SourceTierPrice,
    SourceVariantRecord,
    is_sensitive_field,
    redact_sensitive_text,
)


MAX_PRODUCT_IMAGES = 8
MAX_DETAIL_IMAGES = 12

_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)")
_DESCRIPTION_IMAGE = re.compile(r"\bsrc\s*=\s*['\"]((?:https?:)?//[^'\"\s>]+)", re.IGNORECASE)
_RAW_DESCRIPTION_FIELDS = frozenset({"desc", "description", "desc_html", "description_html"})
_WEIGHT_KEY_RE = re.compile(r"(重量|毛重|净重|单重|克重|weight|gross|net)", re.IGNORECASE)
_PHYSICAL_KEY_RE = re.compile(r"(尺寸|规格|大小|dimension|size)", re.IGNORECASE)
_LABELED_WEIGHT_RE = re.compile(
    r"(?:重量|毛重|净重|单重|克重|item\s*weight|gross\s*weight|net\s*weight)"
    r"[^0-9]{0,16}?(\d+(?:\.\d+)?)\s*(g|克|kg|千克|公斤)",
    re.IGNORECASE,
)
_LABELED_SIZE_RE = re.compile(
    r"(?:尺寸|规格|大小|product\s*size|dimension)[^0-9]{0,16}?"
    r"(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*(cm|mm|厘米|毫米)?",
    re.IGNORECASE,
)


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


def _platform_from_evidence(evidence: ApiEvidence | None) -> str:
    """Derive the collection platform from a provider evidence record."""
    if evidence is not None:
        provider = getattr(evidence, "provider", "") or ""
        if provider == "onebound-taobao" or provider.endswith("-taobao"):
            return "taobao"
    return "1688"


def _provider_name(platform: str) -> str:
    return f"onebound-{platform}"


def normalize_search_response(
    payload: Mapping[str, Any], *, evidence: ApiEvidence | None = None
) -> tuple[DailySelectionCandidate, ...]:
    """Normalize every usable offer in a keyword or image-search response."""
    cleaned = sanitize_raw_payload(payload)
    items = _items_from_payload(cleaned)
    platform = _platform_from_evidence(evidence)
    audit = evidence or _response_evidence(cleaned, "item_search", platform)
    candidates: list[DailySelectionCandidate] = []
    for item in items:
        candidate = _candidate_from_search_item(
            item,
            {"search_payload": cleaned, "detail_payload": None},
            audit,
            platform,
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
    platform = candidate.source_platform
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
    category_path = _text_value(
        detail,
        ("cat_name", "category_name", "category_path", "categoryPath", "category", "root_cat_name", "parent_cat_name"),
    ) or candidate.category_path
    category_id = _text_value(
        detail, ("cat_id", "category_id", "leaf_category_id", "cid")
    ) or candidate.category_id
    variants = _variants_from(detail)
    mined_weight, mined_size = _physical_evidence(detail, attributes)
    package_info = mined_size or _text_value(detail, ("package_info", "package_info_text", "package", "packing"))
    weight = mined_weight
    freight = _number_value(detail, ("freight", "freight_cny", "post_fee", "shipping_fee"))
    detail_price = _number_value(detail, ("price", "price_cny", "promotion_price"))
    price = detail_price if detail_price is not None else candidate.price_cny
    moq = _moq_or_none(
        detail,
        ("moq", "min_order_quantity", "begin_num", "start_quantity", "min_num", "begin_amount", "beginAmount"),
    ) or candidate.min_order_quantity
    main_image = _url_value(detail, ("main_image_url", "main_image", "pic_url", "image_url")) or candidate.main_image_url
    shop_name = _shop_name_from_detail(detail) or candidate.shop_name
    location = _text_value(detail, ("location", "area", "province")) or candidate.location
    evidence_records = candidate.evidence + ((evidence or _response_evidence(cleaned, "item_get", platform)),)
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
            "category_path": category_path,
            "category_id": category_id,
            "package_info_text": package_info,
            "weight_text": weight,
            "freight_cny": freight,
            "shop_name": shop_name,
            "location": location,
        },
    )
    return DailySelectionCandidate(
        candidate_id=candidate.candidate_id,
        offer_id=candidate.offer_id,
        source_platform=platform,
        source_url=candidate.source_url,
        source_title=_text_value(detail, ("title", "name")) or candidate.source_title,
        query_keyword=candidate.query_keyword,
        selection_result_label=candidate.selection_result_label,
        listed_at=_text_value(detail, ("listed_at", "listing_time", "online_time", "start_time")) or candidate.listed_at,
        main_image_url=main_image,
        source_image_urls=product_images,
        source_detail_image_urls=detail_images,
        source_variant_records=variants or candidate.source_variant_records,
        source_attributes=attributes or candidate.source_attributes,
        category_path=category_path,
        category_id=category_id,
        price_cny=price,
        min_order_quantity=moq,
        selection_score=candidate.selection_score,
        selection_reasons=candidate.selection_reasons,
        risk_tags=candidate.risk_tags,
        status=candidate.status,
        evidence=evidence_records,
        shop_name=shop_name,
        location=location,
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


def normalize_detail_response(
    payload: Mapping[str, Any],
    *,
    evidence: ApiEvidence | None = None,
    platform: str | None = None,
) -> DailySelectionCandidate:
    """Normalize one complete OneBound item response into the canonical candidate contract.

    ``platform`` wins over evidence when both are supplied; evidence remains the
    default so existing callers keep working unchanged.
    """
    cleaned = sanitize_raw_payload(payload)
    detail = _detail_from_payload(cleaned)
    resolved_platform = platform or _platform_from_evidence(evidence)
    offer_id = _text_value(detail, ("num_iid", "offer_id", "item_id", "id"))
    if offer_id is None:
        raise ValueError("item detail did not include an offer ID")
    source_url = _canonical_platform_url(
        resolved_platform, _text_value(detail, ("detail_url", "url", "item_url", "offer_url")), offer_id
    )
    if source_url is None:
        raise ValueError(f"item detail did not include a valid {resolved_platform} URL")
    title = _text_value(detail, ("title", "name"))
    if title is None:
        raise ValueError("item detail did not include a title")
    main_image = _url_value(detail, ("main_image_url", "main_image", "pic_url", "image_url", "image", "pic"))
    product_images = _limited_urls(
        _urls_from(detail, ("item_imgs", "images", "image_urls", "item_images")),
        MAX_PRODUCT_IMAGES,
        prefix=(main_image,) if main_image else (),
    )
    detail_images = _limited_urls(
        _urls_from(detail, ("detail_images", "desc_imgs", "desc_img", "detail_img", "description_images"))
        + _description_image_urls(detail),
        MAX_DETAIL_IMAGES,
    )
    attributes = _mapping_value(detail, ("props", "item_props", "attributes", "properties"))
    variants = _variants_from(detail)
    mined_weight, mined_size = _physical_evidence(detail, attributes)
    price = _number_value(detail, ("price", "price_cny", "promotion_price"))
    moq = _moq_or_none(detail, ("moq", "min_order_quantity", "begin_num", "start_quantity", "min_num", "begin_amount", "beginAmount"))
    stock = _integer_value(detail, ("quantity", "stock", "inventory", "num"))
    sales = _text_value(detail, ("sales", "sales_text", "sold", "volume"))
    missing = _missing_fields(
        (),
        {
            "main_image_url": main_image,
            "price_cny": price,
            "min_order_quantity": moq,
            "source_image_urls": product_images,
            "source_detail_image_urls": detail_images,
            "source_attributes": attributes,
            "source_variant_records": variants,
        },
    )
    return DailySelectionCandidate(
        candidate_id=f"{resolved_platform}:{offer_id}",
        offer_id=offer_id,
        source_platform=resolved_platform,
        source_url=source_url,
        source_title=title,
        listed_at=_text_value(detail, ("listed_at", "listing_time", "online_time", "start_time")),
        main_image_url=main_image,
        source_image_urls=product_images,
        source_detail_image_urls=detail_images,
        source_variant_records=variants,
        source_attributes=attributes,
        category_path=_text_value(detail, ("cat_name", "category_name", "category_path", "categoryPath", "category")),
        category_id=_text_value(detail, ("cat_id", "category_id", "leaf_category_id", "cid")),
        price_cny=price,
        min_order_quantity=moq,
        evidence=(evidence or _response_evidence(cleaned, "item_get", platform),),
        shop_name=_shop_name_from_detail(detail),
        location=_text_value(detail, ("location", "area", "province")),
        sales_text=sales,
        weight_text=mined_weight,
        package_info_text=mined_size or _text_value(detail, ("package_info", "package_info_text", "package", "packing")),
        freight_cny=_number_value(detail, ("freight", "freight_cny", "post_fee", "shipping_fee")),
        original_price_cny=_number_value(detail, ("original_price", "original_price_cny", "market_price")),
        stock_quantity=stock,
        unit=_text_value(detail, ("unit", "unit_name")),
        brand=_text_value(detail, ("brand", "brand_name")),
        video_url=_url_value(detail, ("video", "video_url", "videoUrl")),
        tiered_prices=_tiered_prices(detail),
        captured_fields=_captured_fields(missing),
        missing_capture_fields=missing,
        raw_payload=_without_raw_description_html(cleaned),
    )


def _candidate_from_search_item(
    item: Mapping[str, Any], raw_payload: Mapping[str, Any], evidence: ApiEvidence, platform: str
) -> DailySelectionCandidate | None:
    offer_id = _text_value(item, ("num_iid", "offer_id", "item_id", "id"))
    source_url = _canonical_platform_url(platform, _text_value(item, ("detail_url", "url", "item_url", "offer_url")), offer_id)
    if source_url is None:
        return None
    stable_offer_id = offer_id or source_url
    title = _text_value(item, ("title", "name"))
    if title is None:
        return None
    main_image = _url_value(item, ("pic_url", "main_image_url", "image_url", "image", "pic"))
    price = _number_value(item, ("price", "price_cny", "promotion_price"))
    moq = _moq_or_none(item, ("moq", "min_order_quantity", "begin_num", "start_quantity", "min_num"))
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
        candidate_id=f"{platform}:{stable_offer_id}",
        offer_id=stable_offer_id,
        source_platform=platform,
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
    if isinstance(data, Mapping):
        return data
    # OneBound 1688 item_get wraps the product under ``item``.
    item = payload.get("item")
    return item if isinstance(item, Mapping) else payload


def _shop_name_from_detail(detail: Mapping[str, Any]) -> str | None:
    seller_info = detail.get("seller_info")
    if isinstance(seller_info, Mapping):
        name = _text_value(seller_info, ("shop_name", "nick", "title", "seller_name"))
        if name:
            return name
    return _text_value(
        detail,
        ("nick", "shop_name", "seller_name", "company", "companyName", "company_name"),
    )


def _canonical_platform_url(platform: str, value: str | None, offer_id: str | None) -> str | None:
    candidate = value.strip() if isinstance(value, str) else ""
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if candidate:
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme in {"http", "https"} and _platform_host_matches(platform, hostname) and parsed.path:
            if platform == "taobao":
                # Taobao item IDs live in the query string (?id=...). Keep only
                # that parameter and drop the tracking noise (skuId, scm, ...).
                query_id = _query_digit(parsed.query, ("id", "item_id", "itemId", "num_iid"))
                query = f"id={query_id}" if query_id else ""
                return urlunparse(("https", parsed.netloc.casefold(), parsed.path, "", query, ""))
            return urlunparse(("https", parsed.netloc.casefold(), parsed.path, "", "", ""))
    if offer_id:
        if platform == "taobao":
            return f"https://item.taobao.com/item.htm?id={offer_id}"
        return f"https://detail.1688.com/{offer_id}.html"
    return None


def _platform_host_matches(platform: str, hostname: str) -> bool:
    if platform == "taobao":
        return (
            hostname == "taobao.com"
            or hostname.endswith(".taobao.com")
            or hostname == "tmall.com"
            or hostname.endswith(".tmall.com")
        )
    return hostname == "1688.com" or hostname.endswith(".1688.com")


def _query_digit(query: str, names: Sequence[str]) -> str | None:
    for key, value in parse_qsl(query):
        if key in names and value.isdigit():
            return value
    return None


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _normalized_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    return candidate if _is_http_url(candidate) else None


def _url_value(source: Mapping[str, Any], names: Sequence[str]) -> str | None:
    for name in names:
        value = source.get(name)
        normalized = _normalized_http_url(value)
        if normalized:
            return normalized
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
        normalized = _normalized_http_url(value)
        if normalized:
            urls.append(normalized)
    return tuple(urls)


def _limited_urls(urls: Sequence[str], limit: int, *, prefix: Sequence[str] = ()) -> tuple[str, ...]:
    selected: list[str] = []
    for url in tuple(prefix) + tuple(urls):
        normalized = _normalized_http_url(url)
        if normalized and normalized not in selected:
            selected.append(normalized)
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


def _moq_or_none(source: Mapping[str, Any], names: Sequence[str]) -> int | None:
    """MOQ 必须是正整数；0/负数（淘宝常见“无起批量”= 0）归一化为 None。

    ``SourceVariantRecord.min_order_quantity`` 与候选级 MOQ 都校验“正数”，
    上游返回 0 时直接透传会让整个候选契约校验失败，导致补全任务报
    “min_order_quantity must be a positive integer”而失败。
    """
    moq = _integer_value(source, names)
    return moq if moq is not None and moq >= 1 else None


def _mapping_value(source: Mapping[str, Any], names: Sequence[str]) -> Mapping[str, Any]:
    for name in names:
        value = source.get(name)
        if isinstance(value, Mapping):
            return sanitize_raw_payload(value)
        if isinstance(value, (list, tuple)):
            # OneBound 1688 props are ``[{"name": ..., "value": ...}, ...]``.
            converted: dict[str, Any] = {}
            for entry in value:
                if not isinstance(entry, Mapping):
                    continue
                key = entry.get("name")
                if isinstance(key, str) and key.strip():
                    converted[key.strip()] = entry.get("value")
            if converted:
                return converted
    return {}


def _description_plain_text(detail: Mapping[str, Any]) -> str:
    """Strip HTML from the raw description fields, returning searchable plain text."""
    html = _text_value(detail, ("desc", "description", "detail", "item_desc"))
    if not html:
        return ""
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _physical_evidence(
    detail: Mapping[str, Any], attributes: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    """Mine weight and size evidence from item_weight, labelled props and description text.

    OneBound 1688 responses frequently leave ``item_weight`` empty while the seller
    states the weight/size inside the description or a labelled property, so the
    extraction falls back to those sources before downstream estimation runs.
    """
    weight = _text_value(
        detail,
        (
            "weight", "weight_text", "item_weight", "gross_weight", "net_weight",
            "package_weight", "single_weight", "unit_weight",
        ),
    )
    size_parts: list[str] = []
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            key_text = str(key or "").strip()
            value_text = str(value or "").strip()
            if not value_text:
                continue
            if not weight and _WEIGHT_KEY_RE.search(key_text):
                weight = value_text
            elif _PHYSICAL_KEY_RE.search(key_text):
                size_parts.append(f"{key}: {value_text}")
    description = _description_plain_text(detail)
    if description:
        if not weight:
            match = _LABELED_WEIGHT_RE.search(description)
            if match:
                unit = (match.group(2) or "g").casefold()
                weight = (
                    f"{match.group(1)} kg"
                    if unit in {"kg", "千克", "公斤"}
                    else f"{match.group(1)} g"
                )
        if not size_parts:
            match = _LABELED_SIZE_RE.search(description)
            if match:
                unit = (match.group(4) or "cm").casefold()
                scale = 0.1 if unit in {"mm", "毫米"} else 1.0
                size_parts.append(
                    "尺寸: "
                    f"{float(match.group(1)) * scale:g}*{float(match.group(2)) * scale:g}"
                    f"*{float(match.group(3)) * scale:g}cm"
                )
    return weight or None, ("; ".join(size_parts) if size_parts else None)


def _variants_from(source: Mapping[str, Any]) -> tuple[SourceVariantRecord, ...]:
    records: list[SourceVariantRecord] = []
    entries: Any = None
    for name in ("skus", "sku", "sku_list", "variants"):
        if name in source:
            entries = source[name]
            break
    if isinstance(entries, Mapping):
        # OneBound 1688 item_get wraps SKUs as ``{"sku": [...]}``.
        for key in ("sku", "list", "items", "variant"):
            nested = entries.get(key)
            if isinstance(nested, (list, tuple)):
                entries = nested
                break
        else:
            return ()
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
        return ()
    property_images = _property_image_index(source)
    color_property_ids = _color_property_ids(source)
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        sku_id = _text_value(entry, ("sku_id", "skuId", "id", "spec_id"))
        if sku_id is None:
            continue
        spec_text = _text_value(entry, ("properties_name", "property_alias", "spec_text", "sku_attr"))
        attributes = _mapping_value(entry, ("attributes", "props", "properties", "spec"))
        if not attributes:
            # OneBound 1688 item_get returns per-SKU specs as string fields,
            # e.g. ``properties_name: "0:0:颜色:粉色"`` or ``"颜色:粉色;尺寸:L"``.
            attributes = _string_spec_attributes(spec_text)
        # SKU 行本身通常没有图片字段；1688 把规格图按属性值挂在 ``props_img``
        # （``"pid:vid" -> url``）上，只能靠颜色属性值回查。
        image_url = _url_value(entry, ("image_url", "pic_url", "image", "sku_image")) or _color_variant_image(
            entry, property_images, color_property_ids
        )
        records.append(
            SourceVariantRecord(
                sku_id=sku_id,
                attributes=attributes or {},
                spec_text=spec_text,
                image_url=image_url,
                price_cny=_number_value(entry, ("price", "price_cny", "promotion_price")),
                min_order_quantity=_moq_or_none(entry, ("moq", "min_order_quantity", "begin_num")),
                quantity=_integer_value(entry, ("quantity", "stock", "inventory", "num")),
                sales=_integer_value(entry, ("sales", "sold", "volume")),
            )
        )
    return tuple(records)


_COLOR_PROPERTY_NAMES = ("颜色", "色彩", "颜色分类")


def _is_color_property_name(value: Any) -> bool:
    """规格名是否代表颜色维度（尺寸、纯度等其它规格一律不参与配图）。"""
    if not isinstance(value, str):
        return False
    name = value.strip().casefold()
    if not name:
        return False
    if any(token in name for token in _COLOR_PROPERTY_NAMES):
        return True
    return name in {"color", "colour"}


def _property_image_index(source: Mapping[str, Any]) -> dict[str, str]:
    """把 ``pid:vid`` 映射到对应的属性值图片 URL。

    OneBound 1688 ``item_get`` 提供两种形态：``props_img``（``{"0:0": url}``）
    或 ``prop_imgs.prop_img``（``[{"properties": "0:0", "url": url}]``）。
    """
    index: dict[str, str] = {}
    direct = source.get("props_img")
    if isinstance(direct, Mapping):
        for key, value in direct.items():
            url = _normalized_http_url(value)
            if url:
                index[str(key)] = url
    wrapped = source.get("prop_imgs")
    if isinstance(wrapped, Mapping):
        entries = wrapped.get("prop_img")
        if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes, bytearray)):
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                key = entry.get("properties")
                url = _normalized_http_url(entry.get("url")) or _normalized_http_url(entry.get("image_url"))
                if isinstance(key, str) and key and url:
                    index.setdefault(key, url)
    return index


def _color_property_ids(source: Mapping[str, Any]) -> frozenset[str]:
    """从 ``props_list``/``props_name`` 里找出颜色维度的属性 ID。"""
    ids: set[str] = set()
    props_list = source.get("props_list")
    if isinstance(props_list, Mapping):
        for key, value in props_list.items():
            if _is_color_property_name(str(value).split(":", 1)[0]):
                ids.add(str(key).split(":", 1)[0])
    if ids:
        return frozenset(ids)
    for field in ("props_name", "property_alias"):
        text = source.get(field)
        if not isinstance(text, str):
            continue
        for segment in text.split(";"):
            parts = segment.split(":")
            if len(parts) >= 3 and parts[0].strip().isdigit() and _is_color_property_name(parts[2]):
                ids.add(parts[0].strip())
    return frozenset(ids)


def _color_variant_image(
    entry: Mapping[str, Any],
    property_images: Mapping[str, str],
    color_property_ids: frozenset[str],
) -> str | None:
    """按 SKU 的颜色属性值取规格图，尺寸等其它规格不参与配图。"""
    if not property_images:
        return None
    for pair in _color_property_pairs(entry, color_property_ids):
        url = property_images.get(pair)
        if url:
            return url
    return None


def _color_property_pairs(entry: Mapping[str, Any], color_property_ids: frozenset[str]) -> tuple[str, ...]:
    """列出该 SKU 中代表颜色的 ``pid:vid`` 组合。"""
    pairs: list[str] = []
    spec_text = entry.get("properties_name")
    if isinstance(spec_text, str):
        # ``properties_name`` 形如 ``pid:vid:名称:值``，即使缺少 ``props_list`` 也能定位颜色。
        for segment in spec_text.split(";"):
            parts = segment.split(":")
            if len(parts) >= 3 and parts[0].strip().isdigit() and _is_color_property_name(parts[2]):
                pair = f"{parts[0].strip()}:{parts[1].strip()}"
                if pair not in pairs:
                    pairs.append(pair)
    properties = entry.get("properties")
    if isinstance(properties, str):
        # ``properties`` 形如 ``0:0;1:0``，配合 ``props_list`` 推导出的颜色属性 ID 使用。
        for piece in properties.split(";"):
            pair = piece.strip()
            if pair and pair.split(":", 1)[0] in color_property_ids and pair not in pairs:
                pairs.append(pair)
    return tuple(pairs)


def _string_spec_attributes(value: Any) -> dict[str, str]:
    """Parse OneBound 1688 string-form SKU specs into a ``{name: value}`` map.

    ``item_get`` returns per-SKU specs as strings such as ``"0:0:颜色:粉色"``
    (``pid:vid:name:value``) or ``"颜色:粉色;尺寸:L"``.  The leading numeric
    ``pid:vid:`` prefix is dropped when present.
    """
    if not isinstance(value, str):
        return {}
    result: dict[str, str] = {}
    for segment in re.split(r"[;；]", value):
        segment = segment.strip()
        if not segment:
            continue
        parts = [part.strip() for part in segment.split(":") if part.strip()]
        # ``pid:vid:name:value`` 前缀；属性值本身可能含冒号（如"颜色:按需定制"）。
        # 去掉 pid:vid 后 name 取首段，value 拼接剩余段，避免属性名错位。
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            parts = parts[2:]
        if len(parts) >= 2:
            result[parts[0]] = ":".join(parts[1:])
        elif len(parts) == 1:
            # No attribute name exposed; keep the value under a generic key.
            result.setdefault("规格", parts[0])
    return result


def _description_image_urls(detail: Mapping[str, Any]) -> tuple[str, ...]:
    urls: list[str] = []
    for name in _RAW_DESCRIPTION_FIELDS:
        value = detail.get(name)
        if not isinstance(value, str):
            continue
        for match in _DESCRIPTION_IMAGE.finditer(value):
            normalized = _normalized_http_url(match.group(1))
            if normalized and normalized not in urls:
                urls.append(normalized)
    return tuple(urls)


def _without_raw_description_html(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_raw_description_html(item)
            for key, item in value.items()
            if str(key).casefold() not in _RAW_DESCRIPTION_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return tuple(_without_raw_description_html(item) for item in value)
    return value


def _tiered_prices(source: Mapping[str, Any]) -> tuple[SourceTierPrice, ...]:
    entries: Any = None
    for name in ("price_range", "price_ranges", "tiered_prices", "price_ladder"):
        if name in source:
            entries = source[name]
            break
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes, bytearray)):
        return ()
    tiers: list[SourceTierPrice] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        moq = _integer_value(entry, ("begin_amount", "min_order_quantity", "moq", "start_quantity"))
        price = _number_value(entry, ("price", "price_cny", "promotion_price"))
        if moq is None or moq < 1 or price is None:
            continue
        tiers.append(SourceTierPrice(min_order_quantity=moq, price_cny=price))
    return tuple(tiers)


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


def _response_evidence(payload: Mapping[str, Any], operation: str, platform: str = "1688") -> ApiEvidence:
    request_id = payload.get("request_id")
    return ApiEvidence(
        provider=_provider_name(platform),
        operation=operation,
        request_id=request_id if isinstance(request_id, str) else None,
    )
