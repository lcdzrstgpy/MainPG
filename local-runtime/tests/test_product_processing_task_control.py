from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

import pytest

from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import (
    ProductProcessingConflict,
    ProductProcessingService,
    _TaskControlStopped,
)
from wh_local.modules.product_processing import service as service_module


def _service(tmp_path: Path) -> ProductProcessingService:
    return ProductProcessingService(
        ProductProcessingRepository(create_database(f"sqlite:///{tmp_path / 'control.sqlite3'}")),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def _draft(service: ProductProcessingService, title: str) -> dict[str, Any]:
    draft, _created = service.create_draft(
        {
            "source_type": "manual",
            "title": title,
            "image_url": "https://example.test/product.jpg",
        }
    )
    return draft


def _task_with_two_items(service: ProductProcessingService) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    draft_a = _draft(service, "control-product-a")
    draft_b = _draft(service, "control-product-b")
    task = service.repository.create_task(
        title="control-task",
        preflight_only=False,
        settings={
            "processing_scope": ["title"],
            "title_optimize": True,
            "async_mode": True,
            "_billing": {
                "account_id": "account-1",
                "source_ref": "task:control",
                "pricing_version": "v1",
            },
        },
        drafts=[draft_a, draft_b],
        idempotency_key=None,
    )
    return task, task["items"][0], task["items"][1]


def test_raise_if_task_stopped_raises_when_paused_or_cancelled(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)

    # 正常运行/排队/终态：检查点不抛出
    service._raise_if_task_stopped(task["id"], "local")
    service.repository.set_task_status(task["id"], "running", "local")
    service._raise_if_task_stopped(task["id"], "local")
    service.repository.set_task_status(task["id"], "completed", "local")
    service._raise_if_task_stopped(task["id"], "local")

    # 暂停：检查点抛出（供 _process 捕获后跳过本条，等待断点续跑）
    service.repository.set_task_status(task["id"], "running", "local")
    service.pause_task(task["id"], "local")
    with pytest.raises(_TaskControlStopped) as paused_exc:
        service._raise_if_task_stopped(task["id"], "local")
    assert paused_exc.value.reason == "用户已暂停任务"

    # 取消：检查点同样抛出
    service.repository.set_task_status(task["id"], "running", "local")
    service.cancel_task(task["id"], "local")
    with pytest.raises(_TaskControlStopped) as cancelled_exc:
        service._raise_if_task_stopped(task["id"], "local")
    assert cancelled_exc.value.reason == "用户已取消任务"


def test_cancel_task_marks_pending_items_failed_and_task_cancelled(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task, item_a, item_b = _task_with_two_items(service)

    # 模拟 item_a 已完成、item_b 尚未开始
    service.repository.update_item_progress(
        task["id"],
        int(item_a["item_id"]),
        status="completed",
        result={"optimized_title": "done"},
        workspace_id="local",
    )
    service.repository.set_task_status(task["id"], "running", "local")

    response = service.cancel_task(task["id"], "local")
    assert response["task"]["status"] == "cancelled"

    final = service.repository.get_task(task["id"])
    assert final is not None and final["status"] == "cancelled"
    items = {int(item["item_id"]): item for item in final["items"]}
    # 已完成链接保持 completed；未处理链接标记为用户取消（终态、不可重试）
    assert items[int(item_a["item_id"])]["status"] == "completed"
    assert items[int(item_b["item_id"])]["status"] == "failed"
    assert items[int(item_b["item_id"])]["reason"] == "用户已取消任务"
    result = items[int(item_b["item_id"])]["result"] or {}
    assert result.get("failure_class") == "task_control"
    assert result.get("retryable") is False
    assert result.get("error_type") == "task_cancelled"


def test_cancel_task_is_idempotent_on_terminal_task(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.cancel_task(task["id"], "local")
    second = service.cancel_task(task["id"], "local")
    assert second["task"]["status"] == "cancelled"
    assert "无需取消" in second["message"]


def test_finalize_paused_successes_cancels_remainder_and_previews_only_success(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    task, item_a, item_b = _task_with_two_items(service)
    service.repository.update_item_progress(
        task["id"],
        int(item_a["item_id"]),
        status="completed",
        result={
            "product_draft_id": item_a["product_draft_id"],
            "optimized_title": "successful product",
        },
        workspace_id="local",
    )
    service.repository.set_task_status(task["id"], "paused", "local")

    response = service.finalize_paused_successes(task["id"], "local")
    preview = service.task_preview(task["id"], workspace_id="local")

    assert response["task"]["status"] == "cancelled"
    assert response["success_count"] == 1
    assert "永久取消" in response["message"]
    final_items = {int(item["item_id"]): item for item in response["items"]}
    assert final_items[int(item_b["item_id"])]["status"] == "failed"
    assert preview["item_count"] == 1
    assert preview["items"][0]["item_id"] == item_a["item_id"]
    assert preview["items"][0]["status"] == "completed"


def test_finalize_paused_successes_requires_exportable_success(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "paused", "local")

    with pytest.raises(ProductProcessingConflict, match="没有可预检并导出"):
        service.finalize_paused_successes(task["id"], "local")

    assert service.repository.get_task(task["id"])["status"] == "paused"


def test_cancelled_task_rejects_late_item_progress(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task, item_a, _b = _task_with_two_items(service)
    service.cancel_task(task["id"], "local")

    service.repository.update_item_progress(
        task["id"],
        int(item_a["item_id"]),
        status="completed",
        result={"optimized_title": "late AI result"},
        workspace_id="local",
    )

    final = service.repository.get_task(task["id"])
    assert final is not None and final["status"] == "cancelled"
    items = {int(item["item_id"]): item for item in final["items"]}
    assert items[int(item_a["item_id"])]["status"] == "failed"


def test_pause_does_not_mark_items_failed(tmp_path: Path) -> None:
    """暂停保留未处理项为 pending/running，供 resume 断点续跑（与取消不同）。"""
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "running", "local")

    service.pause_task(task["id"], "local")

    final = service.repository.get_task(task["id"])
    assert final is not None and final["status"] == "paused"
    assert all(
        item["status"] in {"pending", "running"} for item in final["items"]
    )


def test_pause_does_not_move_terminal_items_back_to_paused(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task, item_a, item_b = _task_with_two_items(service)
    for item in (item_a, item_b):
        service.repository.update_item_progress(
            task["id"],
            int(item["item_id"]),
            status="completed",
            result={"product_draft_id": item["product_draft_id"], "optimized_title": "done"},
            workspace_id="local",
        )
    service.repository.set_task_status(task["id"], "running", "local")

    response = service.pause_task(task["id"], "local")

    assert response["task"]["status"] == "running"
    assert "正在生成导出文件" in response["message"]
    assert service.repository.get_task(task["id"])["status"] == "running"


def test_pause_transition_cannot_regress_completed_task(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "completed", "local")

    changed = service.repository.pause_task_execution(task["id"], "local")

    assert changed is False
    assert service.repository.get_task(task["id"])["status"] == "completed"


def test_sweep_does_not_pause_running_task_without_page_leave(tmp_path: Path) -> None:
    """页面没发离开信标就不许暂停——哪怕已经很久没有轮询。

    背景（2026-10-15）：此前按「多久没心跳」猜用户走了（90/180 秒），页面被浏览器冻结、
    整机休眠时定时器根本不跑，正在跑的任务就这样被误停。现在只认真实事件（pagehide 信标）。
    """
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "running", "local")
    # 心跳停在很久以前（标签被冻结、电脑睡了一觉都可能长这样）
    service._task_last_seen[("local", task["id"])] = time.monotonic() - 10_000.0

    service._sweep_departed_tasks_once()

    assert service.repository.get_task(task["id"])["status"] == "running"


def test_sweep_pauses_task_after_page_leave_without_new_polling(tmp_path: Path) -> None:
    """页面关闭信标送达、宽限期内也没人再看 → 暂停（与手动暂停同语义，可从历史记录继续）。"""
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "running", "local")
    # 时间线：400 秒前最后轮询 → 300 秒前关掉页面 → 之后没人再看
    service._task_last_seen[("local", task["id"])] = time.monotonic() - 400.0
    service.mark_task_left(task["id"], "local")
    service._task_left_keys[("local", task["id"])] = time.monotonic() - 300.0

    service._sweep_departed_tasks_once()

    assert service.repository.get_task(task["id"])["status"] == "paused"
    assert ("local", task["id"]) not in service._task_left_keys
    assert ("local", task["id"]) not in service._task_last_seen


def test_page_leave_defers_while_polling_continues(tmp_path: Path) -> None:
    """离开信标发出后页面还在轮询（刷新/重开刚开始）→ 这一轮不暂停，标记先留着。"""
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "running", "local")
    service._task_last_seen[("local", task["id"])] = time.monotonic() - 5.0
    service.mark_task_left(task["id"], "local")

    service._sweep_departed_tasks_once()

    assert service.repository.get_task(task["id"])["status"] == "running"
    assert ("local", task["id"]) in service._task_left_keys


def test_page_leave_is_revoked_by_ongoing_polling(tmp_path: Path) -> None:
    """离开之后还有轮询＝刷新/重开/另一个页签还在看 → 撤销这次离开。"""
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "running", "local")
    now = time.monotonic()
    service._task_last_seen[("local", task["id"])] = now - 400.0
    with service._task_left_lock:
        service._task_left_keys[("local", task["id"])] = now - 300.0

    # 刷新后的页面从 300 秒前一直轮询到现在
    service._touch_task_heartbeat(task["id"], "local")

    service._sweep_departed_tasks_once()

    assert service.repository.get_task(task["id"])["status"] == "running"
    assert ("local", task["id"]) not in service._task_left_keys


def test_single_in_flight_poll_does_not_revoke_page_leave(tmp_path: Path) -> None:
    """关页面瞬间在途的那一次轮询不算「页面回来了」——否则关了页面任务会一直跑下去。"""
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "running", "local")
    now = time.monotonic()
    # 时间线：300 秒前关掉页面；关页面瞬间那一次在途请求 295 秒前被服务端处理，
    # 之后再没有任何轮询（静默 295 秒，远超阈值）→ 该暂停
    with service._task_left_lock:
        service._task_left_keys[("local", task["id"])] = now - 300.0
    service._task_last_seen[("local", task["id"])] = now - 295.0

    service._sweep_departed_tasks_once()

    assert service.repository.get_task(task["id"])["status"] == "paused"


