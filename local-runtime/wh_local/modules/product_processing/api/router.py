from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from ..infrastructure.assets import ProductProcessingAssets
from ..infrastructure.database import create_database
from ..infrastructure.repository import ProductProcessingRepository
from ..service import (
    ProductProcessingConflict,
    ProductProcessingNotFound,
    ProductProcessingService,
)
from .schemas import (
    DailySelectionIntakeRequest,
    DailySelectionHandoffRequest,
    DraftCreateRequest,
    DraftDeleteRequest,
    DraftProcessRequest,
    DraftUpdateRequest,
    PromptUpdateRequest,
    RetryTaskRequest,
    extras,
)


def create_product_processing_router(
    service: ProductProcessingService | None = None,
    *,
    database_url: str | None = None,
    assets_root: Path | None = None,
) -> APIRouter:
    """Create the complete local API used by the Product Processing screen."""
    owned_database = None
    if service is None:
        owned_database = create_database(database_url)
        service = ProductProcessingService(
            ProductProcessingRepository(owned_database),
            ProductProcessingAssets(assets_root),
        )
    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            if owned_database is not None:
                owned_database.dispose()

    router = APIRouter(prefix="/product-processing", tags=["product_processing"], lifespan=lifespan)

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
    ) -> dict[str, Any]:
        payload = {**body.model_dump(), **extras(body)}
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
    ) -> dict[str, Any]:
        form, file = await _upload_form(request, "file")
        try:
            return _call(
                service.process_workbook,
                _filename(file, "products.xlsx"),
                await file.read(),
                _normalize_form(form),
                idempotency_key=request.headers.get("Idempotency-Key"),
                workspace_id=_workspace(workspace_id),
            )
        finally:
            await file.close()

    @router.post("/engine/single")
    async def process_single(
        request: Request,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        form = await request.form()
        image = form.get("image_file")
        content = await image.read() if image is not None and hasattr(image, "read") else None
        try:
            return _call(
                service.process_single,
                _normalize_form(dict(form)),
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
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return service.task_history(limit, _workspace(workspace_id))

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

    @router.post("/tasks/{task_id}/pause")
    def pause_task(
        task_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.pause_task, task_id, _workspace(workspace_id))

    @router.post("/tasks/{task_id}/resume")
    def resume_task(
        task_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.resume_task, task_id, _workspace(workspace_id))

    @router.post("/tasks/{task_id}/retry-attention")
    def retry_attention(
        task_id: int,
        body: RetryTaskRequest | None = None,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        _ = body
        return _call(service.retry_attention, task_id, _workspace(workspace_id))

    @router.post("/tasks/{task_id}/clear")
    def clear_task(
        task_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.clear_task, task_id, _workspace(workspace_id))

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
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


async def _upload_form(request: Request, field: str):
    form_data = await request.form()
    file = form_data.get(field)
    if file is None or not hasattr(file, "read"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{field} is required")
    return dict(form_data), file


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
