"""Recoverable background executor for OneBound whole-shop collection."""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .budget import SQLiteDailyApiBudget, credential_fingerprint
from .contracts import DailySelectionCandidate
from .normalizer import normalize_detail_response
from .service import DailySelectionActor
from .shop_repository import (
    InvalidShopBatchTransition,
    ShopBatchLease,
    ShopCollectionRepository,
    ShopLeaseLost,
)


logger = logging.getLogger("wh_local.data_collection.shop_worker")
_TRANSIENT_ERRORS = frozenset({"timeout", "rate_limited", "upstream_failed", "network_error"})


class _WorkerStopping(RuntimeError):
    pass


class _StaleItemLease(RuntimeError):
    pass


class ShopApiBudgetExhausted(RuntimeError):
    code = "api_budget_exhausted"


class ShopCollectionWorker:
    """Runs durable batches with one global three-slot detail pool."""

    DETAIL_CONCURRENCY = 3
    BATCH_LEASE_SECONDS = 120
    ITEM_LEASE_SECONDS = 120

    def __init__(
        self,
        *,
        repository: ShopCollectionRepository,
        provider_config_resolver: Callable[[DailySelectionActor], Mapping[str, Any]],
        provider_factory: Callable[[Mapping[str, Any]], Any],
        intake_shop_candidate: Callable[..., Mapping[str, Any]],
        page_normalizer: Callable[..., Any] | None = None,
        detail_normalizer: Callable[[Any, Any], Mapping[str, Any] | DailySelectionCandidate] | None = None,
        retry_delay_seconds: float = 0.05,
        unfinished_poll_seconds: float = 0.25,
        budget: Any | None = None,
        max_api_calls: int = 300,
    ) -> None:
        self.repository = repository
        self._provider_config_resolver = provider_config_resolver
        self._provider_factory = provider_factory
        self._intake = intake_shop_candidate
        self._page_normalizer = page_normalizer or _default_page_normalizer
        self._detail_normalizer = detail_normalizer or _default_detail_normalizer
        self._retry_delay_seconds = retry_delay_seconds
        if unfinished_poll_seconds <= 0:
            raise ValueError("unfinished_poll_seconds must be positive")
        self._unfinished_poll_seconds = unfinished_poll_seconds
        if isinstance(max_api_calls, bool) or not 1 <= int(max_api_calls) <= 300:
            raise ValueError("max_api_calls must be between 1 and 300")
        self._max_api_calls = int(max_api_calls)
        self._budget = budget or SQLiteDailyApiBudget(repository.database_path)
        self._owner = f"shop-worker-{uuid.uuid4().hex}"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seed_details: dict[tuple[str, str], Any] = {}
        self._seed_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.repository.recover_interrupted_work()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="shop-collection-worker", daemon=True)
        self._thread.start()
        self.notify()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join()

    def notify(self) -> None:
        self._wake.set()

    def retry_failed(self, *, workspace_id: str, batch_id: str) -> Any:
        batch = self.repository.get_batch(workspace_id=workspace_id, batch_id=batch_id)
        if batch.status not in {"partial", "failed"}:
            raise ValueError("only failed or partial batches can be retried")
        reset = self.repository.reset_failed_items(batch_id=batch_id)
        if batch.status == "failed" and reset == 0 and not batch.listing_complete:
            # Listing failures resume from the persisted page checkpoint.
            pass
        self.repository.transition_batch(batch_id, "queued")
        self.notify()
        return self.repository.get_batch(workspace_id=workspace_id, batch_id=batch_id)

    def process_batch(self, batch_id: str, *, lease: ShopBatchLease | None = None) -> None:
        active_lease = lease or self.repository.claim_batch(
            batch_id=batch_id, owner=self._owner, lease_seconds=self.BATCH_LEASE_SECONDS
        )
        if active_lease is None:
            return
        try:
            batch = self.repository.get_batch_internal(batch_id)
            if self._stop.is_set() or self._apply_control_state(batch, active_lease):
                return
            actor = DailySelectionActor(actor_id=batch.actor_id, workspace_id=batch.workspace_id)
            config = self._provider_config_resolver(actor)
            provider = self._provider_factory(config)
            provider = self._budgeted_provider(
                provider,
                batch_id=batch.batch_id,
                workspace_id=batch.workspace_id,
                provider_fingerprint=credential_fingerprint(config),
            )
            if batch.status == "queued":
                batch = self._transition(active_lease, "resolving", {"queued"})
            if batch.status == "resolving":
                batch = self._resolve_shop(provider, batch, active_lease)
                if self._stop.is_set():
                    return
                batch = self._transition(active_lease, "listing", {"resolving"})
            if batch.status == "listing":
                batch = self._list_shop(provider, batch, active_lease)
                if self._stop.is_set() or self._apply_control_state(batch, active_lease):
                    return
                batch = self._transition(active_lease, "enriching", {"listing"})
            if batch.status == "enriching":
                self._enrich(provider, batch, active_lease)
        except (ShopLeaseLost, _WorkerStopping):
            return
        except InvalidShopBatchTransition:
            current = self.repository.get_batch_internal(batch_id)
            self._apply_control_state(current, active_lease)
        except Exception as error:
            logger.warning("shop collection batch %s failed: %s", batch_id, type(error).__name__)
            current = self.repository.get_batch_internal(batch_id)
            if current.status in {"pausing", "cancelling"}:
                self._apply_control_state(current, active_lease)
                return
            if current.status not in {"cancelled", "completed", "partial", "failed", "paused"}:
                try:
                    self.repository.transition_batch(
                        batch_id, "failed", expected_statuses={current.status}, owner=active_lease.lease_owner,
                        lease_token=active_lease.lease_token, error_code=_error_code(error),
                        error_message=_error_message(error),
                    )
                except (InvalidShopBatchTransition, ShopLeaseLost):
                    pass
        finally:
            self.repository.release_batch_lease(
                batch_id=batch_id, owner=active_lease.lease_owner, lease_token=active_lease.lease_token
            )

    def _resolve_shop(self, provider: Any, batch: Any, lease: ShopBatchLease) -> Any:
        if not batch.seed_offer_id:
            return batch
        result = self._call_detail(provider, batch.seed_offer_id)
        self._raise_if_stopping()
        if not _result_ok(result):
            raise RuntimeError(_result_error_message(result))
        seller = _seller_info(_result_response(result))
        sid = str(seller.get("sid") or "").strip()
        if not sid:
            raise ValueError("item detail did not include a shop SID")
        try:
            from .shop_parsing import validate_shop_sid

            sid = validate_shop_sid(sid)
        except ImportError:
            if not sid:
                raise ValueError("invalid shop SID")
        with self._seed_lock:
            self._seed_details[(batch.batch_id, batch.seed_offer_id)] = result
        self._renew(lease)
        return self.repository.resolve_shop_identity(
            batch_id=batch.batch_id,
            shop_sid=sid,
            shop_name=str(seller.get("shop_name") or seller.get("nick") or ""),
        )

    def _list_shop(self, provider: Any, batch: Any, lease: ShopBatchLease) -> Any:
        if batch.listing_complete:
            return batch
        page = batch.next_page
        while page <= min(batch.max_pages, 100):
            self._raise_if_stopping()
            self._renew(lease)
            current = self.repository.get_batch_internal(batch.batch_id)
            if self._apply_control_state(current, lease):
                return self.repository.get_batch_internal(batch.batch_id)
            result = provider.search_shop(current.shop_sid, page)
            self._raise_if_stopping()
            if not _result_ok(result):
                raise RuntimeError(_result_error_message(result))
            response = _result_response(result)
            normalized = self._page_normalizer(response, getattr(result, "audit", None))
            values = _page_values(normalized)
            missing_count = _non_negative_int(_value(normalized, "missing_offer_count", 0))
            has_next = _page_has_next(normalized, response=response, current_page=page)
            total_pages = _value(normalized, "total_pages", None)
            if isinstance(total_pages, int):
                has_next = page < min(total_pages, batch.max_pages, 100)
            if page >= min(batch.max_pages, 100):
                has_next = False
            self._renew(lease)
            self.repository.record_shop_page(
                batch_id=batch.batch_id,
                page=page,
                items=values,
                has_next=has_next,
                missing_id_count=missing_count,
            )
            if not has_next:
                break
            page += 1
        return self.repository.get_batch_internal(batch.batch_id)

    def _enrich(self, provider: Any, batch: Any, lease: ShopBatchLease) -> None:
        while True:
            self._raise_if_stopping()
            self._renew(lease)
            current = self.repository.get_batch_internal(batch.batch_id)
            if self._apply_control_state(current, lease):
                return
            claimed = self.repository.claim_pending_items(
                batch_id=batch.batch_id,
                owner=self._owner,
                limit=self.DETAIL_CONCURRENCY,
                lease_seconds=self.ITEM_LEASE_SECONDS,
            )
            if not claimed:
                if self.repository.has_unfinished_items(batch_id=batch.batch_id):
                    if self._stop.wait(self._unfinished_poll_seconds):
                        raise _WorkerStopping()
                    continue
                final = self.repository.get_batch_internal(batch.batch_id)
                status = "partial" if final.failed_count else "completed"
                self._transition(lease, status, {"enriching"})
                return
            with ThreadPoolExecutor(max_workers=self.DETAIL_CONCURRENCY, thread_name_prefix="shop-detail") as pool:
                futures = {pool.submit(self._enrich_one, provider, batch, item): item for item in claimed}
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        candidate, action = future.result()
                    except _WorkerStopping:
                        self.repository.release_item(
                            batch_id=batch.batch_id, item_id=item.item_id,
                            owner=item.lease_owner, lease_token=item.lease_token,
                        )
                    except _StaleItemLease:
                        pass
                    except Exception as error:
                        try:
                            self.repository.fail_item(
                                batch_id=batch.batch_id, item_id=item.item_id,
                                owner=item.lease_owner, lease_token=item.lease_token,
                                error_code=_error_code(error), error_message=_error_message(error),
                            )
                        except ShopLeaseLost:
                            pass
                    else:
                        try:
                            self.repository.complete_item(
                                batch_id=batch.batch_id, item_id=item.item_id,
                                owner=item.lease_owner, lease_token=item.lease_token,
                                intake_action=action, candidate=candidate,
                            )
                        except ShopLeaseLost:
                            pass

    def _enrich_one(self, provider: Any, batch: Any, item: Any) -> tuple[Mapping[str, Any], str]:
        with self._seed_lock:
            result = self._seed_details.pop((batch.batch_id, item.offer_id), None)
        if result is None:
            result = self._call_detail(provider, item.offer_id)
        if not _result_ok(result):
            raise RuntimeError(_result_error_message(result))
        self._raise_if_stopping()
        normalized = self._detail_normalizer(item, result)
        if isinstance(normalized, DailySelectionCandidate):
            candidate = normalized.model_dump(mode="json")
        elif isinstance(normalized, Mapping):
            candidate = dict(normalized)
        else:
            raise TypeError("detail normalizer returned an invalid candidate")
        self._raise_if_stopping()
        if not self.repository.renew_item_lease(
            batch_id=batch.batch_id, item_id=item.item_id, owner=item.lease_owner,
            lease_token=item.lease_token, lease_seconds=self.ITEM_LEASE_SECONDS,
        ):
            raise _StaleItemLease("item lease expired or was reclaimed before intake")
        intake = self._intake(
            batch_id=batch.batch_id,
            workspace_id=batch.workspace_id,
            candidate=candidate,
        )
        action = str(intake.get("action") or "")
        if action not in {"created", "refreshed", "skipped"}:
            raise ValueError("product intake returned an invalid action")
        return candidate, action

    def _call_detail(self, provider: Any, offer_id: str) -> Any:
        result = None
        for attempt in range(3):
            result = provider.get_item_detail(offer_id)
            if _result_ok(result):
                return result
            code = _result_error_code(result)
            if code not in _TRANSIENT_ERRORS or attempt == 2:
                return result
            if self._retry_delay_seconds:
                if self._stop.wait(self._retry_delay_seconds * (attempt + 1)):
                    raise _WorkerStopping()
        return result

    def _budgeted_provider(
        self,
        provider: Any,
        *,
        batch_id: str,
        workspace_id: str,
        provider_fingerprint: str,
    ) -> Any:
        def reserve(operation: str) -> None:
            state = self._budget.reserve(
                workspace_id=workspace_id,
                provider_fingerprint=provider_fingerprint,
                max_api_calls=self._max_api_calls,
                api_calls=1,
            )
            granted = bool(getattr(state, "reservation_granted", False))
            self.repository.record_api_call_reservation(
                batch_id=batch_id,
                workspace_id=workspace_id,
                operation=operation,
                reservation_granted=granted,
            )
            if not granted:
                raise ShopApiBudgetExhausted(
                    f"OneBound API budget exhausted before {operation}"
                )

        installer = getattr(provider, "install_api_call_guard", None)
        if callable(installer):
            installer(reserve)
            return provider
        return _BudgetedProviderProxy(provider, reserve)

    def _apply_control_state(self, batch: Any, lease: ShopBatchLease) -> bool:
        if batch.status == "pausing":
            self._transition(lease, "paused", {"pausing"})
            return True
        if batch.status == "cancelling":
            self.repository.cancel_pending_items(batch_id=batch.batch_id)
            self._transition(lease, "cancelled", {"cancelling"})
            return True
        return batch.status in {"paused", "cancelled", "completed", "partial", "failed"}

    def _transition(self, lease: ShopBatchLease, status: str, expected: set[str]) -> Any:
        return self.repository.transition_batch(
            lease.batch_id, status, expected_statuses=expected, owner=lease.lease_owner,
            lease_token=lease.lease_token,
        )

    def _renew(self, lease: ShopBatchLease) -> None:
        if not self.repository.renew_batch_lease(
            batch_id=lease.batch_id, owner=lease.lease_owner, lease_token=lease.lease_token,
            lease_seconds=self.BATCH_LEASE_SECONDS,
        ):
            raise ShopLeaseLost("batch lease expired or was reclaimed")

    def _raise_if_stopping(self) -> None:
        if self._stop.is_set():
            raise _WorkerStopping()

    def _run(self) -> None:
        while not self._stop.is_set():
            lease = self.repository.claim_next_runnable_batch(
                owner=self._owner, lease_seconds=self.BATCH_LEASE_SECONDS
            )
            if lease is None:
                self._wake.wait(timeout=2)
                self._wake.clear()
                continue
            self.process_batch(lease.batch_id, lease=lease)


