from __future__ import annotations

import hashlib
import math
import inspect
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ...session import Actor
from .assets import PodAssetStore
from .errors import PodExecutionExpired, image_provider_outcome_for_exception, safe_error_message
from .billing_contract import (
    PodBillingAuthorizationRequired,
    PodBillingCoordinator,
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
)
from .images import compose_fixed_scene, split_grid_2x2
from .prompts import LISTING_IMAGE_ROLES, build_style_listing_prompt
from .repository import PodCustomizationRepository, PodRepositoryError
from .runtime import RuntimeClosedError
from .runtime_contracts import DirectListingGridRequest, PatternGridRequest, PodAiRuntime, SceneOptimizationRequest
from .title_runtime import PodTitleRequest, visual_signature


# Inactivity deadline for a running batch. A batch that produces no durable
# progress (no provider result, no post-process completion, no title) for this
# many seconds will be reaped by the live reaper and its epoch revoked.
# Must be above the ai_runtime provider polling ceiling (600 s) so a legitimately
# slow but still-progressing style is not reaped mid-flight.
POD_PROGRESS_TIMEOUT_SECONDS: int = 900

# How often the deadline-aware wait loop polls for new completions.
POD_WAIT_POLL_SECONDS: float = 5.0

# Provider status classes whose failure should be auto-retried once on the first
# image-generation attempt (e.g. the 600s polling timeout is "transient"). These
# mirror the retryable classes used by the media provider runtime.
_RETRYABLE_PROVIDER_STATUS_CLASSES = frozenset({
    "transient", "server_error", "rate_limited", "connection_error",
    "unknown_outcome_timeout", "invalid_response",
    "gateway_unavailable", "gateway_bad_response", "gateway_in_progress",
})


def _is_retryable_generation_error(exc: BaseException) -> bool:
    return str(getattr(exc, "status_class", "") or "") in _RETRYABLE_PROVIDER_STATUS_CLASSES


class PodBatchCancelled(RuntimeClosedError):
    """Raised from a batch control checkpoint when the batch was cancelled."""


class PodBatchPaused(RuntimeClosedError):
    """Raised from a batch control checkpoint when the batch was paused."""


@dataclass
class BatchExecutionContext:
    """Carries the durable execution epoch claimed for a single batch run.

    Every worker-side repository write that should be fenced passes this context
    so the repository can include ``AND execution_epoch = ?`` in its SQL.  A
    stale worker whose epoch no longer matches the DB value will receive a
    PodExecutionExpired exception rather than silently succeeding.
    """

    batch_id: str
    epoch: int


@dataclass
class PodBillingRun:
    actor: Actor
    coordinator: PodBillingCoordinator
    plan: PodCallPlan
    grant: PodExecutionGrant = field(repr=False)
    repository: PodCustomizationRepository | None = field(default=None, repr=False)
    action_key: str = ""
    resumed: bool = False
    execution_epoch: int = 0
    _outcomes: dict[str, PodCallOutcome] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def record(self, call_id: str, feature: str, status: str) -> None:
        outcome = PodCallOutcome(call_id, feature, status)  # type: ignore[arg-type]
        with self._lock:
            existing = self._outcomes.get(call_id)
            if existing is not None and existing != outcome:
                raise RuntimeError(f"POD call {call_id} has conflicting outcomes")
            if self.repository is not None:
                if self.execution_epoch > 0:
                    current = self.repository.get_batch_execution_epoch(self.action_key)
                    if current is not None and current != self.execution_epoch:
                        from .errors import PodExecutionExpired
                        raise PodExecutionExpired(
                            f"billing record for {call_id} rejected: batch epoch has advanced"
                        )
                self.repository.record_billing_outcome(self.action_key, outcome)
            self._outcomes[call_id] = outcome

    def start(self, call_id: str, feature: str) -> None:
        provider = "ark" if feature == "pod.title" else "wuyin"
        if not self.grant.provider_key(provider):
            raise PodBillingAuthorizationRequired(
                "POD provider grant unavailable; this call will be recorded as failed"
            )
        if self.repository is not None:
            if self.execution_epoch > 0:
                current = self.repository.get_batch_execution_epoch(self.action_key)
                if current is not None and current != self.execution_epoch:
                    from .errors import PodExecutionExpired
                    raise PodExecutionExpired(
                        f"billing start for {call_id} rejected: batch epoch has advanced"
                    )
            self.repository.start_billing_call(self.action_key, call_id, feature)

    def settle(self) -> None:
        if self.repository is not None:
            if self.execution_epoch > 0:
                current = self.repository.get_batch_execution_epoch(self.action_key)
                if current is not None and current != self.execution_epoch:
                    from .errors import PodExecutionExpired
                    raise PodExecutionExpired(
                        f"billing settle rejected: batch epoch has advanced (expected {self.execution_epoch}, got {current})"
                    )
            outcomes = self.repository.prepare_billing_settlement(self.action_key)
        else:
            with self._lock:
                outcomes = tuple(
                    self._outcomes.get(call.call_id)
                    or PodCallOutcome(call.call_id, call.feature, "no_return")
                    for call in self.plan.calls
                )
        try:
            self.coordinator.settle(self.actor, self.grant, self.plan, outcomes)
        except Exception as exc:
            if self.repository is not None:
                self.repository.mark_billing_pending(self.action_key, safe_error_message(exc))
            raise
        if self.repository is not None:
            self.repository.mark_billing_settled(self.action_key)

    def has_outcome(self, call_id: str) -> bool:
        if self.repository is not None:
            return self.repository.billing_call_status(self.action_key, call_id) in {
                "success",
                "no_return",
            }
        with self._lock:
            return call_id in self._outcomes

    def call_status(self, call_id: str) -> str:
        if self.repository is not None:
            return self.repository.billing_call_status(self.action_key, call_id)
        with self._lock:
            outcome = self._outcomes.get(call_id)
        return outcome.status if outcome is not None else "planned"


