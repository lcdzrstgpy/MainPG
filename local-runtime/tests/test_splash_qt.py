"""启动浮动卡片（``launcher/splash_qt.py``）与端口决策的护栏测试。

Qt 版把窗口生命周期、圆角裁剪、动画都交给组件了，这里只钉住几件容易退化的事：

* 平台/素材探测（没有素材就该回落，而不是崩）；
* 卡片能创建、更新状态、关闭（跨线程接口不能抛异常）；
* ``run_workbench`` 侧的两条关键判断：复用实例时读版本、端口冲突只清自己的残留。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from launcher import splash_qt

needs_windows = pytest.mark.skipif(os.name != "nt", reason="启动卡片仅 Windows")
needs_qt = pytest.mark.skipif(not splash_qt.splash_available(), reason="未安装 PySide6")


# -------------------------------------------------------------- 探测


def test_splash_asset_exists_in_repo() -> None:
    path = splash_qt.splash_asset_path()
    assert path is not None, "仓库里必须有启动卡片底图"
    assert path.is_file() and path.stat().st_size > 10_000


def test_splash_available_reports_platform_and_qt() -> None:
    available = splash_qt.splash_available()
    if os.name == "nt":
        try:
            import PySide6  # noqa: F401

            assert available is True
        except ImportError:
            assert available is False
    else:
        assert available is False


# ---------------------------------------------------------------- 窗口


@needs_windows
@needs_qt
def test_card_shows_updates_and_closes() -> None:
    asset = splash_qt.splash_asset_path()
    assert asset is not None
    card = splash_qt.SplashCard(background=asset, version="1.5.7", host="127.0.0.1", port=8010)
    try:
        assert card.start() is True
        card.set_port(8012)
        card.set_status("正在启动", "8010 被占用，已改用 8012")
        time.sleep(0.4)
    finally:
        card.close()
        time.sleep(0.6)  # 等淡出结束再结束用例


@needs_windows
@needs_qt
def test_card_finish_triggers_the_ready_callback() -> None:
    asset = splash_qt.splash_asset_path()
    assert asset is not None
    opened: list[str] = []
    card = splash_qt.SplashCard(background=asset, version="1.5.7", host="127.0.0.1", port=8010)
    try:
        assert card.start()
        # 探活立刻返回 True → 应当淡出并回调
        card.watch(probe=lambda: True, on_ready=lambda: opened.append("workbench"))
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not opened:
            time.sleep(0.05)
        assert opened == ["workbench"], "就绪后必须回调一次"
    finally:
        card.close()
        time.sleep(0.4)


@needs_windows
@needs_qt
def test_card_watch_can_be_attached_after_start() -> None:
    """卡片必须比 import 后端更早出现，所以探活是「事后补挂」的，不能漏。"""
    asset = splash_qt.splash_asset_path()
    assert asset is not None
    card = splash_qt.SplashCard(background=asset, version="1.5.7", host="127.0.0.1", port=8010)
    try:
        assert card.start()  # 不传 probe
        card.watch(probe=lambda: False, on_ready=lambda: None)
        card.set_port(8013)
    finally:
        card.close()
        time.sleep(0.3)


# ------------------------------------------------- 启动器的两个关键判断


def test_probe_running_workbench_reports_the_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/health`` 现在带 app_version —— 复用旧进程时必须能读到。"""
    import http.client

    import run_workbench

    class FakeResponse:
        status = 200

        def read(self) -> bytes:
            return b'{"ok": true, "database_path": "D:/x.sqlite3", "app_version": "1.5.7"}'

    class FakeConn:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def request(self, *args: object, **kwargs: object) -> None:
            pass

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPConnection", FakeConn)
    assert run_workbench._probe_running_workbench("127.0.0.1", 8010) == ("D:/x.sqlite3", "1.5.7")


def test_port_conflict_never_kills_another_program(monkeypatch: pytest.MonkeyPatch) -> None:
    """别的程序占着 8010 且用户选「换端口」→ 绝不杀它。"""
    import run_workbench

    killed: list[int] = []
    monkeypatch.setattr(run_workbench, "_port_owners", lambda port: [4242])
    monkeypatch.setattr(run_workbench, "_process_name", lambda pid: "chrome.exe")
    monkeypatch.setattr(run_workbench, "_kill_pid", lambda pid: killed.append(pid))

    asked: list[str] = []
    notes: list[str] = []

    class FakeCard:
        def ask_port_conflict(self, *, port, process_name, pid, timeout=20.0) -> str:
            asked.append(process_name)
            return "fallback"

        def set_status(self, text: str, note: str = "") -> None:
            notes.append(note or text)

    assert run_workbench._resolve_port_conflict("127.0.0.1", 8010, FakeCard()) is False
    assert killed == [], "不能杀别的程序"
    assert asked == ["chrome.exe"], "必须先在卡片上问用户"
    assert notes and "chrome.exe" in notes[0], "卡片上要写清是谁占的"


def test_port_conflict_kills_only_when_user_confirms(monkeypatch: pytest.MonkeyPatch) -> None:
    """用户在卡片上点了「关掉它」→ 才允许结束占用进程。"""
    import run_workbench

    killed: list[int] = []
    monkeypatch.setattr(run_workbench, "_port_owners", lambda port: [4242])
    monkeypatch.setattr(run_workbench, "_process_name", lambda pid: "chrome.exe")
    monkeypatch.setattr(run_workbench, "_kill_pid", lambda pid: killed.append(pid))
    monkeypatch.setattr(run_workbench, "_port_in_use", lambda host, port: False)

    class FakeCard:
        def ask_port_conflict(self, *, port, process_name, pid, timeout=20.0) -> str:
            return "kill"

        def set_status(self, text: str, note: str = "") -> None:
            pass

    assert run_workbench._resolve_port_conflict("127.0.0.1", 8010, FakeCard()) is True
    assert killed == [4242], "用户确认后才杀，且只杀那一个"


def test_port_conflict_clears_our_own_leftover(monkeypatch: pytest.MonkeyPatch) -> None:
    """本程序自己的残留进程 → 直接清掉，继续用 8010。"""
    import run_workbench

    killed: list[int] = []
    monkeypatch.setattr(run_workbench, "_port_owners", lambda port: [991])
    monkeypatch.setattr(run_workbench, "_process_name", lambda pid: "MainPG.exe")
    monkeypatch.setattr(run_workbench, "_kill_pid", lambda pid: killed.append(pid))
    monkeypatch.setattr(run_workbench, "_port_in_use", lambda host, port: False)

    assert run_workbench._resolve_port_conflict("127.0.0.1", 8010, None) is True
    assert killed == [991]
