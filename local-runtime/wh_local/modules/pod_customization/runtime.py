"""Instance-owned execution resources for POD provider calls."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import requests


class RuntimeClosedError(RuntimeError):
    pass


class AiRuntimeCapacityError(TimeoutError):
    pass


@dataclass(frozen=True, slots=True)
class AiRuntimeConfig:
    name: str
    executor_workers: int = 4
    pool_connections: int = 4
    pool_maxsize: int = 8
    provider_concurrency: int = 4
    requests_per_minute: float = 0.0
    user_agent: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("AI runtime name is required")
        for field_name in ("executor_workers", "pool_connections", "pool_maxsize", "provider_concurrency"):
            if int(getattr(self, field_name)) < 1:
                raise ValueError(f"{field_name} must be at least 1")
        if self.requests_per_minute < 0:
            raise ValueError("requests_per_minute cannot be negative")


class _TokenBucket:
    def __init__(self, rate_per_minute: float, *, clock: Callable[[], float], sleeper: Callable[[float], None]) -> None:
        self._rate = max(0.0, float(rate_per_minute)) / 60.0
        self._capacity = max(1.0, self._rate)
        self._tokens = self._capacity
        self._clock = clock
        self._sleeper = sleeper
        self._updated = clock()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if not self._rate:
            return
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self._capacity, self._tokens + max(0.0, now - self._updated) * self._rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                delay = (1.0 - self._tokens) / self._rate
            self._sleeper(min(delay, 0.5))


class AiRuntime:
    """No mutable transport or scheduling state is shared with another module."""

    def __init__(self, config: AiRuntimeConfig, *, session: Any | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep) -> None:
        self.config = config
        self._session = session or self._build_session(config)
        self._executor = ThreadPoolExecutor(max_workers=config.executor_workers, thread_name_prefix=f"{config.name}-ai")
        self._limiter = _TokenBucket(config.requests_per_minute, clock=clock, sleeper=sleeper)
        self._connections = threading.BoundedSemaphore(config.pool_maxsize)
        self._providers = threading.BoundedSemaphore(config.provider_concurrency)
        self._lock = threading.Lock()
        self._closed = False

    @staticmethod
    def _build_session(config: AiRuntimeConfig) -> requests.Session:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=config.pool_connections,
                                                pool_maxsize=config.pool_maxsize, max_retries=0, pool_block=True)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.trust_env = False
        if config.user_agent:
            session.headers.update({"User-Agent": config.user_agent})
        return session

    @property
    def session(self) -> Any:
        return self._session

    @property
    def executor(self) -> ThreadPoolExecutor:
        return self._executor

    def _ensure_open(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeClosedError(f"AI runtime {self.config.name!r} is closed")

    def submit(self, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        self._ensure_open()
        return self._executor.submit(function, *args, **kwargs)

    def acquire_request_token(self) -> None:
        self._ensure_open()
        self._limiter.acquire()

    @contextmanager
    def connection_slot(self, timeout_seconds: float) -> Iterator[None]:
        self._ensure_open()
        if not self._connections.acquire(timeout=max(0.0, timeout_seconds)):
            raise AiRuntimeCapacityError("AI provider connection pool timed out")
        try:
            yield
        finally:
            self._connections.release()

    @contextmanager
    def provider_slot(self, timeout_seconds: float | None = None) -> Iterator[None]:
        self._ensure_open()
        acquired = self._providers.acquire() if timeout_seconds is None else self._providers.acquire(timeout=max(0.0, timeout_seconds))
        if not acquired:
            raise AiRuntimeCapacityError("AI provider concurrency slot timed out")
        try:
            yield
        finally:
            self._providers.release()

    def close(self, *, wait: bool = True, cancel_futures: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)
        close = getattr(self._session, "close", None)
        if callable(close):
            close()
