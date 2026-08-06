from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...session import Actor, actor_from_authorization, require_admin
from .schemas import SystemConfigUpdate
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
        require_admin(actor)
        try:
            return service.save_config(payload, actor_id=actor.id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("/system-config/publish")
    def publish_system_config(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        # 生成给桌面端/运行任务消费的发布摘要，暂不直接触发线上变更。
        require_admin(actor)
        return service.publish_manifest(actor_id=actor.id)

    return router
