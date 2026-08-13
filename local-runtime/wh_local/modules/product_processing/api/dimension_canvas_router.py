from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import ValidationError

from ..dimension_canvas_service import (
    DimensionCanvasConflict,
    DimensionCanvasNotFound,
    DimensionCanvasService,
)
from .dimension_canvas_schemas import (
    CompleteDimensionItemRequest,
    ImportPreviewItemRequest,
    ImportTaskRequest,
    SaveDimensionItemRequest,
)


def create_dimension_canvas_router(service: DimensionCanvasService) -> APIRouter:
    router = APIRouter(prefix="/dimension-canvas", tags=["product_processing_dimension_canvas"])

    @router.get("/importable-tasks")
    def importable_tasks(
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> list[dict[str, Any]]:
        return _call(service.importable_tasks, workspace_id=_workspace(workspace_id))

    @router.get("/tasks/{task_id}/eligibility")
    def task_eligibility(
        task_id: int,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, list[dict[str, Any]]]:
        return _call(service.task_eligibility, task_id, workspace_id=_workspace(workspace_id))

    @router.post("/items/import-preview-item")
    def import_preview_item(
        body: ImportPreviewItemRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(
            service.import_preview_item,
            body.task_id,
            body.task_item_id,
            workspace_id=_workspace(workspace_id),
        )

    @router.get("/items/{item_id}")
    def get_item(
        item_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.get_item, item_id, workspace_id=_workspace(workspace_id))

    @router.patch("/items/{item_id}")
    def save_item(
        item_id: str,
        body: SaveDimensionItemRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        patch = body.model_dump(exclude={"expected_revision"}, exclude_unset=True)
        return _call(
            service.save_item,
            item_id,
            body.expected_revision,
            patch,
            workspace_id=_workspace(workspace_id),
        )

    @router.post("/items/{item_id}/assets")
    async def upload_asset(
        item_id: str,
        request: Request,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is required")
        try:
            content = await file.read(25 * 1024 * 1024 + 1)
            return _call(
                service.upload_asset,
                item_id,
                content,
                str(getattr(file, "filename", "") or "uploaded-image"),
                str(getattr(file, "content_type", "") or ""),
                workspace_id=_workspace(workspace_id),
            )
        finally:
            await file.close()

    @router.post("/items/{item_id}/complete")
    def complete_item(
        item_id: str,
        body: CompleteDimensionItemRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        if body.expected_revision is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "expected_revision is required")
        return _call(
            service.complete_item,
            item_id,
            body.expected_revision,
            workspace_id=_workspace(workspace_id),
        )

    @router.post("/items/{item_id}/retry-render")
    def retry_render(
        item_id: str,
        body: CompleteDimensionItemRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(
            service.retry_render,
            item_id,
            body.expected_revision,
            workspace_id=_workspace(workspace_id),
        )

    @router.get("/batches")
    def list_batches(
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> list[dict[str, Any]]:
        return _call(service.list_batches, workspace_id=_workspace(workspace_id))

    @router.get("/batches/{batch_id}")
    def get_batch(
        batch_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.get_batch, batch_id, workspace_id=_workspace(workspace_id))

    @router.post("/batches/import-task")
    def import_task(
        body: ImportTaskRequest,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(
            service.import_task,
            body.task_id,
            body.task_item_ids,
            existing_dimension_actions=body.existing_dimension_actions,
            workspace_id=_workspace(workspace_id),
        )

    @router.post("/batches/{batch_id}/submit-review")
    def submit_review(
        batch_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.submit_review, batch_id, workspace_id=_workspace(workspace_id))

    @router.get("/change-sets/{change_set_id}")
    def get_change_set(
        change_set_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.get_change_set, change_set_id, workspace_id=_workspace(workspace_id))

    @router.post("/change-sets/{change_set_id}/accept")
    def accept_change_set(
        change_set_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(service.accept_change_set, change_set_id, workspace_id=_workspace(workspace_id))

    @router.post("/change-sets/{change_set_id}/items/{change_item_id}/accept")
    def accept_change_item(
        change_set_id: str,
        change_item_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        _call(
            service.accept_change_item,
            change_set_id,
            change_item_id,
            workspace_id=_workspace(workspace_id),
        )
        return _call(service.get_change_set, change_set_id, workspace_id=_workspace(workspace_id))

    @router.post("/change-sets/{change_set_id}/items/{change_item_id}/reject")
    def reject_change_item(
        change_set_id: str,
        change_item_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        _call(
            service.reject_change_item,
            change_set_id,
            change_item_id,
            workspace_id=_workspace(workspace_id),
        )
        return _call(service.get_change_set, change_set_id, workspace_id=_workspace(workspace_id))

    @router.get("/notifications")
    def notifications(
        after: str = Query(default=""),
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> list[dict[str, Any]]:
        return _call(
            service.list_notifications,
            workspace_id=_workspace(workspace_id),
            after=after,
        )

    @router.post("/notifications/{notification_id}/read")
    def mark_notification_read(
        notification_id: str,
        workspace_id: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return _call(
            service.mark_notification_read,
            notification_id,
            workspace_id=_workspace(workspace_id),
        )

    return router


def _call(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except DimensionCanvasNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except DimensionCanvasConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _workspace(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "workspace id must not be empty")
    return normalized
