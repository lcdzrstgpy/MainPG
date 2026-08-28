from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from ...customer.contracts import (
    CustomerAuthRejected,
    CustomerAuthUnavailable,
    CustomerBillingPermissionError,
    CustomerBillingProtocolError,
)
from ...session import Actor, actor_from_authorization, require_permission
from .contracts import (
    BatchCreate,
    BatchRetryFailedCreate,
    CalibrationUpdate,
    DirectListingTrialCreate,
    RegenerateItemCreate,
    SceneOptimizationCreate,
)
from .billing_contract import PodBillingCoordinator
from .errors import safe_error_message
from .repository import PodRepositoryError
from .runtime_contracts import PodAiRuntime
from .service import PodCustomizationService


MAX_TEMPLATE_UPLOAD_BYTES = 20 * 1024 * 1024


def create_router(
    database_path: Path,
    asset_root: Path,
    ai_runtime: PodAiRuntime,
    *,
    title_runtime: Any | None = None,
    billing_coordinator: PodBillingCoordinator | None = None,
    start_workers: bool = True,
) -> APIRouter:
    router = APIRouter(prefix="/api/pod-customization", tags=["pod-customization"])
    service = PodCustomizationService(
        database_path,
        asset_root,
        ai_runtime,
        title_runtime=title_runtime,
        billing_coordinator=billing_coordinator,
        start_workers=start_workers,
    )
    setattr(router, "pod_customization_service", service)

    def permitted(actor: Actor, permission: str) -> None:
        require_permission(actor, permission, database_path)

    @router.get("/templates")
    def list_templates(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "pod_customization.read")
        return _call(service.list_templates, actor)

    @router.post("/templates")
    async def upload_template(request: Request, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "pod_customization.template_manage")
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_TEMPLATE_UPLOAD_BYTES + 1024 * 1024:
            raise HTTPException(status_code=413, detail="POD template upload is too large")
        form = await request.form()
        upload = form.get("file")
        name = str(form.get("name") or "").strip()
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="file is required")
        try:
            content = await upload.read(MAX_TEMPLATE_UPLOAD_BYTES + 1)
            if len(content) > MAX_TEMPLATE_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="POD template upload is too large")
            return _call(
                service.upload_template,
                actor,
                name=name,
                filename=str(getattr(upload, "filename", "template-image")),
                content=content,
            )
        finally:
            await upload.close()

    @router.post("/templates/{template_id}/calibrate")
    def calibrate_template(template_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "pod_customization.template_manage")
        return _call(service.calibrate_template, actor, template_id)

    @router.patch("/templates/{template_id}/calibration")
    def update_calibration(
        template_id: str,
        body: CalibrationUpdate,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.template_manage")
        return _call(service.update_template_calibration, actor, template_id, body.calibration)

    @router.get("/batches")
    def list_batches(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.read")
        return _call(service.list_batches, actor, limit=limit, offset=offset)

    @router.post("/batches")
    def create_batch(body: BatchCreate, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        return _call(service.create_batch, actor, body, enqueue=start_workers)

    @router.post("/direct-listing-trials")
    def run_direct_listing_trial(
        body: DirectListingTrialCreate, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        return _call(service.run_direct_listing_trial, actor, body)

    @router.get("/direct-listing-trials/{trial_id}")
    def get_direct_listing_trial(
        trial_id: str, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.read")
        return _call(service.get_direct_listing_trial, actor, trial_id)

    @router.get("/batches/{batch_id}")
    def get_batch(batch_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "pod_customization.read")
        return _call(service.get_batch, actor, batch_id)

    @router.post("/batches/{batch_id}/pause")
    def pause_batch(batch_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        return _call(service.pause_batch, actor, batch_id)

    @router.post("/batches/{batch_id}/cancel")
    def cancel_batch(batch_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        return _call(service.cancel_batch, actor, batch_id)

    @router.post("/batches/{batch_id}/resume")
    def resume_batch(batch_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        return _call(service.resume_batch, actor, batch_id)

    @router.get("/billing-runs/pending")
    def list_pending_billing_runs(
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        return _call(service.list_pending_billing_runs, actor)

    @router.post("/billing-runs/{run_id}/resume")
    def resume_billing_run(
        run_id: str, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        return _call(service.resume_billing_run, actor, run_id, enqueue=start_workers)

    @router.get("/batches/{batch_id}/exports")
    def list_exports(
        batch_id: str, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.read")
        return _call(service.list_exports, actor, batch_id)

    @router.get("/batches/{batch_id}/exports/dianxiaomi")
    def export_dianxiaomi(
        batch_id: str, actor: Actor = Depends(actor_from_authorization)
    ) -> Response:
        permitted(actor, "pod_customization.export")
        exported = _call(service.export_dianxiaomi, actor, batch_id)
        return Response(
            content=exported.content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{exported.filename}"',
                "X-POD-Exported-Styles": str(exported.exported_style_count),
                "X-POD-Skipped-Styles": str(exported.skipped_style_count),
                "X-POD-Export-ID": exported.export_id,
            },
        )

    @router.post("/batches/{batch_id}/items/{item_id}/optimize-scene")
    def optimize_scene(
        batch_id: str,
        item_id: str,
        body: SceneOptimizationCreate,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        raise HTTPException(
            status_code=409,
            detail="POD scene optimization is not available in this release",
        )

    @router.post("/batches/{batch_id}/items/{item_id}/regenerate")
    def regenerate_item(
        batch_id: str,
        item_id: str,
        body: RegenerateItemCreate,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        raise HTTPException(
            status_code=409,
            detail="POD single-image regeneration is not available in this release",
        )

    @router.post("/batches/{batch_id}/styles/{style_index}/regenerate")
    def regenerate_style(
        batch_id: str,
        style_index: int,
        body: RegenerateItemCreate,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        return _call(
            service.regenerate_style,
            actor,
            batch_id,
            style_index,
            creative_prompt=body.creative_prompt,
            enqueue=start_workers,
        )

    @router.post("/batches/{batch_id}/retry-failed")
    def retry_failed(
        batch_id: str,
        body: BatchRetryFailedCreate,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        return _call(
            service.retry_failed,
            actor,
            batch_id,
            image_style_indices=body.image_style_indices,
            title_style_indices=body.title_style_indices,
            enqueue=start_workers,
        )

    @router.post("/batches/{batch_id}/styles/{style_index}/title/regenerate")
    def regenerate_title(
        batch_id: str,
        style_index: int,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        permitted(actor, "pod_customization.create")
        return _call(
            service.regenerate_title,
            actor,
            batch_id,
            style_index,
            enqueue=start_workers,
        )

    @router.get("/assets/{asset_id}")
    def download_asset(
        asset_id: str,
        download: bool = False,
        actor: Actor = Depends(actor_from_authorization),
    ) -> FileResponse:
        permitted(actor, "pod_customization.export" if download else "pod_customization.read")
        info = _call(service.asset_info, actor, asset_id)
        path = _call(service.asset_path, actor, asset_id)
        return FileResponse(
            path,
            media_type=info["content_type"],
            filename=info["filename"] if download else None,
            content_disposition_type="attachment" if download else "inline",
        )

    return router


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except CustomerAuthRejected as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail="POD billing request was rejected",
        ) from exc
    except CustomerBillingPermissionError as exc:
        status_code = getattr(exc, "status_code", 401)
        if type(status_code) is not int or status_code not in {401, 403}:
            status_code = 401
        raise HTTPException(
            status_code=status_code,
            detail=(
                "POD billing permission is required"
                if status_code == 403
                else "POD billing authentication is required"
            ),
        ) from exc
    except CustomerBillingProtocolError as exc:
        raise HTTPException(
            status_code=502,
            detail="POD billing service returned an invalid response",
        ) from exc
    except CustomerAuthUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="POD billing service is unavailable",
        ) from exc
    except PodRepositoryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=safe_error_message(exc)) from exc
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=safe_error_message(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=safe_error_message(exc)) from exc
