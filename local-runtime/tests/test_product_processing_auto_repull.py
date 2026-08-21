"""产品处理失败项自动补跑逻辑测试。

验证 _maybe_launch_auto_repull 的判定与状态流转：
仅对 retryable 的技术失败项自动补跑、有轮次上限、不重复触发。
"""

from __future__ import annotations

import threading
import time

import pytest

from wh_local.modules.product_processing import service as service_module


class FakeRepository:
    def __init__(self, task: dict):
        self.task = task
        self.settings_updates: list[dict] = []
        self.reset_calls: list[list[int]] = []

    def get_task(self, task_id: int, workspace_id: str) -> dict:
        return self.task

    def merge_task_settings(self, task_id: int, workspace_id: str, **updates: object) -> dict:
        self.settings_updates.append(updates)
        self.task.setdefault("settings", {}).update(updates)
        return self.task

    def reset_failed_items(self, task_id: int, workspace_id: str, *, draft_ids: list[int] | None = None) -> bool:
        self.reset_calls.append(draft_ids or [])
        for item in self.task["items"]:
            if item["status"] in {"failed", "attention_required"}:
                if draft_ids is None or item.get("product_draft_id") in draft_ids:
                    item["status"] = "pending"
        return True


def _make_service(repository: FakeRepository) -> service_module.ProductProcessingService:
    service = object.__new__(service_module.ProductProcessingService)
    service.repository = repository
    service._task_worker_lock = threading.Lock()
    service._task_workers = {}
    service._task_remote_tokens = {}
    service._submission_lock = threading.RLock()
    service._task_execution_gate = threading.BoundedSemaphore(1)
    return service


def _task_with_failures() -> dict:
    return {
        "id": 1,
        "settings": {},
        "items": [
            {"id": 11, "item_id": 11, "product_draft_id": 100, "status": "failed",
             "result": {"retryable": True, "failure_class": "technical_retryable"}},
            {"id": 12, "item_id": 12, "product_draft_id": 101, "status": "failed",
             "result": {"retryable": False, "failure_class": "configuration_blocked"}},
            {"id": 13, "item_id": 13, "product_draft_id": 102, "status": "completed", "result": {}},
        ],
    }


def _retryable_failures() -> list[dict]:
    return [{
        "id": 11, "item_id": 11, "product_draft_id": 100, "status": "failed",
        "result": {"retryable": True, "failure_class": "technical_retryable"},
    }]


