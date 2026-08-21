from __future__ import annotations

import math
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ...session import Actor
from .assets import PodAssetStore
from .billing_contract import (
    PodBillingCoordinator,
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
)
from .images import PatternQualityGate, compose_fixed_scene, split_grid_2x2
from .prompts import LISTING_IMAGE_ROLES, build_style_listing_prompt
from .repository import PodCustomizationRepository
from .runtime_contracts import DirectListingGridRequest, PatternGridRequest, PodAiRuntime, SceneOptimizationRequest
from .title_runtime import PodTitleRequest, visual_signature


@dataclass
class PodBillingRun:
    actor: Actor
    coordinator: PodBillingCoordinator
    plan: PodCallPlan
    grant: PodExecutionGrant = field(repr=False)
    _outcomes: dict[str, PodCallOutcome] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def record(self, call_id: str, feature: str, status: str) -> None:
        outcome = PodCallOutcome(call_id, feature, status)  # type: ignore[arg-type]
        with self._lock:
            existing = self._outcomes.get(call_id)
            if existing is not None and existing != outcome:
                raise RuntimeError(f"POD call {call_id} has conflicting outcomes")
            self._outcomes[call_id] = outcome

    def settle(self) -> None:
        with self._lock:
            outcomes = tuple(
                self._outcomes.get(call.call_id)
                or PodCallOutcome(call.call_id, call.feature, "no_return")
                for call in self.plan.calls
            )
        self.coordinator.settle(self.actor, self.grant, self.plan, outcomes)

    def has_outcome(self, call_id: str) -> bool:
        with self._lock:
            return call_id in self._outcomes


