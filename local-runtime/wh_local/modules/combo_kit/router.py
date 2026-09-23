"""combo_kit FastAPI 路由：独立前缀 /api/combo-kit，独立授权与资源托管。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

from ...customer.contracts import (
    CustomerAuthRejected,
    CustomerAuthUnavailable,
    CustomerBillingPermissionError,
    CustomerBillingProtocolError,
)
from ...session import Actor, actor_from_authorization, actor_from_bearer_token
from .ai_runtime import ComboKitAiRuntime
from .assets import ComboKitAssets
from .billing import ComboKitBillingCoordinator
from .contracts import (
    DEFAULT_GENERATION_MODE,
    EDITABLE_PROMPT_ROLES,
    GENERATION_MODES,
    ComboKitConflict,
    ComboKitError,
    ComboKitNotFound,
    ComboKitValidationError,
)
from .export import ComboDianxiaomiExportError
from .prompts import all_image_roles, default_image_prompts, default_prompts_by_mode
from .repository import ComboKitRepository
from .service import ComboKitService
from .worker import TASK_IMAGE, TASK_SUBJECT, TASK_TEXT, ComboKitTaskWorker


def create_combo_kit_router(
    repository: ComboKitRepository,
    assets: ComboKitAssets,
    ai_runtime: ComboKitAiRuntime,
    billing: ComboKitBillingCoordinator,
    worker: ComboKitTaskWorker,
) -> APIRouter:
    service = ComboKitService(repository, assets, ai_runtime, billing)
    router = APIRouter(prefix="/api/combo-kit", tags=["combo-kit"])
    setattr(router, "combo_kit_service", service)
    setattr(router, "combo_kit_worker", worker)

    @router.get("/roles")
    def stats() -> dict[str, Any]:
        return {
            "image_roles": all_image_roles(),
            "default_image_prompts": default_image_prompts(),
            # 生成选型（bundle / multiview）：前端据此渲染选型卡片并按需切换默认辅助词。
            "generation_modes": [dict(item) for item in GENERATION_MODES],
            "default_generation_mode": DEFAULT_GENERATION_MODE,
            "default_image_prompts_by_mode": default_prompts_by_mode(),
            "editable_prompt_roles": list(EDITABLE_PROMPT_ROLES),
            "min_images": 2,
            "max_images": 6,
            "text_points": 20,
            "image_points": 100,
        }

    @router.get("/sets")
    def list_sets(
        limit: int = Query(default=100, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        return service.list_sets(actor.workspace_id, limit=limit, offset=offset)

    @router.post("/sets")
    async def create_set(
        request: Request,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        return service.create_set(await _body(request), workspace_id=actor.workspace_id, owner_user_id=actor.id)

    @router.get("/sets/{set_id}")
    def get_set(set_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        return service.get_set(set_id)

    @router.patch("/sets/{set_id}")
    async def update_set(
        set_id: str,
        request: Request,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        return service.update_set(set_id, await _body(request), workspace_id=actor.workspace_id)

    @router.delete("/sets/{set_id}")
    def delete_set(set_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        return service.remove_set(set_id)

    @router.get("/sets/{set_id}/items")
    def list_items(set_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        return service.list_items(set_id)

    @router.post("/sets/{set_id}/items")
    async def add_item(
        set_id: str,
        request: Request,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        form = await request.form()
        file: UploadFile | None = form.get("image_file")
        content = await file.read() if file is not None and hasattr(file, "read") else None
        try:
            payload = {key: value for key, value in form.items() if not hasattr(value, "read")}
            return service.add_item(
                set_id,
                payload,
                image_content=content,
                image_filename=str(getattr(file, "filename", "") or "combo-image.jpg"),
                image_content_type=str(getattr(file, "content_type", "") or ""),
                workspace_id=actor.workspace_id,
                owner_user_id=actor.id,
            )
        finally:
            if file is not None and hasattr(file, "close"):
                await file.close()

    @router.patch("/sets/{set_id}/items/{item_id}")
    async def update_item(
        set_id: str,
        item_id: str,
        request: Request,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        return service.update_item(set_id, item_id, await _body(request))

    @router.delete("/sets/{set_id}/items/{item_id}")
    def remove_item(
        set_id: str, item_id: str, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        return service.remove_item(set_id, item_id)

    @router.post("/sets/{set_id}/items/{item_id}/primary")
    def set_primary_item(
        set_id: str, item_id: str, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        return service.set_primary_item(set_id, item_id)

    @router.post("/sets/{set_id}/items/{item_id}/auto-mask")
    def auto_mask_item(
        set_id: str, item_id: str, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        """算法预框选：本地分割出主体轮廓作为初始蒙版，用户只需拖动控制点微调。

        本地推理，不调外部 API、不计费；失败时返回空点，由前端回落到默认六边形。
        """
        return service.auto_mask_item(set_id, item_id)

    @router.post("/sets/{set_id}/items/order")
    async def reorder_items(
        set_id: str, request: Request, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        return service.set_item_order(set_id, await _body(request))

    # 三个长耗时 AI 动作统一「提交后台任务 + 轮询状态」：
    # 单次耗时远超前端 30s HTTP 超时，同步返回会让前端先超时、后端仍在跑并扣费。
    @router.post("/sets/{set_id}/analyze-subject")
    async def analyze_subject(
        set_id: str, request: Request, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        payload = await _body(request)
        return worker.submit(
            set_id=set_id,
            task_type=TASK_SUBJECT,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            job=lambda report: service.analyze_subject(set_id, payload, actor=actor, progress=report),
        )

    @router.get("/sets/{set_id}/tasks/{task_type}")
    def get_task(
        set_id: str, task_type: str, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        """轮询任务状态：进行中返回进度，终态返回结果或错误。"""
        return worker.task_state(set_id, task_type)

    @router.get("/sets/{set_id}/prompt")
    def get_prompt(set_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        return service.get_prompt(set_id)

    @router.post("/sets/{set_id}/prompt")
    async def save_prompt(
        set_id: str,
        request: Request,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        return service.save_prompt(
            set_id, await _body(request), workspace_id=actor.workspace_id, owner_user_id=actor.id
        )

    @router.post("/sets/{set_id}/generate-text")
    def generate_text(set_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        return worker.submit(
            set_id=set_id,
            task_type=TASK_TEXT,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            job=lambda report: service.generate_text(set_id, actor=actor, progress=report),
        )

    @router.post("/sets/{set_id}/generate-images")
    async def generate_images(
        set_id: str,
        request: Request,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        body = await _body(request)
        roles = body.get("roles")
        target_roles = [str(r).strip() for r in roles if str(r).strip()] if isinstance(roles, list) else None
        return worker.submit(
            set_id=set_id,
            task_type=TASK_IMAGE,
            workspace_id=actor.workspace_id,
            owner_user_id=actor.id,
            job=lambda report: service.generate_images(set_id, actor=actor, roles=target_roles, progress=report),
        )

    @router.delete("/sets/{set_id}/images/{role}")
    def delete_image(
        set_id: str, role: str, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        return service.delete_generated_image(set_id, role)

    @router.post("/sets/{set_id}/watermark/apply")
    def apply_watermark(
        set_id: str, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        """把当前水印配置立即烧到已生成的成品图上（本地合成，不重新生图、不计费）。"""
        return service.apply_watermark_to_images(set_id)

    @router.post("/sets/{set_id}/preview")
    def create_preview(set_id: str, actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        return service.create_preview(set_id, workspace_id=actor.workspace_id, owner_user_id=actor.id)

    @router.patch("/sets/{set_id}/preview")
    async def review_preview(
        set_id: str, request: Request, actor: Actor = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        return service.review_preview(set_id, await _body(request), workspace_id=actor.workspace_id)

    @router.get("/sets/{set_id}/export-dianxiaomi")
    def export_dianxiaomi(
        set_id: str, actor: Actor = Depends(actor_from_authorization)
    ) -> Response:
        export = service.export_dianxiaomi(set_id)
        return Response(
            content=export.content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
        )

    @router.get("/originals/{set_id}/{name}")
    def original_asset(
        set_id: str, name: str, actor: Actor = Depends(actor_from_query_token)
    ) -> FileResponse:
        items = service.repository.list_items(set_id)
        item = next(
            (i for i in items if str(i.get("original_url") or "").endswith(f"/{name}")), None
        )
        if item is None:
            raise HTTPException(404, "asset not found")
        ws = str(item.get("workspace_id") or actor.workspace_id)
        path = service.assets.require_original(
            str(item.get("original_path") or ""), workspace_id=ws
        )
        return FileResponse(path, media_type=_media_type(path))

    @router.get("/generated/{set_id}/{name}")
    def generated_asset(
        set_id: str, name: str, actor: Actor = Depends(actor_from_query_token)
    ) -> FileResponse:
        try:
            base = service.repository.get_set(set_id)
        except KeyError:
            raise HTTPException(404, "set not found") from None
        outputs = _read_json(base.get("image_results_json") or "[]")
        item = next((o for o in outputs if str(o.get("role") or "") == Path(name).stem), None)
        if item is None:
            raise HTTPException(404, "asset not found")
        ws = str(base.get("workspace_id") or actor.workspace_id)
        path = service.assets.require_generated(
            str(item.get("path") or ""), workspace_id=ws
        )
        # 成品图会被原样覆盖（重新生成 / 立即应用水印），URL 不变，
        # 必须要求浏览器每次回源校验，否则界面仍显示旧图。
        return FileResponse(path, media_type=_media_type(path), headers={"Cache-Control": "no-cache"})

    return router


def register_combo_kit_exception_handlers(app: Any) -> None:
    """把 combo_kit 领域异常映射到 HTTP 状态码（挂在 FastAPI app 上）。"""

    @app.exception_handler(ComboKitError)
    async def _combo_error(request: Request, exc: ComboKitError):  # noqa: ARG001
        if isinstance(exc, ComboKitNotFound):
            return _json_response(404, str(exc))
        if isinstance(exc, ComboKitConflict):
            return _json_response(409, str(exc))
        if isinstance(exc, ComboKitValidationError):
            return _json_response(422, str(exc))
        return _json_response(getattr(exc, "status_code", 400), str(exc))

    @app.exception_handler(ComboDianxiaomiExportError)
    async def _combo_export_error(request: Request, exc: ComboDianxiaomiExportError):  # noqa: ARG001
        return _json_response(422, str(exc))

    @app.exception_handler(CustomerBillingPermissionError)
    async def _combo_forbidden(request: Request, exc: CustomerBillingPermissionError):  # noqa: ARG001
        return _json_response(403, "remote billing session was rejected")

    @app.exception_handler(CustomerBillingProtocolError)
    async def _combo_protocol(request: Request, exc: CustomerBillingProtocolError):  # noqa: ARG001
        return _json_response(502, "remote billing service returned an invalid response")

    @app.exception_handler(CustomerAuthRejected)
    async def _combo_rejected(request: Request, exc: CustomerAuthRejected):  # noqa: ARG001
        return _json_response(502, "remote billing request was rejected")

    @app.exception_handler(CustomerAuthUnavailable)
    async def _combo_unavailable(request: Request, exc: CustomerAuthUnavailable):  # noqa: ARG001
        return _json_response(503, "remote billing service is unavailable")


def _json_response(status_code: int, detail: str):
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    return JSONResponse(status_code=status_code, content={"detail": detail})


async def _body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "request body must be valid JSON") from None
    return payload if isinstance(payload, dict) else {}


def _read_json(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    try:
        parsed = json.loads(str(value)) if str(value).strip() else []
        return parsed if isinstance(parsed, list) else [parsed]
    except (ValueError, TypeError):
        return []


def actor_from_query_token(token: str = Query(default="")) -> Actor:
    """图片路由鉴权：<img> 无法携带 Bearer header，改用 query 传 token。"""
    if not token:
        raise HTTPException(status_code=401, detail="missing asset token")
    return actor_from_bearer_token(token)


def _media_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "image/jpeg"
