"""Deterministic, local scoring for daily-selection candidates."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from .contracts import DailySelectionCandidate, SourceVariantRecord
from .criteria import DailySelectionCriteria


_SUPPLY_PRICE = Decimal("10")
_SUPPLY_MOQ = Decimal("10")
_SUPPLY_SHOP = Decimal("5")
_MATCH_TITLE = Decimal("15")
_MATCH_ATTRIBUTES = Decimal("10")
_EVIDENCE_BONUS = Decimal("8")
_FRESH_EVIDENCE = Decimal("10")


def score_candidate(
    candidate: DailySelectionCandidate, criteria: DailySelectionCriteria | None = None
) -> DailySelectionCandidate:
    """Return a candidate with a stable Decimal score and explainable components.

    The score is strictly local: it never reads a clock or external source, so
    identical candidates and criteria always produce identical values.
    """
    supply = _supply_score(candidate)
    match = _match_score(candidate, criteria)
    evidence, bonuses = _evidence_score(candidate)
    freshness = _freshness_score(candidate)
    total = supply + match + evidence + freshness
    components = {
        "supply": supply,
        "match": match,
        "evidence": evidence,
        "freshness": freshness,
        **bonuses,
    }
    return candidate.model_copy(update={"selection_score": total, "score_components": components})


def score_candidates(
    candidates: Sequence[DailySelectionCandidate], criteria: DailySelectionCriteria | None = None
) -> tuple[DailySelectionCandidate, ...]:
    """Score and sort candidates deterministically, highest score first."""
    scored = tuple(score_candidate(candidate, criteria) for candidate in candidates)
    return tuple(sorted(scored, key=lambda candidate: (-candidate.selection_score, candidate.candidate_id)))


def _supply_score(candidate: DailySelectionCandidate) -> Decimal:
    return (
        (_SUPPLY_PRICE if candidate.price_cny is not None else Decimal("0"))
        + (_SUPPLY_MOQ if candidate.min_order_quantity is not None else Decimal("0"))
        + (_SUPPLY_SHOP if candidate.shop_name else Decimal("0"))
    )


def _match_score(candidate: DailySelectionCandidate, criteria: DailySelectionCriteria | None) -> Decimal:
    title = candidate.source_title.casefold()
    if criteria is None:
        title_match = bool(title)
    else:
        title_match = any(keyword.casefold() in title for keyword in criteria.keywords)
    return (_MATCH_TITLE if title_match else Decimal("0")) + (
        _MATCH_ATTRIBUTES if candidate.source_attributes else Decimal("0")
    )


def _evidence_score(candidate: DailySelectionCandidate) -> tuple[Decimal, dict[str, Decimal]]:
    bonuses = {
        "main_image": _EVIDENCE_BONUS if candidate.main_image_url else Decimal("0"),
        "image_gallery": _EVIDENCE_BONUS if candidate.source_image_urls else Decimal("0"),
        "detail_images": _EVIDENCE_BONUS if candidate.source_detail_image_urls else Decimal("0"),
        "attributes": _EVIDENCE_BONUS if candidate.source_attributes else Decimal("0"),
        "complete_sku": _EVIDENCE_BONUS if _has_complete_sku_evidence(candidate.source_variant_records) else Decimal("0"),
    }
    return sum(bonuses.values(), Decimal("0")), bonuses


def _has_complete_sku_evidence(records: Sequence[SourceVariantRecord]) -> bool:
    return bool(records) and all(
        record.sku_id
        and record.attributes
        and record.image_url is not None
        and record.price_cny is not None
        and record.min_order_quantity is not None
        for record in records
    )


def _freshness_score(candidate: DailySelectionCandidate) -> Decimal:
    return _FRESH_EVIDENCE if any(evidence.captured_at for evidence in candidate.evidence) else Decimal("0")


# Alias kept for pipeline callers that describe this operation as ranking.
rank_candidates = score_candidates
