"""远端会话保活的单元测试（不触网）。"""
from __future__ import annotations

import time

from wh_local.customer import session_keepalive as sk
from wh_local.customer.session_keepalive import (
    RemoteSessionKeepalive,
    keepalive_interval_seconds,
    start_remote_session_keepalive,
)


class _FakeRemoteAuth:
    def __init__(self, *, configured: bool = True, fail: bool = False) -> None:
        self._configured = configured
        self._fail = fail
        self.touched: list[str] = []

    def configured(self) -> bool:
        return self._configured

    def touch_session(self, remote_token: str) -> dict:
        if self._fail:
            raise RuntimeError("remote unavailable")
        self.touched.append(remote_token)
        return {"ok": True}


class _FakeStore:
    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens

    def active_remote_tokens(self) -> list[str]:
        return list(self._tokens)


class _FakeSessions:
    """模拟 LocalSessionService：令牌挂在 .store 上。"""

    def __init__(self, tokens: list[str]) -> None:
        self.store = _FakeStore(tokens)


# ---------------- 间隔配置 ----------------


def test_default_interval_is_above_remote_cache_ttl(monkeypatch):
    monkeypatch.delenv(sk._ENV_INTERVAL, raising=False)
    value = keepalive_interval_seconds()
    # 远端会话缓存 TTL 是 120s，缓存命中不写 last_used_at —— 间隔必须大于它
    assert value > 120
    assert value == sk._DEFAULT_INTERVAL_S


def test_interval_env_override_and_disable(monkeypatch):
    monkeypatch.setenv(sk._ENV_INTERVAL, "240")
    assert keepalive_interval_seconds() == 240
    monkeypatch.setenv(sk._ENV_INTERVAL, "0")
    assert keepalive_interval_seconds() == 0.0
    monkeypatch.setenv(sk._ENV_INTERVAL, "-5")
    assert keepalive_interval_seconds() == 0.0


def test_interval_floor_and_invalid_value(monkeypatch):
    # 小于远端缓存 TTL 的间隔无意义，被抬到下限
    monkeypatch.setenv(sk._ENV_INTERVAL, "5")
    assert keepalive_interval_seconds() == sk._MIN_INTERVAL_S
    # 非法值回落到默认，而不是抛异常
    monkeypatch.setenv(sk._ENV_INTERVAL, "abc")
    assert keepalive_interval_seconds() == sk._DEFAULT_INTERVAL_S


# ---------------- 单轮刷新 ----------------


def test_run_once_touches_every_active_token():
    auth = _FakeRemoteAuth()
    keepalive = RemoteSessionKeepalive(auth, _FakeSessions(["tok-a", "tok-b"]), interval_s=1)
    assert keepalive.run_once() == 2
    assert auth.touched == ["tok-a", "tok-b"]
    assert keepalive.touched_total == 2


def test_run_once_accepts_store_directly_and_skips_empty():
    auth = _FakeRemoteAuth()
    keepalive = RemoteSessionKeepalive(auth, _FakeStore([]), interval_s=1)
    assert keepalive.run_once() == 0
    assert auth.touched == []


def test_run_once_swallows_touch_failure():
    auth = _FakeRemoteAuth(fail=True)
    keepalive = RemoteSessionKeepalive(auth, _FakeSessions(["tok-a"]), interval_s=1)
    # 保活失败必须静默：不能把异常抛给业务/启动链路
    assert keepalive.run_once() == 0
    assert keepalive.touched_total == 0


def test_active_tokens_tolerates_broken_lister():
    class _Broken:
        def active_remote_tokens(self):
            raise RuntimeError("boom")

    keepalive = RemoteSessionKeepalive(_FakeRemoteAuth(), _Broken(), interval_s=1)
    assert keepalive.active_tokens() == []
    # 连 active_remote_tokens 都没有时同样安全
    keepalive2 = RemoteSessionKeepalive(_FakeRemoteAuth(), object(), interval_s=1)
    assert keepalive2.active_tokens() == []


# ---------------- 生命周期 ----------------


def test_start_skipped_when_remote_not_configured():
    keepalive = RemoteSessionKeepalive(_FakeRemoteAuth(configured=False), _FakeSessions([]), interval_s=1)
    assert keepalive.start() is None


def test_start_skipped_when_interval_disabled():
    keepalive = RemoteSessionKeepalive(_FakeRemoteAuth(), _FakeSessions(["tok"]), interval_s=0)
    keepalive._interval_s = 0.0
    assert keepalive.start() is None


def test_thread_refreshes_repeatedly_then_stops():
    auth = _FakeRemoteAuth()
    sessions = _FakeSessions(["tok-a"])
    keepalive = RemoteSessionKeepalive(auth, sessions, interval_s=0.05)
    assert keepalive.start() is not None
    try:
        deadline = time.monotonic() + 3.0
        while len(auth.touched) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        keepalive.stop()
    assert len(auth.touched) >= 2, "保活线程应当反复刷新"
    assert set(auth.touched) == {"tok-a"}


def test_convenience_entrypoint_returns_handle():
    auth = _FakeRemoteAuth()
    handle = start_remote_session_keepalive(auth, _FakeSessions(["tok-a"]))
    try:
        assert handle is not None
        assert handle.interval_seconds == sk._DEFAULT_INTERVAL_S
    finally:
        if handle is not None:
            handle.stop()