def _default_page_normalizer(payload: Mapping[str, Any], evidence: Any = None) -> Any:
    from .shop_parsing import normalize_shop_page

    return normalize_shop_page(payload, evidence)


class _BudgetedProviderProxy:
    """Apply the same guard to injected providers used outside OneBound's client."""

    def __init__(self, provider: Any, reserve: Callable[[str], None]) -> None:
        self._provider = provider
        self._reserve = reserve

    def search_shop(self, seller_nick: str, page: int) -> Any:
        self._reserve("item_search_shop")
        return self._provider.search_shop(seller_nick, page)

    def get_item_detail(self, offer_id: str) -> Any:
        self._reserve("item_get")
        return self._provider.get_item_detail(offer_id)


def _default_detail_normalizer(item: Any, result: Any) -> DailySelectionCandidate:
    return normalize_detail_response(
        _result_response(result), evidence=getattr(result, "audit", None)
    )


def _page_values(page: Any) -> tuple[Mapping[str, Any], ...]:
    raw = _value(page, "items", ())
    if not raw:
        raw = tuple({"offer_id": offer_id} for offer_id in _value(page, "offer_ids", ()))
    values: list[Mapping[str, Any]] = []
    for item in raw:
        if isinstance(item, Mapping):
            data = dict(item)
        elif hasattr(item, "model_dump"):
            data = item.model_dump(mode="python")
        else:
            continue
        data["offer_id"] = str(data.get("offer_id") or data.get("id") or data.get("num_iid") or "")
        data["source_url"] = str(data.get("source_url") or data.get("detail_url") or data.get("url") or "")
        values.append(data)
    return tuple(values)


