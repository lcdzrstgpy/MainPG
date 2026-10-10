"""源图同步的启动补偿线程。

采集回来的货源图要先"同步"落地成本地文件，预检页才显示得出来。但同步目前只在
创建草稿时挂一次 FastAPI ``BackgroundTask``：进程一重启（或被强杀），排队中的
同步就永久丢失，行状态停在 ``pending`` 再没人处理——前端看到还有待同步的图，
就会一直轮询预检接口，表现为"图片不出来 + 页面一直转圈"。

这个常驻 daemon 线程在启动后把仍有待同步行的草稿重新捞回来推进，逐批处理、
失败留待下一轮，避免永久卡死。批量与节奏都保守，避免把图源打爆。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 每轮处理的草稿数：单个草稿要顺序抓十几张图，批量太大会长时间占住线程。
DEFAULT_BATCH_SIZE = 6
# 还有活干时的轮次间隔，以及后台已经清空时的空闲间隔。
DEFAULT_DRAIN_INTERVAL_S = 1.0
DEFAULT_IDLE_INTERVAL_S = 20.0


class SourceImageSyncWorker:
    """把「漏掉的」源图同步补跑完的常驻线程。"""

    def __init__(
        self,
        service: Any,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        drain_interval_s: float = DEFAULT_DRAIN_INTERVAL_S,
        idle_interval_s: float = DEFAULT_IDLE_INTERVAL_S,
    ) -> None:
        self._service = service
        self._batch_size = batch_size
        self._drain_interval_s = drain_interval_s
        self._idle_interval_s = idle_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="source-image-sync", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=5.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            handled = False
            try:
                targets = self._service.claimable_source_image_drafts(limit=self._batch_size)
            except Exception as exc:  # noqa: BLE001 - 补偿线程不能因单次扫描失败退出
                logger.warning("source-image sync scan failed: %s", exc)
                targets = []
            for workspace_id, draft_id in targets:
                if self._stop.is_set():
                    return
                handled = True
                try:
                    result = self._service.sync_draft_source_images(draft_id, workspace_id)
                except Exception as exc:  # noqa: BLE001 - 单个草稿失败不影响其它草稿
                    logger.warning("source-image sync draft=%s failed: %s", draft_id, exc)
                    continue
                if result.get("ready") or result.get("failed"):
                    logger.info(
                        "source-image sync draft=%s ready=%s failed=%s",
                        draft_id,
                        result.get("ready"),
                        result.get("failed"),
                    )
            self._stop.wait(self._drain_interval_s if handled else self._idle_interval_s)
