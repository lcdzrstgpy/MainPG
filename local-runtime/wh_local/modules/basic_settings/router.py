from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...session import Actor, actor_from_authorization, require_admin
from .schemas import ImageModelSelection, PodImageModelSelection, SystemConfigUpdate
from .service import SystemConfigService


def create_router(database_path: Path) -> APIRouter:
    router = APIRouter(prefix="/desktop/basic-settings", tags=["basic-settings"])
    service = SystemConfigService(database_path)

    @router.get("/system-config")
    def get_system_config(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        # GET 只返回公开配置 + 密钥是否已配置的状态（不含密钥原文），无需 admin 限制。
        return service.get_config()

    @router.put("/system-config")
    def save_system_config(
        payload: SystemConfigUpdate,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        # 保存页面表单；空密钥表示不修改，clear_* 字段才表示清空。
        # 已去掉 require_admin 限制（PUT 写入加密密钥，鉴权由 actor_from_authorization 保证）。
        try:
            return service.save_config(payload, actor_id=actor.id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.get("/image-model")
    def get_image_model(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        # 个人中心「模型选择」：读取当前生图模型与可选项（只含模型名，不含凭据）。
        return service.get_image_model()

    @router.put("/image-model")
    def save_image_model(
        payload: ImageModelSelection,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        # 只改生图模型，不动系统配置的其他字段（避免整表 PUT 清空 cos 等未提交字段）。
        return service.save_image_model(payload.model, actor_id=actor.id)

    @router.get("/pod-image-model")
    def get_pod_image_model(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        # POD 使用独立模型配置，不跟随 AI处理 的生图模型。
        return service.get_pod_image_model()

    @router.put("/pod-image-model")
    def save_pod_image_model(
        payload: PodImageModelSelection,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        return service.save_pod_image_model(payload.model, actor_id=actor.id)

    @router.post("/system-config/publish")
    def publish_system_config(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        # 生成给桌面端/运行任务消费的发布摘要，暂不直接触发线上变更。
        require_admin(actor)
        return service.publish_manifest(actor_id=actor.id)

    return router