class PodBatchWorker:
    """Coordinates POD jobs without borrowing product-processing workers or gates."""

    def __init__(
        self,
        repository: PodCustomizationRepository,
        assets: PodAssetStore,
        ai_runtime: PodAiRuntime,
        quality_gate: PatternQualityGate,
        *,
        title_runtime: Any | None = None,
        coordinator_workers: int = 2,
    ) -> None:
        self.repository = repository
        self.assets = assets
        self.ai_runtime = ai_runtime
        self.title_runtime = title_runtime
        self.quality_gate = quality_gate
        self.style_quality_gate = PatternQualityGate(
            text_inspector=quality_gate.text_inspector,
            duplicate_distance=max(1, quality_gate.duplicate_distance),
        )
        self._coordinator = ThreadPoolExecutor(
            max_workers=max(1, min(coordinator_workers, 4)),
            thread_name_prefix="pod-customization-batch",
        )
        self._futures: dict[tuple[str, str], Future[Any]] = {}
        self._futures_lock = threading.Lock()
        self._title_batch_locks: dict[str, threading.Lock] = {}
        self._billing_runs: dict[str, PodBillingRun] = {}

    def register_billing_run(self, batch_id: str, billing_run: PodBillingRun) -> None:
        with self._futures_lock:
            self._billing_runs[batch_id] = billing_run

    def register_action_billing_run(self, action_key: str, billing_run: PodBillingRun) -> None:
        with self._futures_lock:
            self._billing_runs[action_key] = billing_run

    def submit(self, batch_id: str, billing_run: PodBillingRun | None = None) -> Future[Any]:
        if billing_run is not None:
            self.register_billing_run(batch_id, billing_run)
        key = ("batch", batch_id)
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(self.process_batch, batch_id)
            self._futures[key] = future
            future.add_done_callback(lambda _: self._forget(key))
            return future

    def submit_scene_optimization(
        self, batch_id: str, item_id: str, instruction: str, billing_run: PodBillingRun | None = None
    ) -> Future[Any]:
        key = ("scene", item_id)
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(self.optimize_scene, batch_id, item_id, instruction, billing_run)
            self._futures[key] = future
            future.add_done_callback(lambda _: self._forget(key))
            return future

    def submit_item_regeneration(
        self, batch_id: str, item_id: str, creative_prompt: str, billing_run: PodBillingRun | None = None
    ) -> Future[Any]:
        key = ("regenerate", item_id)
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(self.regenerate_item, batch_id, item_id, creative_prompt, billing_run)
            self._futures[key] = future
            future.add_done_callback(lambda _: self._forget(key))
            return future

    def submit_style_regeneration(
        self,
        batch_id: str,
        style_index: int,
        creative_prompt: str,
        billing_run: PodBillingRun | None = None,
    ) -> Future[Any]:
        key = ("regenerate-style", f"{batch_id}:{style_index}")
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self._coordinator.submit(
                self.regenerate_style, batch_id, style_index, creative_prompt, billing_run
            )
            self._futures[key] = future
            future.add_done_callback(lambda _: self._forget(key))
            return future

    def submit_title_regeneration(
        self, batch_id: str, style_index: int, billing_run: PodBillingRun | None = None
    ) -> Future[Any]:
        if self.title_runtime is None:
            raise RuntimeError("POD title runtime is disabled")
        key = ("regenerate-title", f"{batch_id}:{style_index}")
        with self._futures_lock:
            existing = self._futures.get(key)
            if existing is not None and not existing.done():
                return existing
            future = self.title_runtime.submit(self.regenerate_title, batch_id, style_index, billing_run)
            self._futures[key] = future
            future.add_done_callback(lambda _: self._forget(key))
            return future

    def process_batch(self, batch_id: str, billing_run: PodBillingRun | None = None) -> None:
        run = billing_run or self._billing_runs.get(batch_id)
        if run is None:
            self.repository.set_batch_status(
                batch_id,
                "billing_auth_required",
                "POD execution requires a fresh short-lived billing grant",
            )
            return
        try:
            self._process_batch_authorized(batch_id, run)
        finally:
            try:
                run.settle()
            except Exception as exc:
                self.repository.set_batch_status(batch_id, "settlement_pending", str(exc))
            finally:
                self._discard_billing_run(batch_id, run)

    def _process_batch_authorized(self, batch_id: str, billing_run: PodBillingRun) -> None:
        if not self.repository.claim_batch(batch_id):
            batch = self.repository.get_batch_internal(batch_id)
            if batch["status"] in {"completed", "partial_failure", "failed"}:
                return
            raise RuntimeError("POD batch is already running")
        batch = self.repository.get_batch_internal(batch_id)
        try:
            snapshot = batch["template"]
            template_asset = self.repository.get_asset(
                snapshot["asset_id"], batch["workspace_id"], batch["owner_user_id"]
            )
            template_content = self.assets.read(template_asset["relative_path"])
            if batch.get("style_grid"):
                initial = self._run_style_grid_calls(
                    batch, template_content, template_asset["content_type"], billing_run
                )
                self._process_style_grids(batch, initial, billing_run)
                self.repository.fail_remaining_items(batch_id, "本款四宫格生成未返回完整结果")
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
        except Exception as exc:
            self.repository.fail_remaining_items(batch_id, str(exc))
            self.repository.fail_unready_titles(batch_id, str(exc))
            if self.title_runtime is None:
                settled = self.repository.get_batch_internal(batch_id)
                status = "partial_failure" if settled["completed_count"] else "failed"
                self.repository.set_batch_status(batch_id, status, str(exc))
            else:
                self.repository.settle_batch_by_listing_readiness(batch_id, str(exc))

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
                self.repository.set_batch_status(batch_id, "settlement_pending", str(exc))
            finally:
                self._discard_billing_run(f"scene:{batch_id}:{item_id}", run)

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
            fingerprints = self.repository.accepted_fingerprints(batch_id)
            for grid_cell, cell in enumerate(split_grid_2x2(content), start=1):
                assessment = self.quality_gate.assess(cell, accepted_fingerprints=fingerprints)
                pattern_asset = self._save_asset(
                    batch, "pattern_candidate", f"regenerate-{call['call_id']}-{grid_cell}.png", cell
                )
                if not assessment.accepted:
                    self.repository.record_candidate(
                        batch,
                        call_id=call["call_id"],
                        grid_cell=grid_cell,
                        status="rejected",
                        rejection_reason=assessment.rejection_reason,
                        fingerprint=assessment.fingerprint,
                        pattern_asset_id=pattern_asset["asset_id"],
                    )
                    continue
                composite = compose_fixed_scene(template_content, cell, calibration)
                composite_asset = self._save_asset(
                    batch, "fixed_composite", f"regenerate-composite-{item_id}.png", composite
                )
                self.repository.finish_item_regeneration(
                    batch,
                    item_id,
                    call_id=call["call_id"],
                    grid_cell=grid_cell,
                    fingerprint=assessment.fingerprint,
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
                self.repository.set_batch_status(batch_id, "settlement_pending", str(exc))
            finally:
                self._discard_billing_run(f"item:{batch_id}:{item_id}", run)

    def regenerate_style(
        self,
        batch_id: str,
        style_index: int,
        creative_prompt: str,
        billing_run: PodBillingRun | None = None,
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
        accepted_fingerprints = self._accepted_style_fingerprints(
            batch, exclude_style_index=style_index
        )
        prepared: list[tuple[dict[str, Any], Any, list[Any], str]] = []
        last_error = "整款重新生成未返回完整结果"
        last_call_id = ""
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
                    provider_call_id = f"{run.plan.calls[0].call_id.rsplit(':image:', 1)[0]}:image:{attempt}"
                    grid = self.ai_runtime.submit(
                        self._generate_listing_grid, batch, call, request, run, provider_call_id
                    ).result()
                    panels, fingerprint = self._validate_style_grid(grid, accepted_fingerprints)
                    prepared = [(call, grid, panels, fingerprint)]
                    break
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
            try:
                run.settle()
            except Exception as exc:
                self.repository.set_batch_status(batch_id, "settlement_pending", str(exc))
            finally:
                self._discard_billing_run(f"style:{batch_id}:{style_index}", run)

    def regenerate_title(
        self, batch_id: str, style_index: int, billing_run: PodBillingRun | None = None
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
                self._hero_media(context),
                run,
                tuple(call.call_id for call in run.plan.calls if call.feature == "pod.title"),
            )
        except Exception as exc:
            self.repository.fail_style_title(batch_id, style_index, str(exc))
        finally:
            self.repository.settle_batch_by_listing_readiness(batch_id)
            try:
                run.settle()
            except Exception as exc:
                self.repository.set_batch_status(batch_id, "settlement_pending", str(exc))
            finally:
                self._discard_billing_run(f"title:{batch_id}:{style_index}", run)

    def close(self) -> None:
        self._coordinator.shutdown(wait=True, cancel_futures=False)

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

    def _run_style_grid_calls(
        self,
        batch: dict[str, Any],
        template_content: bytes,
        template_content_type: str,
        billing_run: PodBillingRun,
    ) -> list[tuple[dict[str, Any], Any, list[Any], str]]:
        style_indices = list(range(1, batch["requested_count"] + 1))
        first, first_errors = self._submit_style_attempts(
            batch,
            style_indices,
            template_content,
            template_content_type,
            attempt=1,
            billing_run=billing_run,
        )
        accepted_fingerprints = self._accepted_style_fingerprints(batch)
        prepared: dict[int, tuple[dict[str, Any], Any, list[Any], str]] = {}
        retry_reasons = dict(first_errors)
        for style_index in style_indices:
            result = first.get(style_index)
            if result is None:
                continue
            call, grid = result
            try:
                panels, fingerprint = self._validate_style_grid(grid, accepted_fingerprints)
            except Exception as exc:
                retry_reasons[style_index] = str(exc).strip() or exc.__class__.__name__
                continue
            prepared[style_index] = (call, grid, panels, fingerprint)
            accepted_fingerprints.append(fingerprint)

        retry_indices = sorted(set(style_indices) - set(prepared))
        if retry_indices:
            second, second_errors = self._submit_style_attempts(
                batch,
                retry_indices,
                template_content,
                template_content_type,
                attempt=2,
                billing_run=billing_run,
            )
            retry_reasons.update(second_errors)
            for style_index in retry_indices:
                result = second.get(style_index)
                if result is None:
                    continue
                call, grid = result
                try:
                    panels, fingerprint = self._validate_style_grid(grid, accepted_fingerprints)
                except Exception as exc:
                    retry_reasons[style_index] = str(exc).strip() or exc.__class__.__name__
                    continue
                prepared[style_index] = (call, grid, panels, fingerprint)
                accepted_fingerprints.append(fingerprint)

        for style_index in style_indices:
            if style_index not in prepared:
                self.repository.fail_style_grid(
                    batch,
                    style_index,
                    retry_reasons.get(style_index, "本款两次四宫格生成均失败"),
                )
        return [prepared[index] for index in sorted(prepared)]

    def _submit_style_attempts(
        self,
        batch: dict[str, Any],
        style_indices: list[int],
        template_content: bytes,
        template_content_type: str,
        *,
        attempt: int,
        billing_run: PodBillingRun,
    ) -> tuple[dict[int, tuple[dict[str, Any], Any]], dict[int, str]]:
        futures: dict[Future[Any], tuple[dict[str, Any], int]] = {}
        call_kind = "initial" if attempt == 1 else "retry"
        for style_index in style_indices:
            prompt = build_style_listing_prompt(
                batch["prompt_snapshot"], style_index=style_index, attempt=attempt
            )
            call = self.repository.create_generation_call(
                batch,
                call_kind=call_kind,
                call_index=style_index,
                prompt_snapshot=prompt,
            )
            call["style_index"] = style_index
            request = DirectListingGridRequest(
                trial_id=f"{batch['batch_id']}-style-{style_index}-attempt-{attempt}",
                template_id=batch["template_id"],
                template_image=template_content,
                template_content_type=template_content_type,
                prompt=prompt,
                attempt=attempt,
            )
            provider_call_id = f"{batch['batch_id']}:style:{style_index}:image:{attempt}"
            futures[self.ai_runtime.submit(
                self._generate_listing_grid,
                batch,
                call,
                request,
                billing_run,
                provider_call_id,
            )] = (call, style_index)
        completed: dict[int, tuple[dict[str, Any], Any]] = {}
        errors: dict[int, str] = {}
        for future in as_completed(futures):
            call, style_index = futures[future]
            try:
                completed[style_index] = (call, future.result())
            except Exception as exc:
                errors[style_index] = str(exc).strip() or exc.__class__.__name__
        return completed, errors

    def _validate_style_grid(
        self,
        grid: Any,
        accepted_fingerprints: list[str],
    ) -> tuple[list[Any], str]:
        panels = self.ai_runtime.split_listing_grid(grid)
        if len(panels) != 4:
            raise RuntimeError("generated four-grid image did not yield exactly four panels")
        assessment = self.style_quality_gate.assess(
            panels[1].content,
            accepted_fingerprints=accepted_fingerprints,
        )
        if not assessment.accepted:
            reason = assessment.rejection_reason or "invalid"
            raise RuntimeError(f"style_detail_{reason}")
        return panels, assessment.fingerprint

    def _accepted_style_fingerprints(
        self,
        batch: dict[str, Any],
        *,
        exclude_style_index: int | None = None,
    ) -> list[str]:
        return [
            item["pattern_fingerprint"]
            for item in self.repository.get_batch_internal(batch["batch_id"])["items"]
            if item.get("pattern_fingerprint")
            and (exclude_style_index is None or item.get("style_index") != exclude_style_index)
        ]

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
            media = self.ai_runtime.generate_listing_grid(
                request,
                grant=billing_run.grant,
                call_id=provider_call_id,
            )
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
        except Exception as exc:
            if not provider_returned:
                billing_run.record(provider_call_id, "pod.image", "no_return")
            self.repository.finish_generation_call(call["call_id"], status="failed", error_message=str(exc))
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
            content = self.ai_runtime.generate_pattern_grid(
                request, grant=billing_run.grant, call_id=provider_call_id
            )
            billing_run.record(provider_call_id, "pod.image", "success")
            stored = self._save_asset(batch, "grid", f"{call['call_kind']}-{call['call_index']}.png", content)
            self.repository.finish_generation_call(call["call_id"], status="succeeded", grid_asset_id=stored["asset_id"])
            return content
        except Exception as exc:
            if not billing_run.has_outcome(provider_call_id):
                billing_run.record(provider_call_id, "pod.image", "no_return")
            self.repository.finish_generation_call(call["call_id"], status="failed", error_message=str(exc))
            raise

    def _process_grids(
        self,
        batch: dict[str, Any],
        grids: list[tuple[dict[str, Any], bytes]],
        template_content: bytes,
    ) -> None:
        fingerprints = self.repository.accepted_fingerprints(batch["batch_id"])
        calibration = _calibration(batch["template"]["calibration_json"])
        self.repository.set_batch_status(batch["batch_id"], "compositing")
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
                assessment = self.quality_gate.assess(content, accepted_fingerprints=fingerprints)
                pattern_asset = self._save_asset(batch, "pattern_candidate", f"pattern-{call['call_id']}-{grid_cell}.png", content)
                if not assessment.accepted:
                    self.repository.record_candidate(
                        batch, call_id=call["call_id"], grid_cell=grid_cell, status="rejected",
                        rejection_reason=assessment.rejection_reason, fingerprint=assessment.fingerprint,
                        pattern_asset_id=pattern_asset["asset_id"],
                    )
                    continue
                try:
                    composite = compose_fixed_scene(template_content, content, calibration)
                    composite_asset = self._save_asset(
                        batch, "fixed_composite", f"composite-{call['call_id']}-{grid_cell}.png", composite
                    )
                except Exception:
                    self.repository.record_candidate(
                        batch, call_id=call["call_id"], grid_cell=grid_cell, status="rejected",
                        rejection_reason="composite_error", fingerprint=assessment.fingerprint,
                        pattern_asset_id=pattern_asset["asset_id"],
                    )
                    continue
                item = self.repository.accept_candidate(
                    batch,
                    call_id=call["call_id"],
                    grid_cell=grid_cell,
                    fingerprint=assessment.fingerprint,
                    pattern_asset_id=pattern_asset["asset_id"],
                    composite_asset_id=composite_asset["asset_id"],
                )
                if item is not None:
                    fingerprints.append(assessment.fingerprint)

    def _process_style_grids(
        self,
        batch: dict[str, Any],
        grids: list[tuple[dict[str, Any], Any, list[Any], str]],
        billing_run: PodBillingRun,
    ) -> None:
        self.repository.set_batch_status(batch["batch_id"], "compositing")
        roles = LISTING_IMAGE_ROLES
        title_futures: dict[Future[Any], int] = {}
        for call, _grid, panels, fingerprint in grids:
            style_index = call["style_index"]
            hero_public_ready = False
            for variant_index, (role, panel) in enumerate(zip(roles, panels, strict=True), start=1):
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
                if variant_index == 1:
                    hero_public_ready = True
                    if self.title_runtime is not None:
                        try:
                            self.repository.claim_style_title(
                                batch["batch_id"], style_index, style_task_id=call["call_id"]
                            )
                            future = self.title_runtime.submit(
                                self._generate_style_title,
                                batch,
                                style_index,
                                call["call_id"],
                                panel,
                                billing_run,
                                self._title_call_ids(billing_run, batch["batch_id"], style_index),
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
            if not hero_public_ready:
                self.repository.fail_style_title(
                    batch["batch_id"], style_index, "本款 hero 图片未成功发布，暂不能生成标题",
                    style_task_id=call["call_id"],
                )
                continue
        for future in as_completed(title_futures):
            future.result()

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
                    accepted_visual_signatures=self.repository.accepted_visual_signatures(
                        batch["batch_id"], exclude_style_index=style_index
                    ),
                )
                result = self.title_runtime.generate_title(
                    request,
                    grant=billing_run.grant,
                    call_id=provider_call_ids[0],
                    call_ids=provider_call_ids,
                    on_outcome=lambda call_id, status: billing_run.record(
                        call_id, "pod.title", status
                    ),
                )
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
            except Exception as exc:
                if not any(billing_run.has_outcome(call_id) for call_id in provider_call_ids):
                    billing_run.record(provider_call_ids[0], "pod.title", "no_return")
                attempt_count = int(getattr(exc, "attempt_count", 0) or 0)
                self.repository.fail_style_title(
                    batch["batch_id"], style_index, str(exc), attempt_count=attempt_count
                )

    @staticmethod
    def _title_call_ids(
        billing_run: PodBillingRun, batch_id: str, style_index: int
    ) -> tuple[str, ...]:
        prefix = f"{batch_id}:style:{style_index}:title:"
        matching = tuple(
            call.call_id
            for call in billing_run.plan.calls
            if call.feature == "pod.title" and call.call_id.startswith(prefix)
        )
        if matching:
            return matching
        return tuple(call.call_id for call in billing_run.plan.calls if call.feature == "pod.title")

    def _title_batch_lock(self, batch_id: str) -> threading.Lock:
        with self._futures_lock:
            return self._title_batch_locks.setdefault(batch_id, threading.Lock())

    def _hero_media(self, context: dict[str, Any]) -> Any:
        batch = context["batch"]
        asset = self.repository.get_asset(
            context["hero"]["pattern_asset_id"], batch["workspace_id"], batch["owner_user_id"]
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

    def _forget(self, key: tuple[str, str]) -> None:
        with self._futures_lock:
            self._futures.pop(key, None)

    def _discard_billing_run(self, key: str, run: PodBillingRun) -> None:
        with self._futures_lock:
            if self._billing_runs.get(key) is run:
                self._billing_runs.pop(key, None)


def _calibration(value: str):
    from .contracts import Calibration

    return Calibration.model_validate_json(value)