def test_throttled_polling_keeps_task_running_after_page_leave(tmp_path: Path) -> None:
    """隐藏页被浏览器节流到「约 1 分钟一次」也算还在看：刷新后切到后台不该被误停。

    阈值必须盖住节流节奏（Chrome/Edge 隐藏满 5 分钟后定时器约 1 分钟一次），
    否则「刷新一下、切去别的窗口」就会把任务停掉 —— 正是这轮要消灭的那类误停。
    """
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "running", "local")
    now = time.monotonic()
    with service._task_left_lock:
        service._task_left_keys[("local", task["id"])] = now - 300.0
    # 被节流：最近一次轮询是 70 秒前（> 节流节奏，但仍在静默阈值之内）
    service._task_last_seen[("local", task["id"])] = now - 70.0

    service._sweep_departed_tasks_once()

    assert service.repository.get_task(task["id"])["status"] == "running"


def test_sweep_skips_round_after_process_was_suspended(tmp_path: Path) -> None:
    """整机休眠唤醒那一轮不判：进程自己也被冻住了，期间的「静默」不算页面离开。

    否则「刷新一下页面、合上盖子走人」会在醒来瞬间被判成页面已离开而误停；醒来后页面
    会立刻恢复轮询，下一轮按新时间线判断（并有轮询就撤销这次离开）。
    """
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "running", "local")
    now = time.monotonic()
    with service._task_left_lock:
        service._task_left_keys[("local", task["id"])] = now - 4000.0
    service._task_last_seen[("local", task["id"])] = now - 3900.0
    # 上一轮清扫是一小时前：本进程自己刚被冻过（休眠/挂起）
    service._last_sweep_at = now - 3600.0

    service._sweep_departed_tasks_once()

    assert service.repository.get_task(task["id"])["status"] == "running"

    # 醒来后页面恢复轮询 → 下一轮撤销这次离开
    service._touch_task_heartbeat(task["id"], "local")
    service._sweep_departed_tasks_once()

    assert service.repository.get_task(task["id"])["status"] == "running"
    assert ("local", task["id"]) not in service._task_left_keys


