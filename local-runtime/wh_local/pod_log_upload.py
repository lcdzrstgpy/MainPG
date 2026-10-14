"""POD 业务日志（``pod_processing.log``）的每日静默上传。

**零常驻后台**：不启动任何守护线程 / 定时器 / 轮询循环。应用启动时做**一次**
due check——若"今天的上传时刻（默认 12:00）已过"且"今天还没成功上传"，就把
``pod_processing.log`` 上报一次；否则什么都不做。上传完成后当天不会再传。

语义与代价（已与产品方确认）：

- 应用每天启动、且启动时已过 12:00 → 每天上传一次；
- 早上启动且整天不重启 → 当天不传，等下次启动（因为不常驻，无法卡在 12:00 整点）；
- 服务的 ``upload_hour`` 可配置（设为 0 即"启动就传"）。

服务端契约：``POST /api/customer/pod-log``（**POD 专用新接口**，非启动器那个
``log-upload``），Bearer 鉴权，body 为 base64 JSON：
``{"log_name": "pod_processing.log", "content_b64": "...", "app_version": "1.5.2", "platform": "macos"}``。
路径可用环境变量 ``WH_LOCAL_POD_LOG_UPLOAD_PATH`` 覆盖。

远端 token 只存在于进程内存（见 ``customer/db_store.py``），所以由调用方注入
``token_provider`` 取"最近活跃"令牌；取不到就跳过本轮（当天仍算未上传，下次启动再试）。
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import APP_VERSION

logger = logging.getLogger(__name__)

LOG_NAME = "pod_processing.log"
UPLOAD_HOUR = 12
# 服务端上限 16MB；本地留 1MB 余量，避免 base64 解码后刚好越界被拒。
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
TRUNCATION_MARKER = "……（日志过大，仅上传末尾内容）\n"


def current_platform() -> str:
    """与 customer/routes.py 提交反馈时用的平台口径保持一致。"""

    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def read_log_tail(path: Path, *, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """读取日志内容；超过上限时只取末尾，并在行边界切开（不拦腰截断一行）。"""

    size = path.stat().st_size
    if size <= max_bytes:
        return path.read_bytes()
    with path.open("rb") as handle:
        handle.seek(size - max_bytes)
        tail = handle.read()
    newline = tail.find(b"\n")
    if newline != -1:
        tail = tail[newline + 1 :]
    return TRUNCATION_MARKER.encode("utf-8") + tail


def build_payload(
    log_path: Path,
    *,
    app_version: str = APP_VERSION,
    platform: str | None = None,
) -> dict[str, Any] | None:
    """构造上传体；日志不存在、为空或读不到时返回 ``None``（调用方按"跳过"处理）。"""

    try:
        if not log_path.is_file():
            return None
        raw = read_log_tail(log_path)
    except OSError as exc:
        logger.warning("POD 日志上传：读取 %s 失败：%s", log_path, exc)
        return None
    if not raw:
        return None
    return {
        "log_name": LOG_NAME,
        "content_b64": base64.b64encode(raw).decode("ascii"),
        "app_version": str(app_version or ""),
        "platform": platform or current_platform(),
    }


class _DailyState:
    """「今天是否已成功上传」的落盘状态（只存日期，不含任何凭据）。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def last_success_date(self) -> str:
        try:
            return str(json.loads(self.path.read_text(encoding="utf-8")).get("last_success_date") or "")
        except (OSError, ValueError, AttributeError):
            return ""

    def mark_success(self, day: str) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"last_success_date": day}, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("POD 日志上传：写入状态文件失败：%s", exc)


class PodLogUploadTask:
    """一次性任务：应用启动时调用 ``run_if_due()``，内部不创建线程。

    同一天重复调用只会成功上传一次（按本地自然日去重，状态落盘，重启也不会重复）。
    """

    def __init__(
        self,
        *,
        log_dir: Path,
        token_provider: Callable[[], str],
        client_factory: Callable[[], Any],
        state_path: Path | None = None,
        app_version: str = APP_VERSION,
        upload_hour: int = UPLOAD_HOUR,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.log_dir = Path(log_dir)
        self._token_provider = token_provider
        self._client_factory = client_factory
        self._app_version = app_version
        self._upload_hour = int(upload_hour)
        self._now = now or datetime.now
        self._state = _DailyState(
            Path(state_path) if state_path is not None else self.log_dir / "pod_log_upload_state.json"
        )

    @property
    def log_path(self) -> Path:
        return self.log_dir / LOG_NAME

    def uploaded_today(self) -> bool:
        return self._state.last_success_date() == self._today()

    def should_upload_now(self) -> bool:
        """今天的上传时刻已过、且今天还没成功上传 → 该传了。"""

        now = self._now()
        slot = now.replace(hour=self._upload_hour, minute=0, second=0, microsecond=0)
        return now >= slot and not self.uploaded_today()

    def run_if_due(self) -> str:
        """启动时调用一次。返回状态串，永不抛异常（调用方无需 try）。

        ``uploaded`` / ``skipped:not-due`` / ``skipped:already-uploaded`` /
        ``skipped:no-token`` / ``skipped:no-log`` / ``failed``。
        """

        try:
            if not self.should_upload_now():
                return "skipped:not-due"
            return self._attempt()
        except Exception as exc:  # noqa: BLE001 - 上报永远不得影响主流程
            logger.warning("POD 日志上传：本轮异常（忽略）：%s", exc)
            return "failed"

    # ---- 内部 ----

    def _attempt(self) -> str:
        if self.uploaded_today():
            return "skipped:already-uploaded"

        token = ""
        try:
            token = str(self._token_provider() or "")
        except Exception as exc:  # noqa: BLE001 - token provider 故障按"没 token"处理
            logger.warning("POD 日志上传：取远端 token 失败：%s", exc)
        if not token:
            # 没有活跃远端会话（未登录 / 重启后未再登录）：跳过，不标记当天完成。
            return "skipped:no-token"

        payload = build_payload(self.log_path, app_version=self._app_version)
        if payload is None:
            return "skipped:no-log"

        try:
            self._client_factory().upload_pod_log(token, payload)
        except Exception as exc:  # noqa: BLE001 - 失败就等下次启动，绝不冒泡
            logger.warning("POD 日志上传失败（等下次启动重试）：%s", exc)
            return "failed"

        self._state.mark_success(self._today())
        logger.info("POD 日志上传成功：%s（%d 字节 base64）", LOG_NAME, len(payload["content_b64"]))
        return "uploaded"

    def _today(self) -> str:
        return self._now().strftime("%Y-%m-%d")
