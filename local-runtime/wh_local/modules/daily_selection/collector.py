"""Network-free orchestration around an injected daily-selection provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from .budget import BudgetState, SQLiteDailyApiBudget, credential_fingerprint
from .contracts import ApiEvidence, DailySelectionCandidate, DailySelectionError
from .criteria import DailySelectionCriteria
from .normalizer import enrich_candidate_with_detail, normalize_search_response
from .provider import ProviderCallResult


LOCAL_EXPANSION_RULESET_VERSION = "local-v1"
_LOCAL_EXPANSIONS = {"露营灯": ("便携露营灯",)}
_IMAGE_OPERATION_BUDGET_COST = 3  # download, upload, then image search


class DailySelectionProvider(Protocol):
    credential_fingerprint: str

    def search_keyword(self, criteria: DailySelectionCriteria) -> ProviderCallResult: ...

    def search_by_image(self, criteria: DailySelectionCriteria) -> ProviderCallResult: ...

    def get_item_detail(self, offer_id: str) -> ProviderCallResult: ...


@dataclass(frozen=True)
class QueryAttempt:
    query: str | None
    expanded: bool
    expansion_rule_version: str | None
    audits: tuple[ApiEvidence, ...]


@dataclass(frozen=True)
class CollectedCandidate:
    """A normalized candidate plus collection-specific audit context."""

    candidate: DailySelectionCandidate
    reference_image_url: str | None = None
    detail_error: DailySelectionError | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.candidate, name)


@dataclass(frozen=True)
class CollectionResult:
    status: str
    query_attempts: tuple[QueryAttempt, ...]
    candidates: tuple[CollectedCandidate, ...]
    errors: tuple[DailySelectionError, ...]
    detail_errors: Mapping[str, DailySelectionError]
    search_calls: int
    image_search_calls: int
    detail_calls: int
    api_calls: int
    budget_state: BudgetState
    expansion_rule_version: str | None = None
    derived_image_terms: tuple[str, ...] = ()


class DailySelectionCollector:
    def __init__(
        self,
        *,
        workspace_id: str,
        provider: DailySelectionProvider,
        budget: SQLiteDailyApiBudget,
        provider_credentials: Mapping[str, Any] | str | None = None,
        provider_credential_fingerprint: str | None = None,
        clock: callable | None = None,
    ) -> None:
        self._workspace_id = workspace_id
        self._provider = provider
        self._budget = budget
        self._clock = clock or datetime.now
        inherited = getattr(provider, "credential_fingerprint", None)
        self._provider_fingerprint = provider_credential_fingerprint or inherited
        if self._provider_fingerprint is None and provider_credentials is not None:
            self._provider_fingerprint = credential_fingerprint(provider_credentials)
        if not isinstance(self._provider_fingerprint, str) or not self._provider_fingerprint.strip():
            raise ValueError("provider credential fingerprint or credentials are required")

    def collect(self, criteria: DailySelectionCriteria) -> CollectionResult:
        errors: list[DailySelectionError] = []
        attempts: list[QueryAttempt] = []
        candidates: list[CollectedCandidate] = []
        detail_errors: dict[str, DailySelectionError] = {}
        search_calls = image_search_calls = detail_calls = api_calls = 0
        latest_budget = self._budget.state(
            workspace_id=self._workspace_id,
            provider_fingerprint=self._provider_fingerprint,
            max_api_calls=criteria.max_api_calls,
            now=self._clock(),
        )

        if criteria.collection_mode == "image":
            latest_budget = self._reserve(criteria, _IMAGE_OPERATION_BUDGET_COST)
            if not latest_budget.allowed:
                errors.append(_budget_error())
            else:
                response = self._provider.search_by_image(criteria)
                image_search_calls = 1
                api_calls += len(response.audits)
                attempts.append(QueryAttempt(None, False, None, response.audits))
                candidates.extend(_collected_candidates(response, criteria.reference_image_url))
                if response.error is not None:
                    errors.append(response.error)
        else:
            for query, expanded in _queries(criteria):
                latest_budget = self._reserve(criteria, 1)
                if not latest_budget.allowed:
                    errors.append(_budget_error())
                    break
                per_query = DailySelectionCriteria(
                    **{**criteria.model_dump(mode="python"), "keywords": (query,)},
                )
                response = self._provider.search_keyword(per_query)
                search_calls += 1
                api_calls += len(response.audits)
                attempts.append(
                    QueryAttempt(
                        query,
                        expanded,
                        LOCAL_EXPANSION_RULESET_VERSION if criteria.selection_scope == "divergent" else None,
                        response.audits,
                    )
                )
                candidates.extend(_collected_candidates(response, None))
                if response.error is not None:
                    errors.append(response.error)

        unique = _deduplicate(candidates)
        for index, collected in enumerate(unique):
            if index >= criteria.detail_count:
                break
            latest_budget = self._reserve(criteria, 1)
            if not latest_budget.allowed:
                errors.append(_budget_error())
                break
            response = self._provider.get_item_detail(collected.offer_id)
            detail_calls += 1
            api_calls += len(response.audits)
            if response.error is not None:
                errors.append(response.error)
                detail_errors[collected.offer_id] = response.error
                unique[index] = CollectedCandidate(collected.candidate, collected.reference_image_url, response.error)
            else:
                unique[index] = CollectedCandidate(
                    enrich_candidate_with_detail(collected.candidate, response.response, evidence=response.audit),
                    collected.reference_image_url,
                )

        derived_terms = _titles(unique) if criteria.collection_mode == "image" and criteria.selection_scope == "divergent" else ()
        status = _status(unique, errors)
        return CollectionResult(
            status=status,
            query_attempts=tuple(attempts),
            candidates=tuple(unique),
            errors=tuple(errors),
            detail_errors=detail_errors,
            search_calls=search_calls,
            image_search_calls=image_search_calls,
            detail_calls=detail_calls,
            api_calls=api_calls,
            budget_state=latest_budget,
            expansion_rule_version=LOCAL_EXPANSION_RULESET_VERSION if criteria.selection_scope == "divergent" else None,
            derived_image_terms=derived_terms,
        )

    def _reserve(self, criteria: DailySelectionCriteria, api_calls: int) -> BudgetState:
        return self._budget.reserve(
            workspace_id=self._workspace_id,
            provider_fingerprint=self._provider_fingerprint,
            max_api_calls=criteria.max_api_calls,
            api_calls=api_calls,
            now=self._clock(),
        )


def _queries(criteria: DailySelectionCriteria) -> tuple[tuple[str, bool], ...]:
    base = [(keyword, False) for keyword in criteria.keywords]
    if criteria.selection_scope != "divergent":
        return tuple(base)
    additions = [(query, True) for keyword in criteria.keywords for query in _LOCAL_EXPANSIONS.get(keyword, ())]
    return tuple(base + additions)


def _collected_candidates(response: ProviderCallResult, reference_image_url: str | None) -> list[CollectedCandidate]:
    if not response.ok:
        return []
    return [CollectedCandidate(candidate, reference_image_url) for candidate in normalize_search_response(response.response, evidence=response.audit)]


def _deduplicate(candidates: Sequence[CollectedCandidate]) -> list[CollectedCandidate]:
    selected: list[CollectedCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.candidate_id not in seen:
            selected.append(candidate)
            seen.add(candidate.candidate_id)
    return selected


def _titles(candidates: Sequence[CollectedCandidate]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(candidate.source_title for candidate in candidates if candidate.source_title))


def _budget_error() -> DailySelectionError:
    return DailySelectionError(code="budget_exhausted", message="daily API-call budget is exhausted")


def _status(candidates: Sequence[CollectedCandidate], errors: Sequence[DailySelectionError]) -> str:
    if candidates:
        return "partial" if errors else "completed"
    return "failed" if errors else "empty"
