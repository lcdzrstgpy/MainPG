"""新手引导配置的 HTTP 接口。

* ``GET  /api/guide/config`` —— 所有登录用户都要读它来播放引导，只做登录校验
* ``PUT  /api/guide/config`` —— 仅管理员（admin / owner）可写，对应前端「编辑引导」入口

写入前由 ``service.normalize_config`` 做结构校验，错误以 400 返回，前端直接展示 detail。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...session import Actor, actor_from_authorization
from .service import GuideConfigService

# 与前端 isAdminRole 保持一致：admin 与 owner 都可以编辑引导。
EDITOR_ROLES = ("admin", "owner")


def create_router(database_path: Path) -> APIRouter:
    router = APIRouter(prefix="/api/guide", tags=["guide"])
    service = GuideConfigService(database_path)

    def require_editor(actor: Actor) -> None:
        if actor.role.strip().lower() not in EDITOR_ROLES:
            raise HTTPException(status_code=403, detail="admin permission required")

    @router.get("/config")
    def get_config(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        return service.load()

    @router.put("/config")
    async def save_config(
        request: Request,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        require_editor(actor)
        try:
            payload = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="请求体不是合法的 JSON") from exc
        try:
            return service.save(payload, actor_id=actor.id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
