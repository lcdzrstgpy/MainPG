from __future__ import annotations

import os

from wh_local.app.main import _RuntimeExitController, _compose_exit_cleanup


def _controller(monkeypatch) -> _RuntimeExitController:
    monkeypatch.setenv("WH_LOCAL_RUNTIME_EXIT_ON_CLOSE", "1")
    monkeypatch.setenv("WH_LOCAL_RUNTIME_EXIT_GRACE_S", "60")
    monkeypatch.setenv("WH_LOCAL_RUNTIME_IDLE_TIMEOUT_S", "120")
    return _RuntimeExitController()


def test_closing_one_of_two_tabs_does_not_schedule_exit(monkeypatch) -> None:
    controller = _controller(monkeypatch)
    controller.touch("tab-a")
    controller.touch("tab-b")

    controller.bye("tab-a")

    assert set(controller._clients) == {"tab-b"}
    assert controller._bye_deadline is None


def test_closing_last_tab_schedules_exit(monkeypatch) -> None:
    controller = _controller(monkeypatch)
    controller.touch("tab-a")
    controller.touch("tab-b")
    controller.bye("tab-a")

    controller.bye("tab-b")

    assert controller._clients == {}
    assert controller._bye_deadline is not None


def test_default_last_tab_exit_grace_is_sixty_seconds(monkeypatch) -> None:
    monkeypatch.setenv("WH_LOCAL_RUNTIME_EXIT_ON_CLOSE", "1")
    monkeypatch.delenv("WH_LOCAL_RUNTIME_EXIT_GRACE_S", raising=False)

    controller = _RuntimeExitController()

    assert controller._grace_s == 60


def test_is_exiting_reports_pending_exit_and_clears_on_heartbeat(monkeypatch) -> None:
    """任务级暂停要让位给「退出前取消 + 结算」，所以控制器必须能报出「正在退出」。

    产品处理的自动暂停会读这个信号（service.set_shutdown_probe）：为真时让位，避免
    「关掉工作台」这一个动作有时被记成暂停（可继续）、有时被记成取消（按 50% 收费）。
    """
    controller = _controller(monkeypatch)
    controller.touch("tab-a")

    assert controller.is_exiting() is False

    controller.bye("tab-a")

    assert controller.is_exiting() is True

    # 刷新回来的心跳会取消这次退出（新页面重新心跳，后端不该退）
    controller.touch("tab-a")

    assert controller.is_exiting() is False


def test_reload_heartbeat_cancels_last_tab_exit(monkeypatch) -> None:
    controller = _controller(monkeypatch)
    controller.touch("old-page")
    controller.bye("old-page")
    assert controller._bye_deadline is not None

    controller.touch("reloaded-page")

    assert set(controller._clients) == {"reloaded-page"}
    assert controller._bye_deadline is None


def test_stale_tab_is_pruned_without_removing_live_tab(monkeypatch) -> None:
    controller = _controller(monkeypatch)
    controller._clients = {"stale-tab": (10.0, 1), "live-tab": (125.0, 1)}

    controller._prune_stale_clients(131.0)

    assert set(controller._clients) == {"live-tab"}
    assert controller._closed_clients["stale-tab"][1] == 1


def test_server_mode_does_not_track_clients(monkeypatch) -> None:
    monkeypatch.delenv("WH_LOCAL_RUNTIME_EXIT_ON_CLOSE", raising=False)
    controller = _RuntimeExitController()

    controller.touch("public-request")

    assert controller._clients == {}


def test_heartbeat_queued_before_bye_cannot_resurrect_closed_tab(monkeypatch) -> None:
    controller = _controller(monkeypatch)
    controller.touch("tab-a", 1)
    controller.touch("tab-b", 1)

    controller.bye("tab-a", 3)
    controller.touch("tab-a", 2)
    controller.bye("tab-b", 2)

    assert controller._clients == {}
    assert controller._bye_deadline is not None


def test_newer_heartbeat_can_reopen_same_tab_after_page_restore(monkeypatch) -> None:
    controller = _controller(monkeypatch)
    controller.touch("tab-a", 1)
    controller.bye("tab-a", 2)

    controller.touch("tab-a", 3)

    assert set(controller._clients) == {"tab-a"}
    assert controller._bye_deadline is None


def test_old_heartbeat_cannot_restore_timed_out_tab(monkeypatch) -> None:
    controller = _controller(monkeypatch)
    controller._clients = {"tab-a": (10.0, 4)}
    controller._prune_stale_clients(131.0)

    controller.touch("tab-a", 3)

    assert controller._clients == {}


