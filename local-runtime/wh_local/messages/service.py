from __future__ import annotations

import logging
import threading
from typing import Any, Callable
from urllib.parse import quote

import httpx

from ..config import is_ip_literal_host
from .repository import MessagesRepository

logger = logging.getLogger("wh_local.messages")


class AnnouncementSyncService:
    """从公告发布后台拉取公告并写入本地消息表。

    服务器不可达时静默降级（仅记录日志），不影响工作台任何功能。
    定向发送：通过 account_id_provider 提供当前登录账号，同步时带上
    ``?account_id=``，后台只返回全员公告 + 发给该账号的定向公告。
    """

    def __init__(
        self,
        repository: MessagesRepository,
        base_url: str,
        *,
        interval_seconds: int = 300,
        account_id_provider: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.interval_seconds = interval_seconds
        self.account_id_provider = account_id_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def configured(self) -> bool:
        return bool(self.base_url)

    def _request_kwargs(self) -> dict[str, Any]:
        # IP-direct connections to a test/staging host cannot match the public
        # certificate's hostname; skip verification only for bare IP literals.
        if is_ip_literal_host(self.base_url):
            return {"timeout": 10, "verify": False}
        return {"timeout": 10}

    def sync_once(self) -> int:
        """执行一次同步，返回新增消息数；失败返回 0。"""
        if not self.configured():
            return 0
        url = f"{self.base_url}/api/announcements/public"
        account_id = ""
        try:
            if self.account_id_provider is not None:
                account_id = (self.account_id_provider() or "").strip()
        except Exception:  # 身份查询失败不影响同步：退化为仅拉全员公告
            account_id = ""
        if account_id:
            url += f"?account_id={quote(account_id)}"
        try:
            response = httpx.get(url, **self._request_kwargs())
            response.raise_for_status()
            payload = response.json()
            items = payload.get("announcements") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                logger.warning("announcement sync: unexpected payload from %s", url)
                return 0
            new_count = self.repository.upsert_server_announcements(items)
            # 撤回：服务器返回完整在线列表时，把已下线/已删除的本地消息一并移除。
            # id 异常（非数字）的条目单独跳过，避免单个坏数据让整轮撤回清理被跳过。
            active_ids: list[int] = []
            for item in items:
                try:
                    active_ids.append(int(item.get("id") or 0))
                except (TypeError, ValueError):
                    logger.warning("announcement sync: bad server id %r", item.get("id"))
            self.repository.prune_retracted(active_ids)
            self._sync_pending_images()
            return new_count
        except Exception as exc:  # 离线/服务器未就绪：静默降级
            logger.info("announcement sync unavailable (%s): %s", url, exc)
            return 0

    def _sync_pending_images(self) -> None:
        """按需拉取带图公告的图片本体，存本地缓存供弹窗直接读取。

        列表接口只回 image_count/image_rev，避免每轮同步都搬运 base64；
        这里只补本地缺图的公告（每轮最多 10 条），失败静默跳过下次再试。
        """
        try:
            pending = self.repository.messages_missing_images()
        except Exception:
            logger.exception("announcement images: scan pending failed")
            return
        for row in pending:
            server_id = int(row.get("server_id") or 0)
            if server_id <= 0:
                continue
            try:
                response = httpx.get(
                    f"{self.base_url}/api/announcements/{server_id}/images",
                    **self._request_kwargs(),
                )
                response.raise_for_status()
                payload = response.json()
                images = payload.get("images") if isinstance(payload, dict) else None
                if not isinstance(images, list):
                    continue
                image_rev = int(payload.get("image_rev") or row.get("image_rev") or 0)
                self.repository.save_message_images(server_id, image_rev, images)
            except Exception as exc:  # 单条失败不影响其余公告
                logger.info("announcement images unavailable (%s): %s", server_id, exc)

    def start(self) -> None:
        if self._thread is not None or not self.configured():
            return
        self._stop.clear()

        def _run() -> None:
            while not self._stop.is_set():
                try:
                    self.sync_once()
                except Exception:
                    logger.exception("announcement sync crashed")
                self._stop.wait(self.interval_seconds)

        self._thread = threading.Thread(
            target=_run, name="announcement-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None


class FeedbackReplySyncService:
    """从后台拉取管理员对用户反馈的回复，写入本地消息表（kind=feedback_reply）。

    与 AnnouncementSyncService 同构，仅拉取端点与消息类型不同：
    - 端点：{base_url}/api/feedback-replies/public
    - kind：feedback_reply（前端据此在消息中心做轻微样式区分）
    """

    def __init__(
        self,
        repository: MessagesRepository,
        base_url: str,
        *,
        interval_seconds: int = 180,
        account_id_provider: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.interval_seconds = interval_seconds
        self.account_id_provider = account_id_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def configured(self) -> bool:
        return bool(self.base_url)

    def sync_once(self) -> int:
        """执行一次同步，返回新增消息数；失败返回 0。"""
        if not self.configured():
            return 0
        url = f"{self.base_url}/api/feedback-replies/public"
        account_id = ""
        try:
            if self.account_id_provider is not None:
                account_id = (self.account_id_provider() or "").strip()
        except Exception:  # 身份查询失败不影响同步
            account_id = ""
        if account_id:
            url += f"?account_id={quote(account_id)}"
        try:
            request_kwargs = {"timeout": 10, "verify": False} if is_ip_literal_host(self.base_url) else {"timeout": 10}
            response = httpx.get(url, **request_kwargs)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("announcements") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                logger.warning("feedback reply sync: unexpected payload from %s", url)
                return 0
            # 反馈回复以 feedback_replies 的 id 作为 server_id，加 10 亿偏移
            # 与公告（announcements.id）共享同一 messages 表但不冲突。
            reply_items = [
                {**item, "id": int(item.get("id") or 0) + 1_000_000_000}
                for item in items
            ]
            new_count = self.repository.upsert_server_announcements(
                reply_items, kind="feedback_reply"
            )
            # 撤回：服务器已删除的反馈回复，本地对应消息一并移除。
            active_ids = [int(item.get("id") or 0) for item in reply_items]
            self.repository.prune_retracted(active_ids, kind="feedback_reply")
            return new_count
        except Exception as exc:  # 离线/服务器未就绪：静默降级
            logger.info("feedback reply sync unavailable (%s): %s", url, exc)
            return 0

    def start(self) -> None:
        if self._thread is not None or not self.configured():
            return
        self._stop.clear()

        def _run() -> None:
            while not self._stop.is_set():
                try:
                    self.sync_once()
                except Exception:
                    logger.exception("feedback reply sync crashed")
                self._stop.wait(self.interval_seconds)

        self._thread = threading.Thread(
            target=_run, name="feedback-reply-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None
