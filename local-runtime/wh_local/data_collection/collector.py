"""Network-free orchestration around an injected daily-selection provider."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
import re
from typing import Any, Callable, Mapping, Protocol, Sequence

from .budget import BudgetState, TaskApiBudget, credential_fingerprint, is_credential_fingerprint
from .contracts import ApiEvidence, DailySelectionCandidate, DailySelectionError
from .criteria import DailySelectionCriteria
from .normalizer import enrich_candidate_with_detail, normalize_search_response
from .provider import ProviderCallResult


LOCAL_EXPANSION_RULESET_VERSION = "local-v1"
_LOCAL_EXPANSIONS = {"露营灯": ("便携露营灯",)}
_IMAGE_OPERATION_BUDGET_COST = 3  # download, upload, then image search
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)")


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
        budget: TaskApiBudget,
        provider_credentials: Mapping[str, Any] | str | None = None,
        provider_credential_fingerprint: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._workspace_id = workspace_id
        self._provider = provider
        self._budget = budget
        self._clock = clock or datetime.now
        inherited = getattr(provider, "credential_fingerprint", None)
        fingerprint = provider_credential_fingerprint or inherited
        if fingerprint is None and provider_credentials is not None:
            fingerprint = credential_fingerprint(provider_credentials)
        if not is_credential_fingerprint(fingerprint):
            raise ValueError("provider credential fingerprint must be a SHA-256 hexadecimal digest")
        self._provider_fingerprint = fingerprint.casefold()

    def collect(self, criteria: DailySelectionCriteria) -> CollectionResult:
        max_parallel = max(1, min(10, int(criteria.max_parallel_collect)))
        errors: list[DailySelectionError] = []
        attempts: list[QueryAttempt] = []
        candidates: list[CollectedCandidate] = []
        detail_errors: dict[str, DailySelectionError] = {}
        search_calls = image_search_calls = detail_calls = api_calls = 0
        collection_time = self._clock()
        self._budget.start()
        latest_budget = self._budget.state(
            workspace_id=self._workspace_id,
            provider_fingerprint=self._provider_fingerprint,
            max_api_calls=criteria.max_api_calls,
            now=collection_time,
        )

        if criteria.collection_mode == "image":
            latest_budget = self._reserve(criteria, _IMAGE_OPERATION_BUDGET_COST, collection_time)
            if not latest_budget.reservation_granted:
                errors.append(_budget_error())
            else:
                response = self._provider.search_by_image(criteria)
                actual_calls = len(response.audits)
                latest_budget = self._settle(criteria, _IMAGE_OPERATION_BUDGET_COST, actual_calls, collection_time)
                image_search_calls = int(any(audit.operation == "item_search_img" for audit in response.audits))
                api_calls += actual_calls
                attempts.append(QueryAttempt(None, False, None, response.audits))
                if not _valid_image_audits(response):
                    errors.append(_provider_sequence_error())
                else:
                    candidates.extend(_collected_candidates(response, criteria.reference_image_url))
                if response.error is not None:
                    errors.append(response.error)
        else:
            queries = _queries(criteria)
            if max_parallel <= 1:
                # 串行模式：保持原有行为
                for query, expanded in queries:
                    latest_budget = self._reserve(criteria, 1, collection_time)
                    if not latest_budget.reservation_granted:
                        errors.append(_budget_error())
                        break
                    per_query = DailySelectionCriteria(
                        **{**criteria.model_dump(mode="python"), "keywords": (query,)},
                    )
                    response = self._provider.search_keyword(per_query)
                    latest_budget = self._settle(criteria, 1, len(response.audits), collection_time)
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
                    candidates.extend(_tagged_candidates(response, query, expanded=expanded))
                    if response.error is not None:
                        errors.append(response.error)
            else:
                # 并行关键词搜索
                response_by_keyword: dict[str, ProviderCallResult] = {}
                ordered_queries: list[tuple[str, bool]] = list(queries)
                _reserve_all = True
                for _ in ordered_queries:
                    latest_budget = self._reserve(criteria, 1, collection_time)
                    if not latest_budget.reservation_granted:
                        errors.append(_budget_error())
                        _reserve_all = False
                        break
                if _reserve_all:
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    def _search(kw: str) -> tuple[str, ProviderCallResult]:
                        per_query = DailySelectionCriteria(
                            **{**criteria.model_dump(mode="python"), "keywords": (kw,)},
                        )
                        return kw, self._provider.search_keyword(per_query)

                    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                        future_map = {
                            executor.submit(_search, query): query
                            for query, _ in ordered_queries
                        }
                        for future in as_completed(future_map):
                            kw, response = future.result()
                            response_by_keyword[kw] = response
                    # Settle（超额 audit 释放差值）
                    total_audits = sum(len(r.audits) for r in response_by_keyword.values())
                    latest_budget = self._settle(criteria, len(ordered_queries), total_audits, collection_time)
                for query, expanded in ordered_queries:
                    response = response_by_keyword.get(query)
                    if response is None:
                        continue
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
                    candidates.extend(_tagged_candidates(response, query, expanded=expanded))
                    if response.error is not None:
                        errors.append(response.error)

        unique = _rank_candidates(_deduplicate(candidates))
        if max_parallel <= 1:
            for index, collected in enumerate(unique):
                latest_budget = self._reserve(criteria, 1, collection_time)
                if not latest_budget.reservation_granted:
                    errors.append(_budget_error())
                    break
                response = self._provider.get_item_detail(collected.offer_id)
                latest_budget = self._settle(criteria, 1, len(response.audits), collection_time)
                detail_calls += 1
                api_calls += len(response.audits)
                if response.error is not None:
                    errors.append(response.error)
                    detail_errors[collected.offer_id] = response.error
                    unique[index] = CollectedCandidate(
                        collected.candidate.model_copy(
                            update={"evidence": collected.candidate.evidence + response.audits}
                        ),
                        collected.reference_image_url,
                        response.error,
                    )
                else:
                    unique[index] = CollectedCandidate(
                        enrich_candidate_with_detail(collected.candidate, response.response, evidence=response.audit),
                        collected.reference_image_url,
                    )
        else:
            # 并行拉取详情（所有候选，预算自然限制）
            detail_items = list(unique)
            _reserve_all = True
            for _ in detail_items:
                latest_budget = self._reserve(criteria, 1, collection_time)
                if not latest_budget.reservation_granted:
                    errors.append(_budget_error())
                    _reserve_all = False
                    break
            if _reserve_all and detail_items:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                detail_results: dict[str, ProviderCallResult] = {}

                def _fetch_detail(idx: int, offer_id: str) -> tuple[int, str, ProviderCallResult]:
                    return idx, offer_id, self._provider.get_item_detail(offer_id)

                with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                    future_map = {
                        executor.submit(_fetch_detail, idx, item.offer_id): (idx, item.offer_id)
                        for idx, item in enumerate(detail_items)
                    }
                    for future in as_completed(future_map):
                        idx, offer_id, response = future.result()
                        detail_results[offer_id] = response
                        detail_calls += 1
                        api_calls += len(response.audits)
                        collected = detail_items[idx]
                        if response.error is not None:
                            errors.append(response.error)
                            detail_errors[offer_id] = response.error
                            unique[idx] = CollectedCandidate(
                                collected.candidate.model_copy(
                                    update={"evidence": collected.candidate.evidence + response.audits}
                                ),
                                collected.reference_image_url,
                                response.error,
                            )
                        else:
                            unique[idx] = CollectedCandidate(
                                enrich_candidate_with_detail(collected.candidate, response.response, evidence=response.audit),
                                collected.reference_image_url,
                            )
                total_audits = sum(len(r.audits) for r in detail_results.values())
                latest_budget = self._settle(criteria, len(detail_items), total_audits, collection_time)

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

    def _reserve(self, criteria: DailySelectionCriteria, api_calls: int, now: datetime) -> BudgetState:
        return self._budget.reserve(
            workspace_id=self._workspace_id,
            provider_fingerprint=self._provider_fingerprint,
            max_api_calls=criteria.max_api_calls,
            api_calls=api_calls,
            now=now,
        )

    def _settle(self, criteria: DailySelectionCriteria, reserved_calls: int, actual_calls: int, now: datetime) -> BudgetState:
        if actual_calls > reserved_calls:
            raise ValueError("provider audit count exceeds the operation budget")
        if actual_calls == reserved_calls:
            return self._budget.state(
                workspace_id=self._workspace_id,
                provider_fingerprint=self._provider_fingerprint,
                max_api_calls=criteria.max_api_calls,
                now=now,
            )
        return self._budget.release(
            workspace_id=self._workspace_id,
            provider_fingerprint=self._provider_fingerprint,
            max_api_calls=criteria.max_api_calls,
            api_calls=reserved_calls - actual_calls,
            now=now,
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
    return [
        CollectedCandidate(
            candidate.model_copy(update={"selection_result_label": "API 图搜候选"}),
            reference_image_url,
        )
        for candidate in normalize_search_response(response.response, evidence=response.audit)
    ]


def _tagged_candidates(response: ProviderCallResult, query: str, *, expanded: bool) -> list[CollectedCandidate]:
    """Attach the search keyword and result label that produced each candidate.

    ``精准参考`` marks the seed query for a keyword center; ``同类发散`` marks
    rule-based expansions, mirroring the reference workbench result labels.
    """
    label = "同类发散" if expanded else "精准参考"
    return [
        CollectedCandidate(
            candidate.model_copy(update={"query_keyword": query, "selection_result_label": label}),
            None,
        )
        for candidate in normalize_search_response(response.response, evidence=response.audit)
    ]


def _deduplicate(candidates: Sequence[CollectedCandidate]) -> list[CollectedCandidate]:
    selected: list[CollectedCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.candidate_id not in seen:
            selected.append(candidate)
            seen.add(candidate.candidate_id)
    return selected


def _rank_candidates(candidates: Sequence[CollectedCandidate]) -> list[CollectedCandidate]:
    """Rank by score then source sales, price, and MOQ before fetching details.

    Collection candidates commonly have a zero ``selection_score`` before later
    scoring stages. Source fields give the production path a local, auditable
    ordering; an exact tie retains the provider order as Python's stable sort.
    """
    return sorted(candidates, key=_pre_detail_rank_key)


def _pre_detail_rank_key(candidate: CollectedCandidate) -> tuple[Decimal, Decimal, bool, Decimal, bool, int]:
    score = candidate.selection_score if candidate.selection_score is not None else Decimal("0")
    sales = _sales_count(candidate.sales_text)
    price = candidate.price_cny
    moq = candidate.min_order_quantity
    return (
        -score,
        -sales,
        price is None,
        price if price is not None else Decimal("Infinity"),
        moq is None,
        moq if moq is not None else 2**63 - 1,
    )


def _sales_count(value: str | None) -> Decimal:
    if not isinstance(value, str):
        return Decimal("0")
    match = _NUMBER.search(value.replace(",", ""))
    return Decimal(match.group()) if match else Decimal("0")


def _titles(candidates: Sequence[CollectedCandidate]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(candidate.source_title for candidate in candidates if candidate.source_title))


def _budget_error() -> DailySelectionError:
    return DailySelectionError(code="budget_exhausted", message="daily API-call budget is exhausted")


def _provider_sequence_error() -> DailySelectionError:
    return DailySelectionError(
        code="invalid_provider_sequence",
        message="image provider audits must show download, upload, then image search",
    )


def _valid_image_audits(response: ProviderCallResult) -> bool:
    expected = ("download_reference_image", "upload_img", "item_search_img")
    observed = tuple(audit.operation for audit in response.audits)
    if observed != expected[: len(observed)]:
        return False
    return not response.ok or observed == expected


def _status(candidates: Sequence[CollectedCandidate], errors: Sequence[DailySelectionError]) -> str:
    if candidates:
        return "partial" if errors else "completed"
    return "failed" if errors else "empty"
