"""自动同步：按间隔给已绑定店铺下发采集指令。

本机常驻场景下用户只需开机，插件会自己轮询命令队列；这里只负责
「什么时候发命令」。汇总上传由 ``team_store.sync.TeamRollupTask`` 独立负责，
两者互不依赖。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

_DEFAULT_TICK_SECONDS = 30


class AutoCaptureTask:
    def __init__(
        self,
        *,
        service: Any,
        workspaces: Callable[[], Iterable[str]],
        tick_seconds: int = _DEFAULT_TICK_SECONDS,
    ) -> None:
        self._service = service
        self._workspaces = workspaces
        self._tick = max(10, int(tick_seconds))
        self._thread: threading.Thread | None = None
        self._busy = threading.Lock()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="order-profit-auto-capture", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        # 启动后先等一会儿，避开登录与初始化高峰。
        time.sleep(30)
        while True:
            try:
                self.run_once()
            except Exception as error:  # noqa: BLE001 - 后台任务不得拖垮主进程
                logger.warning("auto capture tick failed: %s", error)
            time.sleep(self._tick)

    def run_once(self) -> list[dict[str, Any]]:
        """跑一轮；上一轮未结束时直接跳过，避免重复下发。"""
        if not self._busy.acquire(blocking=False):
            return []
        try:
            results: list[dict[str, Any]] = []
            for workspace_id in self._workspaces():
                try:
                    outcome = self._service.auto_capture_sweep(workspace_id)
                except Exception as error:  # noqa: BLE001
                    logger.warning("auto capture failed for %s: %s", workspace_id, error)
                    continue
                if outcome.get("ran"):
                    results.append({"workspace_id": workspace_id, **outcome})
            return results
        finally:
            self._busy.release()
