"""远端会话保活：让远端持续认为"客户端还在线"。

背景
----
远端账号服务（customer-auth）按 ``client_type``（PC / 手机）分组做单端登录：同组内
已存在"未撤销且未过期"的会话时——

* 该会话 ``last_used_at`` 仍在 ``SESSION_STALE_SECONDS``（300 秒）阈值内 → 拒绝新登录；
* 已超过阈值（判定为失联）→ **放行新登录，并撤销旧会话**。

而远端只在"查库命中"的鉴权路径写 ``last_used_at``（命中 Redis 会话缓存时跳过），
本地绝大多数请求（``/api/customer/me``、任务数轮询、维度通知……）走的都是本地会话、
根本不触达远端 —— 用户闲置几分钟后，远端即认为该会话已失联，此后任何一次同类设备
登录都会**静默顶掉**它，前端表现为"登录态莫名其妙掉了"。

本模块周期性地用活跃远端令牌做一次只读 ``GET /api/customer/me``，刷新远端
``last_used_at``，使"客户端在线"这一事实对远端持续可见：重复登录会被明确拒绝
（"该账号已在其他设备登录"），而不是悄悄把当前会话撤掉。

实现要点
--------
* 纯后台 daemon 线程，只在本地后端进程内运行，不改变远端任何行为；
* 间隔必须**大于**远端会话缓存 TTL（120s），否则缓存命中不会写入 ``last_used_at``，
  保活等于白跑 —— 默认 180s；
* 任何异常都只记日志、绝不向上抛：保活失败最多退化成原有行为（闲置后仍会失联）。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

_LOGGER = logging.getLogger("wh_local.customer.session_keepalive")

# 远端会话缓存 TTL 为 120s（auth_server._account_by_token 的 cache_set(ttl=120)）。
# 缓存命中时不会写 last_used_at，因此保活间隔必须大于它。
_DEFAULT_INTERVAL_S = 180.0
# 下限保护：小于远端缓存 TTL 的间隔没有意义，也避免误配成极短间隔打爆远端。
_MIN_INTERVAL_S = 30.0

# 环境变量：设 0（或负数）可完全关闭保活。
_ENV_INTERVAL = "WH_LOCAL_SESSION_KEEPALIVE_S"


def keepalive_interval_seconds() -> float:
    """保活间隔（秒）；返回 0 表示关闭。"""
    raw = str(os.environ.get(_ENV_INTERVAL, "") or "").strip()
    if not raw:
        return _DEFAULT_INTERVAL_S
    try:
        value = float(raw)
    except (TypeError, ValueError):
        _LOGGER.warning("invalid %s=%r, fallback to %ss", _ENV_INTERVAL, raw, _DEFAULT_INTERVAL_S)
        return _DEFAULT_INTERVAL_S
    if value <= 0:
        return 0.0
    return max(value, _MIN_INTERVAL_S)


class RemoteSessionKeepalive:
    """按固定间隔刷新远端会话活跃度的后台线程。

    ``sessions`` 可以是 ``LocalSessionService``（自动取其 ``.store``），
    也可以是 ``SQLiteCustomerSessionStore`` 本身。
    """

    def __init__(
        self,
        remote_auth: Any,
        sessions: Any,
        *,
        interval_s: float = 0.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._remote_auth = remote_auth
        self._sessions = sessions
        self._interval_s = float(interval_s) if interval_s else keepalive_interval_seconds()
        self._logger = logger or _LOGGER
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._touched_total = 0

    # ---- 供测试与诊断使用 ----
    @property
    def interval_seconds(self) -> float:
        return self._interval_s

    @property
    def touched_total(self) -> int:
        return self._touched_total

    def active_tokens(self) -> list[str]:
        """当前进程内存里仍有效的远端令牌（取不到时返回空表）。"""
        lister = getattr(self._sessions, "active_remote_tokens", None)
        if not callable(lister):
            store = getattr(self._sessions, "store", None)
            lister = getattr(store, "active_remote_tokens", None)
        if not callable(lister):
            return []
        try:
            return [str(token) for token in lister() if token]
        except Exception:  # noqa: BLE001 - 读取失败按"无活跃令牌"处理
            return []

    def run_once(self) -> int:
        """刷新一轮，返回成功 touch 的令牌数；异常一律吞掉。"""
        touched = 0
        for token in self.active_tokens():
            try:
                self._remote_auth.touch_session(token)
                touched += 1
            except Exception as exc:  # noqa: BLE001 - 保活失败不得影响业务
                self._logger.debug("remote session keepalive touch failed: %s", exc)
        self._touched_total += touched
        return touched

    def start(self) -> "RemoteSessionKeepalive | None":
        """启动后台线程；未启用/未配置远端时返回 None。"""
        if self._interval_s <= 0:
            self._logger.info("remote session keepalive disabled by config")
            return None
        if self._thread is not None:
            return self
        configured = getattr(self._remote_auth, "configured", None)
        if not callable(configured) or not configured():
            self._logger.info("remote session keepalive skipped: remote auth not configured")
            return None
        self._thread = threading.Thread(
            target=self._loop,
            name="mainpg-session-keepalive",
            daemon=True,
        )
        self._thread.start()
        self._logger.info("remote session keepalive started (interval=%ss)", self._interval_s)
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # wait() 既能休眠又可被 stop() 立刻唤醒，避免退出时多等一个间隔。
        while not self._stop.wait(self._interval_s):
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - 循环里再兜一层
                self._logger.debug("remote session keepalive loop error: %s", exc)


def start_remote_session_keepalive(
    remote_auth: Any,
    sessions: Any,
    *,
    logger: logging.Logger | None = None,
) -> RemoteSessionKeepalive | None:
    """进程启动时调用；返回句柄（None 表示未启用），退出时应调用 stop()。"""
    return RemoteSessionKeepalive(remote_auth, sessions, logger=logger).start()
