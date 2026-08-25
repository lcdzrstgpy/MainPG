from __future__ import annotations

import json
import inspect
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
)
from .contracts import BatchCreate, Calibration, DirectListingTrialCreate, NormalizedPoint, NormalizedRect
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
from .worker import PodBatchWorker, PodBillingRun


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
        self.worker = (
            PodBatchWorker(
                self.repository,
                self.assets,
                ai_runtime,
                title_runtime=title_runtime,
                coordinator_workers=getattr(ai_runtime, "batch_workers", 1),
            )
            if start_workers
            else None
        )
        self.start_workers = start_workers
        # Restarts never inherit request-local secrets. Queued work remains
        # paused until the composition root obtains an explicit regrant.

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

        billing_auth_required = False
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
        except PodBillingAuthorizationRequired:
            billing_auth_required = True
            self.repository.mark_billing_auth_required(
                billing_run.action_key,
                "POD provider grant expired; sign in to resume this direct trial",
            )
            raise
        finally:
            if not billing_auth_required:
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

    def _preflight_style_retry(self, actor: Actor, batch_id: str, style_index: int) -> None:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] not in {"completed", "partial_failure", "failed"}:
            raise PodRepositoryError("POD batch must settle before regenerating one style", 409)
        results = [
            item for item in batch.get("items", [])
            if int(item.get("style_index") or 0) == int(style_index)
        ]
        if len(results) != 4 or any(item.get("status") != "failed" for item in results):
            raise PodRepositoryError("only a failed POD style can be regenerated", 409)

    def _preflight_title_retry(self, actor: Actor, batch_id: str, style_index: int) -> None:
        batch = self.repository.get_batch(batch_id, actor.workspace_id, actor.id)
        if batch["status"] not in {"completed", "partial_failure", "failed"}:
            raise PodRepositoryError("POD batch must settle before regenerating its title", 409)
        title = next(
            (row for row in batch.get("style_titles", []) if int(row["style_index"]) == int(style_index)),
            None,
        )
        results = [
            item for item in batch.get("items", [])
            if int(item.get("style_index") or 0) == int(style_index)
        ]
        if title is None or title.get("status") != "failed":
            raise PodRepositoryError("only a failed POD title can be regenerated", 409)
        if len(results) != 4 or any(
            item.get("status") != "completed" or not item.get("public_url") for item in results
        ):
            raise PodRepositoryError("all four public POD images are required before regenerating a title", 409)

    @staticmethod
    def _settle_unclaimed_retry(billing_run: PodBillingRun) -> None:
        try:
            billing_run.settle()
        except Exception:
            # The durable billing run remains settlement_pending and can be resumed.
            pass

    def close(self) -> None:
        if self.worker is not None:
            self.worker.close()

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
            self.repository.mark_billing_auth_required(
                stored["action_key"], "POD billing authentication is required"
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
                    self.repository.mark_billing_auth_required(run.action_key, str(exc))
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
            if stored["action_type"] == "scene_optimization":
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
            if stored["batch_id"]:
                self.repository.set_batch_status(stored["batch_id"], "settlement_pending", str(exc))
            raise
        if stored["batch_id"]:
            refreshed = self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
            result_status = refreshed["result_status"]
            if result_status and result_status not in {"billing_auth_required", "settlement_pending"}:
                self.repository.set_batch_status(stored["batch_id"], result_status)
        return self._billing_run_payload(
            self.repository.get_billing_run(run_id, actor.workspace_id, actor.id)
        )

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
            max_attempts=3 if feature == "pod.title" else 1,
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
            "listing_ready": bool(title.get("listing_ready", False)),
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
