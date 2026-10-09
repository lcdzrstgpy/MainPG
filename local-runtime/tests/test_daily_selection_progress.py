from __future__ import annotations

from datetime import timedelta

import pytest

from wh_local.data_collection.progress import (
    DailySelectionProgressTracker,
    DailySelectionTaskNotFound,
)
from wh_local.data_collection.service import _report_collection_progress


def test_progress_tracker_keeps_only_small_task_state_and_enforces_workspace() -> None:
    tracker = DailySelectionProgressTracker(completed_ttl=timedelta(minutes=1))
    created = tracker.create(workspace_id="workspace-a")

    tracker.update(
        created.task_id,
        stage="details",
        progress=57,
        completed=12,
        total=30,
        message="正在读取商品详情",
    )
    running = tracker.get(created.task_id, workspace_id="workspace-a")
    assert running.status == "running"
    assert (running.completed, running.total, running.progress) == (12, 30, 57)
    assert running.run_id is None

    with pytest.raises(DailySelectionTaskNotFound):
        tracker.get(created.task_id, workspace_id="workspace-b")

    tracker.complete(created.task_id, run_id="run-1")
    completed = tracker.get(created.task_id, workspace_id="workspace-a")
    assert completed.status == "completed"
    assert completed.progress == 100
    assert completed.run_id == "run-1"


def test_collection_progress_is_derived_from_real_completed_counts() -> None:
    updates: list[tuple[str, int, int, int, str]] = []

    _report_collection_progress(
        lambda stage, progress, completed, total, message: updates.append(
            (stage, progress, completed, total, message)
        ),
        "details",
        15,
        30,
    )

    assert updates == [("details", 57, 15, 30, "正在读取商品详情")]
