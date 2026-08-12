"""Construction of bounded, image-only source browser tasks."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from ..quote_normalizer import QuoteItem
from .contracts import SourceBrowserImageSearchPayload, SourceSearchTask


@dataclass(frozen=True)
class _SourceQuote:
    quote_key: str
    skc_id: str
    main_image_url: str


def build_source_browser_image_search_payload(
    quotes: Iterable[QuoteItem | Mapping[str, Any]], *, max_quotes: int = 50
) -> SourceBrowserImageSearchPayload:
    """Group eligible quote evidence into one read-only image search per SKC.

    A persisted quote must carry its saved ``quote_key``.  Direct normalized
    ``QuoteItem`` values have the equivalent stable ``SKC:SKU`` key derived so
    callers can preview a batch before persistence.
    """
    if isinstance(max_quotes, bool) or not isinstance(max_quotes, int) or max_quotes < 1:
        raise ValueError("max_quotes must be a positive integer")

    grouped: OrderedDict[str, list[_SourceQuote]] = OrderedDict()
    skipped: list[str] = []
    accepted = 0
    for quote in quotes:
        candidate, fallback_key = _eligible_source_quote(quote)
        if candidate is None:
            if fallback_key and fallback_key not in skipped:
                skipped.append(fallback_key)
            continue
        if accepted >= max_quotes:
            if candidate.quote_key not in skipped:
                skipped.append(candidate.quote_key)
            continue
        entries = grouped.setdefault(candidate.skc_id, [])
        if all(item.quote_key != candidate.quote_key for item in entries):
            entries.append(candidate)
            accepted += 1

    tasks = tuple(
        SourceSearchTask(
            task_key=skc_id,
            skc_id=skc_id,
            main_image_url=entries[0].main_image_url,
            source_quote_keys=tuple(entry.quote_key for entry in entries),
        )
        for skc_id, entries in grouped.items()
    )
    return SourceBrowserImageSearchPayload(tasks=tasks, skipped_quote_keys=tuple(skipped))


def build_retained_source_browser_image_search_payload(
    quotes: Iterable[Mapping[str, Any]], *, max_quotes: int = 50
) -> SourceBrowserImageSearchPayload:
    """Create one frozen image-search task for each retained official link."""
    if isinstance(max_quotes, bool) or not isinstance(max_quotes, int) or max_quotes < 1:
        raise ValueError("max_quotes must be a positive integer")
    tasks: list[SourceSearchTask] = []
    skipped: list[str] = []
    for quote in quotes:
        quote_key = _text(quote.get("quote_key"))
        if len(tasks) >= max_quotes:
            if quote_key:
                skipped.append(quote_key)
            continue
        task = SourceSearchTask(
            task_key=quote_key,
            quote_key=quote_key,
            skc_id=_text(quote.get("skc_id")),
            sku_id=_text(quote.get("sku_id")),
            spu_or_goods_id=_text(quote.get("spu_or_goods_id")),
            product_title=_text(quote.get("product_title")),
            main_image_url=_text(quote.get("main_image_url")),
            official_link_url=_text(quote.get("official_link_url")),
            selected_price_cny=_text(quote.get("selected_price_cny")),
            source_quote_keys=(quote_key,),
        )
        tasks.append(task)
    return SourceBrowserImageSearchPayload(tasks=tuple(tasks), skipped_quote_keys=tuple(skipped))


def build_batch_sourcing_payload(
    selections: Iterable[Mapping[str, Any]], *, max_tasks: int = 100
) -> SourceBrowserImageSearchPayload:
    """Create one image-search task per retained SKC with its requested candidate cap.

    ``max_candidates`` rides along with each task so the plugin can bound the
    number of similar products it returns, and the preview can honor the same
    cap when rendering each SKC group.
    """
    if isinstance(max_tasks, bool) or not isinstance(max_tasks, int) or max_tasks < 1:
        raise ValueError("max_tasks must be a positive integer")
    tasks: list[SourceSearchTask] = []
    skipped: list[str] = []
    for selection in selections:
        skc_id = _text(selection.get("skc_id"))
        if not skc_id:
            continue
        if len(tasks) >= max_tasks:
            if skc_id not in skipped:
                skipped.append(skc_id)
            continue
        price = _selected_price(selection)
        tasks.append(
            SourceSearchTask(
                task_key=skc_id,
                skc_id=skc_id,
                main_image_url=_text(selection.get("main_image_url")),
                official_link_url=_text(selection.get("official_link_url")),
                selected_price_cny=str(price) if price is not None else "",
                product_title=_text(selection.get("product_title")),
                source_quote_keys=tuple(
                    str(key) for key in (selection.get("quote_keys") or ()) if str(key).strip()
                ),
                quote_key=skc_id,
                max_candidates=_candidate_cap(selection.get("max_candidates")),
            )
        )
    return SourceBrowserImageSearchPayload(tasks=tuple(tasks), skipped_quote_keys=tuple(skipped))


def _selected_price(selection: Mapping[str, Any]) -> Decimal | None:
    """Pick the adjusted (recommended) price first, then the original declared price."""
    for field in ("adjusted_declared_price_cny", "original_declared_price_cny"):
        for sku in selection.get("sku_prices") or ():
            if isinstance(sku, Mapping):
                price = _declared_price(sku.get(field))
                if price is not None:
                    return price
    return None


def _candidate_cap(value: object) -> int:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 10
    return 10 if parsed < 1 or parsed > 100 else parsed


def _eligible_source_quote(
    quote: QuoteItem | Mapping[str, Any],
) -> tuple[_SourceQuote | None, str]:
    if isinstance(quote, QuoteItem):
        skc_id, sku_id = quote.skc_id.strip(), quote.sku_id.strip()
        quote_key = f"{skc_id}:{sku_id}" if skc_id and sku_id else ""
        image = quote.main_image_url.strip()
        declared_price = _declared_price(
            quote.adjusted_declared_price_cny,
            quote.new_declared_price_cny,
            quote.original_declared_price_cny,
        )
    elif isinstance(quote, Mapping):
        skc_id = _text(quote.get("skc_id") or quote.get("skcId"))
        quote_key = _text(quote.get("quote_key"))
        image = _text(quote.get("main_image_url") or quote.get("mainImageUrl"))
        declared_price = _declared_price(
            quote.get("adjusted_declared_price_cny"),
            quote.get("new_declared_price_cny"),
            quote.get("original_declared_price_cny"),
        )
    else:
        return None, ""
    fallback_key = quote_key or skc_id
    if not (quote_key and skc_id and image and declared_price is not None):
        return None, fallback_key
    return _SourceQuote(quote_key=quote_key, skc_id=skc_id, main_image_url=image), fallback_key


def _declared_price(*values: object) -> Decimal | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            price = value if isinstance(value, Decimal) else Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            continue
        if price.is_finite() and price > 0:
            return price
    return None


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
