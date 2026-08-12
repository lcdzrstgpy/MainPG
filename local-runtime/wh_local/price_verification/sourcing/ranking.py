"""Deterministic local ordering for source candidates."""

from __future__ import annotations

from typing import Any, Iterable, Mapping


def rank_source_candidates(
    candidates: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """Place closed, lower-cost candidates ahead of review-only evidence."""
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                candidate.get("cost_status") != "closed",
                _number(candidate.get("landed_cost")),
                str(candidate.get("candidate_key") or candidate.get("offer_id") or ""),
            ),
        )
    )


def rank_candidates_by_image_order(
    candidates: Iterable[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """Prefer locally verified visual similarity, then title and provider order.

    OneBound does not provide a documented visual similarity score. A title hit
    never becomes a candidate by itself; it only prevents an obviously
    unrelated image hit from taking first place when an image candidate is also
    confirmed by the translated Temu title search.
    """
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -_similarity(candidate.get("image_similarity_score")),
                not bool(candidate.get("title_search_confirmed")),
                _number(candidate.get("image_search_rank")),
                str(candidate.get("candidate_key") or candidate.get("offer_id") or ""),
            ),
        )
    )


def _similarity(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if 0.0 <= parsed <= 1.0 else 0.0


def _number(value: object) -> float:
    try:
        return float(value) if value is not None else float("inf")
    except (TypeError, ValueError):
        return float("inf")