def test_leave_quiet_threshold_stays_above_runtime_exit_grace() -> None:
    """静默阈值必须大于 runtime 退出宽限：否则关掉整个工作台时两条路会抢同一个任务。

    关闭整个工作台时，watchdog 会在退出宽限（默认 60 秒）后退出后端并执行「取消任务 +
    按冻结积分 50% 结算」（beforeunload 弹窗向用户承诺的正是这个）。任务级暂停必须在它
    之后才可能发生，否则结果会在「暂停（可继续）」与「取消（按 50% 收费）」之间摇摆。
    """
    assert service_module._TASK_LEAVE_QUIET_SECONDS > service_module._RUNTIME_EXIT_GRACE_SECONDS
    # 留出清扫间隔的余量，别贴着边界。
    assert (
        service_module._TASK_LEAVE_QUIET_SECONDS
        >= service_module._RUNTIME_EXIT_GRACE_SECONDS + service_module._TASK_AUTO_PAUSE_SWEEP_SECONDS * 2
    )


def test_page_leave_does_not_pause_task_during_export_finalization(
    tmp_path: Path,
) -> None:
    """商品已全部终态、只剩本地收尾时即使页面离开也不暂停：否则会卡在 99% 并阻断预检/导出。"""
    service = _service(tmp_path)
    task, item_a, item_b = _task_with_two_items(service)
    for item in (item_a, item_b):
        service.repository.update_item_progress(
            task["id"],
            int(item["item_id"]),
            status="completed",
            result={"product_draft_id": item["product_draft_id"], "optimized_title": "done"},
            workspace_id="local",
        )
    service.repository.set_task_status(task["id"], "running", "local")
    service._task_last_seen[("local", task["id"])] = time.monotonic() - 400.0
    service.mark_task_left(task["id"], "local")
    service._task_left_keys[("local", task["id"])] = time.monotonic() - 300.0

    service._sweep_departed_tasks_once()

    assert service.repository.get_task(task["id"])["status"] == "running"


