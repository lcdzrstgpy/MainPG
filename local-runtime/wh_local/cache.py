# -*- coding: utf-8 -*-
"""Redis 缓存层：为积分/会话/定价等高频读路径提供可降级的缓存。

设计原则：
- SQLite 始终是唯一持久化事实源，Redis 只是可丢失的缓存层。
- 写操作（冻结/结算/充值/调账）直写 SQLite，成功后主动失效相关缓存。
- 余额校验等强一致读绝不走缓存；缓存只服务展示型读。
- Redis 不可用时自动降级直查 SQLite（读 fail-open），不影响业务。

通过环境变量启用：
  WH_REDIS_URL=redis://127.0.0.1:6379/0   （留空则完全禁用缓存）
  WH_REDIS_KEY_PREFIX=wh                  （可选，多环境隔离前缀）
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Callable

_REDIS_URL = os.environ.get("WH_REDIS_URL", "").strip()
_PREFIX = os.environ.get("WH_REDIS_KEY_PREFIX", "wh").strip() or "wh"

_redis: Any = None
_available = False
_initialized = False
_lock = threading.Lock()


def _client() -> Any:
    """惰性初始化 Redis 客户端；任何失败都置为不可用（读降级直查库）。"""
    global _redis, _available, _initialized
    if _initialized:
        return _redis if _available else None
    with _lock:
        if _initialized:
            return _redis if _available else None
        _initialized = True
        if not _REDIS_URL:
            _available = False
            return None
        try:
            import redis  # noqa: PLC0415

            pool = redis.ConnectionPool.from_url(
                _REDIS_URL,
                decode_responses=True,
                max_connections=64,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
                socket_keepalive=True,
            )
            probe = redis.Redis(connection_pool=pool)
            probe.ping()
            _redis = probe
            _available = True
        except Exception:
            _redis = None
            _available = False
    return _redis if _available else None


def redis_available() -> bool:
    return _client() is not None


def _key(name: str) -> str:
    return f"{_PREFIX}:{name}"


def cache_get(name: str) -> Any:
    """读缓存；不可用/异常一律返回 None（走回源）。"""
    client = _client()
    if client is None:
        return None
    try:
        raw = client.get(_key(name))
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def cache_set(name: str, value: Any, ttl: int = 60) -> bool:
    client = _client()
    if client is None:
        return False
    try:
        client.set(_key(name), json.dumps(value, ensure_ascii=False), ex=ttl)
        return True
    except Exception:
        return False


def cache_delete(*names: str) -> None:
    client = _client()
    if client is None:
        return
    try:
        client.delete(*[_key(name) for name in names])
    except Exception:
        pass


def get_or_set(name: str, ttl: int, loader: Callable[[], Any]) -> Any:
    """读缓存；miss 时执行 loader 回填，并用 SETNX 单飞锁防击穿。

    Redis 不可用或锁竞争超时都直接回源，保证功能不受缓存影响。
    """
    cached = cache_get(name)
    if cached is not None:
        return cached
    client = _client()
    if client is None:
        return loader()
    lock_name = f"{_key(name)}:lock"
    try:
        acquired = client.set(lock_name, "1", nx=True, ex=5)
        if acquired:
            try:
                value = loader()
                cache_set(name, value, ttl)
                return value
            finally:
                client.delete(lock_name)
        # 未抢到锁：短等后重读，仍无则直接回源（不阻塞业务）
        import time as _time  # noqa: PLC0415

        for _ in range(3):
            _time.sleep(0.05)
            cached = cache_get(name)
            if cached is not None:
                return cached
        return loader()
    except Exception:
        return loader()


def invalidate_wallet(account_id: str) -> None:
    cache_delete(f"wallet:{account_id}")


def invalidate_pricing() -> None:
    cache_delete("pricing:active")


def invalidate_session(token_hash: str) -> None:
    cache_delete(f"sess:{token_hash}")


def invalidate_admin_summary() -> None:
    cache_delete("admin:billing:summary")
