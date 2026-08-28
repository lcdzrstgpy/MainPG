from __future__ import annotations

import logging
import threading

import httpx

from .repository import MessagesRepository

logger = logging.getLogger("wh_local.messages")


class AnnouncementSyncService:
    """从公告发布后台拉取公告并写入本地消息表。

    服务器不可达时静默降级（仅记录日志），不影响工作台任何功能。
    """

    def __init__(
        self,
        repository: MessagesRepository,
        base_url: str,
        *,
        interval_seconds: int = 300,
    ) -> None:
        self.repository = repository
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def configured(self) -> bool:
        return bool(self.base_url)

    def sync_once(self) -> int:
        """执行一次同步，返回新增消息数；失败返回 0。"""
        if not self.configured():
            return 0
        url = f"{self.base_url}/api/announcements/public"
        try:
            response = httpx.get(url, timeout=10)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("announcements") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                logger.warning("announcement sync: unexpected payload from %s", url)
                return 0
            new_count = self.repository.upsert_server_announcements(items)
            # 撤回：服务器返回完整在线列表时，把已下线/已删除的本地消息一并移除。
            active_ids = [int(item.get("id") or 0) for item in items]
            self.repository.prune_retracted(active_ids)
            return new_count
        except Exception as exc:  # 离线/服务器未就绪：静默降级
            logger.info("announcement sync unavailable (%s): %s", url, exc)
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
                    logger.exception("announcement sync crashed")
                self._stop.wait(self.interval_seconds)

        self._thread = threading.Thread(
            target=_run, name="announcement-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread = None
