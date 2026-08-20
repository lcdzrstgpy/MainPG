from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, NoReturn

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from ....customer.contracts import (
    CustomerAuthRejected,
    CustomerAuthUnavailable,
    CustomerBillingPermissionError,
    CustomerBillingProtocolError,
)
from ....customer.local_session import LocalSessionService
from ....customer.remote_client import CustomerAuthClient
from ....session import Actor, actor_from_authorization
from ..dimension_canvas_service import DimensionCanvasService
from ..infrastructure.assets import ProductProcessingAssets
from ..infrastructure.database import create_database
from ..infrastructure.dimension_canvas_repository import DimensionCanvasRepository
from ..infrastructure.dimension_renderer import DimensionRenderer
from ..infrastructure.repository import ProductProcessingRepository
from ..service import (
    ProductProcessingConflict,
    ProductProcessingNotFound,
    ProductProcessingService,
    ProductProcessingValidationError,
)
from .schemas import (
    DailySelectionIntakeRequest,
    DailySelectionHandoffRequest,
    DraftCreateRequest,
    DraftDeleteRequest,
    DraftProcessRequest,
    DraftUpdateRequest,
    PreviewFinalizeRequest,
    PreviewSaveRequest,
    PromptUpdateRequest,
    RetryTaskRequest,
    extras,
)
from .dimension_canvas_router import create_dimension_canvas_router


