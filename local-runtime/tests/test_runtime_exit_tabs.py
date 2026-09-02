from __future__ import annotations

from wh_local.app.main import _RuntimeExitController


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