def test_sweep_prunes_expired_heartbeat_records(tmp_path: Path) -> None:
    """心跳记录只服务于「离开之后有没有人回来」：过期即弃，避免长会话里无限增长。"""
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service._task_last_seen[("local", task["id"])] = time.monotonic() - 7200.0

    service._sweep_departed_tasks_once()

    assert ("local", task["id"]) not in service._task_last_seen


def test_task_leave_beacon_route_is_reachable(tmp_path: Path) -> None:
    """前端 pagehide 的信标地址（写死在 ProductProcessingTaskPage 里）必须真的能通。

    路径一旦对不上，「关掉页面就暂停」会静默失效（信标打空、任务继续跑），界面上看不出来。
    同时锁定语义：任务号不存在也返回 ok —— 信标是尽力而为的，任何时候都不许抛错。
    """
    from fastapi.testclient import TestClient

    from wh_local.app.main import create_app

    client = TestClient(create_app(tmp_path / "workbench.sqlite3"))

    response = client.post("/api/product-processing/tasks/999999/leave?workspace=local")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_execute_finishes_export_when_pause_lands_after_last_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    task, item_a, item_b = _task_with_two_items(service)
    for item in (item_a, item_b):
        service.repository.update_item_progress(
            task["id"],
            int(item["item_id"]),
            status="completed",
            result={"product_draft_id": item["product_draft_id"], "optimized_title": "done"},
            workspace_id="local",
        )

    original_claim = service.repository.claim_task_execution

    def claim_then_pause(task_id: int, workspace_id: str = "local") -> bool:
        claimed = original_claim(task_id, workspace_id)
        assert claimed is True
        assert service.repository.pause_task_execution(task_id, workspace_id) is True
        return True

    monkeypatch.setattr(service.repository, "claim_task_execution", claim_then_pause)

    final = service._execute_task_impl(task["id"], "local")

    assert final["status"] == "completed"
    assert Path(final["output_file"]).is_file()
    assert Path(final["error_report_file"]).is_file()


