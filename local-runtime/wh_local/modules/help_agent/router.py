"""操作答疑智能体的 HTTP 接口。

接口设计与 ``service.py`` 返回结构一一对应，前端只需要按 ``type`` 分支渲染：

* ``hit``       —— 直接展示答案
* ``candidates``—— 展示候选问题列表（**不含答案**），用户点选后调 ``/confirm``
* ``fallback``  —— 展示「没找到，去反馈」的引导

鉴权沿用项目既有约定：``Depends(actor_from_authorization)`` + ``require_permission``。

**例外：``/search`` 与 ``/confirm`` 允许未登录访问**（``optional_actor_from_authorization``）。
注册/登录页也挂着答疑悬浮球 —— 新人最容易卡在"插件怎么装、邀请码从哪来"这一步，
不让问等于把帮助锁在门内。这里只放行**只读检索**：答疑内容是本地 FAQ 文本，
不含任何用户数据；``/reload``、``/missed``(GET/DELETE)、``/missed/purge`` 仍然必须登录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...session import (
    Actor,
    actor_from_authorization,
    optional_actor_from_authorization,
    require_permission,
)
from .service import MISSED_RETENTION_DAYS, HelpAgentService, faq_data_path


def create_router(database_path: Path) -> APIRouter:
    router = APIRouter(prefix="/api/help-agent", tags=["help-agent"])
    service = HelpAgentService(database_path)

    def permitted(actor: Actor | None, permission: str) -> None:
        """``actor`` 为 ``None`` 表示未登录（注册/登录页），只放行只读检索。

        写操作、维护类接口的 ``actor`` 仍是必填的 ``Actor``，未登录根本进不来。
        """
        if actor is None:
            return
        require_permission(actor, permission, database_path)

    @router.get("/bootstrap")
    def bootstrap(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        """冷启动信息：FAQ 库规模与版本，方便前端展示与排查。"""
        permitted(actor, "help_agent.read")
        library = service.library()
        return {
            "faq_count": len(library.faqs),
            "version": library.version,
            "data_path": str(faq_data_path()),
        }

    @router.post("/search")
    async def search(
        request: Request,
        actor: Actor | None = Depends(optional_actor_from_authorization),
    ) -> dict[str, Any]:
        """检索 FAQ。**未登录也可调用**（注册/登录页的答疑悬浮球）。

        请求体：``{"question": "怎么上传图片"}``

        响应 ``type`` 三选一：

        * ``hit``        —— ``faq_id`` / ``question`` / ``answer`` / ``category`` / ``matched_layer``
        * ``candidates`` —— ``candidates: [{faq_id, question, category}]``（无答案）
        * ``fallback``   —— 无答案无候选，前端引导去反馈
        """
        permitted(actor, "help_agent.read")
        body = await _body(request)
        question = body.get("question")
        if not isinstance(question, str):
            raise HTTPException(status_code=400, detail="question is required")
        return service.search(question)

    @router.post("/confirm")
    async def confirm(
        request: Request,
        actor: Actor | None = Depends(optional_actor_from_authorization),
    ) -> dict[str, Any]:
        """用户点选候选后取答案。**未登录也可调用**（与 ``/search`` 同一限制）。

        请求体：``{"faq_id": "sku-image-upload"}``

        候选阶段不下发答案，只有用户明确点选后才返回，避免前端出现
        「答案已经在手里但没展示」的误导。
        """
        permitted(actor, "help_agent.read")
        body = await _body(request)
        faq_id = body.get("faq_id")
        if not isinstance(faq_id, str) or not faq_id.strip():
            raise HTTPException(status_code=400, detail="faq_id is required")
        faq = service.confirm(faq_id.strip())
        if faq is None:
            raise HTTPException(status_code=404, detail="faq not found")
        return faq

    @router.post("/reload")
    def reload_library(actor: Actor = Depends(actor_from_authorization)) -> dict[str, Any]:
        """重新加载 FAQ 数据文件（FAQ 更新后无需重启进程）。"""
        permitted(actor, "help_agent.read")
        library = service.reload_library()
        return {"faq_count": len(library.faqs), "version": library.version}

    @router.get("/missed")
    def missed(
        actor: Actor = Depends(actor_from_authorization),
        limit: int = 100,
    ) -> dict[str, Any]:
        """列出未命中问题（按热度倒序）。仅存问题文本，无任何用户身份信息。

        时间戳按**本机时区**返回（库里存 UTC，见 ``service._to_local_time``）。
        """
        permitted(actor, "help_agent.read")
        return {"items": service.list_missed(limit=limit)}

    @router.delete("/missed")
    def clear_missed(actor: Actor = Depends(actor_from_authorization)) -> dict[str, int]:
        """清空未命中清单。"""
        permitted(actor, "help_agent.delete")
        return {"deleted": service.clear_missed()}

    @router.post("/missed/purge")
    def purge_missed(actor: Actor = Depends(actor_from_authorization)) -> dict[str, int]:
        """清理超过保留期的未命中记录。"""
        permitted(actor, "help_agent.delete")
        return {"deleted": service.purge_expired(retention_days=MISSED_RETENTION_DAYS)}

    return router


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="JSON body is required") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object is required")
    return body