def test_hard_exit_runs_every_cleanup_step_before_exiting(monkeypatch) -> None:
    controller = _controller(monkeypatch)
    calls: list[str] = []
    exits: list[int] = []
    controller.set_on_before_exit(
        _compose_exit_cleanup(
            lambda: calls.append("product-processing"),
            lambda: calls.append("clipforge-sidecar"),
        )
    )
    monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))

    controller._exit("test")

    # 硬退出不得绕过任何一个清理步骤：任务结算与 sidecar 回收都必须先跑完。
    assert calls == ["product-processing", "clipforge-sidecar"]
    assert exits == [0]


def test_cleanup_step_failure_does_not_skip_the_sidecar_cleanup(monkeypatch) -> None:
    controller = _controller(monkeypatch)
    calls: list[str] = []
    exits: list[int] = []

    def explode() -> None:
        raise RuntimeError("product processing cleanup failed")

    controller.set_on_before_exit(_compose_exit_cleanup(explode, lambda: calls.append("clipforge-sidecar")))
    monkeypatch.setattr(os, "_exit", lambda code: exits.append(code))

    controller._exit("test")

    assert calls == ["clipforge-sidecar"]
    assert exits == [0]


class _StopWatchdog(Exception):
    """只跑一轮看门狗循环用的哨兵。"""


def _run_one_watchdog_round(
    controller: _RuntimeExitController, monkeypatch, *, stale_request: bool = True
) -> list[str]:
    """跑一轮 _run_watchdog 的判定，返回被调用的退出原因。"""
    import time

    import wh_local.app.main as main_module

    exits: list[str] = []
    monkeypatch.setattr(controller, "_exit", exits.append)
    with controller._lock:
        controller._ever_connected = True   # 走「心跳断了」分支，而非「最后一个页面关闭」
        controller._clients.clear()
        controller._bye_deadline = None
        # 默认把「最近请求」推到很久以前：否则它会和心跳一起挡住退出。
        if stale_request:
            controller._last_request_at = time.monotonic() - 10_000

    calls = {"n": 0}

    def _twice(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] > 1:
            raise _StopWatchdog

    monkeypatch.setattr(main_module.time, "sleep", _twice)
    try:
        controller._run_watchdog()
    except _StopWatchdog:
        pass
    return exits


def test_watchdog_stays_alive_while_work_is_in_flight(monkeypatch) -> None:
    """心跳中断 ≠ 用户走了：有任务在跑时看门狗不得自杀。

    系统休眠 / 浏览器冻结标签页都会让 sendBeacon 停发，而任务还在后端跑着。原来
    这里直接退出，把任务取消并按冻结积分 50% 结算，用户回来只看到「本地服务掉线
    了」+ 任务没了 + 一笔扣费（2026-10-22 定位）。
    """
    controller = _controller(monkeypatch)
    controller.set_busy_checker(lambda: True)
    assert _run_one_watchdog_round(controller, monkeypatch) == [], "有任务在跑时不得退出"


def test_watchdog_exits_when_idle_and_no_work(monkeypatch) -> None:
    """没任务、也没心跳 → 照旧退出，保持「关页即停」原语义不变。"""
    controller = _controller(monkeypatch)
    controller.set_busy_checker(lambda: False)
    assert _run_one_watchdog_round(controller, monkeypatch) == ["frontend heartbeat idle timeout"]


def test_watchdog_exits_when_no_busy_checker_wired(monkeypatch) -> None:
    """没接探测（服务器环境）→ 按「没活儿」处理，不能因为漏接就永不退出。"""
    controller = _controller(monkeypatch)
    assert _run_one_watchdog_round(controller, monkeypatch) == ["frontend heartbeat idle timeout"]


def test_watchdog_stays_alive_when_a_recent_request_arrived(monkeypatch) -> None:
    """最近还有 HTTP 请求 → 不退出。

    POD / 组合套装 / AI 视频这些模块没有自己的任务计数接口，靠这条兜住：它们的
    页面本来就在轮询，有请求就说明还有人在用。
    """
    controller = _controller(monkeypatch)
    controller.set_busy_checker(lambda: False)
    assert _run_one_watchdog_round(controller, monkeypatch, stale_request=False) == []


def test_watchdog_ignores_sleep_gap(monkeypatch) -> None:
    """系统休眠/睡眠的时间不计入「没人用」。

    用户合盖半小时回来，后端不该已经把自己判成无人并退出（用户角度就是「一起来
    就掉线」）。墙钟跳变量会把这段时间从所有 monotonic 锚点上扣掉。
    """
    import time

    controller = _controller(monkeypatch)
    controller.set_busy_checker(lambda: False)
    with controller._lock:
        # 最后一次活动发生在「刚睡着」那一刻：合盖前用户还在用。
        controller._last_request_at = time.monotonic() - 3600
        controller._last_tick_wall = time.time() - 3600   # 刚从 1 小时休眠中醒来
    assert (
        _run_one_watchdog_round(controller, monkeypatch, stale_request=False) == []
    ), "睡眠时间不得计入 idle"
