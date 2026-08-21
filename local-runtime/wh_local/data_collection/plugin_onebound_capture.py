"""Session-scoped, short-lived 1688 capture batches for the browser plugin.

The browser sends only public 1688 URLs.  Provider credentials, the provider
instance, and daily budget guard stay in this local runtime and are created at
most once for a started batch.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query
from pydantic import ValidationError

from .budget import credential_fingerprint
from .link_collection import canonical_1688_offer_url
from .normalizer import normalize_detail_response
from .plugin_queue import DataCollectionPluginQueue
from .plugin_onebound_capture_repository import PluginOneBoundCaptureRepository
from .service import DailySelectionActor


_TTL_SECONDS = 30 * 60
_MAX_BATCHES_PER_IDENTITY = 2
_MAX_URLS_PER_BATCH = 80
_MAX_API_CALLS = 300
_MAX_DETAIL_CONCURRENCY = 3
_DETAIL_CALL_SEMAPHORE = threading.BoundedSemaphore(_MAX_DETAIL_CONCURRENCY)


@dataclass(frozen=True)
class PluginOneBoundCaptureDependencies:
    """Host-owned adapters shared with the other data-collection paths."""

    plugin_queue: DataCollectionPluginQueue
    provider_config_resolver: Callable[[DailySelectionActor], Mapping[str, Any]]
    provider_factory: Callable[[Mapping[str, Any]], Any]
    budget: Any
    draft_writer: Any
    database_path: str | None = None
    resolve_actor: Callable[..., Any] | None = None


@dataclass
class _Item:
    offer_id: str
    source_url: str
    source_title: str = ""
    status: str = "pending"
    outcome: str = ""
    draft_id: int | None = None
    error_code: str = ""
    message: str = ""


@dataclass
class _Batch:
    token: str
    batch_id: str
    actor_id: str
    workspace_id: str
    page_url: str
    expires_at: float
    expires_at_text: str
    items: dict[str, _Item]
    existing_offer_ids: tuple[str, ...]
    provider: tuple[Any, Callable[[str], None]] | None = None
    provider_has_guard: bool = False
    started: bool = False
    closing: bool = False
    finished: bool = False
    running_items: int = 0
    fatal_code: str = ""
    fatal_message: str = ""
    final_summary: Mapping[str, Any] | None = None
    materialization_claimed: bool = False
    execution_claimed: bool = False
    expiry_timer: Any | None = None
    condition: threading.Condition = field(default_factory=lambda: threading.Condition(threading.RLock()))


@dataclass
class _CompletedBatch:
    actor_id: str
    workspace_id: str
    summary: Mapping[str, Any]
    cleanup_timer: Any | None = None


class PluginOneBoundCaptureService:
    """Owns only ephemeral batch state; product processing owns all drafts."""

    def __init__(
        self,
        dependencies: PluginOneBoundCaptureDependencies,
        *,
        ttl_seconds: float = _TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        timer_factory: Callable[[float, Callable[[], None]], Any] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._dependencies = dependencies
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._timer_factory = timer_factory
        self._repository = PluginOneBoundCaptureRepository(dependencies.database_path) if dependencies.database_path else None
        self._batches: dict[str, _Batch] = {}
        self._completed: dict[str, _CompletedBatch] = {}
        self._lock = threading.RLock()

    def prepare(self, *, session_token: str, page_url: str, source_urls: list[str]) -> Mapping[str, Any]:
        identity = self._identity(session_token)
        return self._prepare_for_identity(identity, page_url=page_url, source_urls=source_urls)

    def _prepare_for_identity(
        self, identity: Mapping[str, str], *, page_url: str, source_urls: list[str], parent_batch_id: str = ""
    ) -> Mapping[str, Any]:
        if not isinstance(page_url, str) or not page_url.strip():
            raise ValueError("page_url is required")
        if not isinstance(source_urls, list) or not source_urls:
            raise ValueError("source_urls must contain at least one URL")
        normalized: dict[str, tuple[str, str]] = {}
        for source_url in source_urls:
            canonical_url, offer_id = canonical_1688_offer_url(source_url)
            normalized.setdefault(offer_id, (canonical_url, offer_id))

        prepared: dict[str, _Item] = {}
        persisted_items: list[dict[str, str]] = []
        existing: list[str] = []
        for canonical_url, offer_id in tuple(normalized.values())[:_MAX_URLS_PER_BATCH]:
            draft = _onebound_draft_by_candidate(
                self._dependencies.draft_writer.repository,
                f"1688:{offer_id}",
                identity["workspace_id"],
            )
            if _is_active_onebound_draft(draft):
                existing.append(offer_id)
                persisted_items.append({"offer_id": offer_id, "source_url": canonical_url, "status": "skipped", "outcome": "skipped"})
                continue
            prepared[offer_id] = _Item(offer_id=offer_id, source_url=canonical_url)
            persisted_items.append({"offer_id": offer_id, "source_url": canonical_url})

        now = self._clock()
        expires_at = now + self._ttl_seconds
        expiry_text = (datetime.now(timezone.utc) + timedelta(seconds=self._ttl_seconds)).isoformat()
        with self._lock:
            active = sum(
                1
                for batch in self._batches.values()
                if (
                    batch.actor_id == identity["actor_id"]
                    and batch.workspace_id == identity["workspace_id"]
                    and not batch.finished
                )
            )
            if active >= _MAX_BATCHES_PER_IDENTITY:
                raise RuntimeError("at most two active capture batches are allowed")
            token = secrets.token_urlsafe(24)
            batch_id = str(uuid.uuid4())
            batch = _Batch(
                token=token,
                batch_id=batch_id,
                actor_id=identity["actor_id"],
                workspace_id=identity["workspace_id"],
                page_url=page_url.strip(),
                expires_at=expires_at,
                expires_at_text=expiry_text,
                items=prepared,
                existing_offer_ids=tuple(existing),
            )
            self._batches[token] = batch
        if self._repository is not None:
            try:
                self._repository.create(
                    batch_id=batch.batch_id, actor_id=batch.actor_id, workspace_id=batch.workspace_id,
                    parent_batch_id=parent_batch_id,
                    page_url=batch.page_url, items=tuple(persisted_items),
                )
            except Exception:
                with self._lock:
                    if self._batches.get(token) is batch:
                        self._batches.pop(token, None)
                raise
        timer = self._start_timer(self._ttl_seconds, lambda: self._expire_batch(token))
        with self._lock:
            if self._batches.get(token) is batch:
                batch.expiry_timer = timer
            elif hasattr(timer, "cancel"):
                timer.cancel()
        pending_urls = [item.source_url for item in prepared.values()]
        return {
            "ok": True,
            "batch_token": token,
            "batch_id": batch_id,
            "total_count": len(prepared) + len(existing),
            "existing_count": len(existing),
            "pending_count": len(prepared),
            "pending_urls": pending_urls,
            "existing_offer_ids": existing,
            "expires_at": expiry_text,
            "statusText": "批次已准备",
        }

    def start(self, *, session_token: str, batch_token: str) -> Mapping[str, Any]:
        return self._start_for_identity(self._identity(session_token), batch_token)

    def _start_for_identity(self, identity: Mapping[str, str], batch_token: str) -> Mapping[str, Any]:
        batch = self._owned_batch_for_identity(identity, batch_token)
        with batch.condition:
            self._raise_if_unavailable(batch)
            if not batch.started:
                try:
                    actor = DailySelectionActor(
                        actor_id=identity["actor_id"], workspace_id=identity["workspace_id"]
                    )
                    config = self._dependencies.provider_config_resolver(actor)
                    if not isinstance(config, Mapping):
                        raise ValueError("provider configuration is unavailable")
                    provider = self._dependencies.provider_factory(config)
                    fingerprint = credential_fingerprint(config)

                    def reserve(_operation: str) -> None:
                        state = self._dependencies.budget.reserve(
                            workspace_id=batch.workspace_id,
                            provider_fingerprint=fingerprint,
                            max_api_calls=_MAX_API_CALLS,
                            api_calls=1,
                        )
                        if not bool(getattr(state, "reservation_granted", False)):
                            raise _BudgetExhausted()

                    installer = getattr(provider, "install_api_call_guard", None)
                    if callable(installer):
                        installer(reserve)
                        batch.provider_has_guard = True
                    batch.provider = (provider, reserve)
                    batch.started = True
                    if self._repository is not None:
                        self._repository.set_status(batch.batch_id, "running")
                except Exception:
                    batch.provider = None
                    batch.provider_has_guard = False
                    self._set_fatal(batch, "start_failed", "1688 采集服务启动失败")
                    raise _BatchFatal(batch.fatal_code, batch.fatal_message) from None
            return {"ok": True, "batch_token": batch.token, "statusText": "批次已启动"}

    def item(self, *, session_token: str, batch_token: str, source_url: str) -> Mapping[str, Any]:
        return self._item_for_identity(self._identity(session_token), batch_token, source_url)

    def _item_for_identity(self, identity: Mapping[str, str], batch_token: str, source_url: str) -> Mapping[str, Any]:
        batch = self._owned_batch_for_identity(identity, batch_token)
        canonical_url, offer_id = canonical_1688_offer_url(source_url)
        with batch.condition:
            self._raise_if_unavailable(batch)
            if not batch.started:
                raise RuntimeError("capture batch has not been started")
            item = batch.items.get(offer_id)
            if item is None or item.source_url != canonical_url:
                raise LookupError("source_url is not pending in this capture batch")
            while item.status == "running":
                batch.condition.wait()
            if item.status in {"succeeded", "failed", "skipped"}:
                return _item_response(item)
            item.status = "running"
            batch.running_items += 1
            if self._repository is not None:
                self._repository.update_item(batch.batch_id, item.offer_id, status="running", increment_attempt=True)

        try:
            response = self._capture_item(batch, item)
        except _BudgetExhausted:
            with batch.condition:
                _failure(item, "api_budget_exhausted", "今日采集额度已用完")
                self._set_fatal(batch, "api_budget_exhausted", "今日采集额度已用完")
            raise _BatchFatal("api_budget_exhausted", "今日采集额度已用完") from None
        except Exception:
            # Provider detail errors remain per-item diagnostics.  Do not expose
            # arbitrary upstream text because it can contain request metadata.
            with batch.condition:
                response = _failure(item, "capture_failed", "1688 商品详情采集失败")
        finally:
            with batch.condition:
                if item.status == "running":
                    item.status = "failed"
                    item.error_code = "capture_failed"
                    item.message = "1688 商品详情采集失败"
                batch.running_items -= 1
                batch.condition.notify_all()
            self._persist_item(batch, item)
        return response

    def finish(self, *, session_token: str, batch_token: str, cancelled: bool) -> Mapping[str, Any]:
        """Finalize a batch without performing media I/O on the caller thread."""
        summary, _materialize = self.finish_deferred(
            session_token=session_token, batch_token=batch_token, cancelled=cancelled
        )
        return summary

    def finish_deferred(
        self, *, session_token: str, batch_token: str, cancelled: bool
    ) -> tuple[Mapping[str, Any], bool]:
        """Return the authoritative summary and the one-time materialization claim."""
        return self._finish_for_identity(self._identity(session_token), batch_token, cancelled)

    def _finish_for_identity(
        self, identity: Mapping[str, str], batch_token: str, cancelled: bool
    ) -> tuple[Mapping[str, Any], bool]:
        completed = self._completed_summary(identity, batch_token)
        if completed is not None:
            return completed, False
        batch = self._owned_batch_for_identity(identity, batch_token)
        return self._finalize_batch(batch, cancelled=cancelled, retain_summary=True)

    def materialize(self, workspace_id: str) -> Any:
        return self._dependencies.draft_writer.media_assets.materialize_until_idle(
            workspace_id=workspace_id
        )

    def materialize_best_effort(self, workspace_id: str) -> None:
        try:
            self.materialize(workspace_id)
        except Exception:
            # Media materialization is retryable V2 follow-up work; its failure
            # must never rewrite the already-authoritative capture summary.
            return None

    def prepare_persistent_start(
        self, *, actor_id: str, workspace_id: str, batch_id: str
    ) -> Mapping[str, Any]:
        """Claim one prepared persistent batch for workbench-owned execution."""
        if self._repository is None:
            raise LookupError("persistent capture storage is unavailable")
        persistent = self._repository.get(workspace_id=workspace_id, batch_id=batch_id)
        if persistent is None:
            raise LookupError("capture batch not found")
        if str(persistent.get("status") or "") != "prepared":
            raise ValueError("only a prepared capture batch can be started")
        persisted_items = self._repository.items(
            workspace_id=workspace_id, batch_id=batch_id, limit=_MAX_URLS_PER_BATCH, offset=0
        )
        restored = False
        with self._lock:
            batch = next(
                (
                    candidate
                    for candidate in self._batches.values()
                    if candidate.batch_id == batch_id
                    and candidate.actor_id == actor_id
                    and candidate.workspace_id == workspace_id
                    and not candidate.finished
                ),
                None,
            )
            if batch is None:
                token = secrets.token_urlsafe(24)
                now = self._clock()
                expiry_text = (datetime.now(timezone.utc) + timedelta(seconds=self._ttl_seconds)).isoformat()
                pending = {
                    str(item["offer_id"]): _Item(
                        offer_id=str(item["offer_id"]),
                        source_url=str(item["source_url"]),
                        source_title=str(item.get("source_title") or ""),
                    )
                    for item in persisted_items
                    if str(item.get("status") or "pending") in {"pending", "unprocessed"}
                }
                batch = _Batch(
                    token=token,
                    batch_id=batch_id,
                    actor_id=actor_id,
                    workspace_id=workspace_id,
                    page_url=str(persistent.get("page_url") or ""),
                    expires_at=now + self._ttl_seconds,
                    expires_at_text=expiry_text,
                    items=pending,
                    existing_offer_ids=tuple(
                        str(item["offer_id"])
                        for item in persisted_items
                        if str(item.get("status") or "") == "skipped"
                    ),
                )
                self._batches[token] = batch
                restored = True
        if restored:
            timer = self._start_timer(self._ttl_seconds, lambda: self._expire_batch(batch.token))
            with self._lock:
                if self._batches.get(batch.token) is batch:
                    batch.expiry_timer = timer
                elif hasattr(timer, "cancel"):
                    timer.cancel()
        with batch.condition:
            if batch.execution_claimed:
                raise ValueError("capture batch is already queued")
            if batch.closing or batch.finished:
                raise ValueError("capture batch is already finished")
            batch.execution_claimed = True
        self._repository.set_status(batch.batch_id, "queued")
        return {
            "batch": self._repository.get(workspace_id=workspace_id, batch_id=batch_id),
            "batch_token": batch.token,
            "execute": True,
        }

    def execute_persistent_batch(
        self, *, actor_id: str, workspace_id: str, batch_token: str
    ) -> None:
        """Run a workbench-started batch; a provider-start failure stays retryable."""
        identity = {"actor_id": actor_id, "workspace_id": workspace_id}
        try:
            self._start_for_identity(identity, batch_token)
        except Exception:
            try:
                batch = self._owned_batch_for_identity(identity, batch_token)
            except Exception:
                return
            with batch.condition:
                batch.provider = None
                batch.provider_has_guard = False
                batch.started = False
                batch.execution_claimed = False
                batch.fatal_code = ""
                batch.fatal_message = ""
                batch.condition.notify_all()
            if self._repository is not None:
                self._repository.set_status(
                    batch.batch_id,
                    "prepared",
                    error_code="start_failed",
                    error_message="万邦临时凭据申请失败，请稍后重新启动",
                )
            return

        try:
            self._execute_pending_items(identity, batch_token)
        except Exception:
            try:
                batch = self._owned_batch_for_identity(identity, batch_token)
                with batch.condition:
                    self._set_fatal(batch, "execution_failed", "万邦批次执行失败")
            except Exception:
                return
        finally:
            try:
                _summary, materialize = self._finish_for_identity(identity, batch_token, False)
                if materialize:
                    self.materialize_best_effort(workspace_id)
            except Exception:
                return

    def prepare_retry_child(self, *, actor_id: str, workspace_id: str, batch_id: str) -> Mapping[str, Any]:
        """Synchronously validate and create a child; never call the provider here."""
        if self._repository is None:
            raise LookupError("persistent capture storage is unavailable")
        with self._lock:
            parent = self._repository.get(workspace_id=workspace_id, batch_id=batch_id)
            if parent is None:
                raise LookupError("capture batch not found")
            if str(parent.get("status") or "") not in {"completed", "partial", "cancelled", "failed", "expired"}:
                raise ValueError("capture batch must be finished before retry")
            existing = self._repository.retry_child(workspace_id=workspace_id, parent_batch_id=batch_id)
            if existing is not None:
                child_status = str(existing.get("status") or "")
                if child_status in {"completed", "partial"}:
                    return {"batch": existing, "batch_token": "", "execute": False}
                if child_status in {"prepared", "queued", "running"}:
                    token = next(
                        (
                            candidate_token
                            for candidate_token, candidate_batch in self._batches.items()
                            if candidate_batch.batch_id == existing["batch_id"]
                            and candidate_batch.actor_id == actor_id
                            and candidate_batch.workspace_id == workspace_id
                        ),
                        "",
                    )
                    if token:
                        return {"batch": existing, "batch_token": token, "execute": False}
                    raise ValueError("retry child is still active; wait for expiry before retrying")
            urls = self._repository.failed_urls(workspace_id=workspace_id, batch_id=batch_id)
            if not urls:
                raise ValueError("capture batch has no failed URLs")
            identity = {"actor_id": actor_id, "workspace_id": workspace_id}
            child = self._prepare_for_identity(
                identity, page_url=urls[0], source_urls=list(urls), parent_batch_id=batch_id
            )
            token = str(child["batch_token"])
            return {
                "batch": self._repository.get(workspace_id=workspace_id, batch_id=str(child["batch_id"])),
                "batch_token": token,
                "execute": True,
            }

    def execute_retry_child(self, *, actor_id: str, workspace_id: str, batch_token: str) -> None:
        identity = {"actor_id": actor_id, "workspace_id": workspace_id}
        try:
            self._start_for_identity(identity, batch_token)
            self._execute_pending_items(identity, batch_token)
        except Exception:
            try:
                batch = self._owned_batch_for_identity(identity, batch_token)
                with batch.condition:
                    self._set_fatal(batch, "retry_execution_failed", "服务器重试执行失败")
            except Exception:
                return
        finally:
            try:
                _summary, materialize = self._finish_for_identity(identity, batch_token, False)
                if materialize:
                    self.materialize_best_effort(workspace_id)
            except Exception:
                return

    def retry_failed(self, *, actor_id: str, workspace_id: str, batch_id: str) -> Mapping[str, Any]:
        """Compatibility helper for callers that explicitly need synchronous execution."""
        child = self.prepare_retry_child(actor_id=actor_id, workspace_id=workspace_id, batch_id=batch_id)
        token = str(child.pop("batch_token"))
        if child.pop("execute", False):
            self.execute_retry_child(actor_id=actor_id, workspace_id=workspace_id, batch_token=token)
        return child

    def _execute_pending_items(
        self, identity: Mapping[str, str], batch_token: str
    ) -> None:
        """Dispatch with three workers while the module semaphore caps all batches."""
        batch = self._owned_batch_for_identity(identity, batch_token)
        iterator = iter(tuple(batch.items.values()))
        iterator_lock = threading.Lock()
        stop = threading.Event()

        def worker() -> None:
            while not stop.is_set():
                with iterator_lock:
                    try:
                        item = next(iterator)
                    except StopIteration:
                        return
                try:
                    self._item_for_identity(identity, batch_token, item.source_url)
                except _BatchFatal:
                    stop.set()
                    return

        worker_count = min(_MAX_DETAIL_CONCURRENCY, len(batch.items))
        if worker_count <= 0:
            return
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="onebound-item") as executor:
            futures = [executor.submit(worker) for _ in range(worker_count)]
            for future in futures:
                future.result()


    def _capture_item(self, batch: _Batch, item: _Item) -> Mapping[str, Any]:
        if batch.provider is None:
            raise RuntimeError("capture batch provider is unavailable")
        provider, reserve = batch.provider
        if not batch.provider_has_guard:
            reserve("item_get")
        # Production OneBound has its own guard, but this boundary also covers
        # injected/fake providers and concurrent plugin batches.
        with _DETAIL_CALL_SEMAPHORE:
            result = provider.get_item_detail(item.offer_id)
        if not bool(getattr(result, "ok", False)):
            raise RuntimeError("provider item detail failed")
        candidate = normalize_detail_response(
            getattr(result, "response"), evidence=getattr(result, "audit", None)
        ).model_dump(mode="python")
        if str(candidate.get("offer_id", "")) != item.offer_id:
            raise ValueError("provider returned a mismatched offer")
        candidate["candidate_id"] = f"1688:{item.offer_id}"
        item.source_title = _candidate_source_title(candidate)
        intake = self._dependencies.draft_writer.intake_shop_candidate(
            batch_id=batch.batch_id,
            workspace_id=batch.workspace_id,
            candidate=candidate,
        )
        action = str(intake.get("action") or "created")
        draft = intake.get("draft")
        item.status = "skipped" if action == "skipped" else "succeeded"
        item.outcome = action
        item.draft_id = int(draft["id"]) if isinstance(draft, Mapping) and draft.get("id") is not None else None
        item.message = "商品已写入草稿"
        return _item_response(item)

    def _identity(self, session_token: str) -> Mapping[str, str]:
        if not isinstance(session_token, str) or not session_token.strip():
            raise PermissionError("session_token is required")
        return self._dependencies.plugin_queue.identity_for_session(session_token)

    def _owned_batch(self, session_token: str, batch_token: str) -> tuple[_Batch, Mapping[str, str]]:
        identity = self._identity(session_token)
        return self._owned_batch_for_identity(identity, batch_token), identity

    def _owned_batch_for_identity(self, identity: Mapping[str, str], batch_token: str) -> _Batch:
        if not isinstance(batch_token, str) or not batch_token.strip():
            raise ValueError("batch_token is required")
        with self._lock:
            batch = self._batches.get(batch_token)
        if batch is None or batch.actor_id != identity["actor_id"] or batch.workspace_id != identity["workspace_id"]:
            raise LookupError("capture batch not found")
        return batch

    def _raise_if_unavailable(self, batch: _Batch) -> None:
        if batch.fatal_code:
            raise _BatchFatal(batch.fatal_code, batch.fatal_message)
        if batch.closing or batch.finished:
            raise _BatchClosed("capture batch is closing or finished")

    def _set_fatal(self, batch: _Batch, code: str, message: str) -> None:
        batch.fatal_code = code
        batch.fatal_message = message

    def _finalize_batch(
        self, batch: _Batch, *, cancelled: bool, retain_summary: bool
    ) -> tuple[Mapping[str, Any], bool]:
        with batch.condition:
            if batch.final_summary is not None:
                return batch.final_summary, False
            if batch.closing:
                while batch.final_summary is None:
                    batch.condition.wait()
                return batch.final_summary, False
            batch.closing = True
            while batch.running_items:
                batch.condition.wait()
            for item in batch.items.values():
                if item.status in {"pending", "running"}:
                    item.status = "unprocessed"
                    item.outcome = "unprocessed"
                    item.message = "批次已结束，未执行"
            batch.final_summary = _finish_summary(batch, cancelled=cancelled)
            batch.finished = True
            batch.provider = None
            batch.provider_has_guard = False
            materialize = not batch.materialization_claimed
            batch.materialization_claimed = True
            batch.condition.notify_all()
        if self._repository is not None:
            for item in batch.items.values():
                self._persist_item(batch, item)
            persistent_status = "expired"
            if retain_summary:
                if cancelled:
                    persistent_status = "cancelled"
                elif batch.fatal_code:
                    persistent_status = "failed"
                elif batch.final_summary.get("failed_count") or batch.final_summary.get("unprocessed_count"):
                    persistent_status = "partial"
                else:
                    persistent_status = "completed"
            self._repository.set_status(
                batch.batch_id,
                persistent_status,
                cancelled=cancelled,
                error_code=batch.fatal_code,
                error_message=batch.fatal_message,
                summary=batch.final_summary,
            )
        self._cancel_timer(batch)
        completed: _CompletedBatch | None = None
        with self._lock:
            if self._batches.get(batch.token) is batch:
                self._batches.pop(batch.token, None)
            if retain_summary:
                completed = _CompletedBatch(
                    actor_id=batch.actor_id,
                    workspace_id=batch.workspace_id,
                    summary=batch.final_summary,
                )
                self._completed[batch.token] = completed
        if completed is not None:
            completed.cleanup_timer = self._start_timer(
                self._ttl_seconds, lambda: self._discard_completed(batch.token)
            )
        return batch.final_summary, materialize

    def _expire_batch(self, batch_token: str) -> None:
        with self._lock:
            batch = self._batches.get(batch_token)
        if batch is None:
            return
        _summary, materialize = self._finalize_batch(
            batch, cancelled=True, retain_summary=False
        )
        if materialize:
            self.materialize_best_effort(batch.workspace_id)

    def _completed_summary(
        self, identity: Mapping[str, str], batch_token: str
    ) -> Mapping[str, Any] | None:
        with self._lock:
            completed = self._completed.get(batch_token)
        if completed is None:
            return None
        if (
            completed.actor_id != identity["actor_id"]
            or completed.workspace_id != identity["workspace_id"]
        ):
            raise LookupError("capture batch not found")
        return completed.summary

    def _discard_completed(self, batch_token: str) -> None:
        with self._lock:
            self._completed.pop(batch_token, None)

    def _persist_item(self, batch: _Batch, item: _Item) -> None:
        if self._repository is not None:
            self._repository.update_item(
                batch.batch_id, item.offer_id, status=item.status, outcome=item.outcome,
                draft_id=item.draft_id, source_title=item.source_title, error_code=item.error_code,
                error_message=item.message,
            )

    def _start_timer(self, delay: float, callback: Callable[[], None]) -> Any:
        timer = self._timer_factory(delay, callback) if self._timer_factory else threading.Timer(delay, callback)
        if hasattr(timer, "daemon"):
            timer.daemon = True
        timer.start()
        return timer

    @staticmethod
    def _cancel_timer(batch: _Batch) -> None:
        timer = batch.expiry_timer
        if timer is not None and hasattr(timer, "cancel"):
            timer.cancel()
        batch.expiry_timer = None


class _BudgetExhausted(RuntimeError):
    pass


class _BatchFatal(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _BatchClosed(RuntimeError):
    pass


def register_plugin_onebound_capture_routes(
    router: APIRouter, dependencies: PluginOneBoundCaptureDependencies
) -> PluginOneBoundCaptureService:
    """Register the four browser capture endpoints without expanding legacy APIs."""
    service = PluginOneBoundCaptureService(dependencies)

    @router.post("/plugin/product-capture/onebound-batches/prepare")
    def prepare(payload: Mapping[str, Any] = Body(...)) -> Mapping[str, Any]:
        try:
            source_urls = payload.get("source_urls", payload.get("links"))
            return service.prepare(
                session_token=_required_text(payload.get("session_token"), "session_token"),
                page_url=_required_text(payload.get("page_url"), "page_url"),
                source_urls=list(source_urls) if isinstance(source_urls, list) else source_urls,
            )
        except PermissionError as error:
            raise HTTPException(status_code=401, detail="invalid plugin session") from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/plugin/product-capture/onebound-batches/start")
    def start(payload: Mapping[str, Any] = Body(...)) -> Mapping[str, Any]:
        try:
            return service.start(
                session_token=_required_text(payload.get("session_token"), "session_token"),
                batch_token=_required_text(payload.get("batch_token", payload.get("batch_id")), "batch_token"),
            )
        except PermissionError as error:
            raise HTTPException(status_code=401, detail="invalid plugin session") from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except _BatchFatal as error:
            raise HTTPException(status_code=503, detail=error.code) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail="unable to start capture batch") from error

    @router.post("/plugin/product-capture/onebound-batches/item")
    def item(payload: Mapping[str, Any] = Body(...)) -> Mapping[str, Any]:
        try:
            return service.item(
                session_token=_required_text(payload.get("session_token"), "session_token"),
                batch_token=_required_text(payload.get("batch_token", payload.get("batch_id")), "batch_token"),
                source_url=_required_text(payload.get("source_url"), "source_url"),
            )
        except PermissionError as error:
            raise HTTPException(status_code=401, detail="invalid plugin session") from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except _BatchFatal as error:
            status = 429 if error.code == "api_budget_exhausted" else 503
            raise HTTPException(status_code=status, detail=error.code) from error
        except _BatchClosed as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/plugin/product-capture/onebound-batches/finish")
    def finish(
        background_tasks: BackgroundTasks,
        payload: Mapping[str, Any] = Body(...),
    ) -> Mapping[str, Any]:
        try:
            session_token = _required_text(payload.get("session_token"), "session_token")
            summary, materialize = service.finish_deferred(
                session_token=session_token,
                batch_token=_required_text(payload.get("batch_token", payload.get("batch_id")), "batch_token"),
                cancelled=bool(payload.get("cancelled", False)),
            )
            if materialize:
                workspace_id = service._identity(session_token)["workspace_id"]
                background_tasks.add_task(service.materialize_best_effort, workspace_id)
            return summary
        except PermissionError as error:
            raise HTTPException(status_code=401, detail="invalid plugin session") from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    if dependencies.resolve_actor is not None and service._repository is not None:
        prefix = "/desktop/data-collection/plugin-onebound-batches"

        def desktop_actor(value: Any = Depends(dependencies.resolve_actor)) -> DailySelectionActor:
            try:
                return DailySelectionActor.model_validate(value)
            except (ValidationError, TypeError, ValueError) as error:
                actor_id = getattr(value, "actor_id", None) or getattr(value, "id", None)
                workspace_id = getattr(value, "workspace_id", None)
                if isinstance(actor_id, str) and actor_id.strip() and isinstance(workspace_id, str) and workspace_id.strip():
                    return DailySelectionActor(actor_id=actor_id, workspace_id=workspace_id)
                raise HTTPException(status_code=401, detail="authenticated workspace required") from error

        @router.get(prefix)
        def list_persistent_batches(
            limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0),
            actor: DailySelectionActor = Depends(desktop_actor),
        ) -> Mapping[str, Any]:
            items = service._repository.list(workspace_id=actor.workspace_id, limit=limit, offset=offset)
            return {"items": items, "total": service._repository.count(workspace_id=actor.workspace_id), "limit": limit, "offset": offset}

        @router.get(prefix + "/{batch_id}")
        def get_persistent_batch(batch_id: str, actor: DailySelectionActor = Depends(desktop_actor)) -> Mapping[str, Any]:
            batch = service._repository.get(workspace_id=actor.workspace_id, batch_id=batch_id)
            if batch is None:
                raise HTTPException(status_code=404, detail="capture batch not found")
            return {"batch": batch}

        @router.get(prefix + "/{batch_id}/items")
        def get_persistent_items(
            batch_id: str, limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0),
            actor: DailySelectionActor = Depends(desktop_actor),
        ) -> Mapping[str, Any]:
            batch = service._repository.get(workspace_id=actor.workspace_id, batch_id=batch_id)
            if batch is None:
                raise HTTPException(status_code=404, detail="capture batch not found")
            items = service._repository.items(workspace_id=actor.workspace_id, batch_id=batch_id, limit=limit, offset=offset)
            return {"items": items, "total": service._repository.count_items(workspace_id=actor.workspace_id, batch_id=batch_id), "limit": limit, "offset": offset}

        @router.post(prefix + "/{batch_id}/start", status_code=202)
        def start_persistent_batch(
            background_tasks: BackgroundTasks,
            batch_id: str,
            actor: DailySelectionActor = Depends(desktop_actor),
        ) -> Mapping[str, Any]:
            try:
                claimed = service.prepare_persistent_start(
                    actor_id=actor.actor_id,
                    workspace_id=actor.workspace_id,
                    batch_id=batch_id,
                )
                token = str(claimed.pop("batch_token"))
                if claimed.pop("execute", False):
                    background_tasks.add_task(
                        service.execute_persistent_batch,
                        actor_id=actor.actor_id,
                        workspace_id=actor.workspace_id,
                        batch_token=token,
                    )
                return claimed
            except LookupError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except (ValueError, RuntimeError) as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

        @router.post(prefix + "/{batch_id}/retry-failed", status_code=202)
        def retry_persistent_failed(background_tasks: BackgroundTasks, batch_id: str, actor: DailySelectionActor = Depends(desktop_actor)) -> Mapping[str, Any]:
            try:
                child = service.prepare_retry_child(actor_id=actor.actor_id, workspace_id=actor.workspace_id, batch_id=batch_id)
                token = str(child.pop("batch_token"))
                if child.pop("execute", False):
                    background_tasks.add_task(service.execute_retry_child, actor_id=actor.actor_id, workspace_id=actor.workspace_id, batch_token=token)
                return child
            except LookupError as error:
                raise HTTPException(status_code=404, detail=str(error)) from error
            except ValueError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            except RuntimeError as error:
                raise HTTPException(status_code=409, detail=str(error)) from error

    return service


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _is_active_onebound_draft(draft: Mapping[str, Any] | None) -> bool:
    if not isinstance(draft, Mapping) or draft.get("status") == "deleted":
        return False
    return str(draft.get("source_type", "onebound_api")) == "onebound_api"


def _onebound_draft_by_candidate(
    repository: Any, candidate_id: str, workspace_id: str
) -> Mapping[str, Any] | None:
    """Query the exact OneBound row without breaking older lightweight fakes."""
    try:
        return repository.draft_by_candidate(
            candidate_id, workspace_id, source_type="onebound_api"
        )
    except TypeError:
        return repository.draft_by_candidate(candidate_id, workspace_id)


def _failure(item: _Item, error_code: str, message: str) -> Mapping[str, Any]:
    item.status = "failed"
    item.outcome = "failed"
    item.error_code = error_code
    item.message = message
    return _item_response(item)


def _candidate_source_title(candidate: Mapping[str, Any]) -> str:
    for key in ("source_title", "title", "product_name"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _item_response(item: _Item) -> Mapping[str, Any]:
    result: dict[str, Any] = {
        "ok": item.status != "failed",
        "outcome": item.outcome or item.status,
        "offer_id": item.offer_id,
        "source_url": item.source_url,
        "statusText": item.message or "等待采集",
    }
    if item.draft_id is not None:
        result["draft_id"] = item.draft_id
    if item.error_code:
        result["error_code"] = item.error_code
        result["message"] = item.message
    return result


def _finish_summary(batch: _Batch, *, cancelled: bool) -> Mapping[str, Any]:
    items = tuple(batch.items.values())
    failed = tuple(item for item in items if item.status == "failed")
    return {
        "ok": True,
        "created_count": sum(item.outcome == "created" for item in items),
        "refreshed_count": sum(item.outcome == "refreshed" for item in items),
        "skipped_count": sum(item.status == "skipped" for item in items) + len(batch.existing_offer_ids),
        "failed_count": len(failed),
        "unprocessed_count": sum(item.status in {"pending", "running", "unprocessed"} for item in items),
        "failed_urls": [item.source_url for item in failed],
        "cancelled": cancelled,
        "statusText": "批次已完成" if not cancelled else "批次已取消",
    }


__all__ = [
    "PluginOneBoundCaptureDependencies",
    "PluginOneBoundCaptureService",
    "register_plugin_onebound_capture_routes",
]