def test_execute_keeps_failures_persisted_before_resume_in_error_report(tmp_path: Path) -> None:
    service = _service(tmp_path)
    draft_a = _draft(service, "resume-success")
    draft_b = _draft(service, "resume-failure")
    task = service.repository.create_task(
        title="resume-output-task",
        preflight_only=True,
        settings={"processing_scope": ["title"], "async_mode": False},
        drafts=[draft_a, draft_b],
        idempotency_key=None,
    )
    item_a, item_b = task["items"]
    service.repository.update_item_progress(
        task["id"],
        int(item_a["item_id"]),
        status="completed",
        result={"product_draft_id": item_a["product_draft_id"], "optimized_title": "done"},
        workspace_id="local",
    )
    service.repository.update_item_progress(
        task["id"],
        int(item_b["item_id"]),
        status="failed",
        reason="暂停前已经失败",
        result={"failure_class": "business_non_retryable", "retryable": False},
        workspace_id="local",
    )

    final = service._execute_task_impl(task["id"], "local")

    with Path(final["error_report_file"]).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["item_id"] == str(item_b["item_id"])
    assert rows[0]["reason"] == "暂停前已经失败"


def test_task_outputs_touches_heartbeat(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    assert ("local", task["id"]) not in service._task_last_seen

    service.task_outputs(task["id"], workspace_id="local")

    assert ("local", task["id"]) in service._task_last_seen


def test_finish_task_keeps_paused_when_items_still_unfinished(tmp_path: Path) -> None:
    """收尾竞态防御（2026-10-14）：仍有未处理条目时 finish_task 不得判「完成」。

    历史线上实例：55 条任务被收尾竞态判 completed，仅处理 27 条，剩余条目
    在 UI 上完全消失。pending/running 不计失败（failed_count==0）正是缺口。
    """
    service = _service(tmp_path)
    task, item_a, item_b = _task_with_two_items(service)
    service.repository.update_item_progress(
        task["id"],
        int(item_a["item_id"]),
        status="completed",
        result={"optimized_title": "done"},
        workspace_id="local",
    )
    service.repository.set_task_status(task["id"], "running", "local")

    finished = service.repository.finish_task(
        task["id"],
        [],
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="local",
    )

    assert finished is not None and finished["status"] == "paused"
    items = {int(item["item_id"]): item for item in finished["items"]}
    assert items[int(item_b["item_id"])]["status"] == "pending"


def test_finish_task_marks_completed_only_when_all_items_terminal(tmp_path: Path) -> None:
    """正常路径保护：全部条目终态时 finish_task 仍应判「完成」。"""
    service = _service(tmp_path)
    task, item_a, item_b = _task_with_two_items(service)
    for item in (item_a, item_b):
        service.repository.update_item_progress(
            task["id"],
            int(item["item_id"]),
            status="completed",
            result={"optimized_title": "done"},
            workspace_id="local",
        )
    service.repository.set_task_status(task["id"], "running", "local")

    finished = service.repository.finish_task(
        task["id"],
        [],
        output_file="",
        error_report_file="",
        video_manifest_file="",
        workspace_id="local",
    )

    assert finished is not None and finished["status"] == "completed"


def test_execute_task_impl_falls_back_to_paused_when_items_remain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """收尾竞态防御：worker 收尾读到 running 但仍有未处理条目 → 回退暂停。

    模拟「暂停 → 立刻继续」竞态：旧 worker 收尾时任务状态已被 resume 改写，
    处理函数本轮不再产出终态（返回 None），未处理条目留在库中。
    """
    service = _service(tmp_path)
    task, item_a, item_b = _task_with_two_items(service)
    service.repository.update_item_progress(
        task["id"],
        int(item_a["item_id"]),
        status="completed",
        result={"optimized_title": "done"},
        workspace_id="local",
    )
    # 处理函数返回 None：条目保持未终态（等同竞态窗口内的中断）
    monkeypatch.setattr(service, "_process_one", lambda *args, **kwargs: None)

    final = service._execute_task_impl(task["id"], "local")

    assert final["status"] == "paused"
    items = {int(item["item_id"]): item for item in final["items"]}
    assert items[int(item_b["item_id"])]["status"] in {"pending", "running"}
    assert items[int(item_a["item_id"])]["status"] == "completed"


def test_execute_task_impl_completes_when_all_items_reach_terminal(tmp_path: Path) -> None:
    """正常路径保护：条目全部终态时收尾仍判「完成」。"""
    service = _service(tmp_path)
    task, item_a, item_b = _task_with_two_items(service)
    for item in (item_a, item_b):
        service.repository.update_item_progress(
            task["id"],
            int(item["item_id"]),
            status="completed",
            result={"optimized_title": "done"},
            workspace_id="local",
        )

    final = service._execute_task_impl(task["id"], "local")

    assert final["status"] == "completed"


def test_heal_terminal_orphan_items_marks_failed_and_releases_drafts(tmp_path: Path) -> None:
    """孤儿项自愈：终态任务残留 pending 项 → 标失败 + 草稿回草稿池 + 计数同步。"""
    service = _service(tmp_path)
    task, item_a, item_b = _task_with_two_items(service)
    service.repository.update_item_progress(
        task["id"],
        int(item_a["item_id"]),
        status="completed",
        result={"optimized_title": "done"},
        workspace_id="local",
    )
    # 复刻历史竞态产物：任务被判 completed，但 item_b 从未处理；草稿卡 processing
    service.repository.set_task_status(task["id"], "completed", "local")
    draft_b_id = int(item_b["product_draft_id"])
    service.repository.mark_drafts_status([draft_b_id], "processing", workspace_id="local")

    orphans = service.repository.terminal_tasks_with_unfinished_items()
    assert any(int(t["id"]) == int(task["id"]) for t in orphans)

    healed = service.repository.heal_terminal_orphan_items(task["id"], "local")
    assert healed == 1

    final = service.repository.get_task(task["id"], "local")
    items = {int(item["item_id"]): item for item in final["items"]}
    healed_item = items[int(item_b["item_id"])]
    assert healed_item["status"] == "failed"
    assert healed_item["reason"] == "任务已结束时仍未处理，已自动终止"
    assert (healed_item["result"] or {}).get("failure_class") == "task_control"
    assert final["success_count"] == 1
    assert final["failed_count"] == 1
    # 任务状态保持原有终态，不被自愈改动
    assert final["status"] == "completed"

    drafts = service.repository.get_drafts([draft_b_id], workspace_id="local")
    assert drafts and drafts[0]["status"] == "draft"
