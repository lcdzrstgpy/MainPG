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


def _number(value: object) -> float:
    try:
        return float(value) if value is not None else float("inf")
    except (TypeError, ValueError):
        return float("inf")
