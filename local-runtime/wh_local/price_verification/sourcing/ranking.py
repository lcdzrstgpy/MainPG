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
    """Keep OneBound's image-search order after local category filtering.

    OneBound title search is deliberately not called.  The Temu title is used
    locally only to remove clear product-category conflicts before this ranker
    is invoked.
    """
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                _number(candidate.get("image_search_rank")),
                str(candidate.get("candidate_key") or candidate.get("offer_id") or ""),
            ),
        )
    )


def _number(value: object) -> float:
    try:
        return float(value) if value is not None else float("inf")
    except (TypeError, ValueError):
        return float("inf")