def _page_has_next(page: Any, *, response: Mapping[str, Any], current_page: int) -> bool:
    explicit = _value(page, "has_next", None)
    if isinstance(explicit, bool):
        return explicit
    total_pages = _value(page, "total_pages", None)
    if isinstance(total_pages, int) and not isinstance(total_pages, bool):
        return current_page < min(max(0, total_pages), 100)
    container = response.get("items")
    if not isinstance(container, Mapping):
        data = response.get("data")
        container = data if isinstance(data, Mapping) else response
    total = _non_negative_int(container.get("total_results"))
    size = _non_negative_int(container.get("page_size"))
    if total and size:
        pages = min(100, (total + size - 1) // size)
        return current_page < pages
    # Without trustworthy pagination metadata, an empty page is terminal.
    return bool(_page_values(page))


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _value(value: Any, name: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _result_ok(result: Any) -> bool:
    return bool(getattr(result, "ok", False))


def _result_response(result: Any) -> Mapping[str, Any]:
    value = getattr(result, "response", {})
    return value if isinstance(value, Mapping) else {}


def _result_error_code(result: Any) -> str:
    error = getattr(result, "error", None)
    return str(getattr(error, "code", "upstream_failed") or "upstream_failed")


def _result_error_message(result: Any) -> str:
    error = getattr(result, "error", None)
    return str(getattr(error, "message", "OneBound request failed") or "OneBound request failed")


def _seller_info(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    item = payload.get("item")
    if not isinstance(item, Mapping):
        data = payload.get("data")
        item = data if isinstance(data, Mapping) else payload
    seller = item.get("seller_info") if isinstance(item, Mapping) else None
    return seller if isinstance(seller, Mapping) else {}


def _error_code(error: Exception) -> str:
    return str(getattr(error, "code", "worker_failed") or "worker_failed")[:80]


def _error_message(error: Exception) -> str:
    text = str(error or "shop collection failed")
    lowered = text.casefold()
    if any(marker in lowered for marker in ("api_key", "api_secret", "secret=", "token=", "authorization")):
        return "shop collection request failed"
    return text[:500]
