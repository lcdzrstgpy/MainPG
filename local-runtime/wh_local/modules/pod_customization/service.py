from __future__ import annotations

import json
import inspect
import threading
import uuid
from pathlib import Path
from typing import Any

from ...customer.contracts import CustomerBillingPermissionError
from ...session import Actor
from .assets import PodAssetStore
from .billing_contract import (
    PodBillingAuthorizationRequired,
    PodBillingCoordinator,
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
    PodPlannedCall,
    TITLE_ATTEMPTS,
)
from .contracts import (
    BatchCreate,
    BatchRetryFailedCreate,
    Calibration,
    DirectListingTrialCreate,
    NormalizedPoint,
    NormalizedRect,
)
from .export import (
    DianxiaomiExport,
    analyze_dianxiaomi_export,
    build_pod_dianxiaomi_export,
)
from .export_records import PodExportRecordStore
from .errors import image_provider_outcome_for_exception, safe_error_message
from .repository import PodCustomizationRepository, PodRepositoryError
from .prompts import LISTING_IMAGE_ROLES, build_direct_listing_prompt
from .runtime_contracts import (
    SUPPORTED_TEMPLATE_IMAGE_CONTENT_TYPES,
    DirectListingGridRequest,
    PodAiRuntime,
)
from .title_runtime import PodTitleRequest, visual_signature
from .theme_registry import ThemeRegistry
from .worker import PodBatchWorker, PodBillingRun, POD_PROGRESS_TIMEOUT_SECONDS


