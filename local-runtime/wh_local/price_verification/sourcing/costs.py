"""Local landed-cost calculation for normalized source candidates."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import CandidateCostInputs


@dataclass(frozen=True)
class CandidateCosts:
    """JSON-safe landed cost plus its closure/review state."""

    unit_price: float
    moq: float
    domestic_freight: float | None
    landed_cost: float | None
    cost_status: str
    review_required: bool

    def to_payload(self) -> dict[str, float | str | bool | None]:
        return {
            "unit_price": self.unit_price,
            "moq": self.moq,
            "domestic_freight": self.domestic_freight,
            "landed_cost": self.landed_cost,
            "cost_status": self.cost_status,
            "review_required": self.review_required,
        }


def calculate_candidate_costs(inputs: CandidateCostInputs) -> CandidateCosts:
    """Allocate order-level domestic freight across MOQ when it is evidenced.

    Without domestic freight the unit price is useful evidence, but it is not a
    closed landed cost and must stay in manual review.
    """
    if not isinstance(inputs, CandidateCostInputs):
        raise TypeError("inputs must be CandidateCostInputs")
    freight = inputs.domestic_freight
    if freight is None:
        return CandidateCosts(
            unit_price=float(inputs.price),
            moq=float(inputs.moq),
            domestic_freight=None,
            landed_cost=None,
            cost_status="review_required",
            review_required=True,
        )
    landed = inputs.price + freight / inputs.moq
    return CandidateCosts(
        unit_price=float(inputs.price),
        moq=float(inputs.moq),
        domestic_freight=float(freight),
        landed_cost=float(landed),
        cost_status="closed",
        review_required=False,
    )