def test_auto_repull_only_targets_retryable_drafts(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = FakeRepository(_task_with_failures())
    service = _make_service(repository)
    launched: list[tuple[int, list[int]]] = []
    monkeypatch.setattr(
        service,
        "_launch_auto_repull",
        lambda task_id, workspace_id, draft_ids, **kwargs: launched.append((task_id, draft_ids)),
    )

    service._maybe_launch_auto_repull(1, "local", _retryable_failures())

    assert launched == [(1, [100])]  # 仅技术可重试的草稿 100，配置阻断的 101 不自动补跑
    state = repository.settings_updates[-1]["_auto_repull"]
    assert state["status"] == "running"
    assert state["total"] == 1
    assert state["round"] == 1


def test_auto_repull_skipped_without_retryable_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = FakeRepository(_task_with_failures())
    service = _make_service(repository)
    launched: list = []
    monkeypatch.setattr(service, "_launch_auto_repull", lambda *args: launched.append(args))

    service._maybe_launch_auto_repull(1, "local", [])

    assert launched == []
    assert repository.settings_updates == []


def test_auto_repull_marks_repull_round_completed(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task_with_failures()
    task["settings"] = {"_auto_repull": {"status": "running", "round": 1, "total": 1}}
    # 补跑后：草稿 100 已成功，101 仍是配置阻断
    task["items"][0]["status"] = "completed"
    repository = FakeRepository(task)
    service = _make_service(repository)
    launched: list = []
    monkeypatch.setattr(service, "_launch_auto_repull", lambda *args: launched.append(args))

    service._maybe_launch_auto_repull(1, "local", _retryable_failures())

    assert launched == []  # 补跑轮结束不再触发下一轮
    state = repository.settings_updates[-1]["_auto_repull"]
    assert state["status"] == "completed"
    assert "自动补跑完成" in state["message"]


def test_auto_repull_respects_max_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task_with_failures()
    task["settings"] = {"_auto_repull": {"status": "completed", "round": 1, "total": 1}}
    repository = FakeRepository(task)
    service = _make_service(repository)
    launched: list = []
    monkeypatch.setattr(service, "_launch_auto_repull", lambda *args: launched.append(args))

    service._maybe_launch_auto_repull(1, "local", _retryable_failures())

    assert launched == []
    assert repository.settings_updates == []


def test_auto_repull_disabled_when_rounds_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WH_PP_AUTO_REPULL_ROUNDS", "0")
    repository = FakeRepository(_task_with_failures())
    service = _make_service(repository)
    launched: list = []
    monkeypatch.setattr(service, "_launch_auto_repull", lambda *args: launched.append(args))

    service._maybe_launch_auto_repull(1, "local", _retryable_failures())

    assert launched == []
    assert repository.settings_updates == []


def test_auto_repull_disabled_by_task_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """任务提交时选择「不自动修复失败项」（auto_repull=false）则跳过自动补跑。"""
    task = _task_with_failures()
    task["settings"] = {"auto_repull": False}
    repository = FakeRepository(task)
    service = _make_service(repository)
    launched: list = []
    monkeypatch.setattr(service, "_launch_auto_repull", lambda *args: launched.append(args))

    service._maybe_launch_auto_repull(1, "local", _retryable_failures())

    assert launched == []
    assert repository.settings_updates == []


def test_auto_repull_skipped_for_billed_task_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    task = _task_with_failures()
    task["settings"] = {"_billing": {"account_id": "acct-1"}}
    repository = FakeRepository(task)
    service = _make_service(repository)
    launched: list = []
    monkeypatch.setattr(service, "_launch_auto_repull", lambda *args: launched.append(args))

    service._maybe_launch_auto_repull(1, "local", _retryable_failures())

    assert launched == []
    assert repository.settings_updates == []


def test_auto_repull_launches_for_billed_task_with_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """计费任务仍有远程 token 时必须自动补跑，并把 token 传给补跑线程。

    回归：任务收尾的计费清理会把 token 从内存移除，启动判定必须在清理前
    捕获 token（修复前这里会误判为无 token 而跳过，导致用户仍需手动重试）。
    """
    task = _task_with_failures()
    task["settings"] = {"_billing": {"account_id": "acct-1"}}
    repository = FakeRepository(task)
    service = _make_service(repository)
    service._task_remote_tokens = {1: "tok-1"}
    launched: list[tuple[int, list[int], str]] = []
    monkeypatch.setattr(
        service,
        "_launch_auto_repull",
        lambda task_id, workspace_id, draft_ids, remote_token="": launched.append(
            (task_id, draft_ids, remote_token)
        ),
    )

    service._maybe_launch_auto_repull(1, "local", _retryable_failures())

    assert launched == [(1, [100], "tok-1")]
    state = repository.settings_updates[-1]["_auto_repull"]
    assert state["status"] == "running"


def test_auto_repull_waits_for_original_worker_to_leave(monkeypatch: pytest.MonkeyPatch) -> None:
    """补跑线程必须等待原执行线程注销后再重置并执行。

    回归：任务收尾（计费结算等）可能超过 1 秒，若补跑线程 1 秒后误判原线程
    仍存活而直接放弃，_auto_repull 会永远停在 running。
    """
    task = _task_with_failures()
    task["settings"] = {"_billing": {"account_id": "acct-1"}}
    repository = FakeRepository(task)
    service = _make_service(repository)
    service._task_remote_tokens = {1: "tok-1"}

    # 模拟原执行线程：仍占着 worker 注册表（任务收尾中）
    original_gate = threading.Event()

    def _holder() -> None:
        original_gate.wait(timeout=10)

    holder = threading.Thread(target=_holder, daemon=True)
    with service._task_worker_lock:
        service._task_workers[("local", 1)] = holder  # key = (workspace_id, task_id)
    holder.start()

    executed: list[int] = []
    monkeypatch.setattr(
        service,
        "_execute_task",
        lambda tid, ws: executed.append(int(tid)) or {},
    )

    service._launch_auto_repull(1, "local", [100], remote_token="tok-1")

    # 补跑线程进入等待，尚未重置/执行
    time.sleep(1.5)
    assert executed == []
    assert repository.reset_calls == []

    # 释放原执行线程，补跑线程应继续：重置失败项并重新执行
    original_gate.set()
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and not executed:
        time.sleep(0.1)
    assert executed == [1]
    assert repository.reset_calls == [[100]]
