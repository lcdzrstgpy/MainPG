from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..session import actor_from_authorization
from .repository import MessagesRepository
from .service import AnnouncementSyncService


def create_messages_router(
    repository: MessagesRepository,
    sync_service: AnnouncementSyncService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/messages", tags=["messages"])

    @router.get("")
    def list_messages(
        with_images: int = Query(default=0),
        _: Any = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        # 图片 base64 体积大：默认不带，公告弹窗用 with_images=1 取本地缓存的本体。
        return {"messages": repository.list_messages(with_images=bool(with_images))}

    @router.get("/unread-count")
    def unread_count(_: Any = Depends(actor_from_authorization)) -> dict[str, Any]:
        return {"count": repository.unread_count()}

    @router.post("/read-all")
    def read_all(_: Any = Depends(actor_from_authorization)) -> dict[str, Any]:
        return {"ok": True, "updated": repository.mark_all_read()}

    @router.post("/{message_id}/read")
    def mark_read(
        message_id: int, _: Any = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        if not repository.mark_read(message_id):
            raise HTTPException(status_code=404, detail="消息不存在")
        return {"ok": True}

    @router.delete("/{message_id}")
    def delete_message(
        message_id: int, _: Any = Depends(actor_from_authorization)
    ) -> dict[str, Any]:
        if not repository.delete_message(message_id):
            raise HTTPException(status_code=404, detail="消息不存在")
        return {"ok": True}

    if sync_service is not None:

        @router.post("/sync")
        def sync(_: Any = Depends(actor_from_authorization)) -> dict[str, Any]:
            return {"ok": True, "new": sync_service.sync_once()}

    return router