class PodCustomizationService:
    def __init__(
        self,
        database_path: Path,
        asset_root: Path,
        ai_runtime: PodAiRuntime,
        *,
        title_runtime: Any | None = None,
        billing_coordinator: PodBillingCoordinator | None = None,
        start_workers: bool = True,
    ) -> None:
        self.database_path = Path(database_path)
        self.assets = PodAssetStore(asset_root)
        self.ai_runtime = ai_runtime
        self.title_runtime = title_runtime
        self.billing_coordinator = billing_coordinator
        self.repository = PodCustomizationRepository(self.database_path)
        self.export_records = PodExportRecordStore(self.database_path)
        if start_workers:
            self.repository.recover_interrupted_batches()
            self.repository.recover_billing_runs()
        theme_registry = self._build_theme_registry(asset_root)
        self.worker = (
            PodBatchWorker(
                self.repository,
                self.assets,
                ai_runtime,
                title_runtime=title_runtime,
                coordinator_workers=getattr(ai_runtime, "batch_workers", 1),
                theme_registry=theme_registry,
            )
            if start_workers
            else None
        )
        self.start_workers = start_workers
        # The repository claim is the cross-process correctness boundary. This
        # short local critical section closes the pre-claim freeze window in a
        # single workbench process, so two rapid clicks cannot create separate
        # per-style billing reservations before one claim loses.
        self._regeneration_lock = threading.RLock()
        # The grant is request-local. If a provider call loses it, that call is
        # recorded as a normal failure and can be retried from the batch UI.
        self._reaper_stop: threading.Event = threading.Event()
        self._reaper_thread: threading.Thread | None = None
        if start_workers:
            self._start_reaper()

    @staticmethod
    def _build_theme_registry(asset_root: Path) -> ThemeRegistry:
        """Create the theme registry, optionally wired to Doubao for enrichment.

        Construction never fails startup: if the Ark client cannot be built
        (unconfigured credentials), the registry still loads and layers any
        persisted Doubao-learned pools, it just won't generate new ones.
        """
        registry_path = Path(asset_root) / "pod_theme_registry.json"
        complete = None
        try:
            from wh_local.modules.product_processing.doubao_ark import DoubaoArkClient, DoubaoArkError

            try:
                complete = DoubaoArkClient(usage_kind="text").complete
            except DoubaoArkError:
                complete = None
        except Exception:
            complete = None
        return ThemeRegistry(registry_path, complete=complete)

    def upload_template(
        self,
        actor: Actor,
        *,
        name: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("template name is required")
        stored = self.assets.save_image(actor.workspace_id, actor.id, content)
        asset = self.repository.create_asset(
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            kind="template",
            filename=filename,
            relative_path=stored.relative_path,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
        )
        template = self.repository.create_template(
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            name=clean_name,
            asset=asset,
        )
        return self._template_payload(template)

    def update_template_calibration(
        self,
        actor: Actor,
        template_id: str,
        calibration: Calibration,
    ) -> dict[str, Any]:
        template = self.repository.update_template_calibration(
            template_id,
            actor.workspace_id,
            actor.id,
            calibration,
        )
        return self._template_payload(template)

    def calibrate_template(self, actor: Actor, template_id: str) -> dict[str, Any]:
        template = self.repository.set_template_calibration_state(
            template_id, actor.workspace_id, actor.id, "calibrating"
        )
        try:
            asset = self.repository.get_asset(template["asset_id"], actor.workspace_id, actor.id)
            calibrator = getattr(self.ai_runtime, "calibrate_template", None)
            calibration = (
                Calibration.model_validate(calibrator(self.assets.read(asset["relative_path"])))
                if callable(calibrator)
                else Calibration(
                    mask=NormalizedRect(x=0.2, y=0.2, width=0.6, height=0.6),
                    anchor=NormalizedPoint(x=0.5, y=0.5),
                )
            )
            return self.update_template_calibration(actor, template_id, calibration)
        except Exception as exc:
            self.repository.set_template_calibration_state(
                template_id, actor.workspace_id, actor.id, "failed", str(exc)
            )
            raise

    def list_templates(self, actor: Actor) -> dict[str, Any]:
        return {
            "templates": [
                self._template_payload(template)
                for template in self.repository.list_templates(actor.workspace_id, actor.id)
            ]
        }

    def create_batch(self, actor: Actor, request: BatchCreate, *, enqueue: bool = True) -> dict[str, Any]:
        batch_id = uuid.uuid4().hex
        self.repository.preflight_batch(actor.workspace_id, actor.id, request)
        billing_run = self._freeze_batch(actor, batch_id, request.count) if (enqueue or self.billing_coordinator) else None
        try:
            batch = self.repository.create_batch(actor.workspace_id, actor.id, request, batch_id=batch_id)
        except Exception:
            if billing_run is not None:
                billing_run.settle()
            raise
        if billing_run is not None and self.worker is not None:
            self.worker.register_billing_run(batch_id, billing_run)
        if enqueue and self.worker is not None:
            self.worker.submit(batch["batch_id"], billing_run)
        return self._batch_payload(batch)

    def run_direct_listing_trial(
        self, actor: Actor, request: DirectListingTrialCreate
    ) -> dict[str, Any]:
        """Run exactly one reference-locked listing grid, outside batch workers."""
        template = self.repository.get_template(request.template_id, actor.workspace_id, actor.id)
        if template["source"] != "personal":
            raise ValueError("direct POD listing trial requires a personal template")
        template_asset = self.repository.get_asset(template["asset_id"], actor.workspace_id, actor.id)
        template_image = self.assets.read(template_asset["relative_path"])
        template_content_type = str(template_asset["content_type"] or "").strip().lower()
        if template_content_type not in SUPPORTED_TEMPLATE_IMAGE_CONTENT_TYPES:
            raise ValueError("direct POD listing template must be a JPEG, PNG, or WEBP image")
        trial_id = uuid.uuid4().hex
        billing_run = self._freeze_trial(actor, trial_id, request)
        prompt = build_direct_listing_prompt(request.business_fields, request.creative_prompt)
        grid_asset_ids: list[str] = []
        generated_grids: list[Any] = []
        split_error = ""

        try:
            return self._run_direct_listing_trial_authorized(
                actor,
                request,
                template_image=template_image,
                template_content_type=template_content_type,
                trial_id=trial_id,
                prompt=prompt,
                billing_run=billing_run,
            )
        except PodBillingAuthorizationRequired as exc:
            raise RuntimeError(str(exc)) from exc
        finally:
            billing_run.settle()

    def _run_direct_listing_trial_authorized(
        self,
        actor: Actor,
        request: DirectListingTrialCreate,
        *,
        template_image: bytes,
        template_content_type: str,
        trial_id: str,
        prompt: str,
        billing_run: PodBillingRun,
    ) -> dict[str, Any]:
        grid_asset_ids: list[str] = []
        generated_grids: list[Any] = []
        split_error = ""
        for attempt in (1, 2):
            provider_call_id = f"{trial_id}:image:{attempt}"
            grid_request = DirectListingGridRequest(
                trial_id=trial_id,
                template_id=request.template_id,
                template_image=template_image,
                template_content_type=template_content_type,
                prompt=prompt,
                attempt=attempt,
            )
            try:
                billing_run.start(provider_call_id, "pod.image")
                grid = self.ai_runtime.generate_listing_grid(
                    grid_request,
                    grant=billing_run.grant,
                    call_id=provider_call_id,
                )
                billing_run.record(provider_call_id, "pod.image", "success")
            except PodBillingAuthorizationRequired:
                if billing_run.call_status(provider_call_id) == "started":
                    billing_run.record(provider_call_id, "pod.image", "no_return")
                raise
            except Exception as exc:
                billing_run.record(
                    provider_call_id,
                    "pod.image",
                    image_provider_outcome_for_exception(exc),
                )
                self._raise_direct_trial_generation_error(exc)
            generated_grids.append(grid)
            try:
                panels = self.ai_runtime.split_listing_grid(grid)
                if len(panels) != 4:
                    raise RuntimeError("generated four-grid image did not yield exactly four panels")
            except Exception as exc:
                split_error = safe_error_message(exc)
                if attempt == 1:
                    continue
                grid_asset_ids = self._save_direct_trial_grid_attempts(actor, trial_id, generated_grids)
                failed = self.repository.create_direct_listing_trial(
                    trial_id=trial_id,
                    workspace_id=actor.workspace_id,
                    owner_user_id=actor.id,
                    template_id=request.template_id,
                    status="failed",
                    prompt_snapshot=prompt,
                    grid_attempt_asset_ids=grid_asset_ids,
                    panel_asset_ids={},
                    public_urls={},
                    error_message=split_error,
                )
                return self._direct_listing_trial_payload(failed)

            grid_asset_ids = self._save_direct_trial_grid_attempts(actor, trial_id, generated_grids)
            roles = LISTING_IMAGE_ROLES
            panel_assets = {
                role: self._save_direct_trial_asset(
                    actor,
                    "direct_listing_panel",
                    f"direct-listing-{trial_id}-{role}{panel.suffix}",
                    panel.content,
                )
                for role, panel in zip(roles, panels, strict=True)
            }
            public_urls: dict[str, str] = {}
            title_result: dict[str, Any] | None = None
            try:
                for role, panel in zip(roles, panels, strict=True):
                    public_urls[role] = self.ai_runtime.publish_listing_image(
                        panel, namespace=actor.workspace_id, role=role
                    )
                    if role == "hero":
                        title_result = self._generate_direct_trial_title(
                            trial_id,
                            panel,
                            request.business_fields,
                            request.creative_prompt,
                            billing_run,
                        )
            except PodBillingAuthorizationRequired:
                raise
            except Exception as exc:
                failed = self.repository.create_direct_listing_trial(
                    trial_id=trial_id,
                    workspace_id=actor.workspace_id,
                    owner_user_id=actor.id,
                    template_id=request.template_id,
                    status="failed",
                    prompt_snapshot=prompt,
                    grid_attempt_asset_ids=grid_asset_ids,
                    panel_asset_ids={role: asset["asset_id"] for role, asset in panel_assets.items()},
                    public_urls=public_urls,
                    error_message=f"POD 图床发布失败：{safe_error_message(exc)}",
                    title_result=title_result,
                )
                return self._direct_listing_trial_payload(failed)
            stored = self.repository.create_direct_listing_trial(
                trial_id=trial_id,
                workspace_id=actor.workspace_id,
                owner_user_id=actor.id,
                template_id=request.template_id,
                status="completed",
                prompt_snapshot=prompt,
                grid_attempt_asset_ids=grid_asset_ids,
                panel_asset_ids={role: asset["asset_id"] for role, asset in panel_assets.items()},
                public_urls=public_urls,
                title_result=title_result,
            )
            return self._direct_listing_trial_payload(stored)

        raise RuntimeError(f"direct listing trial did not produce a valid grid: {split_error}")

    def get_direct_listing_trial(self, actor: Actor, trial_id: str) -> dict[str, Any]:
        return self._direct_listing_trial_payload(
            self.repository.get_direct_listing_trial(trial_id, actor.workspace_id, actor.id)
        )

    def list_direct_listing_trials(self, actor: Actor) -> dict[str, Any]:
        rows, total = self.repository.list_direct_listing_trials(actor.workspace_id, actor.id)
        return {"trials": [self._direct_listing_trial_payload(row) for row in rows], "total": total}

    def list_batches(self, actor: Actor, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        rows, total = self.repository.list_batches(
            actor.workspace_id,
            actor.id,
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
        )
        return {"batches": [self._batch_summary(row) for row in rows], "total": total}

    def get_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        if self.worker is None or not self.worker.is_batch_running(batch_id):
            self.repository.reconcile_stale_generating_titles(batch_id)
        return self._batch_payload(self.repository.get_batch(batch_id, actor.workspace_id, actor.id))

    def pause_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] in {"paused", "pausing"}:
            return self._batch_payload(batch)
        if not self.repository.request_pause(batch_id):
            raise PodRepositoryError("仅运行中的 POD 批次可以暂停", 409)
        return self._batch_payload(self.repository.get_batch(batch_id, actor.workspace_id, actor.id))

    def cancel_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] == "cancelled":
            return self._batch_payload(batch)
        worker_running = self.worker is not None and self.worker.is_batch_running(batch_id)

        def finish_cancelled() -> None:
            self.repository.fail_remaining_items(batch_id, "POD 批次已取消")
            self.repository.fail_pending_titles(batch_id, "POD 批次已取消")
            self.repository.mark_batch_cancelled(batch_id, "POD 批次已取消")

        if batch["status"] == "cancelling":
            if not worker_running:
                finish_cancelled()
                batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
            return self._batch_payload(batch)
        was_paused = batch["status"] == "paused"
        if not self.repository.request_cancel(batch_id):
            raise PodRepositoryError("仅运行中或已暂停的 POD 批次可以取消", 409)
        if was_paused or not worker_running:
            # 已暂停或 worker 已退出的批次不会再经过检查点，需同步收尾。
            finish_cancelled()
        return self._batch_payload(self.repository.get_batch(batch_id, actor.workspace_id, actor.id))

    def resume_batch(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] != "paused":
            raise PodRepositoryError("仅已暂停的 POD 批次可以继续", 409)
        # Resuming a paused batch always creates a fresh plan for the
        # remaining styles. It never asks the user to re-authorize an old run.
        run = self._freeze_paused_batch_remainder(actor, batch)
        if not self.repository.resume_paused_batch(batch_id):
            self._settle_unclaimed_retry(run)
            raise PodRepositoryError("POD 批次无法继续", 409)
        if self.worker is None:
            raise RuntimeError("POD worker is disabled")
        self.worker.register_billing_run(batch_id, run)
        self.worker.submit(batch_id, run)
        return self._batch_payload(self.repository.get_batch(batch_id, actor.workspace_id, actor.id))

    def export_dianxiaomi(self, actor: Actor, batch_id: str) -> DianxiaomiExport:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        copies = self.repository.get_style_copies(batch_id, actor.workspace_id, actor.id)
        analysis = analyze_dianxiaomi_export(batch, copies)
        if analysis.block_reason is not None:
            messages = {
                "active_batch": "POD batch is active and cannot be exported",
                "listing_fields_missing": "POD batch listing snapshot is missing",
                "style_copy_missing": "POD style copy is missing",
                "no_exportable_styles": "POD batch has zero exportable styles",
                "all_exportable_styles_unselected": (
                    "POD batch has zero exportable styles because every ready style was deselected"
                ),
                "billing_recovery_required": "POD batch still has incomplete image/title/copy work",
            }
            raise PodRepositoryError(messages[analysis.block_reason], 409)
        exported = build_pod_dianxiaomi_export(batch, copies)
        record = self.export_records.record_success(
            batch_id=batch_id,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            file_name=exported.filename,
            format="dianxiaomi_xlsx",
            exported_count=exported.exported_style_count,
            skipped_count=exported.skipped_style_count,
        )
        return DianxiaomiExport(
            content=exported.content,
            exported_style_count=exported.exported_style_count,
            skipped_style_count=exported.skipped_style_count,
            filename=exported.filename,
            export_id=record["id"],
        )

    def set_style_export_selection(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        *,
        selected: bool,
    ) -> dict[str, Any]:
        selected = self.repository.upsert_style_export_selection(
            batch_id,
            actor.workspace_id,
            actor.id,
            style_index,
            selected=selected,
        )
        return {"style_index": style_index, "export_selected": selected}

    def list_exports(self, actor: Actor, batch_id: str) -> dict[str, Any]:
        self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        rows = self.export_records.list_for_batch(
            batch_id=batch_id,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
        )
        return {"exports": rows, "total": len(rows)}

    def optimize_scene(
        self,
        actor: Actor,
        batch_id: str,
        item_id: str,
        *,
        instruction: str = "",
        enqueue: bool = True,
    ) -> dict[str, Any]:
        item = self.repository.claim_scene_optimization(
            batch_id, item_id, actor.workspace_id, actor.id
        )
        billing_run = self._freeze_retry(
            actor,
            f"{batch_id}:item:{item_id}:scene:{uuid.uuid4().hex}",
            "pod.image",
            action_type="scene_optimization",
            target_id=item_id,
            batch_id=batch_id,
            action_payload={"instruction": instruction},
        )
        if self.worker is not None:
            self.worker.register_action_billing_run(f"scene:{batch_id}:{item_id}", billing_run)
        if enqueue:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            self.worker.submit_scene_optimization(batch_id, item_id, instruction, billing_run)
        return self._item_payload(item)

    def regenerate_item(
        self,
        actor: Actor,
        batch_id: str,
        item_id: str,
        *,
        creative_prompt: str = "",
        enqueue: bool = True,
    ) -> dict[str, Any]:
        item = self.repository.claim_item_regeneration(
            batch_id, item_id, actor.workspace_id, actor.id
        )
        billing_run = self._freeze_retry(
            actor,
            f"{batch_id}:item:{item_id}:retry:{uuid.uuid4().hex}",
            "pod.image",
            action_type="item_retry",
            target_id=item_id,
            batch_id=batch_id,
            action_payload={"creative_prompt": creative_prompt},
        )
        if self.worker is not None:
            self.worker.register_action_billing_run(f"item:{batch_id}:{item_id}", billing_run)
        if enqueue:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            self.worker.submit_item_regeneration(batch_id, item_id, creative_prompt, billing_run)
        return self._item_payload(item)

    def regenerate_style(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        *,
        creative_prompt: str = "",
        enqueue: bool = True,
    ) -> dict[str, Any]:
        with self._regeneration_lock:
            return self._regenerate_style(
                actor, batch_id, style_index, creative_prompt=creative_prompt, enqueue=enqueue
            )

    def _regenerate_style(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        *,
        creative_prompt: str = "",
        enqueue: bool = True,
    ) -> dict[str, Any]:
        self._preflight_style_retry(actor, batch_id, style_index)
        action_id = f"{batch_id}:style:{style_index}:retry:{uuid.uuid4().hex}"
        billing_run = self._freeze_style_retry(
            actor, action_id, batch_id, style_index, creative_prompt
        )
        try:
            results = self.repository.claim_style_regeneration(
                batch_id, style_index, actor.workspace_id, actor.id
            )
        except Exception:
            self._settle_unclaimed_retry(billing_run)
            raise
        if self.worker is not None:
            self.worker.register_action_billing_run(f"style:{batch_id}:{style_index}", billing_run)
        if enqueue:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            self.worker.submit_style_regeneration(batch_id, style_index, creative_prompt, billing_run)
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        title = next((row for row in batch.get("style_titles", []) if row["style_index"] == style_index), None)
        return {
            "style_index": style_index,
            "results": [self._item_payload(item) for item in results],
            "title": self._title_payload(title) if title is not None else None,
        }

    def regenerate_title(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        *,
        enqueue: bool = True,
    ) -> dict[str, Any]:
        with self._regeneration_lock:
            return self._regenerate_title(actor, batch_id, style_index, enqueue=enqueue)

    def _regenerate_title(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        *,
        enqueue: bool = True,
    ) -> dict[str, Any]:
        self._require_title_runtime_configured(require_present=True)
        self._preflight_title_retry(actor, batch_id, style_index)
        action_id = f"{batch_id}:style:{style_index}:title-retry:{uuid.uuid4().hex}"
        billing_run = self._freeze_retry(
            actor,
            action_id,
            "pod.title",
            action_type="title_retry",
            target_id=str(style_index),
            batch_id=batch_id,
        )
        try:
            title = self.repository.claim_title_regeneration(
                batch_id, style_index, actor.workspace_id, actor.id
            )
        except Exception:
            self._settle_unclaimed_retry(billing_run)
            raise
        if self.worker is not None:
            self.worker.register_action_billing_run(f"title:{batch_id}:{style_index}", billing_run)
        if enqueue:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            self.worker.submit_title_regeneration(batch_id, style_index, billing_run)
        return self._title_payload(title)

    def set_manual_title(
        self,
        actor: Actor,
        batch_id: str,
        style_index: int,
        title: str,
    ) -> dict[str, Any]:
        clean = str(title or "").strip()
        if not clean:
            raise ValueError("manual title is required")
        self._preflight_manual_title(actor, batch_id, style_index)
        self.repository.complete_manual_title(
            batch_id, style_index, clean, actor.workspace_id, actor.id
        )
        self.repository.settle_batch_by_listing_readiness(batch_id)
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        saved = next(
            (row for row in batch["style_titles"] if int(row["style_index"]) == int(style_index)),
            None,
        )
        if saved is None:
            raise PodRepositoryError("POD style title not found", 404)
        return self._title_payload(saved)

    def retry_failed(
        self,
        actor: Actor,
        batch_id: str,
        *,
        image_style_indices: list[int],
        title_style_indices: list[int],
        enqueue: bool = True,
    ) -> dict[str, Any]:
        request = BatchRetryFailedCreate(
            image_style_indices=image_style_indices,
            title_style_indices=title_style_indices,
        )
        image_indices = tuple(sorted(request.image_style_indices))
        title_indices = tuple(sorted(request.title_style_indices))
        if title_indices:
            self._require_title_runtime_configured(require_present=True)
        self._preflight_batch_retry(actor, batch_id, image_indices, title_indices)
        action_id = f"{batch_id}:batch-retry:{uuid.uuid4().hex}"
        billing_run = self._freeze_batch_retry(
            actor, action_id, batch_id, image_indices, title_indices
        )
        try:
            self.repository.claim_batch_retry(
                batch_id,
                actor.workspace_id,
                actor.id,
                image_style_indices=image_indices,
                title_style_indices=title_indices,
            )
        except Exception:
            self._settle_unclaimed_retry(billing_run)
            raise
        if self.worker is not None:
            self.worker.register_action_billing_run(f"batch-retry:{batch_id}", billing_run)
        if enqueue:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            self.worker.submit_batch_retry(batch_id, image_indices, title_indices, billing_run)
        return {
            "image_style_indices": list(image_indices),
            "title_style_indices": list(title_indices),
            "submitted_image_style_count": len(image_indices),
            "submitted_title_style_count": len(title_indices),
        }

    def _preflight_style_retry(self, actor: Actor, batch_id: str, style_index: int) -> None:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled", "settlement_pending"}:
            raise PodRepositoryError("POD batch must settle before regenerating one style", 409)
        results = [
            item for item in batch.get("items", [])
            if int(item.get("style_index") or 0) == int(style_index)
        ]
        statuses = {str(item.get("status") or "") for item in results}
        if len(results) != 4 or statuses not in ({"failed"}, {"completed"}):
            raise PodRepositoryError("only a settled POD style can be regenerated", 409)

    def _preflight_title_retry(self, actor: Actor, batch_id: str, style_index: int) -> None:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled", "settlement_pending"}:
            raise PodRepositoryError("POD batch must settle before regenerating its title", 409)
        title = next(
            (row for row in batch.get("style_titles", []) if int(row["style_index"]) == int(style_index)),
            None,
        )
        results = [
            item for item in batch.get("items", [])
            if int(item.get("style_index") or 0) == int(style_index)
        ]
        if title is None or title.get("status") not in {"failed", "completed"}:
            raise PodRepositoryError("only a settled POD title can be regenerated", 409)
        if len(results) != 4 or any(
            item.get("status") != "completed" or not item.get("public_url") for item in results
        ):
            raise PodRepositoryError("all four public POD images are required before regenerating a title", 409)

    def _preflight_manual_title(self, actor: Actor, batch_id: str, style_index: int) -> None:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled"}:
            if batch["status"] == "settlement_pending":
                raise PodRepositoryError("POD billing settlement is pending", 409)
            raise PodRepositoryError("POD batch must settle before saving a manual title", 409)

    def _preflight_batch_retry(
        self,
        actor: Actor,
        batch_id: str,
        image_style_indices: tuple[int, ...],
        title_style_indices: tuple[int, ...],
    ) -> None:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] not in {"completed", "partial_failure", "failed", "cancelled", "settlement_pending"}:
            raise PodRepositoryError("POD batch must settle before retrying failed styles", 409)
        if any(index > int(batch["requested_count"]) for index in (*image_style_indices, *title_style_indices)):
            raise PodRepositoryError("POD style index is outside the batch range", 422)
        for style_index in image_style_indices:
            results = [item for item in batch["items"] if int(item.get("style_index") or 0) == style_index]
            if len(results) != 4 or any(item.get("status") != "failed" for item in results):
                raise PodRepositoryError("only styles with all four images failed can be retried", 409)
        for style_index in title_style_indices:
            title = next(
                (row for row in batch["style_titles"] if int(row["style_index"]) == style_index),
                None,
            )
            results = [item for item in batch["items"] if int(item.get("style_index") or 0) == style_index]
            if (
                title is None
                or title.get("status") != "failed"
                or not title.get("style_task_id")
                or len(results) != 4
                or any(item.get("status") != "completed" or not item.get("public_url") for item in results)
            ):
                raise PodRepositoryError(
                    "only a failed POD title with four public images can be retried", 409
                )

    @staticmethod
    def _settle_unclaimed_retry(billing_run: PodBillingRun) -> None:
        try:
            billing_run.settle()
        except Exception:
            # The durable billing run remains settlement_pending for settlement
            # bookkeeping; it must not lock the failed generation retry path.
            pass

    def close(self) -> None:
        if self._reaper_thread is not None:
            self._reaper_stop.set()
            self._reaper_thread.join(timeout=5.0)
            self._reaper_thread = None
        # Revoke active epochs before cancelling the local executors.  Provider
        # calls already inside requests cannot be force-killed safely, but any
        # result they deliver after this point is rejected by the repository.
        self.repository.pause_billing_runs_for_shutdown()
        if self.worker is not None:
            self.worker.close()

    def _start_reaper(self) -> None:
        """Start the background reaper daemon that revokes stale batch epochs."""
        self._reaper_stop.clear()
        self._reaper_thread = threading.Thread(
            target=self._run_stuck_batch_reaper,
            name="pod-batch-reaper",
            daemon=True,
        )
        self._reaper_thread.start()

    def _run_stuck_batch_reaper(self) -> None:
        """Loop: reap stale batches every 60 seconds until stopped."""
        import logging
        logger = logging.getLogger(__name__)
        while not self._reaper_stop.wait(timeout=60.0):
            try:
                self.reap_stuck_batches_once()
                self.settle_stuck_billing_runs()
            except Exception as exc:
                logger.warning("POD reaper encountered an error: %s", exc)

    def reap_stuck_batches_once(self) -> list[dict]:
        """Reap batches that have not progressed within the inactivity window.

        Exposed as a public method for deterministic tests and operator diagnostics.
        Returns the list of reaped batch records (batch_id, old_epoch, new_status).
        """
        import logging
        logger = logging.getLogger(__name__)
        reaped = self.repository.reap_stuck_batches(
            stale_after_seconds=POD_PROGRESS_TIMEOUT_SECONDS,
        )
        for record in reaped:
            logger.info(
                "POD reaper: batch %s reaped (old_epoch=%s, new_status=%s, reason=inactivity_timeout)",
                record["batch_id"],
                record["old_epoch"],
                record["new_status"],
            )
        return reaped

    def recover_interrupted_work(self) -> int:
        recovered = self.repository.recover_interrupted_batches()
        self.repository.recover_billing_runs()
        return recovered

    def list_pending_billing_runs(self, actor: Actor) -> dict[str, Any]:
        rows = self.repository.list_pending_billing_runs(actor.workspace_id, actor.id)
        return {"runs": [self._billing_run_payload(row) for row in rows], "total": len(rows)}

    def resume_billing_run(
        self, actor: Actor, run_id: str, *, enqueue: bool = False
    ) -> dict[str, Any]:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        stored = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        if stored["status"] == "settled":
            return self._billing_run_payload(stored)
        if not self.repository.claim_billing_resume(run_id, actor.workspace_id, actor.id):
            current = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            if current["status"] in {"settled", "resume_claimed", "authorized", "settling"}:
                return self._billing_run_payload(current)
            raise PodRepositoryError("POD billing run is already active", 409)
        stored = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        plan = self._billing_plan(stored["plan"])
        has_planned_calls = any(
            outcome["status"] == "planned" for outcome in stored["outcomes"]
        )
        has_uncertain_calls = any(
            outcome["status"] == "started" for outcome in stored["outcomes"]
        )
        if has_uncertain_calls:
            message = (
                "POD provider call outcome is uncertain after interruption; "
                "automatic resume and settlement are blocked"
            )
            self.repository.mark_billing_pending(stored["action_key"], message)
            raise PodRepositoryError(message, 409)
        settlement_grant = getattr(self.billing_coordinator, "settlement_grant", None)
        try:
            if not has_planned_calls and callable(settlement_grant):
                grant = settlement_grant(
                    actor,
                    stored["freeze_id"],
                    rule_version=stored["rule_version"],
                    expires_at=stored["grant_expires_at"],
                )
            else:
                grant = self.billing_coordinator.regrant(actor, stored["freeze_id"])
        except CustomerBillingPermissionError:
            self.repository.mark_billing_pending(
                stored["action_key"], "POD billing service authentication failed"
            )
            raise
        except Exception as exc:
            self.repository.mark_billing_pending(stored["action_key"], str(exc))
            raise
        if grant.freeze_id != stored["freeze_id"]:
            raise RuntimeError("POD billing service returned a mismatched freeze")
        self.repository.mark_billing_authorized(
            stored["action_key"], rule_version=grant.rule_version, expires_at=grant.expires_at
        )
        run = PodBillingRun(
            actor,
            self.billing_coordinator,
            plan,
            grant,
            repository=self.repository,
            action_key=stored["action_key"],
            resumed=True,
        )
        if stored["action_type"] == "batch_initial" and has_planned_calls:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            if enqueue:
                self.worker.submit(stored["batch_id"], run)
            else:
                self.worker.process_batch(stored["batch_id"], run)
            return self._billing_run_payload(
                self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            )
        if has_planned_calls and stored["action_type"] == "direct_trial":
            finalized = [
                outcome for outcome in stored["outcomes"] if outcome["status"] != "planned"
            ]
            if finalized:
                message = "POD direct trial has partial provider outcomes; automatic replay is blocked"
                self.repository.mark_billing_pending(stored["action_key"], message)
                raise PodRepositoryError(message, 409)
            def continue_direct_trial() -> None:
                request = DirectListingTrialCreate.model_validate(stored["action_payload"])
                template = self.repository.get_template(
                    request.template_id, actor.workspace_id, actor.id
                )
                template_asset = self.repository.get_asset(
                    template["asset_id"], actor.workspace_id, actor.id
                )
                try:
                    self._run_direct_listing_trial_authorized(
                        actor,
                        request,
                        template_image=self.assets.read(template_asset["relative_path"]),
                        template_content_type=template_asset["content_type"],
                        trial_id=stored["target_id"],
                        prompt=build_direct_listing_prompt(
                            request.business_fields, request.creative_prompt
                        ),
                        billing_run=run,
                    )
                except PodBillingAuthorizationRequired as exc:
                    self.repository.mark_billing_pending(run.action_key, str(exc))
                    return
                try:
                    run.settle()
                except Exception as exc:
                    self.repository.mark_billing_pending(run.action_key, str(exc))

            if enqueue:
                if self.worker is None:
                    raise RuntimeError("POD worker is disabled")
                self.worker.submit_billing_action(run_id, continue_direct_trial)
            else:
                continue_direct_trial()
            return self._billing_run_payload(
                self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            )
        if has_planned_calls and stored["action_type"] in {
            "scene_optimization",
            "item_retry",
            "style_retry",
            "title_retry",
        }:
            if self.worker is None:
                raise RuntimeError("POD worker is disabled")
            payload = stored["action_payload"]
            if stored["action_type"] == "style_retry" and payload.get("retry_mode") == "batch":
                image_style_indices = tuple(int(index) for index in payload.get("image_style_indices", []))
                title_style_indices = tuple(int(index) for index in payload.get("title_style_indices", []))
                function = (
                    self.worker.submit_batch_retry
                    if enqueue
                    else self.worker.process_batch_retry
                )
                function(stored["batch_id"], image_style_indices, title_style_indices, run)
            elif stored["action_type"] == "scene_optimization":
                function = (
                    self.worker.submit_scene_optimization
                    if enqueue
                    else self.worker.optimize_scene
                )
                function(stored["batch_id"], stored["target_id"], str(payload.get("instruction") or ""), run)
            elif stored["action_type"] == "item_retry":
                function = (
                    self.worker.submit_item_regeneration
                    if enqueue
                    else self.worker.regenerate_item
                )
                function(stored["batch_id"], stored["target_id"], str(payload.get("creative_prompt") or ""), run)
            elif stored["action_type"] == "style_retry":
                function = (
                    self.worker.submit_style_regeneration
                    if enqueue
                    else self.worker.regenerate_style
                )
                function(stored["batch_id"], int(stored["target_id"]), str(payload.get("creative_prompt") or ""), run)
            else:
                function = (
                    self.worker.submit_title_regeneration
                    if enqueue
                    else self.worker.regenerate_title
                )
                function(stored["batch_id"], int(stored["target_id"]), run)
            return self._billing_run_payload(
                self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            )
        try:
            run.settle()
        except PodBillingAuthorizationRequired:
            raise
        except Exception as exc:
            raise
        if stored["batch_id"]:
            refreshed = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            result_status = refreshed["result_status"]
            if result_status and result_status not in {"settlement_pending"}:
                self.repository.set_batch_status(stored["batch_id"], result_status)
        return self._billing_run_payload(
            self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        )

    def cancel_billing_run(self, actor: Actor, run_id: str) -> dict[str, Any]:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        stored = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        if stored["status"] != "settlement_pending":
            raise PodRepositoryError("POD billing run is not cancellable", 409)
        plan = self._billing_plan(stored["plan"])
        has_planned_calls = any(
            outcome["status"] == "planned" for outcome in stored["outcomes"]
        )
        settlement_grant = getattr(self.billing_coordinator, "settlement_grant", None)
        try:
            if not has_planned_calls and callable(settlement_grant):
                grant = settlement_grant(
                    actor,
                    stored["freeze_id"],
                    rule_version=stored["rule_version"],
                    expires_at=stored["grant_expires_at"],
                )
            else:
                grant = self.billing_coordinator.regrant(actor, stored["freeze_id"])
        except CustomerBillingPermissionError:
            self.repository.mark_billing_pending(
                stored["action_key"], "POD billing service authentication failed"
            )
            raise
        except Exception as exc:
            self.repository.mark_billing_pending(stored["action_key"], str(exc))
            raise
        if grant.freeze_id != stored["freeze_id"]:
            raise RuntimeError("POD billing service returned a mismatched freeze")
        self.repository.mark_billing_authorized(
            stored["action_key"], rule_version=grant.rule_version, expires_at=grant.expires_at
        )
        run = PodBillingRun(
            actor,
            self.billing_coordinator,
            plan,
            grant,
            repository=self.repository,
            action_key=stored["action_key"],
            resumed=True,
        )
        try:
            run.settle()
        except Exception as exc:
            self.repository.mark_billing_pending(stored["action_key"], str(exc))
            raise
        if stored["action_type"] == "direct_trial":
            self.repository.fail_direct_listing_trial(
                stored["target_id"],
                actor.workspace_id,
                actor.id,
                "POD 试用已取消，冻结积分将按结算结果释放",
            )
        return self._billing_run_payload(
            self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        )


    def _settle_stored_billing_run(self, stored: dict[str, Any]) -> None:
        """Rebuild a persisted run and settle it, releasing unearned frozen points.

        Only runs whose outcomes are all terminal (no ``started``) can be settled
        safely.  An interrupted provider call with an unknown outcome is left
        ``settlement_pending`` for manual review instead of being guessed.
        """
        if any(outcome["status"] == "started" for outcome in stored["outcomes"]):
            raise PodRepositoryError(
                "POD provider call outcome is uncertain after interruption; automatic settle blocked", 409
            )
        actor = Actor(
            id=str(stored["owner_user_id"]),
            username="",
            role="",
            workspace_id=str(stored["workspace_id"]),
        )
        plan = self._billing_plan(stored["plan"])
        settlement_grant = getattr(self.billing_coordinator, "settlement_grant", None)
        try:
            if callable(settlement_grant):
                grant = settlement_grant(
                    actor,
                    stored["freeze_id"],
                    rule_version=stored["rule_version"],
                    expires_at=stored["grant_expires_at"],
                )
            else:
                grant = self.billing_coordinator.regrant(actor, stored["freeze_id"])
        except CustomerBillingPermissionError:
            self.repository.mark_billing_pending(
                stored["action_key"], "POD billing service authentication failed"
            )
            raise
        except Exception as exc:
            self.repository.mark_billing_pending(stored["action_key"], str(exc))
            raise
        if grant.freeze_id != stored["freeze_id"]:
            raise RuntimeError("POD billing service returned a mismatched freeze")
        self.repository.mark_billing_authorized(
            stored["action_key"], rule_version=grant.rule_version, expires_at=grant.expires_at
        )
        run = PodBillingRun(
            actor,
            self.billing_coordinator,
            plan,
            grant,
            repository=self.repository,
            action_key=stored["action_key"],
            resumed=True,
        )
        run.settle()

    def settle_stuck_billing_runs(self) -> int:
        """Settle abandoned ``settlement_pending`` runs so frozen points are released.

        Runs with genuinely uncertain (``started``) provider outcomes are skipped
        and remain ``settlement_pending`` for a human to reconcile.
        """
        if self.billing_coordinator is None:
            return 0
        settled = 0
        for run_id, workspace_id, owner_user_id in self.repository.list_settlement_pending_runs():
            try:
                stored = self.repository.get_billing_run(run_id, workspace_id, owner_user_id)
                self._settle_stored_billing_run(stored)
                settled += 1
            except Exception:
                # Transient or uncertain (started) — leave pending for a later sweep.
                continue
        return settled

    def asset_info(self, actor: Actor, asset_id: str) -> dict[str, Any]:
        return self.repository.get_asset(asset_id, actor.workspace_id, actor.id)

    def asset_path(self, actor: Actor, asset_id: str) -> Path:
        return self.assets.path(self.asset_info(actor, asset_id)["relative_path"])

    def _save_direct_trial_asset(
        self, actor: Actor, kind: str, filename: str, content: bytes
    ) -> dict[str, Any]:
        stored = self.assets.save_image(actor.workspace_id, actor.id, content)
        return self.repository.create_asset(
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            kind=kind,
            filename=filename,
            relative_path=stored.relative_path,
            content_type=stored.content_type,
            byte_size=stored.byte_size,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
        )

    def _save_direct_trial_grid_attempts(
        self, actor: Actor, trial_id: str, grids: list[Any]
    ) -> list[str]:
        return [
            self._save_direct_trial_asset(
                actor,
                "direct_listing_grid",
                f"direct-listing-{trial_id}-attempt-{attempt}.png",
                grid.content,
            )["asset_id"]
            for attempt, grid in enumerate(grids, start=1)
        ]

    def _require_title_runtime_configured(self, *, require_present: bool = False) -> None:
        if require_present and self.title_runtime is None:
            raise RuntimeError("POD 标题服务未启用")

    def _freeze_batch(self, actor: Actor, batch_id: str, style_count: int) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_batch(batch_id, style_count=style_count)
        return self._freeze_action(
            actor, plan, action_type="batch_initial", target_id=batch_id, batch_id=batch_id
        )

    def _freeze_paused_batch_remainder(
        self, actor: Actor, batch: dict[str, Any]
    ) -> PodBillingRun:
        batch_id = str(batch["batch_id"])
        completed_images: dict[int, int] = {}
        for item in batch.get("items", []):
            if item.get("status") == "completed":
                style_index = int(item.get("style_index") or 0)
                completed_images[style_index] = completed_images.get(style_index, 0) + 1
        title_statuses = {
            int(title["style_index"]): str(title.get("status") or "")
            for title in batch.get("style_titles", [])
        }
        image_indices = tuple(
            style_index
            for style_index in range(1, int(batch["requested_count"]) + 1)
            if completed_images.get(style_index, 0) < 4
        )
        title_indices = tuple(
            style_index
            for style_index in range(1, int(batch["requested_count"]) + 1)
            if completed_images.get(style_index, 0) == 4
            and self.title_runtime is not None
            and title_statuses.get(style_index) != "completed"
        )
        if not image_indices and not title_indices:
            raise PodRepositoryError("POD 批次没有待继续的款式", 409)
        plan = PodCallPlan.for_batch_resume(
            batch_id,
            uuid.uuid4().hex,
            image_style_indices=image_indices,
            title_style_indices=title_indices,
        )
        return self._freeze_action(
            actor,
            plan,
            action_type="batch_initial",
            target_id=batch_id,
            batch_id=batch_id,
            action_payload={
                "resume_image_style_indices": list(image_indices),
                "resume_title_style_indices": list(title_indices),
            },
        )

    def _freeze_trial(
        self, actor: Actor, trial_id: str, request: DirectListingTrialCreate
    ) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_trial(trial_id, include_title=self.title_runtime is not None)
        return self._freeze_action(
            actor,
            plan,
            action_type="direct_trial",
            target_id=trial_id,
            batch_id="",
            action_payload=request.model_dump(mode="json"),
        )

    def _freeze_style_retry(
        self,
        actor: Actor,
        action_id: str,
        batch_id: str,
        style_index: int,
        creative_prompt: str,
    ) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_style_retry(action_id, include_title=self.title_runtime is not None)
        return self._freeze_action(
            actor,
            plan,
            action_type="style_retry",
            target_id=str(style_index),
            batch_id=batch_id,
            action_payload={"creative_prompt": creative_prompt},
        )

    def _freeze_batch_retry(
        self,
        actor: Actor,
        action_id: str,
        batch_id: str,
        image_style_indices: tuple[int, ...],
        title_style_indices: tuple[int, ...],
    ) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_batch_retry(
            action_id,
            image_style_indices=image_style_indices,
            title_style_indices=title_style_indices,
            include_title=self.title_runtime is not None,
        )
        # The existing durable schema restricts action_type values. The payload
        # distinguishes this single-run batch retry without a table migration.
        return self._freeze_action(
            actor,
            plan,
            action_type="style_retry",
            target_id="batch_retry",
            batch_id=batch_id,
            action_payload={
                "retry_mode": "batch",
                "image_style_indices": list(image_style_indices),
                "title_style_indices": list(title_style_indices),
            },
        )

    def _freeze_retry(
        self,
        actor: Actor,
        action_id: str,
        feature: str,
        *,
        action_type: str,
        target_id: str,
        batch_id: str,
        action_payload: dict[str, Any] | None = None,
    ) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        plan = PodCallPlan.for_retry(
            action_id,
            feature=feature,  # type: ignore[arg-type]
            max_attempts=TITLE_ATTEMPTS if feature == "pod.title" else 1,
        )
        return self._freeze_action(
            actor,
            plan,
            action_type=action_type,
            target_id=target_id,
            batch_id=batch_id,
            action_payload=action_payload,
        )

    def _freeze_action(
        self,
        actor: Actor,
        plan: PodCallPlan,
        *,
        action_type: str,
        target_id: str,
        batch_id: str,
        action_payload: dict[str, Any] | None = None,
    ) -> PodBillingRun:
        if self.billing_coordinator is None:
            raise RuntimeError("POD billing coordinator is not configured")
        grant = self.billing_coordinator.freeze(actor, plan)
        try:
            stored = self.repository.create_billing_run(
                action_key=plan.idempotency_key,
                action_type=action_type,
                target_id=target_id,
                batch_id=batch_id,
                actor_id=actor.id,
                workspace_id=actor.workspace_id,
                plan=plan,
                grant=grant,
                action_payload=action_payload,
            )
        except Exception as ledger_error:
            outcomes = tuple(
                PodCallOutcome(call.call_id, call.feature, "no_return") for call in plan.calls
            )
            try:
                self.billing_coordinator.settle(actor, grant, plan, outcomes)
            except Exception as settlement_error:
                raise RuntimeError(
                    "POD freeze succeeded but both the local ledger and compensation settlement failed"
                ) from settlement_error
            raise ledger_error
        return PodBillingRun(
            actor,
            self.billing_coordinator,
            plan,
            grant,
            repository=self.repository,
            action_key=stored["action_key"],
        )

    @staticmethod
    def _billing_plan(payload: dict[str, Any]) -> PodCallPlan:
        return PodCallPlan(
            idempotency_key=str(payload["idempotency_key"]),
            calls=tuple(
                PodPlannedCall(str(call["call_id"]), str(call["feature"]))  # type: ignore[arg-type]
                for call in payload["calls"]
            ),
        )

    @staticmethod
    def _billing_run_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["run_id"],
            "action_type": row["action_type"],
            "target_id": row["target_id"],
            "batch_id": row["batch_id"],
            "freeze_id": row["freeze_id"],
            "rule_version": row["rule_version"],
            "expires_at": row["grant_expires_at"],
            "status": row["status"],
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _generate_direct_trial_title(
        self,
        trial_id: str,
        hero: Any,
        business_fields: Any,
        creative_prompt: str,
        billing_run: PodBillingRun,
    ) -> dict[str, Any] | None:
        if self.title_runtime is None:
            return None
        request = PodTitleRequest(
            style_task_id=trial_id,
            style_index=1,
            hero_image=hero.content,
            hero_content_type=hero.content_type,
            business_fields=business_fields,
            creative_prompt=creative_prompt,
            accepted_titles=(),
        )
        call_ids = tuple(call.call_id for call in billing_run.plan.calls if call.feature == "pod.title")
        try:
            title_kwargs = {
                "grant": billing_run.grant,
                "call_id": call_ids[0],
                "call_ids": call_ids,
                "on_outcome": lambda call_id, status: billing_run.record(
                    call_id, "pod.title", status
                ),
            }
            parameters = inspect.signature(self.title_runtime.generate_title).parameters
            if "on_start" in parameters or any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            ):
                title_kwargs["on_start"] = lambda call_id: billing_run.start(
                    call_id, "pod.title"
                )
            generated = self.title_runtime.generate_title(request, **title_kwargs)
            if not any(billing_run.has_outcome(call_id) for call_id in call_ids):
                billing_run.record(call_ids[0], "pod.title", "success")
            result = vars(generated)
            result["visual_signature"] = visual_signature(generated)
            return {"style_task_id": trial_id, "status": "completed", "error_message": "", **result}
        except PodBillingAuthorizationRequired:
            raise
        except Exception as exc:
            if not any(billing_run.has_outcome(call_id) for call_id in call_ids):
                billing_run.record(call_ids[0], "pod.title", "no_return")
            return {
                "style_task_id": trial_id,
                "status": "failed",
                "title": "",
                "normalized_title": None,
                "visual_theme": "",
                "motif_keywords": (),
                "color_keywords": (),
                "model": "",
                "prompt_version": "",
                "attempt_count": int(getattr(exc, "attempt_count", 0) or 0),
                "error_message": str(exc),
            }

    @staticmethod
    def _raise_direct_trial_generation_error(exc: Exception) -> None:
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 403}:
            raise RuntimeError(
                f"图片服务鉴权失败（HTTP {status_code}）：请检查 POD 图片服务的 API Key 与权限配置；未保存试跑结果。"
            ) from exc
        raise RuntimeError(
            f"POD 图片生成失败：{safe_error_message(exc, fallback=exc.__class__.__name__)}"
        ) from exc

    @staticmethod
    def _template_payload(template: dict[str, Any]) -> dict[str, Any]:
        template_id = template["template_id"]
        calibration = json.loads(template["calibration_json"])
        return {
            "id": template_id,
            "name": template["name"],
            "source": template["source"],
            "preview_url": f"/api/pod-customization/assets/{template['asset_id']}",
            "original_url": f"/api/pod-customization/assets/{template['asset_id']}",
            "width": template["width"],
            "height": template["height"],
            "calibration_status": template["calibration_status"],
            "calibration": calibration,
            "error_message": template["error_message"],
            "version": template["version"],
            "created_at": template["created_at"],
            "updated_at": template["updated_at"],
        }

    def _batch_payload(self, batch: dict[str, Any]) -> dict[str, Any]:
        snapshot = batch["template"]
        template_payload = {
            "id": snapshot["template_id"],
            "name": snapshot["name"],
            "source": snapshot["source"],
            "preview_url": f"/api/pod-customization/assets/{snapshot['asset_id']}",
            "original_url": f"/api/pod-customization/assets/{snapshot['asset_id']}",
            "width": snapshot["width"],
            "height": snapshot["height"],
            "calibration_status": "ready",
            "calibration": json.loads(snapshot["calibration_json"]),
            "created_at": snapshot["created_at"],
            "updated_at": snapshot["created_at"],
        }
        items = [self._item_payload(item) for item in batch["items"]]
        copies = self.repository.get_style_copies(
            batch["batch_id"], batch["workspace_id"], batch["owner_user_id"]
        )
        export_analysis = analyze_dianxiaomi_export(batch, copies)
        return {
            "id": batch["batch_id"],
            "batch_id": batch["batch_id"],
            "title": batch["title"],
            "status": batch["status"],
            "template_id": batch["template_id"],
            "template_snapshot_id": batch["template_snapshot_id"],
            "template_name": batch["template_name"],
            "count": batch["requested_count"],
            "processed_count": batch["processed_count"],
            "completed_count": batch["completed_count"],
            "failed_count": batch["failed_count"],
            "title_completed_count": batch.get("title_completed_count", 0),
            "title_failed_count": batch.get("title_failed_count", 0),
            "listing_ready_count": batch.get("listing_ready_count", 0),
            "style_grid": bool(batch.get("style_grid")),
            "initial_call_count": batch["initial_call_count"],
            "refill_call_count": batch["refill_call_count"],
            "prompt_version": batch["prompt_version"],
            "prompt_snapshot": batch["prompt_snapshot"],
            "business_fields": batch["business_fields"],
            "listing_fields": batch["listing_fields"],
            "dianxiaomi_export": {
                "ready": export_analysis.ready,
                "exportable_style_count": len(export_analysis.exportable_styles),
                "selected_exportable_style_count": export_analysis.selected_exportable_style_count,
                "user_excluded_style_count": export_analysis.user_excluded_style_count,
                "skipped_style_count": export_analysis.skipped_style_count,
                "block_reason": export_analysis.block_reason,
            },
            "creative_prompt": batch["creative_prompt"],
            "error_message": batch["error_message"],
            "created_at": batch["created_at"],
            "updated_at": batch["updated_at"],
            "template": template_payload,
            "items": items,
            "style_titles": [self._title_payload(title) for title in batch.get("style_titles", [])],
        }

    @staticmethod
    def _direct_listing_trial_payload(trial: dict[str, Any]) -> dict[str, Any]:
        attempts = trial["grid_attempt_asset_ids"]
        roles = LISTING_IMAGE_ROLES

        def asset_urls(asset_id: str) -> dict[str, str]:
            return {
                "preview_url": f"/api/pod-customization/assets/{asset_id}",
                "download_url": f"/api/pod-customization/assets/{asset_id}?download=1",
            }

        return {
            "id": trial["trial_id"],
            "status": trial["status"],
            "template_id": trial["template_id"],
            "prompt_snapshot": trial["prompt_snapshot"],
            "grid": asset_urls(attempts[-1]) if attempts else None,
            "grid_attempts": [
                {"attempt": index, **asset_urls(asset_id)}
                for index, asset_id in enumerate(attempts, start=1)
            ],
            "images": [
                {
                    "role": role,
                    **asset_urls(trial["panel_asset_ids"][role]),
                    "public_url": trial["public_urls"].get(role),
                }
                for role in roles
                if role in trial["panel_asset_ids"]
            ],
            "title": (
                PodCustomizationService._title_payload(trial["title_result"])
                if trial.get("title_result") is not None
                else None
            ),
            "error_message": trial["error_message"],
            "created_at": trial["created_at"],
            "updated_at": trial["updated_at"],
        }

    @staticmethod
    def _batch_summary(batch: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": batch["batch_id"],
            "title": batch["title"],
            "status": batch["status"],
            "template_id": batch["template_id"],
            "template_name": batch["template_name"],
            "count": batch["requested_count"],
            "processed_count": batch["processed_count"],
            "completed_count": batch["completed_count"],
            "failed_count": batch["failed_count"],
            "title_completed_count": batch.get("title_completed_count", 0),
            "title_failed_count": batch.get("title_failed_count", 0),
            "listing_ready_count": batch.get("listing_ready_count", 0),
            "style_titles": [
                PodCustomizationService._title_payload(title)
                for title in batch.get("style_titles", [])
            ],
            "style_grid": bool(batch.get("style_grid")),
            "created_at": batch["created_at"],
            "updated_at": batch["updated_at"],
        }

    @staticmethod
    def _title_payload(title: dict[str, Any]) -> dict[str, Any]:
        return {
            "style_index": title.get("style_index", 1),
            "style_task_id": title.get("style_task_id", ""),
            "status": title["status"],
            "title": title.get("title") or None,
            "source": title.get("source", "ai"),
            "listing_ready": bool(title.get("listing_ready", False)),
            "export_selected": bool(title.get("export_selected", True)),
            "error_message": title.get("error_message", ""),
            "updated_at": title.get("updated_at", ""),
        }

    @staticmethod
    def _item_payload(item: dict[str, Any]) -> dict[str, Any]:
        pattern_id = item["pattern_asset_id"]
        composite_id = item["composite_asset_id"]
        public_url = item.get("public_url") or None
        composite_preview_url = (
            f"/api/pod-customization/assets/{composite_id}" if composite_id else None
        )
        return {
            "id": item["item_id"],
            "index": item["item_index"],
            "style_index": item.get("style_index", item["item_index"]),
            "export_selected": bool(item.get("export_selected", True)),
            "variant_index": item.get("variant_index", 1),
            "status": item["status"],
            "pattern_preview_url": f"/api/pod-customization/assets/{pattern_id}" if pattern_id else None,
            "pattern_download_url": f"/api/pod-customization/assets/{pattern_id}?download=1" if pattern_id else None,
            "composite_preview_url": composite_preview_url,
            "composite_download_url": f"/api/pod-customization/assets/{composite_id}?download=1" if composite_id else None,
            "role": item.get("role") or None,
            "public_url": public_url,
            "scene_optimized": bool(item["scene_optimized"]),
            "error_message": item["error_message"],
            "updated_at": item["updated_at"],
        }
