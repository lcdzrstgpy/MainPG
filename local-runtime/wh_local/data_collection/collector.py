"""Network-free orchestration around an injected daily-selection provider."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
import re
import threading
from typing import Any, Callable, Mapping, Protocol, Sequence

from .budget import BudgetState, TaskApiBudget, credential_fingerprint, is_credential_fingerprint
from .contracts import ApiEvidence, DailySelectionCandidate, DailySelectionError
from .criteria import DailySelectionCriteria
from .normalizer import enrich_candidate_with_detail, normalize_search_response
from .provider import ProviderCallResult


LOCAL_EXPANSION_RULESET_VERSION = "local-v1"
_LOCAL_EXPANSIONS = {"露营灯": ("便携露营灯",)}
_IMAGE_OPERATION_BUDGET_COST = 3  # download, upload, then image search
# 单关键词最多翻页数：上游单页返回条数有上限（1688 约 100 条、淘宝固定一页），
# target_count（最高 200）需要按 page 递增补齐；这里给出安全上限，避免上游对深页
# 返回重复数据时无界翻页。
_MAX_SEARCH_PAGES = 10
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)")
CollectionProgressCallback = Callable[[str, int, int], None]


class DailySelectionProvider(Protocol):
    credential_fingerprint: str

    def search_keyword(self, criteria: DailySelectionCriteria, page: int = 1) -> ProviderCallResult: ...

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
        progress_callback: CollectionProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._workspace_id = workspace_id
        self._provider = provider
        self._budget = budget
        self._clock = clock or datetime.now
        self._progress_callback = progress_callback
        self._cancel_event = cancel_event
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
        cancelled = False
        collection_time = self._clock()
        self._budget.start()

        def _cancel_requested() -> bool:
            if self._cancel_event is not None and self._cancel_event.is_set():
                return True
            return False

        latest_budget = self._budget.state(
            workspace_id=self._workspace_id,
            provider_fingerprint=self._provider_fingerprint,
            max_api_calls=criteria.max_api_calls,
            now=collection_time,
        )

        if criteria.collection_mode == "image":
            if _cancel_requested():
                cancelled = True
            else:
                self._progress("searching", 0, 1)
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
                    self._progress("searching", 1, 1)
        else:
            queries = _queries(criteria)
            search_completed = 0
            self._progress("searching", 0, len(queries))
            if max_parallel <= 1:
                # 串行模式：保持原有行为
                for query, expanded in queries:
                    if _cancel_requested():
                        cancelled = True
                        break
                    latest_budget = self._reserve(criteria, 1, collection_time)
                    if not latest_budget.reservation_granted:
                        errors.append(_budget_error())
                        break
                    per_query = DailySelectionCriteria(
                        **{**criteria.model_dump(mode="python"), "keywords": (query,)},
                    )
                    responses = _search_pages(
                        self._provider,
                        per_query,
                        should_stop=lambda: len(candidates) >= criteria.target_count,
                    )
                    latest_budget = self._settle(
                        criteria,
                        1,
                        sum(len(response.audits) for response in responses),
                        collection_time,
                    )
                    for response in responses:
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
                    search_completed += 1
                    self._progress("searching", search_completed, len(queries))
            else:
                # 并行关键词搜索
                responses_by_keyword: dict[str, list[ProviderCallResult]] = {}
                ordered_queries: list[tuple[str, bool]] = list(queries)
                _reserve_all = True
                reserved_search_calls = 0
                if _cancel_requested():
                    cancelled = True
                else:
                    for _ in ordered_queries:
                        latest_budget = self._reserve(criteria, 1, collection_time)
                        if not latest_budget.reservation_granted:
                            errors.append(_budget_error())
                            _reserve_all = False
                            break
                        reserved_search_calls += 1
                if _reserve_all and not cancelled:
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    def _search(kw: str) -> tuple[str, list[ProviderCallResult]]:
                        per_query = DailySelectionCriteria(
                            **{**criteria.model_dump(mode="python"), "keywords": (kw,)},
                        )
                        return kw, _search_pages(self._provider, per_query)

                    with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                        future_map = {
                            executor.submit(_search, query): query
                            for query, _ in ordered_queries
                        }
                        for future in as_completed(future_map):
                            kw, responses = future.result()
                            responses_by_keyword[kw] = responses
                            search_completed += 1
                            self._progress("searching", search_completed, len(ordered_queries))
                    # Settle（超额 audit 释放差值）
                    total_audits = sum(
                        len(response.audits)
                        for responses in responses_by_keyword.values()
                        for response in responses
                    )
                    latest_budget = self._settle(criteria, len(ordered_queries), total_audits, collection_time)
                elif reserved_search_calls:
                    # 预算不足提前退出：释放已预占的差额
                    latest_budget = self._settle(criteria, reserved_search_calls, 0, collection_time)
                for query, expanded in ordered_queries:
                    responses = responses_by_keyword.get(query)
                    if not responses:
                        continue
                    for response in responses:
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
        # 详情拉取量控制：展示候选按「采集数量」收敛，未启用 SKU/起订量筛选时
        # 只拉排名前 target_count 个候选的详情即可。搜索已按 page 翻取到
        # target_count，但仍可能因去重/上游返回偏差多出少量候选，这里统一截断，
        # 避免为多余候选浪费 API 与时长。启用 SKU 硬筛选时需要全量候选的详情数据
        # 才能判定，仍按 detail_count 尽量全量；超出 API 预算时由逐条 _reserve
        # 自然停止。
        has_sku_filter = (
            criteria.min_moq is not None
            or criteria.min_sku_count is not None
            or criteria.max_sku_count is not None
            or criteria.min_sku_price is not None
            or criteria.max_sku_price is not None
            or criteria.min_sku_stock is not None
            or criteria.max_sku_stock is not None
        )
        detail_targets = list(enumerate(unique))
        if not has_sku_filter:
            detail_targets = detail_targets[: criteria.target_count]
        else:
            detail_targets = detail_targets[: max(criteria.detail_count, len(unique))]
        if _cancel_requested():
            cancelled = True
        if max_parallel <= 1:
            self._progress("details", 0, len(detail_targets))
            for index, collected in detail_targets:
                if _cancel_requested():
                    cancelled = True
                    break
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
                self._progress("details", detail_calls, len(detail_targets))
        else:
            # 并行拉取详情：按候选顺序逐个预占预算，预算不足时只处理已预占的候选
            budgeted_items: list[tuple[int, CollectedCandidate]] = []
            for idx, collected in detail_targets:
                if _cancel_requested():
                    cancelled = True
                    break
                latest_budget = self._reserve(criteria, 1, collection_time)
                if not latest_budget.reservation_granted:
                    errors.append(_budget_error())
                    break
                budgeted_items.append((idx, collected))
            if budgeted_items:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                detail_results: dict[int, ProviderCallResult] = {}
                self._progress("details", 0, len(budgeted_items))

                def _fetch_detail(offer_id: str) -> ProviderCallResult:
                    return self._provider.get_item_detail(offer_id)

                # 分批发起详情调用：每次至多放行 max_parallel 个并发请求，
                # 有结果返回再补充新任务，避免一次性堆满未来任务占住线程池队列。
                with ThreadPoolExecutor(max_workers=max_parallel) as executor:
                    pending: dict[Any, tuple[int, str]] = {}
                    it = iter(budgeted_items)
                    for _ in range(min(max_parallel, len(budgeted_items))):
                        try:
                            idx, item = next(it)
                        except StopIteration:
                            break
                        pending[executor.submit(_fetch_detail, item.offer_id)] = (idx, item.offer_id)
                    while pending:
                        # 对当前批次快照等待完成；期间补充的新任务会在下一轮
                        # while 循环被重新快照，保持并发窗口稳定。
                        for future in as_completed(list(pending)):
                            idx, offer_id = pending.pop(future)
                            response = future.result()
                            detail_results[idx] = response
                            detail_calls += 1
                            api_calls += len(response.audits)
                            collected = unique[idx]
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
                            self._progress("details", detail_calls, len(budgeted_items))
                            # 取消后不再补充新任务，等待已提交的请求跑完即结束
                            if _cancel_requested():
                                cancelled = True
                                continue
                            # 补充一个新任务，保持并发窗口稳定
                            try:
                                nidx, nitem = next(it)
                            except StopIteration:
                                continue
                            pending[executor.submit(_fetch_detail, nitem.offer_id)] = (nidx, nitem.offer_id)
                # 逐个结算实际调用并释放未用额度
                for idx, response in detail_results.items():
                    latest_budget = self._settle(criteria, 1, len(response.audits), collection_time)
            else:
                self._progress("details", 0, 0)

        derived_terms = _titles(unique) if criteria.collection_mode == "image" and criteria.selection_scope == "divergent" else ()
        status = "cancelled" if cancelled else _status(unique, errors)
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
            # Provider retries are represented by one audit per HTTP attempt.
            # The extra calls have already happened, so account for them instead
            # of turning a recoverable upstream retry into a failed collection.
            return self._budget.reserve(
                workspace_id=self._workspace_id,
                provider_fingerprint=self._provider_fingerprint,
                max_api_calls=criteria.max_api_calls,
                api_calls=actual_calls - reserved_calls,
                now=now,
            )
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

    def _progress(self, stage: str, completed: int, total: int) -> None:
        if self._progress_callback is not None:
            self._progress_callback(stage, completed, total)


def _queries(criteria: DailySelectionCriteria) -> tuple[tuple[str, bool], ...]:
    base = [(keyword, False) for keyword in criteria.keywords]
    if criteria.selection_scope != "divergent":
        return tuple(base)
    additions = [(query, True) for keyword in criteria.keywords for query in _LOCAL_EXPANSIONS.get(keyword, ())]
    return tuple(base + additions)


def _search_pages(
    provider: DailySelectionProvider,
    criteria: DailySelectionCriteria,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> list[ProviderCallResult]:
    """按 ``page`` 逐页拉取关键词搜索，直到凑满 ``target_count`` 或某页无结果。

    上游对单页返回条数有上限（1688 单页约 100 条、淘宝接口忽略 ``page_size``
    固定返回一页），只发一次请求无法把 ``target_count``（最高 200）凑满，因此
    按页递增翻取；某页返回空即视为结果已尽，不再继续翻页。
    """
    responses: list[ProviderCallResult] = []
    found = 0
    for page in range(1, _MAX_SEARCH_PAGES + 1):
        if should_stop is not None and should_stop():
            break
        response = provider.search_keyword(criteria, page=page)
        responses.append(response)
        if response.error is not None:
            break
        page_candidates = normalize_search_response(response.response, evidence=response.audit)
        if not page_candidates:
            break
        found += len(page_candidates)
        if found >= criteria.target_count:
            break
    return responses


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
