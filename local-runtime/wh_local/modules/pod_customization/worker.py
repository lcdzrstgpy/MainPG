from __future__ import annotations

import hashlib
import math
import inspect
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ...session import Actor
from .assets import PodAssetStore
from .errors import image_provider_outcome_for_exception, safe_error_message
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


class PodBatchCancelled(RuntimeClosedError):
    """Raised from a batch control checkpoint when the batch was cancelled."""


class PodBatchPaused(RuntimeClosedError):
    """Raised from a batch control checkpoint when the batch was paused."""


@dataclass
class PodBillingRun:
    actor: Actor
    coordinator: PodBillingCoordinator
    plan: PodCallPlan
    grant: PodExecutionGrant = field(repr=False)
    repository: PodCustomizationRepository | None = field(default=None, repr=False)
    action_key: str = ""
    resumed: bool = False
    _outcomes: dict[str, PodCallOutcome] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def record(self, call_id: str, feature: str, status: str) -> None:
        outcome = PodCallOutcome(call_id, feature, status)  # type: ignore[arg-type]
        with self._lock:
            existing = self._outcomes.get(call_id)
            if existing is not None and existing != outcome:
                raise RuntimeError(f"POD call {call_id} has conflicting outcomes")
            if self.repository is not None:
                self.repository.record_billing_outcome(self.action_key, outcome)
            self._outcomes[call_id] = outcome

    def start(self, call_id: str, feature: str) -> None:
        provider = "ark" if feature == "pod.title" else "wuyin"
        if not self.grant.provider_key(provider):
            message = "POD billing grant expired; sign in to resume this action"
            if self.repository is not None:
                self.repository.mark_billing_auth_required(self.action_key, message)
            raise PodBillingAuthorizationRequired(message)
        if self.repository is not None:
            self.repository.start_billing_call(self.action_key, call_id, feature)

    def settle(self) -> None:
        if self.repository is not None:
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
    ) -> None:
        self.repository = repository
        self.assets = assets
        self.ai_runtime = ai_runtime
        self.title_runtime = title_runtime
        self._batch_action_lock = threading.RLock()
        self._coordinator = ThreadPoolExecutor(
            max_workers=max(1, min(coordinator_workers, 4)),
            thread_name_prefix="pod-customization-batch",
        )
        self._futures: dict[tuple[str, str], Future[Any]] = {}
        self._futures_lock = threading.Lock()
        self._title_batch_locks: dict[str, threading.Lock] = {}
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
                "billing_auth_required",
                "POD execution requires a fresh short-lived billing grant",
            )
            return
        billing_paused = False
        paused = False
        cancelled = False
        try:
            self._process_batch_authorized(batch_id, run)
        except PodBillingAuthorizationRequired as exc:
            billing_paused = True
            self.repository.mark_billing_auth_required(run.action_key, str(exc))
            try:
                self.repository.set_batch_status(batch_id, "billing_auth_required", str(exc))
            except PodRepositoryError:
                pass
        except PodBatchPaused as exc:
            paused = True
            # 暂停保留已入库进度；丢弃短期 grant，恢复时通过 billing recovery 重新授权后继续。
            self.repository.mark_billing_auth_required(run.action_key, "POD 批次已暂停，重新授权后可继续")
            self.repository.mark_batch_paused(batch_id, str(exc))
        except PodBatchCancelled as exc:
            cancelled = True
            self.repository.fail_remaining_items(batch_id, "POD 批次已取消")
            self.repository.fail_pending_titles(batch_id, "POD 批次已取消")
            self.repository.mark_batch_cancelled(batch_id, str(exc))
        except Exception as exc:
            try:
                self.repository.set_batch_status(batch_id, "failed", str(exc))
            except PodRepositoryError:
                pass
        finally:
            if cancelled:
                # 取消后结算，把未使用的冻结积分返还；结算失败不回退 cancelled 终态。
                try:
                    run.settle()
                except Exception as exc:
                    self.repository.mark_billing_pending(run.action_key, safe_error_message(exc))
            elif not billing_paused and not paused:
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

    def _set_batch_stage(self, batch_id: str, status: str) -> None:
        """Do not overwrite an operator's pause or cancel request while draining."""
        if self.repository.get_batch_status(batch_id) in {"pausing", "cancelling"}:
            return
        self.repository.set_batch_status(batch_id, status)

    def _process_batch_authorized(self, batch_id: str, billing_run: PodBillingRun) -> None:
        current = self.repository.get_batch_internal(batch_id)
        if not self.repository.claim_batch(
            batch_id,
            allow_billing_resume=current["status"] == "billing_auth_required",
        ):
            batch = self.repository.get_batch_internal(batch_id)
            if batch["status"] in {"completed", "partial_failure", "failed", "cancelled"}:
                return
            if batch["status"] == "cancelling":
                raise PodBatchCancelled("POD 批次已取消")
            if batch["status"] in {"pausing", "paused"}:
                raise PodBatchPaused("POD 批次已暂停")
            raise RuntimeError("POD batch is already running")
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
                    batch, template_content, template_asset["content_type"], billing_run
                )
                self.repository.fail_remaining_items(batch_id, "本款图片生成未返回完整结果")
                self.repository.fail_unready_titles(batch_id, "本款四张公开图片未完整生成，暂不能生成标题")
                if self.title_runtime is None:
                    settled = self.repository.get_batch_internal(batch_id)
                    status = "completed" if settled["failed_count"] == 0 else (
                        "partial_failure" if settled["completed_count"] else "failed"
                    )
                    self.repository.set_batch_status(batch_id, status)
                else:
                    self.repository.settle_batch_by_listing_readiness(batch_id)
                return
            self.repository.fail_remaining_items(
                batch_id,
                "legacy non-style POD batches are read-only; create a new reference-locked style batch",
            )
            self.repository.set_batch_status(batch_id, "failed")
        except PodBillingAuthorizationRequired:
            raise
        except (PodBatchCancelled, PodBatchPaused):
            raise
        except Exception as exc:
            self.repository.fail_remaining_items(batch_id, str(exc))
            self.repository.fail_unready_titles(batch_id, str(exc))
            if self.title_runtime is None:
                settled = self.repository.get_batch_internal(batch_id)
                status = "partial_failure" if settled["completed_count"] else "failed"
                self.repository.set_batch_status(batch_id, status, str(exc))
            else:
                self.repository.settle_batch_by_listing_readiness(batch_id, str(exc))

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
        billing_paused = False
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
        except PodBillingAuthorizationRequired as exc:
            billing_paused = True
            self.repository.mark_billing_auth_required(run.action_key, str(exc))
            self.repository.set_batch_status(batch_id, "billing_auth_required", str(exc))
        except Exception as exc:
            if not provider_returned:
                run.record(run.plan.calls[0].call_id, "pod.image", "no_return")
            self.repository.finish_scene_optimization(batch_id, item_id, error_message=str(exc))
        finally:
            if not billing_paused:
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
        billing_paused = False
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
        except PodBillingAuthorizationRequired as exc:
            billing_paused = True
            self.repository.mark_billing_auth_required(run.action_key, str(exc))
            self.repository.set_batch_status(batch_id, "billing_auth_required", str(exc))
        except Exception as exc:
            self.repository.fail_item_regeneration(batch_id, item_id, str(exc))
        finally:
            if not billing_paused:
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
        billing_paused = False
        try:
            for attempt in (1, 2):
                call_kind = "regenerate_style" if attempt == 1 else "regenerate_style_retry"
                call_index = self.repository.next_generation_call_index(batch_id, call_kind)
                prompt = build_style_listing_prompt(base_prompt, style_index=style_index, attempt=attempt)
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
            if self.title_runtime is None:
                settled = self.repository.get_batch_internal(batch_id)
                status = "completed" if settled["failed_count"] == 0 else (
                    "partial_failure" if settled["completed_count"] else "failed"
                )
                self.repository.set_batch_status(batch_id, status)
            else:
                self.repository.settle_batch_by_listing_readiness(batch_id)
        except PodBillingAuthorizationRequired as exc:
            if not finalize:
                raise
            billing_paused = True
            self.repository.mark_billing_auth_required(run.action_key, str(exc))
            self.repository.set_batch_status(batch_id, "billing_auth_required", str(exc))
        except Exception as exc:
            self.repository.fail_style_grid(batch, style_index, str(exc))
            self.repository.fail_unready_titles(batch_id, str(exc))
            if self.title_runtime is None:
                settled = self.repository.get_batch_internal(batch_id)
                status = "partial_failure" if settled["completed_count"] else "failed"
                self.repository.set_batch_status(batch_id, status, str(exc))
            else:
                self.repository.settle_batch_by_listing_readiness(batch_id, str(exc))
        finally:
            if finalize and not billing_paused:
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
        billing_paused = False
        try:
            self._generate_style_title(
                context["batch"],
                style_index,
                title["style_task_id"],
                self._lifestyle_media(context),
                run,
                self._title_call_ids(run, batch_id, style_index),
            )
        except PodBillingAuthorizationRequired as exc:
            if not finalize:
                raise
            billing_paused = True
            self.repository.mark_billing_auth_required(run.action_key, str(exc))
            self.repository.set_batch_status(batch_id, "billing_auth_required", str(exc))
        except Exception as exc:
            self.repository.fail_style_title(batch_id, style_index, str(exc))
        finally:
            if finalize and not billing_paused:
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
        billing_paused = False
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
        except PodBillingAuthorizationRequired as exc:
            billing_paused = True
            self.repository.mark_billing_auth_required(run.action_key, str(exc))
            self.repository.set_batch_status(batch_id, "billing_auth_required", str(exc))
        finally:
            if not billing_paused:
                try:
                    run.settle()
                except Exception as exc:
                    self.repository.mark_billing_pending(run.action_key, safe_error_message(exc))
            self._discard_billing_run(f"batch-retry:{batch_id}", run)

    def close(self) -> None:
        if self._closing.is_set():
            return
        self._closing.set()
        self.repository.pause_billing_runs_for_shutdown()
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
        completed_by_style: dict[int, int] = {}
        for item in batch.get("items", []):
            if item.get("status") == "completed":
                style_index = int(item.get("style_index") or 0)
                completed_by_style[style_index] = completed_by_style.get(style_index, 0) + 1
        completed_titles = {
            int(title["style_index"])
            for title in batch.get("style_titles", [])
            if title.get("status") == "completed"
        }
        style_indices = [
            index
            for index in range(1, batch["requested_count"] + 1)
            if completed_by_style.get(index, 0) < 4
            or (self.title_runtime is not None and index not in completed_titles)
        ]
        processed: set[int] = set()
        retry_reasons: dict[int, str] = {}

        for attempt in (1, 2):
            pending = [index for index in style_indices if index not in processed]
            if not pending:
                break
            attempt_processed, attempt_errors, pause_requested = self._stream_style_attempts(
                batch,
                pending,
                template_content,
                template_content_type,
                attempt=attempt,
                billing_run=billing_run,
            )
            retry_reasons.update(attempt_errors)
            processed |= attempt_processed
            if pause_requested or self.repository.get_batch_status(batch["batch_id"]) == "pausing":
                raise PodBatchPaused("POD 批次已暂停")

        for style_index in style_indices:
            if style_index not in processed:
                self.repository.fail_style_grid(
                    batch,
                    style_index,
                    retry_reasons.get(style_index, "本款两次图片生成均失败"),
                )

    def _stream_style_attempts(
        self,
        batch: dict[str, Any],
        style_indices: list[int],
        template_content: bytes,
        template_content_type: str,
        *,
        attempt: int,
        billing_run: PodBillingRun,
    ) -> tuple[set[int], dict[int, str], bool]:
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
        call_kind = "initial" if attempt == 1 else "retry"
        pending = iter(style_indices)
        exhausted = False
        pause_requested = False
        max_in_flight = self._image_worker_count()

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
                prompt = build_style_listing_prompt(
                    batch["prompt_snapshot"], style_index=style_index, attempt=attempt
                )
                call = self.repository.get_or_create_generation_call(
                    batch,
                    call_kind=call_kind,
                    call_index=style_index,
                    prompt_snapshot=prompt,
                )
                call["style_index"] = style_index
                provider_call_id = f"{batch['batch_id']}:style:{style_index}:image:{attempt}"
                billing_status = billing_run.call_status(provider_call_id)
                if billing_status == "success":
                    asset_id = str(call.get("grid_asset_id") or "")
                    if not asset_id:
                        errors[style_index] = "provider call completed before restart but its asset is unavailable"
                        continue
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
                    continue
                if billing_status in {"started", "no_return"}:
                    errors[style_index] = (
                        "provider call outcome was uncertain during restart"
                        if billing_status == "started"
                        else "provider returned no result"
                    )
                    continue
                request = DirectListingGridRequest(
                    trial_id=f"{batch['batch_id']}-style-{style_index}-attempt-{attempt}",
                    template_id=batch["template_id"],
                    template_image=template_content,
                    template_content_type=template_content_type,
                    prompt=prompt,
                    attempt=attempt,
                )
                future = self.ai_runtime.submit(
                    self._generate_listing_grid,
                    batch,
                    call,
                    request,
                    billing_run,
                    provider_call_id,
                )
                image_futures[future] = (call, style_index)

            # Nothing left in flight; the attempt is complete.
            if not image_futures and not postprocess_futures:
                break

            # Wait for the first completion across generation and post-processing.
            done, _ = wait(
                tuple(image_futures) + tuple(postprocess_futures),
                return_when=FIRST_COMPLETED,
            )
            for future in done:
                if future in image_futures:
                    call, style_index = image_futures.pop(future)
                    try:
                        grid = future.result()
                    except PodBillingAuthorizationRequired:
                        raise
                    except PodBatchPaused:
                        pause_requested = True
                        continue
                    except PodBatchCancelled:
                        raise
                    except Exception as exc:
                        errors[style_index] = str(exc).strip() or exc.__class__.__name__
                        continue
                    submit_postprocess(style_index, call, grid)
                elif future in postprocess_futures:
                    style_index = postprocess_futures.pop(future)
                    try:
                        future.result()
                        processed.add(style_index)
                    except PodBillingAuthorizationRequired:
                        for pending_future in postprocess_futures:
                            pending_future.cancel()
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

        return processed, errors, pause_requested

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
    ) -> Any:
        self.repository.mark_generation_call_running(call["call_id"])
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
            self.repository.finish_generation_call(call["call_id"], status="succeeded", grid_asset_id=stored["asset_id"])
            return media
        except PodBillingAuthorizationRequired:
            raise
        except PodBatchPaused:
            self.repository.requeue_generation_call(call["call_id"])
            raise
        except PodBatchCancelled:
            self.repository.requeue_generation_call(call["call_id"])
            raise
        except RuntimeClosedError as exc:
            raise PodBillingAuthorizationRequired(
                "POD runtime stopped before this provider call; sign in to resume"
            ) from exc
        except Exception as exc:
            if not provider_returned:
                billing_run.record(
                    provider_call_id,
                    "pod.image",
                    image_provider_outcome_for_exception(exc),
                )
            self.repository.finish_generation_call(
                call["call_id"], status="failed", error_message=safe_error_message(exc)
            )
            raise

    def _generate_grid(
        self,
        batch: dict[str, Any],
        call: dict[str, Any],
        request: PatternGridRequest,
        billing_run: PodBillingRun,
        provider_call_id: str,
    ) -> bytes:
        self.repository.mark_generation_call_running(call["call_id"])
        try:
            billing_run.start(provider_call_id, "pod.image")
            content = self.ai_runtime.generate_pattern_grid(
                request, grant=billing_run.grant, call_id=provider_call_id
            )
            billing_run.record(provider_call_id, "pod.image", "success")
            stored = self._save_asset(batch, "grid", f"{call['call_kind']}-{call['call_index']}.png", content)
            self.repository.finish_generation_call(call["call_id"], status="succeeded", grid_asset_id=stored["asset_id"])
            return content
        except PodBillingAuthorizationRequired:
            raise
        except Exception as exc:
            if not billing_run.has_outcome(provider_call_id):
                billing_run.record(provider_call_id, "pod.image", "no_return")
            self.repository.finish_generation_call(
                call["call_id"], status="failed", error_message=safe_error_message(exc)
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
    ) -> None:
        self._set_batch_stage(batch["batch_id"], "compositing")
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
                    )
                    continue
                self.repository.finish_style_grid_result(
                    batch, style_index=style_index, variant_index=variant_index, call_id=call["call_id"],
                    status="completed", pattern_asset_id=panel_asset["asset_id"],
                    composite_asset_id=panel_asset["asset_id"], fingerprint=fingerprint,
                    role=role, public_url=public_url,
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
                            )
                            future = self.title_runtime.submit(
                                self._generate_style_title,
                                batch,
                                style_index,
                                call["call_id"],
                                panel,
                                billing_run,
                                title_call_ids,
                            )
                            title_futures[future] = style_index
                        except Exception as exc:
                            try:
                                self.repository.fail_style_title(
                                    batch["batch_id"],
                                    style_index,
                                    str(exc),
                                    style_task_id=call["call_id"],
                                )
                            except Exception:
                                pass
            if self.title_runtime is None:
                continue
            if not lifestyle_public_ready:
                self.repository.fail_style_title(
                    batch["batch_id"], style_index, "本款主图未成功发布，暂不能生成标题",
                    style_task_id=call["call_id"],
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

    def _generate_style_title(
        self,
        batch: dict[str, Any],
        style_index: int,
        style_task_id: str,
        hero: Any,
        billing_run: PodBillingRun,
        provider_call_ids: tuple[str, ...],
    ) -> None:
        if self.title_runtime is None:
            raise RuntimeError("POD title runtime is disabled")
        if not provider_call_ids:
            raise RuntimeError("POD title call plan is missing")
        with self._title_batch_lock(batch["batch_id"]):
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
                )
            except PodBillingAuthorizationRequired:
                raise
            except RuntimeClosedError as exc:
                raise PodBillingAuthorizationRequired(
                    "POD runtime stopped before the next title call; sign in to resume"
                ) from exc
            except Exception as exc:
                if not any(billing_run.has_outcome(call_id) for call_id in provider_call_ids):
                    billing_run.record(provider_call_ids[0], "pod.title", "no_return")
                attempt_count = int(getattr(exc, "attempt_count", 0) or 0)
                self.repository.fail_style_title(
                    batch["batch_id"], style_index, safe_error_message(exc), attempt_count=attempt_count
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
        return tuple(
            call.call_id
            for call in billing_run.plan.calls
            if call.feature == "pod.title" and billing_run.call_status(call.call_id) == "planned"
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

    def _title_batch_lock(self, batch_id: str) -> threading.Lock:
        with self._futures_lock:
            return self._title_batch_locks.setdefault(batch_id, threading.Lock())

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
