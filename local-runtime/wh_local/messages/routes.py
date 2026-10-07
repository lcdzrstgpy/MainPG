from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from ..session import actor_from_authorization
from .repository import MessagesRepository
from .service import AnnouncementSyncService, FeedbackReplySyncService


def create_messages_router(
    repository: MessagesRepository,
    sync_service: AnnouncementSyncService | None = None,
    reply_sync_service: FeedbackReplySyncService | None = None,
    remote_token_provider: Callable[[str], str] | None = None,
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

    if sync_service is not None or reply_sync_service is not None:

        @router.post("/sync")
        def sync(
            authorization: str | None = Header(default=None),
            _: Any = Depends(actor_from_authorization),
        ) -> dict[str, Any]:
            # 公告与反馈回复各自独立撤回（prune_retracted 按 kind 隔离），
            # 一次调用同时刷新两个通道，让「登录/进入工作台立即同步」把两边的
            # 陈旧消息都清掉，避免同机换号后短暂看到上一个账号的定向内容。
            #
            # 身份：把本地会话令牌换成远端会话令牌带给发布后台，后台据此解析
            # 账号再做定向过滤 —— 不能只带 account_id（它可由邮箱推导、可伪造）。
            remote_token = ""
            if remote_token_provider is not None and authorization:
                local_token = authorization.removeprefix("Bearer ").strip()
                try:
                    remote_token = str(remote_token_provider(local_token) or "")
                except Exception:  # 换不到就退化为仅拉全员公告，不影响同步
                    remote_token = ""
            new_announcements = (
                sync_service.sync_once(auth_token=remote_token)
                if sync_service is not None
                else 0
            )
            new_replies = (
                reply_sync_service.sync_once(auth_token=remote_token)
                if reply_sync_service is not None
                else 0
            )
            return {
                "ok": True,
                "new": new_announcements,
                "new_replies": new_replies,
            }

    return router
