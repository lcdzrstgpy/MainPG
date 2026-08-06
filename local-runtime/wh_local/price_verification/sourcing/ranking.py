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


def rank_candidates_by_mode(
    candidates: Iterable[Mapping[str, Any]],
    *,
    mode: str = "similarity",
) -> tuple[Mapping[str, Any], ...]:
    """Order candidates by the user-selected sorting mode.

    ``similarity`` sorts on the OB image-search similarity score (``turn_head``)
    descending; ``price`` sorts by the cheapest promotion/unit price first.  Any
    other mode falls back to the established closed-cost ordering.
    """
    items = list(candidates)
    if mode == "price":
        return tuple(
            sorted(
                items,
                key=lambda candidate: (
                    _number(candidate.get("promotion_price") if candidate.get("promotion_price") is not None else candidate.get("price")),
                    str(candidate.get("candidate_key") or candidate.get("offer_id") or ""),
                ),
            )
        )
    if mode == "similarity":
        return tuple(
            sorted(
                items,
                key=lambda candidate: (
                    candidate.get("source_channel") == "keyword",
                    -_number(candidate.get("similarity_score")),
                    _number(candidate.get("price")),
                    str(candidate.get("candidate_key") or candidate.get("offer_id") or ""),
                ),
            )
        )
    return rank_source_candidates(items)


def _number(value: object) -> float:
    try:
        return float(value) if value is not None else float("inf")
    except (TypeError, ValueError):
        return float("inf")