def _serial_batch_action(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with self._batch_action_lock:
            self._require_open()
            return function(self, *args, **kwargs)

    return wrapped


class PodBatchWorker:
    """Coordinates POD jobs without borrowing product-processing workers or gates."""

    def __init__(
        self,
        repository: PodCustomizationRepository,
        assets: PodAssetStore,
        ai_runtime: PodAiRuntime,
        *,
        title_runtime: Any | None = None,
        coordinator_workers: int = 1,
        theme_registry: Any | None = None,
    ) -> None:
        self.repository = repository
        self.assets = assets
        self.ai_runtime = ai_runtime
        self.title_runtime = title_runtime
        self._theme_registry = theme_registry
        self._batch_action_lock = threading.RLock()
        self._coordinator = ThreadPoolExecutor(
            max_workers=max(1, min(coordinator_workers, 4)),
            thread_name_prefix="pod-customization-batch",
        )
        self._futures: dict[tuple[str, str], Future[Any]] = {}
        self._futures_lock = threading.Lock()
        self._billing_runs: dict[str, PodBillingRun] = {}
        # 独立的款式后处理执行池：容量与速创生图并发一致（image_workers），
        # 使四图返回后的校验、切图、COS 发布与结果落库也能最多 8 款并行。
        self._style_postprocess_pool = ThreadPoolExecutor(
            max_workers=self._image_worker_count(),
            thread_name_prefix="pod-style-postprocess",
        )
        self._closing = threading.Event()

    def _require_open(self) -> None:
        if self._closing.is_set():
            raise RuntimeError("POD worker is shutting down")

    def _theme_pools(self) -> dict[str, Any] | None:
        return self._theme_registry.pools() if self._theme_registry is not None else None

    def ensure_theme_pool(self, theme_label: str) -> None:
        """Background-enrich a theme's pool via Doubao without blocking generation."""
        if self._theme_registry is None or self._closing.is_set():
            return
        # errors inside ensure() are swallowed there; no result needed.
        self._coordinator.submit(self._theme_registry.ensure, theme_label)

    def _maybe_enrich_theme(self, batch: dict[str, Any]) -> None:
        """Queue a Doubao pool build for a brief whose theme has no pool yet."""
        if self._theme_registry is None:
            return
        theme = str((batch.get("business_fields") or {}).get("design_theme") or "").strip()
        if not theme or self._theme_registry.has_pool(theme):
            return
        self.ensure_theme_pool(theme)

    def register_billing_run(self, batch_id: str, billing_run: PodBillingRun) -> None:
        with self._futures_lock:
            self._billing_runs[batch_id] = billing_run

    def register_action_billing_run(self, action_key: str, billing_run: PodBillingRun) -> None:
        with self._futures_lock:
            self._billing_runs[action_key] = billing_run

    def submit(self, batch_id: str, billing_run: PodBillingRun | None = None) -> Future[Any]:
        self._require_open()
        if billing_run is not None:
            self.register_billing_run(batch_id, billing_run)
        key = ("batch", batch_id)
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(self.process_batch, batch_id)
            self._futures[key] = future
        self._attach_forget_callback(key, future)
        return future

    def is_batch_running(self, batch_id: str) -> bool:
        with self._futures_lock:
            future = self._futures.get(("batch", batch_id))
            return future is not None and not future.done()

    def submit_scene_optimization(
        self, batch_id: str, item_id: str, instruction: str, billing_run: PodBillingRun | None = None
    ) -> Future[Any]:
        self._require_open()
        key = ("scene", item_id)
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(self.optimize_scene, batch_id, item_id, instruction, billing_run)
            self._futures[key] = future
        self._attach_forget_callback(key, future)
        return future

    def submit_item_regeneration(
        self, batch_id: str, item_id: str, creative_prompt: str, billing_run: PodBillingRun | None = None
    ) -> Future[Any]:
        self._require_open()
        key = ("regenerate", item_id)
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(self.regenerate_item, batch_id, item_id, creative_prompt, billing_run)
            self._futures[key] = future
        self._attach_forget_callback(key, future)
        return future

    def submit_style_regeneration(
        self,
        batch_id: str,
        style_index: int,
        creative_prompt: str,
        billing_run: PodBillingRun | None = None,
    ) -> Future[Any]:
        self._require_open()
        key = ("regenerate-style", f"{batch_id}:{style_index}")
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(
                self.regenerate_style, batch_id, style_index, creative_prompt, billing_run
            )
            self._futures[key] = future
        self._attach_forget_callback(key, future)
        return future

    def submit_title_regeneration(
        self, batch_id: str, style_index: int, billing_run: PodBillingRun | None = None
    ) -> Future[Any]:
        self._require_open()
        if self.title_runtime is None:
            raise RuntimeError("POD title runtime is disabled")
        key = ("regenerate-title", f"{batch_id}:{style_index}")
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(self.regenerate_title, batch_id, style_index, billing_run)
            self._futures[key] = future
        self._attach_forget_callback(key, future)
        return future

    def submit_batch_retry(
        self,
        batch_id: str,
        image_style_indices: tuple[int, ...],
        title_style_indices: tuple[int, ...],
        billing_run: PodBillingRun | None = None,
    ) -> Future[Any]:
        """Queue one serialized operation for selected failed styles."""
        self._require_open()
        key = ("batch-retry", batch_id)
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(
                self.process_batch_retry,
                batch_id,
                image_style_indices,
                title_style_indices,
                billing_run,
            )
            self._futures[key] = future
        self._attach_forget_callback(key, future)
        return future

    def submit_billing_action(self, action_id: str, function, *args) -> Future[Any]:
        self._require_open()
        key = ("billing-resume", action_id)
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(function, *args)
            self._futures[key] = future
        self._attach_forget_callback(key, future)
        return future

    @_serial_batch_action
    def process_batch(self, batch_id: str, billing_run: PodBillingRun | None = None) -> None:
        run = billing_run or self._billing_runs.get(batch_id)
        if run is None:
            self.repository.set_batch_status(
                batch_id,
                "failed",
                "POD execution has no billing run; batch was marked failed",
            )
            return
        cancelled = False
        execution_expired = False
        try:
            self._process_batch_authorized(batch_id, run)
        except PodExecutionExpired:
            # The reaper has revoked this execution.  Do not let the stale
            # coordinator settle billing or write a replacement batch status.
            execution_expired = True
        except PodBillingAuthorizationRequired as exc:
            try:
                self._fail_batch_after_provider_error(batch_id, run, str(exc), fail_completed=True)
            except PodExecutionExpired:
                execution_expired = True
        except PodBatchPaused as exc:
            # 暂停保留已入库进度；继续时沿用普通批次恢复流程。
            self.repository.mark_billing_pending(run.action_key, "POD 批次已暂停，等待继续")
            self.repository.mark_batch_paused(batch_id, str(exc))
        except PodBatchCancelled as exc:
            cancelled = True
            self.repository.fail_remaining_items(batch_id, "POD 批次已取消")
            self.repository.fail_pending_titles(batch_id, "POD 批次已取消")
            self.repository.mark_batch_cancelled(batch_id, str(exc))
        except Exception as exc:
            try:
                self._fail_batch_after_provider_error(batch_id, run, str(exc))
            except PodExecutionExpired:
                execution_expired = True
            except PodRepositoryError:
                pass
        finally:
            if execution_expired:
                pass
            elif cancelled:
                # 取消后结算，把未使用的冻结积分返还；结算失败不回退 cancelled 终态。
                try:
                    run.settle()
                except Exception as exc:
                    self.repository.mark_billing_pending(run.action_key, safe_error_message(exc))
            else:
                try:
                    run.settle()
                except Exception as exc:
                    # 账务结算单独保存在 billing run；不能覆盖生成结果，
                    # 否则失败项会被“等待结算”状态反向锁死。
                    self.repository.mark_billing_pending(run.action_key, safe_error_message(exc))
            self._discard_billing_run(batch_id, run)

    def _check_control(self, batch_id: str) -> None:
        status = self.repository.get_batch_status(batch_id)
        if status == "cancelling":
            raise PodBatchCancelled("POD 批次已取消")
        if status == "pausing":
            raise PodBatchPaused("POD 批次已暂停")

    def _fail_batch_after_provider_error(
        self, batch_id: str, billing_run: PodBillingRun, error_message: str,
        *, fail_completed: bool = False,
    ) -> None:
        """Turn provider/grant interruptions into ordinary POD failures.

        A missing short-lived provider grant is not a user-facing recovery
        state.  Remaining calls are reported as no-return by settlement and the
        batch becomes retryable through the normal failed-item flow.
        """
        batch = self.repository.get_batch_internal(batch_id)
        epoch = billing_run.execution_epoch or None
        if fail_completed:
            self.repository.fail_all_items(batch_id, error_message, execution_epoch=epoch)
        else:
            self.repository.fail_remaining_items(batch_id, error_message, execution_epoch=epoch)
        self.repository.fail_unready_titles(
            batch_id, error_message, execution_epoch=epoch
        )
        refreshed = self.repository.get_batch_internal(batch_id)
        status = "partial_failure" if refreshed["completed_count"] else "failed"
        self.repository.set_batch_status(
            batch_id, status, error_message, execution_epoch=epoch
        )

    def _set_batch_stage(self, batch_id: str, status: str, execution_epoch: int | None = None) -> None:
        """Do not overwrite an operator's pause or cancel request while draining."""
        if self.repository.get_batch_status(batch_id) in {"pausing", "cancelling"}:
            return
        self.repository.set_batch_status(batch_id, status, execution_epoch=execution_epoch)

    def _process_batch_authorized(self, batch_id: str, billing_run: PodBillingRun) -> None:
        current = self.repository.get_batch_internal(batch_id)
        epoch = self.repository.claim_batch_with_epoch(
            batch_id,
            allow_billing_resume=False,
        )
        if epoch is None:
            batch = self.repository.get_batch_internal(batch_id)
            if batch["status"] in {"completed", "partial_failure", "failed", "cancelled"}:
                return
            if batch["status"] == "cancelling":
                raise PodBatchCancelled("POD 批次已取消")
            if batch["status"] in {"pausing", "paused"}:
                raise PodBatchPaused("POD 批次已暂停")
            raise RuntimeError("POD batch is already running")
        execution = BatchExecutionContext(batch_id=batch_id, epoch=epoch)
        billing_run.execution_epoch = epoch
        self._check_control(batch_id)
        batch = self.repository.get_batch_internal(batch_id)
        try:
            snapshot = batch["template"]
            template_asset = self.repository.get_asset(
                snapshot["asset_id"], batch["workspace_id"], batch["owner_user_id"]
            )
            template_content = self.assets.read(template_asset["relative_path"])
            if batch.get("style_grid"):
                self._process_style_grids_streaming(
                    batch, template_content, template_asset["content_type"], billing_run,
                    execution=execution,
                )
                self._process_pending_titles_from_existing_images(
                    batch, billing_run, execution_epoch=execution.epoch
                )
                self.repository.fail_remaining_items(
                    batch_id, "本款图片生成未返回完整结果", execution_epoch=execution.epoch
                )
                self.repository.fail_unready_titles(
                    batch_id,
                    "本款四张公开图片未完整生成，暂不能生成标题",
                    execution_epoch=execution.epoch,
                )
                if self.title_runtime is None:
                    settled = self.repository.get_batch_internal(batch_id)
                    status = "completed" if settled["failed_count"] == 0 else (
                        "partial_failure" if settled["completed_count"] else "failed"
                    )
                    self.repository.set_batch_status(batch_id, status, execution_epoch=execution.epoch)
                else:
                    self.repository.fail_orphaned_complete_titles(
                        batch_id,
                        "本款标题任务未执行（标题生成调用缺失），请重试补齐标题",
                        execution_epoch=execution.epoch,
                    )
                    self.repository.settle_batch_by_listing_readiness(
                        batch_id, execution_epoch=execution.epoch
                    )
                return
            self.repository.fail_remaining_items(
                batch_id,
                "legacy non-style POD batches are read-only; create a new reference-locked style batch",
                execution_epoch=execution.epoch,
            )
            self.repository.set_batch_status(batch_id, "failed", execution_epoch=execution.epoch)
        except PodBillingAuthorizationRequired:
            raise
        except (PodBatchCancelled, PodBatchPaused):
            raise
        except Exception as exc:
            self.repository.fail_remaining_items(batch_id, str(exc), execution_epoch=execution.epoch)
            self.repository.fail_unready_titles(batch_id, str(exc), execution_epoch=execution.epoch)
            if self.title_runtime is None:
                settled = self.repository.get_batch_internal(batch_id)
                status = "partial_failure" if settled["completed_count"] else "failed"
                self.repository.set_batch_status(batch_id, status, str(exc), execution_epoch=execution.epoch)
            else:
                self.repository.settle_batch_by_listing_readiness(
                    batch_id, str(exc), execution_epoch=execution.epoch
                )

    @_serial_batch_action
    def optimize_scene(
        self, batch_id: str, item_id: str, instruction: str, billing_run: PodBillingRun | None = None
    ) -> None:
        run = billing_run or self._billing_runs.get(f"scene:{batch_id}:{item_id}")
        if run is None:
            raise RuntimeError("POD scene optimization requires a fresh short-lived billing grant")
        batch = self.repository.get_batch_internal(batch_id)
        item = self.repository.get_item(batch_id, item_id, batch["workspace_id"], batch["owner_user_id"])
        if item["status"] != "optimizing_scene":
            raise RuntimeError("POD item is not awaiting scene optimization")
        provider_returned = False
        try:
            pattern = self.repository.get_asset(item["pattern_asset_id"], batch["workspace_id"], batch["owner_user_id"])
            composite = self.repository.get_asset(item["composite_asset_id"], batch["workspace_id"], batch["owner_user_id"])
            template = self.repository.get_asset(batch["template"]["asset_id"], batch["workspace_id"], batch["owner_user_id"])
            request = SceneOptimizationRequest(
                batch_id=batch_id,
                item_id=item_id,
                instruction=instruction.strip(),
                prompt=(
                    "Optimize only the presentation scene for this one POD item. Preserve the supplied pattern exactly; "
                    "do not add text, logos, watermarks, or change the product geometry.\n"
                    f"Instruction: {instruction.strip() or 'Improve lighting and realism without changing the design.'}"
                ),
                pattern_image=self.assets.read(pattern["relative_path"]),
                fixed_composite_image=self.assets.read(composite["relative_path"]),
                template_image=self.assets.read(template["relative_path"]),
            )
            call_id = run.plan.calls[0].call_id
            run.start(call_id, "pod.image")
            content = self.ai_runtime.optimize_scene(request, grant=run.grant, call_id=call_id)
            provider_returned = True
            run.record(call_id, "pod.image", "success")
            stored = self._save_asset(batch, "scene_optimized", f"scene-{item_id}.png", content)
            self.repository.finish_scene_optimization(batch_id, item_id, composite_asset_id=stored["asset_id"])
        except Exception as exc:
            if not provider_returned:
                run.record(run.plan.calls[0].call_id, "pod.image", "no_return")
            self.repository.finish_scene_optimization(batch_id, item_id, error_message=str(exc))
        finally:
            try:
                run.settle()
            except Exception as exc:
                self.repository.mark_billing_pending(run.action_key, safe_error_message(exc))
            self._discard_billing_run(f"scene:{batch_id}:{item_id}", run)

    @_serial_batch_action
    def regenerate_item(
        self, batch_id: str, item_id: str, creative_prompt: str, billing_run: PodBillingRun | None = None
    ) -> None:
        run = billing_run or self._billing_runs.get(f"item:{batch_id}:{item_id}")
        if run is None:
            raise RuntimeError("POD item retry requires a fresh short-lived billing grant")
        batch = self.repository.get_batch_internal(batch_id)
        item = self.repository.get_item(batch_id, item_id, batch["workspace_id"], batch["owner_user_id"])
        if item["status"] != "generating_pattern":
            raise RuntimeError("POD item is not awaiting regeneration")
        prompt = batch["prompt_snapshot"]
        if creative_prompt.strip():
            prompt += f"\n\nSingle-item regeneration direction: {creative_prompt.strip()}"
        call_index = self.repository.next_generation_call_index(batch_id, "regenerate")
        call = self.repository.create_generation_call(
            batch,
            call_kind="regenerate",
            call_index=call_index,
            prompt_snapshot=prompt,
        )
        request = PatternGridRequest(
            batch_id=batch_id,
            call_kind="regenerate",
            call_index=call_index,
            prompt=prompt,
        )
        try:
            content = self.ai_runtime.submit(
                self._generate_grid, batch, call, request, run, run.plan.calls[0].call_id
            ).result()
            template_asset = self.repository.get_asset(
                batch["template"]["asset_id"], batch["workspace_id"], batch["owner_user_id"]
            )
            template_content = self.assets.read(template_asset["relative_path"])
            calibration = _calibration(batch["template"]["calibration_json"])
            for grid_cell, cell in enumerate(split_grid_2x2(content), start=1):
                fingerprint = hashlib.sha256(cell).hexdigest()
                pattern_asset = self._save_asset(
                    batch, "pattern_candidate", f"regenerate-{call['call_id']}-{grid_cell}.png", cell
                )
                composite = compose_fixed_scene(template_content, cell, calibration)
                composite_asset = self._save_asset(
                    batch, "fixed_composite", f"regenerate-composite-{item_id}.png", composite
                )
                self.repository.finish_item_regeneration(
                    batch,
                    item_id,
                    call_id=call["call_id"],
                    grid_cell=grid_cell,
                    fingerprint=fingerprint,
                    pattern_asset_id=pattern_asset["asset_id"],
                    composite_asset_id=composite_asset["asset_id"],
                )
                return
            raise RuntimeError("regeneration returned no valid POD pattern")
        except Exception as exc:
            self.repository.fail_item_regeneration(batch_id, item_id, str(exc))
        finally:
            try:
                run.settle()
            except Exception as exc:
                self.repository.mark_billing_pending(run.action_key, safe_error_message(exc))
            self._discard_billing_run(f"item:{batch_id}:{item_id}", run)

    @_serial_batch_action
    def regenerate_style(
        self,
        batch_id: str,
        style_index: int,
        creative_prompt: str,
        billing_run: PodBillingRun | None = None,
        *,
        finalize: bool = True,
    ) -> None:
        run = billing_run or self._billing_runs.get(f"style:{batch_id}:{style_index}")
        if run is None:
            raise RuntimeError("POD style retry requires a fresh short-lived billing grant")
        batch = self.repository.get_batch_internal(batch_id)
        base_prompt = batch["prompt_snapshot"]
        if creative_prompt.strip():
            base_prompt += f"\n\nWhole-style regeneration direction: {creative_prompt.strip()}"
        template_asset = self.repository.get_asset(
            batch["template"]["asset_id"], batch["workspace_id"], batch["owner_user_id"]
        )
        template_content = self.assets.read(template_asset["relative_path"])
        prepared: list[tuple[dict[str, Any], Any, list[Any], list[str]]] = []
        last_error = "整款重新生成未返回完整结果"
        last_call_id = ""
        try:
            for attempt in (1, 2):
                call_kind = "regenerate_style" if attempt == 1 else "regenerate_style_retry"
                call_index = self.repository.next_generation_call_index(batch_id, call_kind)
                prompt = build_style_listing_prompt(
                    base_prompt,
                    style_index=style_index,
                    attempt=attempt,
                    business_fields=batch["business_fields"],
                    creative_prompt=batch["creative_prompt"],
                    theme_pools=self._theme_pools(),
                )
                call = self.repository.create_generation_call(
                    batch, call_kind=call_kind, call_index=call_index, prompt_snapshot=prompt
                )
                last_call_id = call["call_id"]
                call["style_index"] = style_index
                request = DirectListingGridRequest(
                    trial_id=f"{batch_id}-style-{style_index}-{call['call_id']}-attempt-{attempt}",
                    template_id=batch["template_id"],
                    template_image=template_content,
                    template_content_type=template_asset["content_type"],
                    prompt=prompt,
                    attempt=attempt,
                )
                try:
                    provider_call_id = self._image_call_id(run, style_index, attempt)
                    grid = self.ai_runtime.submit(
                        self._generate_listing_grid, batch, call, request, run, provider_call_id
                    ).result()
                    panels, fingerprints = self._validate_style_grid(grid)
                    prepared = [(call, grid, panels, fingerprints)]
                    break
                except PodBillingAuthorizationRequired:
                    raise
                except Exception as exc:
                    last_error = str(exc).strip() or exc.__class__.__name__
            if not prepared:
                self.repository.fail_style_grid(batch, style_index, last_error)
                if self.title_runtime is not None:
                    self.repository.fail_style_title(
                        batch_id,
                        style_index,
                        last_error,
                        style_task_id=last_call_id or None,
                    )
            else:
                self._process_style_grids(batch, prepared, run)
            if finalize:
                self.repository.fail_remaining_items(batch_id, "整款重新生成未返回完整结果")
                self.repository.fail_unready_titles(batch_id, "整款重新生成未返回完整结果")
            if finalize:
                if self.title_runtime is None:
                    settled = self.repository.get_batch_internal(batch_id)
                    status = "completed" if settled["failed_count"] == 0 else (
                        "partial_failure" if settled["completed_count"] else "failed"
                    )
                    self.repository.set_batch_status(batch_id, status)
                else:
                    self.repository.settle_batch_by_listing_readiness(batch_id)
        except Exception as exc:
            self.repository.fail_style_grid(batch, style_index, str(exc))
            self.repository.fail_unready_titles(batch_id, str(exc))
            if finalize:
                if self.title_runtime is None:
                    settled = self.repository.get_batch_internal(batch_id)
                    status = "partial_failure" if settled["completed_count"] else "failed"
                    self.repository.set_batch_status(batch_id, status, str(exc))
                else:
                    self.repository.settle_batch_by_listing_readiness(batch_id, str(exc))
        finally:
            if finalize:
                try:
                    run.settle()
                except Exception as exc:
                    self.repository.mark_billing_pending(run.action_key, safe_error_message(exc))
            if finalize:
                self._discard_billing_run(f"style:{batch_id}:{style_index}", run)

    @_serial_batch_action
    def regenerate_title(
        self,
        batch_id: str,
        style_index: int,
        billing_run: PodBillingRun | None = None,
        *,
        finalize: bool = True,
    ) -> None:
        if self.title_runtime is None:
            raise RuntimeError("POD title runtime is disabled")
        run = billing_run or self._billing_runs.get(f"title:{batch_id}:{style_index}")
        if run is None:
            raise RuntimeError("POD title retry requires a fresh short-lived billing grant")
        context = self.repository.get_style_title_context(batch_id, style_index)
        title = context["title"]
        if title["status"] != "generating":
            raise RuntimeError("POD style title is not awaiting regeneration")
        try:
            self._generate_style_title(
                context["batch"],
                style_index,
                title["style_task_id"],
                self._lifestyle_media(context),
                run,
                self._title_call_ids(run, batch_id, style_index),
            )
        except Exception as exc:
            self.repository.fail_style_title(batch_id, style_index, str(exc))
        finally:
            if finalize:
                self.repository.settle_batch_by_listing_readiness(batch_id)
                try:
                    run.settle()
                except Exception as exc:
                    self.repository.mark_billing_pending(run.action_key, safe_error_message(exc))
            if finalize:
                self._discard_billing_run(f"title:{batch_id}:{style_index}", run)

    @_serial_batch_action
    def process_batch_retry(
        self,
        batch_id: str,
        image_style_indices: tuple[int, ...],
        title_style_indices: tuple[int, ...],
        billing_run: PodBillingRun | None = None,
    ) -> None:
        """Process the selected retry actions serially and settle one billing run."""
        run = billing_run or self._billing_runs.get(f"batch-retry:{batch_id}")
        if run is None:
            raise RuntimeError("POD batch retry requires a fresh short-lived billing grant")
        try:
            for style_index in image_style_indices:
                self.regenerate_style(
                    batch_id,
                    style_index,
                    "",
                    run,
                    finalize=False,
                )
            for style_index in title_style_indices:
                self.regenerate_title(batch_id, style_index, run, finalize=False)
            self.repository.settle_batch_by_listing_readiness(batch_id)
        finally:
            try:
                run.settle()
            except Exception as exc:
                self.repository.mark_billing_pending(run.action_key, safe_error_message(exc))
            self._discard_billing_run(f"batch-retry:{batch_id}", run)

    def close(self) -> None:
        if self._closing.is_set():
            return
        self._closing.set()
        with self._futures_lock:
            for future in self._futures.values():
                future.cancel()
        self._coordinator.shutdown(wait=False, cancel_futures=True)
        self._style_postprocess_pool.shutdown(wait=False, cancel_futures=True)

    def _run_grid_calls(self, batch: dict[str, Any], call_kind: str, count: int) -> list[tuple[dict[str, Any], bytes]]:
        futures: dict[Future[Any], dict[str, Any]] = {}
        for call_index in range(1, count + 1):
            call = self.repository.create_generation_call(batch, call_kind=call_kind, call_index=call_index)
            request = PatternGridRequest(
                batch_id=batch["batch_id"],
                call_kind=call_kind,
                call_index=call_index,
                prompt=batch["prompt_snapshot"],
            )
            futures[self.ai_runtime.submit(self._generate_grid, batch, call, request)] = call
        completed: list[tuple[dict[str, Any], bytes]] = []
        for future in as_completed(futures):
            call = futures[future]
            try:
                completed.append((call, future.result()))
            except Exception:
                continue
        return sorted(completed, key=lambda value: value[0]["call_index"])

    def _image_worker_count(self) -> int:
        """Number of concurrent provider/image jobs the POD runtime supports."""
        config = getattr(self.ai_runtime, "config", None)
        return max(1, int(getattr(config, "executor_workers", 1)))

    def _process_style_grids_streaming(
        self,
        batch: dict[str, Any],
        template_content: bytes,
        template_content_type: str,
        billing_run: PodBillingRun,
        *,
        execution: BatchExecutionContext | None = None,
    ) -> None:
        """Generate and persist styles with a bounded generation window and a
        parallel post-processing pool.

        At most ``image_workers`` provider grids are in flight at once. As soon
        as a grid returns, that style's validation, split, COS publish and
        persistence are handed to the dedicated post-processing pool, freeing a
        generation slot for the next style. Progress therefore lands in real
        time instead of queuing behind a single-threaded post-processing phase.
        Titles stay serialized inside the title runtime and continue to use
        already-accepted titles and visual themes as deduplication context.
        """
        self._maybe_enrich_theme(batch)
        completed_by_style: dict[int, int] = {}
        for item in batch.get("items", []):
            if item.get("status") == "completed":
                style_index = int(item.get("style_index") or 0)
                completed_by_style[style_index] = completed_by_style.get(style_index, 0) + 1
        style_indices = [
            index
            for index in range(1, batch["requested_count"] + 1)
            if completed_by_style.get(index, 0) < 4
        ]
        processed: set[int] = set()
        retry_reasons: dict[int, str] = {}
        in_loop_retried: set[int] = set()

        for attempt in (1, 2):
            pending = [
                index for index in style_indices
                if index not in processed and index not in in_loop_retried
            ]
            if not pending:
                break
            attempt_processed, attempt_errors, pause_requested, attempt_retried = self._stream_style_attempts(
                batch,
                pending,
                template_content,
                template_content_type,
                attempt=attempt,
                billing_run=billing_run,
                execution_epoch=execution.epoch if execution is not None else None,
            )
            retry_reasons.update(attempt_errors)
            processed |= attempt_processed
            # styles already auto-retried once inside this pass must not be
            # re-attempted again by the outer second pass.
            in_loop_retried |= attempt_retried
            if pause_requested or self.repository.get_batch_status(batch["batch_id"]) == "pausing":
                raise PodBatchPaused("POD 批次已暂停")

        for style_index in style_indices:
            if style_index not in processed:
                self.repository.fail_style_grid(
                    batch,
                    style_index,
                    retry_reasons.get(style_index, "本款两次图片生成均失败"),
                    execution_epoch=execution.epoch if execution is not None else None,
                )

    def _process_pending_titles_from_existing_images(
        self,
        batch: dict[str, Any],
        billing_run: PodBillingRun,
        *,
        execution_epoch: int | None = None,
    ) -> None:
        """Resume only title work after images were already durably published.

        A paused batch can reach the title stage after its four images are
        finished.  Its new freeze contains title calls only, so it must not
        route back through image generation (which would otherwise consume an
        absent image call from the fresh plan).
        """
        if self.title_runtime is None:
            return
        # A resume plan can carry both image calls (styles half-generated) and
        # title-only calls (styles whose four images are already durable but
        # whose title never ran). Title work for image styles is submitted by
        # `_process_style_grids_streaming` as each lifestyle panel is published,
        # so this pass must only pick up styles that own a planned title call
        # and no image call. Skipping on a blanket "has image call" check would
        # drop the title-only styles and force them to failed.
        pending: list[int] = []
        for title in batch.get("style_titles", []):
            style_index = int(title.get("style_index") or 0)
            if style_index < 1 or title.get("status") == "completed":
                continue
            if self._style_owns_image_call(billing_run, style_index):
                continue
            if self._title_call_ids(billing_run, batch["batch_id"], style_index):
                pending.append(style_index)
        if not pending:
            return
        self._set_batch_stage(batch["batch_id"], "generating_titles", execution_epoch)
        futures: list[Future[Any]] = []
        for style_index in pending:
            self._check_control(batch["batch_id"])
            context = self.repository.get_style_title_context(batch["batch_id"], style_index)
            title = context["title"]
            self.repository.claim_style_title(
                batch["batch_id"],
                style_index,
                style_task_id=str(title.get("style_task_id") or "") or None,
                allow_billing_resume=True,
                execution_epoch=execution_epoch,
            )
            futures.append(
                self.title_runtime.submit(
                    self._generate_style_title,
                    context["batch"],
                    style_index,
                    str(title.get("style_task_id") or ""),
                    self._lifestyle_media(context),
                    billing_run,
                    self._title_call_ids(billing_run, batch["batch_id"], style_index),
                    execution_epoch,
                )
            )
        for future in as_completed(futures):
            future.result()

    def _stream_style_attempts(
        self,
        batch: dict[str, Any],
        style_indices: list[int],
        template_content: bytes,
        template_content_type: str,
        *,
        attempt: int,
        billing_run: PodBillingRun,
        execution_epoch: int | None = None,
    ) -> tuple[set[int], dict[int, str], bool, set[int]]:
        """Pipeline one generation attempt with per-style post-processing.

        A bounded provider window (``image_futures``) overlaps a parallel
        post-processing pool (``postprocess_futures``). Each completed grid is
        validated and handed to the post-processing pool immediately, so it does
        not wait for the whole window to return. A style is marked ``processed``
        only after its full publish/link step succeeds; validation and
        generation failures stay retryable for the second attempt.
        """
        image_futures: dict[Future[Any], tuple[dict[str, Any], int]] = {}
        postprocess_futures: dict[Future[Any], int] = {}
        processed: set[int] = set()
        errors: dict[int, str] = {}
        pending = iter(style_indices)
        exhausted = False
        pause_requested = False
        max_in_flight = self._image_worker_count()
        _progress_deadline = time.monotonic() + POD_PROGRESS_TIMEOUT_SECONDS

        def cancel_inflight() -> None:
            for future in (*image_futures, *postprocess_futures):
                future.cancel()

        def submit_postprocess(style_index: int, call: dict[str, Any], grid: Any) -> None:
            try:
                panels, fingerprints = self._validate_style_grid(grid)
            except PodBillingAuthorizationRequired:
                raise
            except Exception as exc:
                errors[style_index] = str(exc).strip() or exc.__class__.__name__
                return
            future = self._style_postprocess_pool.submit(
                self._process_style_grids,
                batch,
                [(call, grid, panels, fingerprints)],
                billing_run,
            )
            postprocess_futures[future] = style_index

        # Styles already auto-retried once inside this attempt pass, so a second
        # generation failure isn't retried a third time.
        retried: set[int] = set()

        def submit_style(style_index: int, attempt_value: int) -> None:
            attempt_kind = "initial" if attempt_value == 1 else "retry"
            prompt = build_style_listing_prompt(
                batch["prompt_snapshot"],
                style_index=style_index,
                attempt=attempt_value,
                business_fields=batch["business_fields"],
                creative_prompt=batch["creative_prompt"],
                theme_pools=self._theme_pools(),
            )
            call = self.repository.get_or_create_generation_call(
                batch, call_kind=attempt_kind, call_index=style_index, prompt_snapshot=prompt
            )
            call["style_index"] = style_index
            provider_call_id = f"{batch['batch_id']}:style:{style_index}:image:{attempt_value}"
            billing_status = billing_run.call_status(provider_call_id)
            if billing_status == "success":
                asset_id = str(call.get("grid_asset_id") or "")
                if not asset_id:
                    errors[style_index] = "provider call completed before restart but its asset is unavailable"
                    return
                asset = self.repository.get_asset(
                    asset_id, batch["workspace_id"], batch["owner_user_id"]
                )
                submit_postprocess(
                    style_index,
                    call,
                    SimpleNamespace(
                        content=self.assets.read(asset["relative_path"]),
                        content_type=asset["content_type"],
                        suffix=Path(asset["filename"]).suffix or ".png",
                        provider="persisted",
                        model="persisted",
                    ),
                )
                return
            if billing_status in {"started", "no_return"}:
                # Terminal only once the final attempt is spent; on an earlier
                # attempt leave it out of `processed` so the second pass retries
                # the already-planned next image call instead of skipping forever.
                if attempt_value >= 2:
                    errors[style_index] = (
                        "provider call outcome was uncertain during restart" if billing_status == "started"
                        else "provider returned no result"
                    )
                return
            if not billing_run.grant.provider_key("wuyin"):
                raise PodBillingAuthorizationRequired(
                    "POD provider grant expired before the next image call started"
                )
            request = DirectListingGridRequest(
                trial_id=f"{batch['batch_id']}-style-{style_index}-attempt-{attempt_value}",
                template_id=batch["template_id"],
                template_image=template_content,
                template_content_type=template_content_type,
                prompt=prompt,
                attempt=attempt_value,
            )
            future = self.ai_runtime.submit(
                self._generate_listing_grid,
                batch,
                call,
                request,
                billing_run,
                provider_call_id,
                execution_epoch,
            )
            image_futures[future] = (call, style_index)

        while True:
            # Fill the bounded generation window while not paused.
            while not pause_requested and not exhausted and len(image_futures) < max_in_flight:
                if self._closing.is_set():
                    raise PodBillingAuthorizationRequired(
                        "POD worker stopped before the remaining provider calls started"
                    )
                status = self.repository.get_batch_status(batch["batch_id"])
                if status == "cancelling":
                    raise PodBatchCancelled("POD 批次已取消")
                if status == "pausing":
                    pause_requested = True
                    break
                try:
                    style_index = next(pending)
                except StopIteration:
                    exhausted = True
                    break
                submit_style(style_index, attempt)

            # Nothing left in flight; the attempt is complete.
            if not image_futures and not postprocess_futures:
                break

            # Wait for the first completion across generation and post-processing.
            remaining = _progress_deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(
                    f"batch coordinator timed out after {POD_PROGRESS_TIMEOUT_SECONDS}s of inactivity"
                )
            done, _ = wait(
                tuple(image_futures) + tuple(postprocess_futures),
                timeout=min(remaining, POD_WAIT_POLL_SECONDS),
                return_when=FIRST_COMPLETED,
            )
            if not done:
                continue
            for future in done:
                if future in image_futures:
                    call, style_index = image_futures.pop(future)
                    try:
                        grid = future.result()
                    except PodBillingAuthorizationRequired:
                        # A grant failure invalidates the remaining calls in
                        # this batch.  Best-effort cancellation prevents a
                        # previously returned grid from racing the failure
                        # finalization and changing the durable counts.
                        cancel_inflight()
                        raise
                    except PodExecutionExpired:
                        cancel_inflight()
                        raise
                    except PodBatchPaused:
                        pause_requested = True
                        continue
                    except PodBatchCancelled:
                        raise
                    except Exception as exc:
                        if (
                            attempt == 1
                            and style_index not in retried
                            and _is_retryable_generation_error(exc)
                        ):
                            # Auto-retry once on a transient provider failure
                            # (e.g. the 600s polling timeout) via the already
                            # planned second image call.
                            retried.add(style_index)
                            submit_style(style_index, 2)
                            continue
                        errors[style_index] = str(exc).strip() or exc.__class__.__name__
                        continue
                    submit_postprocess(style_index, call, grid)
                elif future in postprocess_futures:
                    style_index = postprocess_futures.pop(future)
                    try:
                        future.result()
                        self.repository.touch_batch_progress(
                            batch["batch_id"], execution_epoch=execution_epoch
                        )
                        processed.add(style_index)
                        _progress_deadline = time.monotonic() + POD_PROGRESS_TIMEOUT_SECONDS
                    except PodBillingAuthorizationRequired:
                        for pending_future in postprocess_futures:
                            pending_future.cancel()
                        raise
                    except PodExecutionExpired:
                        cancel_inflight()
                        raise
                    except PodBatchPaused:
                        processed.add(style_index)
                        pause_requested = True
                    except PodBatchCancelled:
                        raise
                    except Exception as exc:
                        errors[style_index] = str(exc).strip() or exc.__class__.__name__

            # Re-check control state after draining this wave of completions.
            status = self.repository.get_batch_status(batch["batch_id"])
            if status == "cancelling":
                raise PodBatchCancelled("POD 批次已取消")
            if status == "pausing":
                pause_requested = True

        return processed, errors, pause_requested, retried

    def _validate_style_grid(
        self,
        grid: Any,
    ) -> tuple[list[Any], list[str]]:
        panels = self.ai_runtime.split_listing_grid(grid)
        if len(panels) != 4:
            raise RuntimeError("generated four-grid image did not yield exactly four panels")
        return panels, [hashlib.sha256(panel.content).hexdigest() for panel in panels]

    def _generate_listing_grid(
        self,
        batch: dict[str, Any],
        call: dict[str, Any],
        request: DirectListingGridRequest,
        billing_run: PodBillingRun,
        provider_call_id: str,
        execution_epoch: int | None = None,
    ) -> Any:
        epoch = execution_epoch if execution_epoch is not None else billing_run.execution_epoch or None
        self.repository.mark_generation_call_running(call["call_id"], epoch)
        provider_returned = False
        try:
            def start_provider_call() -> None:
                self._check_control(batch["batch_id"])
                billing_run.start(provider_call_id, "pod.image")

            runtime_kwargs = {
                "grant": billing_run.grant,
                "call_id": provider_call_id,
            }
            if _accepts_keyword(self.ai_runtime.generate_listing_grid, "on_start"):
                runtime_kwargs["on_start"] = start_provider_call
            else:
                start_provider_call()
            media = self.ai_runtime.generate_listing_grid(request, **runtime_kwargs)
            provider_returned = True
            # Billing follows provider return, not downstream quality acceptance.
            billing_run.record(provider_call_id, "pod.image", "success")
            stored = self._save_asset(
                batch,
                "direct_listing_grid",
                f"style-grid-{call['call_index']}{media.suffix}",
                media.content,
            )
            self.repository.finish_generation_call(
                call["call_id"], status="succeeded", grid_asset_id=stored["asset_id"],
                execution_epoch=epoch,
            )
            return media
        except PodBillingAuthorizationRequired as exc:
            if not billing_run.has_outcome(provider_call_id):
                billing_run.record(provider_call_id, "pod.image", "no_return")
            self.repository.finish_generation_call(
                call["call_id"], status="failed", error_message=safe_error_message(exc),
                execution_epoch=epoch,
            )
            raise
        except PodBatchPaused:
            self.repository.requeue_generation_call(call["call_id"], epoch)
            raise
        except PodBatchCancelled:
            self.repository.requeue_generation_call(call["call_id"], epoch)
            raise
        except RuntimeClosedError as exc:
            raise RuntimeError("POD worker stopped while processing this provider call") from exc
        except Exception as exc:
            if not provider_returned:
                billing_run.record(
                    provider_call_id,
                    "pod.image",
                    image_provider_outcome_for_exception(exc),
                )
            self.repository.finish_generation_call(
                call["call_id"], status="failed", error_message=safe_error_message(exc),
                execution_epoch=epoch,
            )
            raise

    def _generate_grid(
        self,
        batch: dict[str, Any],
        call: dict[str, Any],
        request: PatternGridRequest,
        billing_run: PodBillingRun,
        provider_call_id: str,
        execution_epoch: int | None = None,
    ) -> bytes:
        epoch = execution_epoch if execution_epoch is not None else billing_run.execution_epoch or None
        self.repository.mark_generation_call_running(call["call_id"], epoch)
        try:
            billing_run.start(provider_call_id, "pod.image")
            content = self.ai_runtime.generate_pattern_grid(
                request, grant=billing_run.grant, call_id=provider_call_id
            )
            billing_run.record(provider_call_id, "pod.image", "success")
            stored = self._save_asset(batch, "grid", f"{call['call_kind']}-{call['call_index']}.png", content)
            self.repository.finish_generation_call(
                call["call_id"], status="succeeded", grid_asset_id=stored["asset_id"],
                execution_epoch=epoch,
            )
            return content
        except PodBillingAuthorizationRequired as exc:
            if not billing_run.has_outcome(provider_call_id):
                billing_run.record(provider_call_id, "pod.image", "no_return")
            self.repository.finish_generation_call(
                call["call_id"], status="failed", error_message=safe_error_message(exc),
                execution_epoch=epoch,
            )
            raise
        except Exception as exc:
            if not billing_run.has_outcome(provider_call_id):
                billing_run.record(provider_call_id, "pod.image", "no_return")
            self.repository.finish_generation_call(
                call["call_id"], status="failed", error_message=safe_error_message(exc),
                execution_epoch=epoch,
            )
            raise

    def _process_grids(
        self,
        batch: dict[str, Any],
        grids: list[tuple[dict[str, Any], bytes]],
        template_content: bytes,
    ) -> None:
        calibration = _calibration(batch["template"]["calibration_json"])
        self._set_batch_stage(batch["batch_id"], "compositing")
        for call, grid_content in grids:
            try:
                cells = split_grid_2x2(grid_content)
            except ValueError as exc:
                self.repository.record_candidate(
                    batch, call_id=call["call_id"], grid_cell=0, status="rejected",
                    rejection_reason="invalid", fingerprint="", pattern_asset_id="",
                )
                continue
            for grid_cell, content in enumerate(cells, start=1):
                fingerprint = hashlib.sha256(content).hexdigest()
                pattern_asset = self._save_asset(batch, "pattern_candidate", f"pattern-{call['call_id']}-{grid_cell}.png", content)
                try:
                    composite = compose_fixed_scene(template_content, content, calibration)
                    composite_asset = self._save_asset(
                        batch, "fixed_composite", f"composite-{call['call_id']}-{grid_cell}.png", composite
                    )
                except Exception:
                    self.repository.record_candidate(
                        batch, call_id=call["call_id"], grid_cell=grid_cell, status="rejected",
                        rejection_reason="composite_error", fingerprint=fingerprint,
                        pattern_asset_id=pattern_asset["asset_id"],
                    )
                    continue
                item = self.repository.accept_candidate(
                    batch,
                    call_id=call["call_id"],
                    grid_cell=grid_cell,
                    fingerprint=fingerprint,
                    pattern_asset_id=pattern_asset["asset_id"],
                    composite_asset_id=composite_asset["asset_id"],
                )

    def _process_style_grids(
        self,
        batch: dict[str, Any],
        grids: list[tuple[dict[str, Any], Any, list[Any], list[str]]],
        billing_run: PodBillingRun,
        execution_epoch: int | None = None,
    ) -> None:
        execution_epoch = execution_epoch if execution_epoch is not None else billing_run.execution_epoch or None
        self._set_batch_stage(batch["batch_id"], "compositing", execution_epoch)
        roles = LISTING_IMAGE_ROLES
        title_futures: dict[Future[Any], int] = {}
        for call, _grid, panels, fingerprints in grids:
            style_index = call["style_index"]
            lifestyle_public_ready = False
            for variant_index, (role, panel, fingerprint) in enumerate(
                zip(roles, panels, fingerprints, strict=True), start=1
            ):
                panel_asset = self._save_asset(
                    batch,
                    "direct_listing_panel",
                    f"style-{style_index}-{role}{panel.suffix}",
                    panel.content,
                )
                public_url = ""
                publish_error = ""
                for _attempt in (1, 2):
                    try:
                        public_url = self.ai_runtime.publish_listing_image(
                            panel, namespace=batch["workspace_id"], role=role
                        )
                        break
                    except Exception as exc:
                        publish_error = str(exc).strip() or exc.__class__.__name__
                if not public_url:
                    self.repository.finish_style_grid_result(
                        batch, style_index=style_index, variant_index=variant_index, call_id=call["call_id"],
                        status="failed", pattern_asset_id=panel_asset["asset_id"], role=role,
                        fingerprint=fingerprint, error_message=f"publish_error: {publish_error}",
                        execution_epoch=execution_epoch,
                    )
                    continue
                self.repository.finish_style_grid_result(
                    batch, style_index=style_index, variant_index=variant_index, call_id=call["call_id"],
                    status="completed", pattern_asset_id=panel_asset["asset_id"],
                    composite_asset_id=panel_asset["asset_id"], fingerprint=fingerprint,
                    role=role, public_url=public_url, execution_epoch=execution_epoch,
                )
                if role == "lifestyle":
                    lifestyle_public_ready = True
                    if self.title_runtime is not None:
                        try:
                            title_call_ids = self._title_call_ids(
                                billing_run, batch["batch_id"], style_index
                            )
                            # A resumed initial batch may revisit persisted images
                            # after every reserved title attempt has already been
                            # consumed. Preserve the original title failure; only
                            # an explicit title retry may create a new call plan.
                            if not title_call_ids:
                                continue
                            self.repository.claim_style_title(
                                batch["batch_id"],
                                style_index,
                                style_task_id=call["call_id"],
                                allow_billing_resume=billing_run.resumed,
                                execution_epoch=execution_epoch,
                            )
                            future = self.title_runtime.submit(
                                self._generate_style_title,
                                batch,
                                style_index,
                                call["call_id"],
                                panel,
                                billing_run,
                                title_call_ids,
                                execution_epoch,
                            )
                            title_futures[future] = style_index
                        except PodExecutionExpired:
                            raise
                        except Exception as exc:
                            try:
                                self.repository.fail_style_title(
                                    batch["batch_id"],
                                    style_index,
                                    str(exc),
                                    style_task_id=call["call_id"],
                                    execution_epoch=execution_epoch,
                                )
                            except Exception:
                                pass
            if self.title_runtime is None:
                continue
            if not lifestyle_public_ready:
                self.repository.fail_style_title(
                    batch["batch_id"], style_index, "本款主图未成功发布，暂不能生成标题",
                    style_task_id=call["call_id"],
                    execution_epoch=execution_epoch,
                )
                continue
        for future in as_completed(title_futures):
            try:
                future.result()
            except PodBillingAuthorizationRequired:
                for pending in title_futures:
                    if pending is not future:
                        pending.cancel()
                raise
            except PodExecutionExpired:
                for pending in title_futures:
                    if pending is not future:
                        pending.cancel()
                raise

    def _generate_style_title(
        self,
        batch: dict[str, Any],
        style_index: int,
        style_task_id: str,
        hero: Any,
        billing_run: PodBillingRun,
        provider_call_ids: tuple[str, ...],
        execution_epoch: int | None = None,
    ) -> None:
        if self.title_runtime is None:
            raise RuntimeError("POD title runtime is disabled")
        if not provider_call_ids:
            raise RuntimeError("POD title call plan is missing")
        try:
            from .contracts import BusinessFields

            request = PodTitleRequest(
                style_task_id=style_task_id,
                style_index=style_index,
                hero_image=hero.content,
                hero_content_type=hero.content_type,
                business_fields=BusinessFields.model_validate(batch["business_fields"]),
                creative_prompt=batch["creative_prompt"],
                accepted_titles=self.repository.accepted_style_titles(
                    batch["batch_id"], exclude_style_index=style_index
                ),
            )
            title_kwargs = {
                "grant": billing_run.grant,
                "call_id": provider_call_ids[0],
                "call_ids": provider_call_ids,
                "on_outcome": lambda call_id, status: billing_run.record(
                    call_id, "pod.title", status
                ),
            }
            if _accepts_keyword(self.title_runtime.generate_title, "on_start"):
                title_kwargs["on_start"] = lambda call_id: billing_run.start(
                    call_id, "pod.title"
                )
            result = self.title_runtime.generate_title(request, **title_kwargs)
            if not any(billing_run.has_outcome(call_id) for call_id in provider_call_ids):
                # Test doubles and legacy injected runtimes may not emit the
                # optional per-attempt callback; one returned result is one success.
                billing_run.record(provider_call_ids[0], "pod.title", "success")
            persisted = vars(result)
            persisted["visual_signature"] = visual_signature(result)
            self.repository.finish_style_title(
                batch["batch_id"],
                style_index,
                persisted,
                workspace_id=batch["workspace_id"],
                owner_user_id=batch["owner_user_id"],
                style_copy={
                    "title": result.title,
                    "english_title": result.english_title,
                    "description": result.description,
                },
                execution_epoch=execution_epoch,
            )
            self.repository.touch_batch_progress(batch["batch_id"], execution_epoch=execution_epoch)
        except PodBillingAuthorizationRequired:
            raise
        except RuntimeClosedError as exc:
            raise RuntimeError("POD worker stopped while processing this title call") from exc
        except PodExecutionExpired:
            raise
        except Exception as exc:
            if not any(billing_run.has_outcome(call_id) for call_id in provider_call_ids):
                billing_run.record(provider_call_ids[0], "pod.title", "no_return")
            attempt_count = int(getattr(exc, "attempt_count", 0) or 0)
            self.repository.fail_style_title(
                batch["batch_id"], style_index, safe_error_message(exc), attempt_count=attempt_count,
                execution_epoch=execution_epoch,
            )

    @staticmethod
    def _title_call_ids(
        billing_run: PodBillingRun, batch_id: str, style_index: int
    ) -> tuple[str, ...]:
        marker = f":style:{style_index}:"
        matching = tuple(
            call.call_id
            for call in billing_run.plan.calls
            if call.feature == "pod.title" and marker in call.call_id
        )
        if matching:
            return tuple(
                call_id for call_id in matching if billing_run.call_status(call_id) == "planned"
            )
        # Style-keyed batch/resume plans reserve per-style title calls. A style
        # that has no own planned title call must NOT borrow another style's
        # reserved calls; doing so drains the other style's attempts and strands
        # its title in a non-terminal state, leaving the batch unexportable.
        # Only legacy single-style plans (no :style: marker) may fall back to
        # every planned title call, all of which belong to that one style.
        if any(
            call.feature == "pod.title" and ":style:" in call.call_id
            for call in billing_run.plan.calls
        ):
            return ()
        return tuple(
            call.call_id
            for call in billing_run.plan.calls
            if call.feature == "pod.title" and billing_run.call_status(call.call_id) == "planned"
        )

    @staticmethod
    def _style_owns_image_call(billing_run: PodBillingRun, style_index: int) -> bool:
        """True when the plan generates this style's four images again.

        Such a style's title is (re)submitted by `_process_style_grids` as soon
        as its lifestyle panel publishes, so this title-only pass must skip it.
        """
        marker = f":style:{style_index}:"
        return any(
            call.feature == "pod.image" and marker in call.call_id
            for call in billing_run.plan.calls
        )

    @staticmethod
    def _image_call_id(billing_run: PodBillingRun, style_index: int, attempt: int) -> str:
        marker = f":style:{style_index}:"
        suffix = f":image:{attempt}"
        matching = [
            call.call_id
            for call in billing_run.plan.calls
            if call.feature == "pod.image" and marker in call.call_id and call.call_id.endswith(suffix)
        ]
        if len(matching) == 1:
            return matching[0]
        # Legacy per-style retry plans have one image call for each attempt.
        fallback = [
            call.call_id
            for call in billing_run.plan.calls
            if call.feature == "pod.image" and call.call_id.endswith(suffix)
        ]
        if len(fallback) == 1:
            return fallback[0]
        raise RuntimeError(f"POD image call plan is missing style {style_index} attempt {attempt}")

    def _lifestyle_media(self, context: dict[str, Any]) -> Any:
        batch = context["batch"]
        asset = self.repository.get_asset(
            context["lifestyle"]["pattern_asset_id"], batch["workspace_id"], batch["owner_user_id"]
        )
        return SimpleNamespace(
            stage="grid_image_1",
            content=self.assets.read(asset["relative_path"]),
            content_type=asset["content_type"],
            suffix=Path(asset["filename"]).suffix or ".png",
            provider="stored-crop",
            model="pillow",
            reference_count=1,
        )

    def _save_asset(self, batch: dict[str, Any], kind: str, filename: str, content: bytes) -> dict[str, Any]:
        stored = self.assets.save_image(batch["workspace_id"], batch["owner_user_id"], content)
        return self.repository.create_asset(
            workspace_id=batch["workspace_id"],
            owner_user_id=batch["owner_user_id"],
            kind=kind,
            filename=filename,
            relative_path=stored.relative_path,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
        )

    def _forget(self, key: tuple[str, str], completed: Future[Any]) -> None:
        with self._futures_lock:
            if self._futures.get(key) is completed:
                self._futures.pop(key, None)

    def _attach_forget_callback(self, key: tuple[str, str], future: Future[Any]) -> None:
        future.add_done_callback(lambda completed: self._forget(key, completed))

    def _discard_billing_run(self, key: str, run: PodBillingRun) -> None:
        with self._futures_lock:
            if self._billing_runs.get(key) is run:
                self._billing_runs.pop(key, None)


def _calibration(value: str):
    from .contracts import Calibration

    return Calibration.model_validate_json(value)


def _accepts_keyword(function: Any, name: str) -> bool:
    parameters = inspect.signature(function).parameters
    return name in parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
