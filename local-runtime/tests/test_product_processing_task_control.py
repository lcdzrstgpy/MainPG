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


def test_sweep_stale_heartbeats_auto_pauses_running_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "running", "local")

    # 模拟页面已关闭：最后一次 /outputs 心跳已远超超时阈值
    service._task_last_seen[("local", task["id"])] = time.monotonic() - 10_000.0
    monkeypatch.setattr(
        "wh_local.modules.product_processing.service._TASK_AUTO_PAUSE_TIMEOUT_SECONDS", 90.0
    )

    service._sweep_stale_heartbeats_once()

    final = service.repository.get_task(task["id"])
    assert final is not None and final["status"] == "paused"
    # 暂停后不再跟踪该任务心跳（等待 resume 后重新轮询刷新）
    assert ("local", task["id"]) not in service._task_last_seen


def test_sweep_keeps_task_with_fresh_heartbeat_running(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    service.repository.set_task_status(task["id"], "running", "local")
    service._task_last_seen[("local", task["id"])] = time.monotonic()

    service._sweep_stale_heartbeats_once()

    assert service.repository.get_task(task["id"])["status"] == "running"


def test_auto_pause_threshold_stays_well_above_browser_throttle_cadence() -> None:
    """自动暂停阈值必须显著长于「隐藏页被节流后的心跳节奏」。

    浏览器把隐藏页的定时器压到约 60 秒一次（Chrome/Edge 隐藏满 5 分钟进入密集节流），
    90 秒的阈值只剩 30 秒余量，主线程一忙就被误判成「页面已关」——这是「工作台明明
    开着却被暂停」工单的主要来源。这条断言是防止阈值被改回短值（前端另有 Web Lock 豁免
    节流，此处是第二道防线）。
    """
    assert service_module._TASK_AUTO_PAUSE_TIMEOUT_SECONDS >= 150.0
    # 清扫节奏要远小于阈值，否则「超时」本身会被清扫间隔拖成一个大区间。
    assert service_module._TASK_AUTO_PAUSE_SWEEP_SECONDS * 4 <= service_module._TASK_AUTO_PAUSE_TIMEOUT_SECONDS


def test_sweep_does_not_pause_task_during_export_finalization(
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
    service.repository.set_task_status(task["id"], "running", "local")
    service._task_last_seen[("local", task["id"])] = time.monotonic() - 10_000.0
    monkeypatch.setattr(
        "wh_local.modules.product_processing.service._TASK_AUTO_PAUSE_TIMEOUT_SECONDS", 90.0
    )

    service._sweep_stale_heartbeats_once()

    assert service.repository.get_task(task["id"])["status"] == "running"
    assert ("local", task["id"]) not in service._task_last_seen


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
