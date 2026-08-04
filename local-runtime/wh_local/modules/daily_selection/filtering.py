"""Local, evidence-preserving hard filters for daily-selection candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
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
    seen: set[tuple[str, str]] = set()

    for candidate in candidates:
        risks = _risk_tags(candidate)
        risk_reasons = tuple(f"risk_{tag}" for tag in risks) if criteria.exclude_risks else ()
        duplicate_reason = _duplicate_reason(candidate, seen)
        if duplicate_reason is not None:
            filtered.append(_filtered(candidate, (duplicate_reason, *risk_reasons), risks))
            continue

        reasons = _hard_filter_reasons(candidate, criteria)
        if risk_reasons:
            reasons = (*reasons, *risk_reasons)
        if reasons:
            filtered.append(_filtered(candidate, reasons, risks))
            continue
        accepted.append(_with_risks(candidate, risks))

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


def _duplicate_reason(candidate: DailySelectionCandidate, seen: set[tuple[str, str]]) -> str | None:
    key, reason = _candidate_identity(candidate)
    if key in seen:
        return reason
    seen.add(key)
    return None


def _candidate_identity(candidate: DailySelectionCandidate) -> tuple[tuple[str, str], str]:
    source_url = canonical_source_url(candidate.source_url)
    offer_id = candidate.offer_id.strip()
    # The normalizer uses the source URL as ``offer_id`` when an upstream item
    # has no product ID.  Keep that fallback distinct from a genuine offer ID.
    if _canonical_url_or_none(offer_id) == source_url:
        return (candidate.source_platform, source_url), "duplicate_source_url"
    return (candidate.source_platform, offer_id), "duplicate_source_offer"


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


def _hard_filter_reasons(
    candidate: DailySelectionCandidate, criteria: DailySelectionCriteria
) -> tuple[str, ...]:
    reasons: list[str] = []
    if candidate.main_image_url is None:
        reasons.append("missing_main_image")
    if criteria.min_price is not None and (candidate.price_cny is None or candidate.price_cny < criteria.min_price):
        reasons.append("missing_price" if candidate.price_cny is None else "price_below_min")
    if criteria.max_price is not None and (candidate.price_cny is None or candidate.price_cny > criteria.max_price):
        reason = "missing_price" if candidate.price_cny is None else "price_above_max"
        if reason not in reasons:
            reasons.append(reason)
    if criteria.min_moq is not None and (
        candidate.min_order_quantity is None or candidate.min_order_quantity < criteria.min_moq
    ):
        reasons.append("missing_moq" if candidate.min_order_quantity is None else "moq_below_min")
    return tuple(reasons)


def _risk_tags(candidate: DailySelectionCandidate) -> tuple[str, ...]:
    text = " ".join(_candidate_text(candidate)).casefold()
    detected = [tag for tag, terms in _RISK_TERMS if any(term in text for term in terms)]
    return _unique((*candidate.risk_tags, *detected))


def _candidate_text(candidate: DailySelectionCandidate) -> tuple[str, ...]:
    values = [candidate.source_title]
    values.extend(_mapping_text(candidate.source_attributes))
    for sku in candidate.source_variant_records:
        values.extend(_mapping_text(sku.attributes))
    return tuple(values)


def _mapping_text(value: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key, item in value.items():
        values.append(str(key))
        if isinstance(item, Mapping):
            values.extend(_mapping_text(item))
        elif isinstance(item, (list, tuple)):
            values.extend(str(entry) for entry in item)
        else:
            values.append(str(item))
    return tuple(values)


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
