"""Read-only sourcing task and financial adapters."""

from .contracts import (
    CandidateCostInputs,
    CandidateProfitInputs,
    SourceBrowserImageSearchPayload,
    SourceSearchTask,
    SourcingContractError,
)
from .costs import CandidateCosts, calculate_candidate_costs
from .profit_adapter import preview_profit
from .ranking import rank_source_candidates
from .task_builder import build_source_browser_image_search_payload

__all__ = [
    "CandidateCostInputs",
    "CandidateCosts",
    "CandidateProfitInputs",
    "SourceBrowserImageSearchPayload",
    "SourceSearchTask",
    "SourcingContractError",
    "build_source_browser_image_search_payload",
    "calculate_candidate_costs",
    "preview_profit",
    "rank_source_candidates",
]
