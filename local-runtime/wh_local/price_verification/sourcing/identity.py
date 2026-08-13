"""Local, explainable product and SKU evidence checks for source offers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..quote_normalizer import QuoteItem


_GENERIC_TOKENS = frozenset(
    {
        "同款", "厂家", "批发", "现货", "新品", "热卖", "爆款",
        "hot", "sale", "new", "women", "woman", "men", "man",
        "girls", "girl", "boys", "boy", "adult", "kids", "kid",
        "summer", "winter", "spring", "autumn", "fashion", "style",
        "large", "small", "mini", "pack", "set",
    }
)
_COLOUR_TOKENS = frozenset({"红", "蓝", "黑", "白", "黄", "绿", "紫", "粉", "橙", "灰", "棕", "red", "blue", "black", "white", "yellow", "green", "purple", "pink", "orange", "grey", "gray", "brown"})
_ENGLISH_CATEGORY_TERMS = (
    (("cooling", "cool"), ("凉", "冰", "冷", "降温", "cooling", "cool")),
    (("mat",), ("垫", "席", "mat")),
    (("pad",), ("垫", "片", "pad")),
    (("bed",), ("床", "窝", "垫", "bed")),
    (("toy",), ("玩具", "球", "磨牙", "逗猫", "toy")),
    (("medicine", "drug", "treatment"), ("药", "剂", "滴", "驱虫", "治疗", "medicine", "drug")),
    (("bowl", "feeder"), ("碗", "盆", "食器", "喂食", "bowl", "feeder")),
    (("collar",), ("项圈", "颈圈", "collar")),
    (("leash",), ("牵引", "拉带", "leash")),
    (("brush", "comb"), ("刷", "梳", "brush", "comb")),
    (("blanket",), ("毯", "blanket")),
    (("pillow", "cushion"), ("枕", "靠垫", "坐垫", "pillow", "cushion")),
    (("cover",), ("罩", "套", "cover")),
    (("basket",), ("篮", "筐", "basket")),
    (("box",), ("箱", "盒", "柜", "box")),
    (("bag",), ("包", "袋", "bag")),
    (("rack", "shelf", "stand"), ("架", "展示", "rack", "shelf", "stand")),
)
_TOY_TERMS = ("玩具", "摆件", "公仔", "toy", "doll", "figurine", "play")

_CONFLICTING_CATEGORY_GROUPS = (
    ("药", "剂", "滴", "驱虫", "喷剂", "medicine", "drug"),
    ("玩具", "磨牙", "逗猫", "发声", "漏食", "toy"),
    ("零食", "冻干", "肉干", "猫条", "狗粮", "猫粮", "food", "snack"),
    ("碗", "食盆", "水盆", "喂食器", "bowl", "feeder"),
    ("衣", "服装", "背心", "雨衣", "costume", "shirt", "clothes"),
)


def evaluate_product_evidence(
    quote: QuoteItem | Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[str, tuple[str, ...]]:
    """Return compatible, conflict, or missing with its local evidence."""
    quote_title = _quote_text(quote, "product_title")
    source_title = _text(candidate.get("source_title") or candidate.get("title") or candidate.get("name"))
    quote_image = _quote_text(quote, "main_image_url")
    source_image = _text(candidate.get("main_image_url") or candidate.get("image") or candidate.get("image_url"))
    explicit = _token(candidate.get("product_match") or candidate.get("identity_status") or candidate.get("product_evidence_status"))
    if explicit in {"false", "different_product", "conflict", "incompatible", "no_match"}:
        return "conflict", ("explicit_product_conflict",)
    if explicit in {"true", "same_product", "compatible", "matched"}:
        return "compatible", ("explicit_product_match",)
    if quote_image and source_image and quote_image == source_image:
        return "compatible", ("matching_product_image",)
    if quote_title and source_title:
        quote_terms = _product_terms(quote_title)
        source_terms = _product_terms(source_title)
        # A single shared title word is category-level evidence at best
        # ("women", "summer", even "dress"), not proof that two offers are
        # the same product.  Require two meaningful shared terms unless the
        # full compact title containment check below succeeds.
        if len(quote_terms & source_terms) >= 2:
            return "compatible", ("overlapping_product_title",)
        if _compact(quote_title) in _compact(source_title) or _compact(source_title) in _compact(quote_title):
            return "compatible", ("containing_product_title",)
        if _cross_language_category_mismatch(quote_title, source_title):
            return "conflict", ("product_category_mismatch",)
        if not _has_shared_language(quote_title, source_title):
            # Chinese 1688 titles cannot be compared with English Temu titles;
            # the absence of overlap is not proof of a different product.
            return "missing", ("cross_language_title_evidence",)
        return "conflict", ("product_title_mismatch",)
    return "missing", ("missing_compatible_product_evidence",)


def _cross_language_category_mismatch(quote_title: str, source_title: str) -> bool:
    """Reject only clear cross-language category conflicts.

    Temu titles are often English while 1688 titles are Chinese.  We keep
    ambiguous results reviewable, but when the English product class has a
    direct Chinese equivalent in the candidate title, require every stated
    class (for example, ``cooling`` + ``mat``) to be present.  This runs for
    every image hit, not just whichever one OneBound returned first.
    """
    if _has_shared_language(quote_title, source_title):
        return False
    quote = _compact(quote_title)
    source = _compact(source_title)
    # Temu 是唯一的类目来源：它明确为 toy/doll 时，1688 候选也必须
    # 明确是玩具。不能反过来仅凭 1688 标题的“玩具”二字排除候选。
    if any(term in quote for term in _TOY_TERMS) and not any(term in source for term in _TOY_TERMS):
        return True
    required = [aliases for triggers, aliases in _ENGLISH_CATEGORY_TERMS if any(trigger in quote for trigger in triggers)]
    if required and any(not any(alias in source for alias in aliases) for aliases in required):
        return True
    # Strongly unrelated merchandise nouns are a hard conflict when none of
    # them occur in the Temu title.  This catches noisy image hits such as pet
    # medicine, chew toys or food for a cooling mat before any link is exposed.
    return any(
        any(alias in source for alias in group)
        and not any(alias in quote for alias in group)
        for group in _CONFLICTING_CATEGORY_GROUPS
    )


def evaluate_sku_evidence(
    quote: QuoteItem | Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[str, tuple[str, ...]]:
    """Return compatible, conflict, or missing for the target SKU attributes."""
    target = _first_quote_attribute(quote)
    source_values = _candidate_sku_values(candidate)
    if not target:
        return "missing", ("missing_quote_sku_attributes",)
    if not source_values:
        return "missing", ("missing_source_sku_attributes",)
    target_compact = _compact(target)
    source_compact = [_compact(value) for value in source_values]
    if any(target_compact in value or value in target_compact for value in source_compact if value):
        return "compatible", ("matching_sku_attributes",)
    target_colours = _colours(target)
    source_colours = set().union(*(_colours(value) for value in source_values))
    if target_colours and source_colours and not (target_colours & source_colours):
        return "conflict", ("sku_variant_conflict",)
    return "conflict", ("sku_attributes_do_not_match",)


def _first_quote_attribute(quote: QuoteItem | Mapping[str, Any]) -> str:
    for name in ("sku_attribute_text", "sku_attribute_set", "product_attribute_summary", "skc_attribute_text"):
        value = _quote_text(quote, name)
        if value:
            return value
    return ""


def _candidate_sku_values(candidate: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("sku_attributes", "sku_attribute_text", "source_sku_attributes", "selected_variant", "source_selected_spec_text"):
        value = candidate.get(key)
        if isinstance(value, Mapping):
            values.extend(_text(value.get(name)) for name in ("name", "text", "label", "value"))
        elif isinstance(value, (list, tuple)):
            for entry in value:
                if isinstance(entry, Mapping):
                    values.extend(_text(entry.get(name)) for name in ("name", "text", "label", "value"))
                else:
                    values.append(_text(entry))
        else:
            values.append(_text(value))
    variants = candidate.get("variants") or candidate.get("source_variant_records") or candidate.get("variant_records")
    if isinstance(variants, (list, tuple)):
        for variant in variants:
            if isinstance(variant, Mapping):
                values.extend(_text(variant.get(name)) for name in ("name", "text", "label", "value", "title"))
            else:
                values.append(_text(variant))
    title = _text(candidate.get("source_title") or candidate.get("title"))
    if title and _colours(title):
        values.append(title)
    return tuple(value for value in values if value)


def _quote_text(quote: QuoteItem | Mapping[str, Any], name: str) -> str:
    return _text(getattr(quote, name, "") if isinstance(quote, QuoteItem) else quote.get(name))


def _product_terms(value: str) -> set[str]:
    return {
        token for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}", value.casefold())
        if token not in _GENERIC_TOKENS
    }


def _has_shared_language(a: str, b: str) -> bool:
    """Whether two titles share a dominant language before treating a title mismatch as conflict.

    A predominantly Chinese 1688 title and a predominantly English Temu title
    cannot be compared textually, so their mismatch proves nothing about
    product identity.  A stray brand token like "ins" in a Chinese title does
    not make it an English title.
    """
    return _dominant_language(a) == _dominant_language(b)


def _dominant_language(value: str) -> str:
    cjk = sum(1 for ch in value if "\u4e00" <= ch <= "\u9fff")
    ascii_alpha = sum(1 for ch in value if ch.isascii() and ch.isalpha())
    if cjk > ascii_alpha:
        return "cjk"
    if ascii_alpha > 0:
        return "ascii"
    return "other"


def _colours(value: str) -> set[str]:
    compact = _compact(value).casefold()
    return {colour for colour in _COLOUR_TOKENS if colour in compact}


def _compact(value: str) -> str:
    return re.sub(r"[\s\-_/|,，;；:：()（）]+", "", value).casefold()


def _token(value: object) -> str:
    return _compact(_text(value)).replace(" ", "_")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
