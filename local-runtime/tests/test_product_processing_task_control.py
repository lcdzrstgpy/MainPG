from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService, _TaskControlStopped


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


def test_task_outputs_touches_heartbeat(tmp_path: Path) -> None:
    service = _service(tmp_path)
    task, _a, _b = _task_with_two_items(service)
    assert ("local", task["id"]) not in service._task_last_seen

    service.task_outputs(task["id"], workspace_id="local")

    assert ("local", task["id"]) in service._task_last_seen
