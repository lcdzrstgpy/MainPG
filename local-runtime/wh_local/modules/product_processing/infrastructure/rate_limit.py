"""进程内共享的 AI 供应商请求速率限制器（token bucket，默认关闭）。

并发治理分两层：
1. 任务级串行闸门（service._task_execution_gate，WH_PRODUCT_MAX_CONCURRENT_TASKS，
   默认 1）：同一时间只执行一个任务，从根上阻止"多批次并发叠加"打爆供应商。
2. 本模块的全局请求速率限制（WH_PRODUCT_AI_RATE_PER_MINUTE，默认 0=关闭）：
   仅供需要额外保护时开启（如供应商仍按窗口计数限流）。单批次内的草稿并行
   不受其限制，避免批量处理被拖慢。
"""

from __future__ import annotations

import os
import threading
import time


def rate_per_minute() -> float:
    raw = os.environ.get("WH_PRODUCT_AI_RATE_PER_MINUTE", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.0  # 默认关闭：单批次内不限速，多批次并发已由任务级闸门阻止
    return max(0.0, value)


class _TokenBucket:
    def __init__(self, rate_per_minute: float):
        self._rate = rate_per_minute / 60.0
        # 桶容量至少允许一个突发，否则单请求也会因首令牌不足而等待。
        self._capacity = max(1.0, self._rate)
        self._tokens = self._capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """阻塞直到拿到一个令牌；rate<=0 时直接放行。"""
        if self._rate <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            # 锁外睡眠，避免长等待时阻塞其它线程取令牌。
            time.sleep(min(wait, 0.5))


_limiter: _TokenBucket | None = None
_limiter_lock = threading.Lock()


def global_ai_request_limiter() -> _TokenBucket:
    global _limiter
    with _limiter_lock:
        if _limiter is None:
            _limiter = _TokenBucket(rate_per_minute())
        return _limiter


def reset_limiter() -> None:
    """测试辅助：清空单例，使下一次调用按当前环境变量重建。"""
    global _limiter
    with _limiter_lock:
        _limiter = None