def create_product_processing_router(
    service: ProductProcessingService | None = None,
    *,
    database_url: str | None = None,
    assets_root: Path | None = None,
    customer_sessions: LocalSessionService | None = None,
    remote_customer_auth: CustomerAuthClient | None = None,
) -> APIRouter:
    """Create the complete local API used by the Product Processing screen."""
    owned_database = None
    if service is None:
        owned_database = create_database(database_url)
        service = ProductProcessingService(
            ProductProcessingRepository(owned_database),
            ProductProcessingAssets(assets_root),
        )
    dimension_service = getattr(service, "_dimension_canvas_service", None)
    owns_dimension_service = dimension_service is None
    if dimension_service is None:
        dimension_service = DimensionCanvasService(
            DimensionCanvasRepository(service.repository.database),
            service.repository,
            service.assets,
            DimensionRenderer(),
            source_loader=service.load_dimension_source,
            media_assets=service.media_assets,
        )
        setattr(service, "_dimension_canvas_service", dimension_service)
    @asynccontextmanager
    async def lifespan(_app):
        try:
            service.recover_background_work()
            yield
        finally:
            if owns_dimension_service:
                dimension_service.close()
            if owned_database is not None:
                owned_database.dispose()

    router = APIRouter(prefix="/product-processing", tags=["product_processing"], lifespan=lifespan)
    router.include_router(create_dimension_canvas_router(dimension_service))

    @router.get("/engine/status")
    def engine_status() -> dict[str, Any]:
        return service.engine_status()

    @router.get("/ai-config")
    def ai_config() -> dict[str, Any]:
        from ..provider_config import ai_provider_summary

        return ai_provider_summary()

    @router.post("/ai/ping")
    def ai_ping() -> dict[str, Any]:
        from ..ai_client import AiClient, AiProviderError

        try:
            return AiClient().ping()
        except AiProviderError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    @router.get("/engine/prompts")
    def get_prompts() -> dict[str, Any]:
        return service.prompts()

    @router.post("/engine/prompts")
    def update_prompts(body: PromptUpdateRequest) -> dict[str, Any]:
        return _call(service.update_prompts, body.prompts)

    @router.post("/engine/prompts/reset")
    def reset_prompts() -> dict[str, Any]:
        return service.reset_prompts()

    @router.post("/demo-draft")
    async def create_demo_draft(
        request: Request,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        if await request.body():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "local demo draft does not accept client fields")
        return service.demo_draft(_workspace(workspace_id))

    @router.get("/drafts")
    def list_drafts(
        status_filter: str | None = Query(default=None, alias="status"),
        source_type: Literal["web_manual_capture", "onebound_api"] | None = None,
        limit: int = Query(default=200, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        view: str = "full",
        selection_run_id: str | None = None,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return service.list_drafts(
            status_filter,
            limit,
            offset,
            summary=view.strip().lower() in {"summary", "compact", "list"},
            selection_run_id=selection_run_id,
            source_type=source_type,
            workspace_id=_workspace(workspace_id),
        )

    @router.get("/drafts/revision")
    def drafts_revision(
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, str]:
        return {"revision": service.drafts_revision(_workspace(workspace_id))}

    @router.post("/drafts")
    def create_draft(
        body: DraftCreateRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        payload = {**body.model_dump(exclude_none=True), **extras(body)}
        draft, created = _call(service.create_draft, payload, workspace_id=_workspace(workspace_id))
        return {"draft": draft, "created": created}

    @router.post("/drafts/import")
    async def import_drafts(
        request: Request,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        form, file = await _upload_form(request, "file")
        try:
            content = await file.read()
            return _call(
                service.import_workbook,
                _filename(file, "products.xlsx"),
                content,
                str(form.get("source_type") or "excel"),
                int(form.get("max_products") or 0),
                _workspace(workspace_id),
            )
        finally:
            await file.close()

    @router.post("/intake/daily-selection")
    @router.post("/drafts/from-daily-selection")
    def intake_daily_selection(
        body: DailySelectionIntakeRequest,
        workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        if workspace_id and _workspace(workspace_id) != body.workspace_id:
            raise HTTPException(status.HTTP_409_CONFLICT, "workspace_id does not match X-Workspace-ID")
        return _call(service.intake_daily_selection, body)

    @router.post("/intake/daily-selection/handoffs")
    def consume_daily_selection_handoffs(
        body: DailySelectionHandoffRequest,
        workspace_id: str | None = Header(default=None, alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        if workspace_id:
            normalized = _workspace(workspace_id)
            if any(item.workspace_id != normalized for item in body.handoffs):
                raise HTTPException(status.HTTP_409_CONFLICT, "handoff workspace does not match X-Workspace-ID")
        return _call(service.consume_daily_selection_handoffs, body.handoffs)

    @router.get("/intake/daily-selection/{run_id}")
    def daily_selection_intake(
        run_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.daily_selection_intake, run_id, _workspace(workspace_id))

    @router.get("/source-images")
    def source_images(
        draft_id: int | None = None,
        task_id: int | None = None,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return service.source_images(draft_id, task_id, _workspace(workspace_id))

    @router.get("/drafts/{draft_id}")
    def draft_detail(
        draft_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return {"draft": _call(service.get_draft, draft_id, _workspace(workspace_id))}

    @router.post("/drafts/{draft_id}/source-images/retry")
    def retry_source_images(
        draft_id: int,
        background_tasks: BackgroundTasks,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        workspace = _workspace(workspace_id)
        draft = _call(service.get_draft, draft_id, workspace)
        background_tasks.add_task(service.retry_draft_source_images, draft_id, workspace)
        return {"draft": draft, "sync": {"status": "scheduled"}}

    @router.patch("/drafts/{draft_id}")
    def update_draft(
        draft_id: int,
        body: DraftUpdateRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        payload = {**body.model_dump(exclude_unset=True), **extras(body)}
        return {"draft": _call(service.update_draft, draft_id, payload, workspace_id=_workspace(workspace_id))}

    @router.post("/drafts/{draft_id}/image")
    async def upload_draft_image(
        draft_id: int,
        request: Request,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        _form, file = await _upload_form(request, "image_file")
        try:
            draft = _call(
                service.save_draft_image,
                draft_id,
                await file.read(),
                _filename(file, "draft-image.jpg"),
                str(getattr(file, "content_type", "") or ""),
                _workspace(workspace_id),
            )
            return {"draft": draft}
        finally:
            await file.close()

    @router.get("/drafts/{draft_id}/image")
    def get_draft_image(
        draft_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ):
        path = _call(service.draft_image_path, draft_id, _workspace(workspace_id))
        return FileResponse(path, filename=path.name)

    @router.get("/drafts/{draft_id}/media")
    def draft_media(
        draft_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.draft_media, draft_id, workspace_id=_workspace(workspace_id))

    @router.get("/media-assets/{asset_id}/content", response_model=None)
    def media_asset_content(
        asset_id: str,
        workspace_id: str = Query(...),
        expires: int = Query(..., gt=0),
        signature: str = Query(..., min_length=32, max_length=128),
        request_workspace: str | None = Header(default=None, alias="X-Workspace-ID"),
    ) -> FileResponse:
        workspace = _workspace(workspace_id)
        if request_workspace is not None and _workspace(request_workspace) != workspace:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "media asset not found")
        try:
            path, media_type = service.media_asset_content(
                asset_id,
                workspace_id=workspace,
                expires=expires,
                signature=signature,
            )
        except (LookupError, OSError, ValueError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "media asset not found") from None
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "Cache-Control": "private, max-age=300",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/media-assets/{asset_id}/retry")
    def retry_media_asset(
        asset_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.retry_media_asset, asset_id, workspace_id=_workspace(workspace_id))

    @router.delete("/drafts/{draft_id}")
    def delete_draft(
        draft_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        result = service.delete_drafts([draft_id], _workspace(workspace_id))
        if not result["ids"]:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "product draft not found")
        return {"draft_id": draft_id, "status": "deleted"}

    @router.post("/drafts/delete")
    def delete_drafts(
        body: DraftDeleteRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        if not body.delete_all and not body.draft_ids:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "draft_ids is required")
        return service.delete_drafts(None if body.delete_all else body.draft_ids, _workspace(workspace_id))

    @router.post("/drafts/process")
    def process_drafts(
        request: Request,
        body: DraftProcessRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        payload = {**body.model_dump(), **extras(body)}
        _attach_billing_context_and_require_points(
            payload,
            actor,
            source_ref="product_processing:drafts/process",
            remote_token=_remote_token(request, customer_sessions),
            remote_customer_auth=remote_customer_auth,
        )
        return _call(
            service.process_drafts,
            payload,
            idempotency_key=request.headers.get("Idempotency-Key"),
            workspace_id=_workspace(workspace_id),
        )

    @router.post("/engine/batch")
    @router.post("/engine/quick")
    @router.post("/import")
    async def process_workbook(
        request: Request,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        form, file = await _upload_form(request, "file")
        try:
            normalized = _normalize_form(form)
            remote_token = _remote_token(request, customer_sessions)
            available_points = _attach_billing_context_and_require_points(
                normalized,
                actor,
                source_ref="product_processing:workbook",
                remote_token=remote_token,
                remote_customer_auth=remote_customer_auth,
            )

            def final_billing_check(parsed_payload: dict[str, Any]) -> None:
                _attach_billing_context_and_require_points(
                    parsed_payload,
                    actor,
                    source_ref="product_processing:workbook",
                    remote_token=remote_token,
                    remote_customer_auth=remote_customer_auth,
                    available_points=available_points,
                )

            return _call(
                service.process_workbook,
                _filename(file, "products.xlsx"),
                await file.read(),
                normalized,
                idempotency_key=request.headers.get("Idempotency-Key"),
                workspace_id=_workspace(workspace_id),
                final_billing_check=final_billing_check,
            )
        finally:
            await file.close()

    @router.post("/engine/single")
    async def process_single(
        request: Request,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        form = await request.form()
        image = form.get("image_file")
        content = await image.read() if image is not None and hasattr(image, "read") else None
        try:
            normalized = _normalize_form(dict(form))
            billing_payload = {**normalized, "max_products": 1, "draft_ids": [1]}
            _attach_billing_context_and_require_points(
                billing_payload,
                actor,
                source_ref="product_processing:single",
                remote_token=_remote_token(request, customer_sessions),
                remote_customer_auth=remote_customer_auth,
            )
            if "_billing" in billing_payload:
                normalized["_billing"] = billing_payload["_billing"]
            return _call(
                service.process_single,
                normalized,
                image_content=content,
                image_filename=_filename(image, "product.jpg") if image else "",
                image_content_type=str(getattr(image, "content_type", "") or "") if image else "",
                idempotency_key=request.headers.get("Idempotency-Key"),
                workspace_id=_workspace(workspace_id),
            )
        finally:
            if image is not None and hasattr(image, "close"):
                await image.close()

    @router.get("/tasks/history")
    def task_history(
        limit: int = Query(default=80, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        date_from: str | None = Query(default=None),
        date_to: str | None = Query(default=None),
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return service.task_history(
            limit,
            _workspace(workspace_id),
            offset=offset,
            date_from=date_from,
            date_to=date_to,
        )

    @router.get("/tasks/{task_id}/outputs")
    def task_outputs(
        task_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.task_outputs, task_id, workspace_id=_workspace(workspace_id))

    @router.get("/tasks/{task_id}/summary")
    def task_summary(
        task_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(
            service.task_outputs,
            task_id,
            summary_only=True,
            workspace_id=_workspace(workspace_id),
        )

    # ---- 预检环节（生成表格 → 预检 → 导出最终版 → 导入店小秘）----
    @router.get("/tasks/{task_id}/preview")
    def task_preview(
        task_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.task_preview, task_id, workspace_id=_workspace(workspace_id))

    @router.patch("/tasks/{task_id}/preview")
    def save_preview(
        task_id: int,
        body: PreviewSaveRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(
            service.save_task_preview,
            task_id,
            [item.model_dump() for item in body.items],
            workspace_id=_workspace(workspace_id),
        )

    @router.post("/tasks/{task_id}/preview/items/{draft_id}/exclude")
    def exclude_preview_item(
        task_id: int,
        draft_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(
            service.set_preview_item_excluded,
            task_id,
            int(draft_id),
            excluded=True,
            workspace_id=_workspace(workspace_id),
        )

    @router.post("/tasks/{task_id}/preview/items/{draft_id}/restore")
    def restore_preview_item(
        task_id: int,
        draft_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(
            service.set_preview_item_excluded,
            task_id,
            int(draft_id),
            excluded=False,
            workspace_id=_workspace(workspace_id),
        )

    @router.get("/preview/assets/{asset_id}/content", response_model=None)
    def preview_asset_content(
        asset_id: str,
        workspace_id: str = Query(...),
        expires: int = Query(..., gt=0),
        signature: str = Query(..., min_length=32, max_length=128),
        request_workspace: str | None = Header(default=None, alias="X-Workspace-ID"),
    ) -> FileResponse:
        workspace = _workspace(workspace_id)
        if request_workspace is not None and _workspace(request_workspace) != workspace:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "preview image asset not found")
        try:
            path, media_type = service.preview_images.preview_asset_content(
                asset_id,
                workspace_id=workspace,
                expires=expires,
                signature=signature,
            )
        except (LookupError, OSError, ValueError):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "preview image asset not found",
            ) from None
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "Cache-Control": "private, max-age=300",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/tasks/{task_id}/preview/assets")
    async def upload_preview_assets(
        task_id: int,
        request: Request,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        form = await request.form()
        try:
            draft_id = int(str(form.get("draft_id") or 0) or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "draft_id is required") from exc
        files = [value for value in form.getlist("image_files") if hasattr(value, "read")]
        if draft_id <= 0 or not files:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "draft_id and image_files are required",
            )
        if len(files) > 20:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "at most 20 images may be uploaded")
        assets: list[dict[str, Any]] = []
        try:
            for upload in files:
                assets.append(
                    _call(
                        service.register_preview_upload,
                        task_id,
                        draft_id,
                        await _read_limited_upload(upload),
                        _filename(upload, "preview-image.jpg"),
                        str(getattr(upload, "content_type", "") or ""),
                        workspace_id=_workspace(workspace_id),
                    )
                )
        finally:
            for upload in files:
                await upload.close()
        return {"assets": assets}

    @router.post(
        "/tasks/{task_id}/preview/finalize",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def finalize_preview(
        task_id: int,
        body: PreviewFinalizeRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return _call(
            service.begin_preview_finalize,
            task_id,
            [item.model_dump() for item in body.items],
            workspace_id=_workspace(workspace_id),
            idempotency_key=str(idempotency_key or "").strip(),
        )

    @router.get("/tasks/{task_id}/preview/finalize/{run_id}")
    def preview_finalize_status(
        task_id: int,
        run_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(
            service.preview_finalize_status,
            task_id,
            run_id,
            workspace_id=_workspace(workspace_id),
        )

    @router.post(
        "/tasks/{task_id}/preview/finalize/{run_id}/retry",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def retry_preview_finalize(
        task_id: int,
        run_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(
            service.retry_preview_finalize,
            task_id,
            run_id,
            workspace_id=_workspace(workspace_id),
        )

    @router.get("/tasks/{task_id}/preview/finalize/{run_id}/download")
    def download_preview_finalize(
        task_id: int,
        run_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ):
        path = _call(
            service.preview_finalize_download_path,
            task_id,
            run_id,
            workspace_id=_workspace(workspace_id),
        )
        return FileResponse(path, filename=path.name, media_type=_download_media_type(path))

    @router.post("/tasks/{task_id}/pause")
    def pause_task(
        task_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.pause_task, task_id, _workspace(workspace_id))

    @router.post("/tasks/{task_id}/resume")
    def resume_task(
        request: Request,
        task_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        normalized_workspace = _workspace(workspace_id)
        snapshot = _task_billing_snapshot(service, task_id, normalized_workspace, actor)
        token = _remote_token(request, customer_sessions)
        task_projection = snapshot.get("task") if isinstance(snapshot, dict) else {}
        metadata = task_projection.get("metadata") if isinstance(task_projection, dict) else {}
        settings = metadata.get("settings") if isinstance(metadata, dict) else {}
        billing_summary = _reconcile_billing_before_precheck(
            service,
            task_id,
            settings if isinstance(settings, dict) else {},
            token,
            remote_customer_auth,
        )
        pending_ids = [
            int(item["product_draft_id"])
            for item in snapshot.get("items", [])
            if isinstance(item, dict)
            and item.get("status") in {"pending", "running"}
            and item.get("product_draft_id")
        ]
        billing_payload = {
            **(settings if isinstance(settings, dict) else {}),
            "draft_ids": pending_ids,
            "preflight_only": bool(metadata.get("preflight_only")) if isinstance(metadata, dict) else False,
        }
        if pending_ids:
            _attach_billing_context_and_require_points(
                billing_payload,
                actor,
                source_ref=f"product_processing:tasks/{task_id}/resume",
                remote_token=token,
                remote_customer_auth=remote_customer_auth,
                available_points=billing_summary["available_points"],
                pricing=billing_summary["pricing"],
            )
        return _call(
            service.resume_task,
            task_id,
            normalized_workspace,
            remote_token=token,
        )

    @router.post("/tasks/{task_id}/retry-attention")
    def retry_attention(
        request: Request,
        task_id: int,
        body: RetryTaskRequest | None = None,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        normalized_workspace = _workspace(workspace_id)
        snapshot = _task_billing_snapshot(service, task_id, normalized_workspace, actor)
        items = snapshot.get("items") if isinstance(snapshot, dict) else []
        requested_ids = set(body.draft_ids or []) if body else set()
        retry_draft_ids = [
            int(item["product_draft_id"])
            for item in items or []
            if isinstance(item, dict)
            and item.get("status") in {"failed", "attention_required"}
            and (not requested_ids or int(item.get("product_draft_id") or 0) in requested_ids)
        ]
        token = _remote_token(request, customer_sessions)
        task_projection = snapshot.get("task") if isinstance(snapshot, dict) else {}
        metadata = task_projection.get("metadata") if isinstance(task_projection, dict) else {}
        settings = metadata.get("settings") if isinstance(metadata, dict) else {}
        billing_summary = _reconcile_billing_before_precheck(
            service,
            task_id,
            settings if isinstance(settings, dict) else {},
            token,
            remote_customer_auth,
        )
        if retry_draft_ids:
            billing_payload = {
                **(settings if isinstance(settings, dict) else {}),
                "draft_ids": retry_draft_ids,
                "preflight_only": bool(metadata.get("preflight_only")) if isinstance(metadata, dict) else False,
            }
            _attach_billing_context_and_require_points(
                billing_payload,
                actor,
                source_ref=f"product_processing:tasks/{task_id}/retry-attention",
                remote_token=token,
                remote_customer_auth=remote_customer_auth,
                available_points=billing_summary["available_points"],
                pricing=billing_summary["pricing"],
            )
        return _call(
            service.retry_attention,
            task_id,
            normalized_workspace,
            draft_ids=body.draft_ids if body else None,
            remote_token=token,
        )

    @router.post("/tasks/{task_id}/identity-confirm")
    def confirm_identity_sellable(
        request: Request,
        task_id: int,
        body: RetryTaskRequest | None = None,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        normalized_workspace = _workspace(workspace_id)
        snapshot = _task_billing_snapshot(service, task_id, normalized_workspace, actor)
        items = snapshot.get("items") if isinstance(snapshot, dict) else []
        requested_ids = set(body.draft_ids or []) if body else set()
        confirm_draft_ids = [
            int(item["product_draft_id"])
            for item in items or []
            if isinstance(item, dict)
            and item.get("status") in {"failed", "attention_required"}
            and isinstance(item.get("result"), dict)
            and item["result"].get("error_type") == "vision_subject_low_confidence"
            and (not requested_ids or int(item.get("product_draft_id") or 0) in requested_ids)
        ]
        token = _remote_token(request, customer_sessions)
        task_projection = snapshot.get("task") if isinstance(snapshot, dict) else {}
        metadata = task_projection.get("metadata") if isinstance(task_projection, dict) else {}
        settings = metadata.get("settings") if isinstance(metadata, dict) else {}
        billing_summary = _reconcile_billing_before_precheck(
            service,
            task_id,
            settings if isinstance(settings, dict) else {},
            token,
            remote_customer_auth,
        )
        if confirm_draft_ids:
            billing_payload = {
                **(settings if isinstance(settings, dict) else {}),
                "draft_ids": confirm_draft_ids,
                "preflight_only": bool(metadata.get("preflight_only")) if isinstance(metadata, dict) else False,
            }
            _attach_billing_context_and_require_points(
                billing_payload,
                actor,
                source_ref=f"product_processing:tasks/{task_id}/identity-confirm",
                remote_token=token,
                remote_customer_auth=remote_customer_auth,
                available_points=billing_summary["available_points"],
                pricing=billing_summary["pricing"],
            )
        return _call(
            service.confirm_identity_sellable,
            task_id,
            normalized_workspace,
            draft_ids=body.draft_ids if body else None,
            remote_token=token,
        )

    @router.post("/tasks/{task_id}/clear")
    def clear_task(
        task_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        normalized_workspace = _workspace(workspace_id)
        _task_billing_snapshot(service, task_id, normalized_workspace, actor)
        return _call(service.clear_task, task_id, normalized_workspace)

    @router.get("/tasks/{task_id}/download")
    def download(
        task_id: int,
        kind: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ):
        path = _call(service.download_path, task_id, kind, _workspace(workspace_id))
        return FileResponse(path, filename=path.name, media_type=_download_media_type(path))

    @router.post("/tasks/{task_id}/download-form")
    async def download_form(
        task_id: int,
        request: Request,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ):
        form = await request.form()
        path = _call(
            service.download_path,
            task_id,
            str(form.get("kind") or ""),
            _workspace(workspace_id),
        )
        return FileResponse(path, filename=path.name, media_type=_download_media_type(path))

    return router


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ProductProcessingNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ProductProcessingConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ProductProcessingValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except CustomerBillingProtocolError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "remote billing service returned an invalid response",
        ) from exc
    except CustomerAuthUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "remote billing service is unavailable",
        ) from exc
    except CustomerAuthRejected as exc:
        remote_status = getattr(exc, "status_code", None)
        if type(remote_status) is int and 400 <= remote_status < 500:
            raise HTTPException(remote_status, "remote billing request was rejected") from exc
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "remote billing service returned an invalid error status",
        ) from exc
    except CustomerBillingPermissionError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "remote billing session was rejected",
        ) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _task_billing_snapshot(
    service: ProductProcessingService,
    task_id: int,
    workspace_id: str,
    actor: Actor,
) -> dict[str, Any]:
    snapshot = _call(service.task_outputs, task_id, workspace_id=workspace_id)
    task_projection = snapshot.get("task") if isinstance(snapshot, dict) else {}
    metadata = task_projection.get("metadata") if isinstance(task_projection, dict) else {}
    settings = metadata.get("settings") if isinstance(metadata, dict) else {}
    billing = settings.get("_billing") if isinstance(settings, dict) else {}
    account_id = str(billing.get("account_id") or "").strip() if isinstance(billing, dict) else ""
    if account_id and account_id != actor.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "product processing task not found")
    return snapshot


def _attach_billing_context_and_require_points(
    payload: dict[str, Any],
    actor: Actor,
    *,
    source_ref: str,
    remote_token: str,
    remote_customer_auth: CustomerAuthClient | None,
    available_points: int | float | None = None,
    pricing: dict[str, Any] | None = None,
) -> None:
    if bool(payload.get("preflight_only")) or bool(payload.get("category_preflight_only")):
        return
    if remote_customer_auth is None or not remote_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "server billing session is unavailable",
        )
    summary = (
        {"available_points": available_points, "pricing": pricing}
        if available_points is not None and isinstance(pricing, dict)
        else _remote_billing_summary(remote_customer_auth, remote_token)
    )
    pricing = summary["pricing"]
    quantity = _billing_quantity(payload)
    estimated_points = quantity * _billing_points_per_item(payload, pricing)
    if estimated_points <= 0:
        return
    available = available_points if available_points is not None else summary["available_points"]
    if available < estimated_points:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"积分不足：本次预计需要 {estimated_points} 积分，当前可用 {available} 积分。",
        )
    payload["_billing"] = {
        "account_id": actor.id,
        "username": actor.username,
        "role": actor.role,
        "workspace_id": actor.workspace_id,
        "workspace_code": actor.workspace_code,
        "source_ref": source_ref,
        "remote_token": remote_token,
        "estimated_points": estimated_points,
        "pricing_version": pricing["rule_version"],
        "pricing_rule": pricing,
    }
    return available


def _reconcile_billing_before_precheck(
    service: ProductProcessingService,
    task_id: int,
    settings: dict[str, Any],
    remote_token: str,
    remote_customer_auth: CustomerAuthClient | None,
) -> dict[str, Any]:
    billing = settings.get("_billing") if isinstance(settings.get("_billing"), dict) else {}
    if not str(billing.get("account_id") or "").strip():
        return {"available_points": None, "pricing": None}
    if remote_customer_auth is None or not remote_token:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "server billing session is unavailable",
        )
    summary = _remote_billing_summary(remote_customer_auth, remote_token)
    if service.repository.product_billing_attempts(task_id=task_id, pending_only=True):
        _call(service.reconcile_product_billing, task_id, remote_token)
        summary = _remote_billing_summary(remote_customer_auth, remote_token)
    return summary


def _remote_available_points(
    remote_customer_auth: CustomerAuthClient,
    remote_token: str,
) -> int | float:
    return _remote_billing_summary(remote_customer_auth, remote_token)["available_points"]


def _remote_billing_summary(
    remote_customer_auth: CustomerAuthClient,
    remote_token: str,
) -> dict[str, Any]:
    try:
        summary = remote_customer_auth.billing_summary(remote_token)
    except CustomerBillingProtocolError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "remote billing service returned an invalid response",
        ) from exc
    except CustomerAuthUnavailable as exc:
        print(f"[billing-diag] CustomerAuthUnavailable: {exc}", flush=True)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "remote billing service is unavailable",
        ) from exc
    except CustomerAuthRejected as exc:
        remote_status = getattr(exc, "status_code", None)
        if type(remote_status) is int and 400 <= remote_status < 500:
            raise HTTPException(remote_status, "remote billing request was rejected") from exc
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "remote billing service returned an invalid error status",
        ) from exc
    except CustomerBillingPermissionError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "remote billing session was rejected",
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "remote billing service returned an invalid response",
        ) from exc

    if not isinstance(summary, dict):
        _raise_invalid_remote_wallet_summary()
    wallet = summary.get("wallet")
    pricing = summary.get("pricing")
    if not isinstance(wallet, dict) or "available_points" not in wallet:
        _raise_invalid_remote_wallet_summary()
    versioned_pricing = (
        isinstance(pricing, dict)
        and isinstance(pricing.get("features"), dict)
        and str(pricing.get("rule_version") or "").strip() != ""
        and str(pricing.get("point_unit_scale") or "").strip().isdigit()
        and int(pricing["point_unit_scale"]) == 10
    )
    if not versioned_pricing:
        # 兼容旧版计费服务：pricing 缺失或仍为 draft/point_ratio 旧结构时，降级为固定费率。
        pricing = _legacy_billing_pricing()
    raw_available = wallet.get("available_points")
    if type(raw_available) is int:
        available: int | float = raw_available
    elif type(raw_available) is float and versioned_pricing:
        available = raw_available
    if isinstance(raw_available, str):
        normalized = raw_available.strip()
        try:
            available = float(normalized)
        except ValueError:
            _raise_invalid_remote_wallet_summary()
    if "available" not in locals():
        _raise_invalid_remote_wallet_summary()
    try:
        scale = int(pricing.get("point_unit_scale"))
        features = pricing.get("features")
    except (TypeError, ValueError):
        _raise_invalid_remote_wallet_summary()
    if not str(pricing.get("rule_version") or "").strip() or scale != 10 or not isinstance(features, dict):
        _raise_invalid_remote_wallet_summary()
    return {"available_points": available, "pricing": pricing}


def _legacy_billing_pricing() -> dict[str, Any]:
    """Support older account services during the server-rules rollout.

    New account services always return `pricing`; legacy services do not.  The
    fallback mirrors the previous fixed reservation values and is deliberately
    used only when the server did not advertise a versioned rule.
    """
    return {
        "rule_version": "legacy-link-40-v2",
        "point_unit_scale": 10,
        "features": {
            "product_processing.text": {"reserve_points": 5},
            "product_processing.image_grid_2k": {"reserve_points": 40},
        },
    }


def _raise_invalid_remote_wallet_summary() -> NoReturn:
    raise HTTPException(
        status.HTTP_502_BAD_GATEWAY,
        "remote billing service returned an invalid wallet summary",
    )


def _remote_token(request: Request, sessions: LocalSessionService | None) -> str:
    if sessions is None:
        return ""
    authorization = request.headers.get("authorization") or ""
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    session = sessions.store.get_session(token) if token else None
    return str(session.remote_token or "") if session is not None else ""


def _billing_points_per_item(payload: dict[str, Any], pricing: dict[str, Any]) -> float:
    scope = set(payload.get("processing_scope") or [])
    text_enabled = (
        "title" in scope
        or "details" in scope
        or "product_dimensions" in scope
        or bool(payload.get("title_optimize", True))
        or bool(payload.get("description", True))
        or bool(payload.get("size", True))
    )
    image_enabled = (
        "four_grid" in scope
        or bool(payload.get("grid_image", True))
        or bool(payload.get("image_rewrite", True))
    )
    features = pricing.get("features") if isinstance(pricing.get("features"), dict) else {}
    try:
        text_points = float(features["product_processing.text"]["reserve_points"])
        image_points = float(features["product_processing.image_grid_2k"]["reserve_points"])
    except (KeyError, TypeError, ValueError):
        _raise_invalid_remote_wallet_summary()
    return (text_points if text_enabled else 0.0) + (image_points if image_enabled else 0.0)


def _billing_quantity(payload: dict[str, Any]) -> int:
    raw_ids = payload.get("draft_ids")
    if isinstance(raw_ids, list):
        count = len(
            {
                int(item)
                for item in raw_ids
                if str(item).strip().isdigit() and int(item) > 0
            }
        )
    else:
        count = 0
    try:
        max_count = int(payload.get("max_products") or 0)
    except (TypeError, ValueError):
        max_count = 0
    if count and max_count > 0:
        return min(count, max_count)
    if count:
        return count
    return max(1, max_count)


async def _upload_form(request: Request, field: str):
    form_data = await request.form()
    file = form_data.get(field)
    if file is None or not hasattr(file, "read"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{field} is required")
    return dict(form_data), file


async def _read_limited_upload(file: Any, limit: int = 25 * 1024 * 1024) -> bytes:
    """Read at most the preview image contract plus one byte, never unbounded."""
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "preview image exceeds 25 MiB")
    return bytes(content)


def _filename(file: Any, fallback: str) -> str:
    return str(getattr(file, "filename", "") or fallback)


def _normalize_form(form: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    bool_fields = {
        "async_mode",
        "skip_duplicates",
        "ip_check",
        "title_optimize",
        "description",
        "size",
        "grid_image",
        "detail_image",
        "product_video_template",
        "cos_upload",
        "strict_external",
        "qualification_mode",
        "ai_media_opt_in",
        "image_rewrite",
        "preserve_source_images",
        "source_image_to_library",
        "preflight_only",
        "include_product_video",
    }
    int_fields = {"max_products", "plugin_session_id"}
    for key, value in form.items():
        if hasattr(value, "read"):
            continue
        if key in bool_fields:
            normalized[key] = str(value).strip().lower() in {"1", "true", "yes", "on", "strict"}
        elif key in int_fields:
            try:
                normalized[key] = int(value or 0)
            except (TypeError, ValueError):
                normalized[key] = 0
        else:
            normalized[key] = value
    return normalized


def _download_media_type(path: Path) -> str:
    if path.suffix.lower() == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "text/csv; charset=utf-8"


def _workspace(value: str | None) -> str:
    normalized = str(value or "local").strip()
    if not normalized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "workspace id must not be empty")
    return normalized
