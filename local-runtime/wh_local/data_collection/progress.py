"""Lightweight in-memory progress for local daily-selection tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock
import threading
import uuid

from pydantic import BaseModel, ConfigDict, Field


class DailySelectionTaskNotFound(PermissionError):
    """Raised without revealing tasks owned by another workspace."""


class DailySelectionTaskStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    status: str
    stage: str
    progress: int = Field(ge=0, le=100)
    completed: int = Field(ge=0)
    total: int = Field(ge=0)
    message: str
    run_id: str | None = None
    error: str | None = None
    updated_at: str


@dataclass
class _TaskRecord:
    workspace_id: str
    status: str
    stage: str
    progress: int
    completed: int
    total: int
    message: str
    run_id: str | None
    error: str | None
    updated_at: datetime
    cancel_event: threading.Event = field(default_factory=threading.Event)


class DailySelectionProgressTracker:
    """Store tiny progress records only; completed runs remain in SQLite."""

    def __init__(self, *, completed_ttl: timedelta = timedelta(minutes=30), max_records: int = 200) -> None:
        self._completed_ttl = completed_ttl
        self._max_records = max(16, max_records)
        self._records: dict[str, _TaskRecord] = {}
        self._lock = Lock()

    def create(self, *, workspace_id: str) -> DailySelectionTaskStatus:
        now = datetime.now(UTC)
        task_id = str(uuid.uuid4())
        with self._lock:
            self._prune(now)
            self._records[task_id] = _TaskRecord(
                workspace_id=workspace_id,
                status="queued",
                stage="queued",
                progress=0,
                completed=0,
                total=0,
                message="任务已创建",
                run_id=None,
                error=None,
                updated_at=now,
            )
            return self._public(task_id, self._records[task_id])

    def update(
        self,
        task_id: str,
        *,
        stage: str,
        progress: int,
        completed: int,
        total: int,
        message: str,
    ) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status in {"completed", "failed", "cancelled"}:
                return
            record.status = "running"
            record.stage = stage
            record.progress = max(record.progress, min(99, max(0, int(progress))))
            record.completed = max(0, int(completed))
            record.total = max(0, int(total))
            record.message = message
            record.updated_at = datetime.now(UTC)

    def complete(self, task_id: str, *, run_id: str) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.status = "completed"
            record.stage = "completed"
            record.progress = 100
            record.completed = record.total
            record.message = "采集完成"
            record.run_id = run_id
            record.error = None
            record.updated_at = datetime.now(UTC)

    def fail(self, task_id: str, *, error: str) -> None:
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.status = "failed"
            record.stage = "failed"
            record.message = "采集失败"
            record.error = error.strip() or "采集任务失败"
            record.updated_at = datetime.now(UTC)

    def cancel(self, task_id: str, *, workspace_id: str) -> DailySelectionTaskStatus:
        """Request a running task to stop; collected candidates are kept."""
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.workspace_id != workspace_id:
                raise DailySelectionTaskNotFound(task_id)
            if record.status not in {"completed", "failed", "cancelled"}:
                record.cancel_event.set()
                record.message = "中断请求已发送，正在停止…"
                record.updated_at = datetime.now(UTC)
            return self._public(task_id, record)

    def cancel_event_for(self, task_id: str, *, workspace_id: str) -> threading.Event:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.workspace_id != workspace_id:
                raise DailySelectionTaskNotFound(task_id)
            return record.cancel_event

    def mark_cancelled(self, task_id: str, *, run_id: str) -> None:
        """Finalize a task that was interrupted but still produced a run."""
        with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.status = "cancelled"
            record.stage = "cancelled"
            record.message = "采集已中断，以下为已采集的候选"
            record.run_id = run_id
            record.error = None
            record.updated_at = datetime.now(UTC)

    def get(self, task_id: str, *, workspace_id: str) -> DailySelectionTaskStatus:
        with self._lock:
            record = self._records.get(task_id)
            if record is None or record.workspace_id != workspace_id:
                raise DailySelectionTaskNotFound(task_id)
            return self._public(task_id, record)

    def _prune(self, now: datetime) -> None:
        finished_statuses = {"completed", "failed", "cancelled"}
        cutoff = now - self._completed_ttl
        expired = [
            task_id
            for task_id, record in self._records.items()
            if record.status in finished_statuses and record.updated_at < cutoff
        ]
        for task_id in expired:
            self._records.pop(task_id, None)
        if len(self._records) < self._max_records:
            return
        finished = sorted(
            (record.updated_at, task_id)
            for task_id, record in self._records.items()
            if record.status in finished_statuses
        )
        for _updated_at, task_id in finished[: max(1, len(self._records) - self._max_records + 1)]:
            self._records.pop(task_id, None)

    @staticmethod
    def _public(task_id: str, record: _TaskRecord) -> DailySelectionTaskStatus:
        return DailySelectionTaskStatus(
            task_id=task_id,
            status=record.status,
            stage=record.stage,
            progress=record.progress,
            completed=record.completed,
            total=record.total,
            message=record.message,
            run_id=record.run_id,
            error=record.error,
            updated_at=record.updated_at.isoformat(),
        )
