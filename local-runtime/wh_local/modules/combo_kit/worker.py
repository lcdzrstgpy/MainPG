"""combo_kit 长耗时 AI 任务的后台执行：提交即返回，前端轮询任务状态。

组合套装的三个 AI 动作（主体解析 / 文本生成 / 生图）单次耗时远超前端 30s
HTTP 超时：同步路由下前端会先放弃、后端仍在执行，用户只看到「请求超时」，
还会因重复点击带来重复扣费风险。这里把执行搬到后台线程池，提交只落一条任务
记录并立即返回，前端按 (set_id, task_type) 轮询进度与结果。

任务记录复用 combo_kit_tasks 表：UNIQUE(set_id, task_type) 天然提供
subject / text / image 三个互不干扰的任务槽，无需新增表或迁移。
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from .contracts import ComboKitError, ComboKitNotFound
from .repository import ComboKitRepository

LOGGER = logging.getLogger(__name__)

TASK_SUBJECT = "subject"
TASK_TEXT = "text"
TASK_IMAGE = "image"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED)

ProgressReporter = Callable[[dict[str, Any]], None]
# 任务体：接收进度回调，返回可直接 JSON 序列化的结果。
TaskJob = Callable[[ProgressReporter], dict[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ComboKitTaskWorker:
    """按 (set_id, task_type) 去重的线程池执行器。

    同一套装同一类型同时只有一个任务在跑：重复提交直接复用进行中的任务，
    既不重复调用 AI（重复扣费），也不与自身抢写同一批产物。
    """

    def __init__(self, repository: ComboKitRepository, *, max_workers: int = 3) -> None:
        self.repository = repository
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="combo-kit-ai")
        self._lock = threading.Lock()
        self._inflight: dict[tuple[str, str], Future[Any]] = {}
        self._fail_interrupted_tasks()

    # ---- 对外接口 ----

    def submit(
        self,
        *,
        set_id: str,
        task_type: str,
        workspace_id: str,
        owner_user_id: str,
        job: TaskJob,
    ) -> dict[str, Any]:
        """提交任务并立即返回任务状态；同类型任务已在跑则直接复用。"""
        key = (set_id, task_type)
        with self._lock:
            running = self._inflight.get(key)
            if running is not None and not running.done():
                return self.task_state(set_id, task_type)
            self.repository.upsert_task(
                {
                    "set_id": set_id,
                    "task_type": task_type,
                    "workspace_id": workspace_id,
                    "owner_user_id": owner_user_id,
                    "status": STATUS_QUEUED,
                    "result_json": "{}",
                    "attempt_count": self._next_attempt(set_id, task_type),
                    "error_kind": "",
                    "error_message": "",
                    "started_at": None,
                    "finished_at": None,
                }
            )
            # 计费/密钥解析依赖请求上下文里的 ContextVar，需显式带进 worker 线程。
            context = contextvars.copy_context()
            future = self._executor.submit(self._run, key, job, context)
            self._inflight[key] = future
        # 回调必须在锁外注册：Future 已完成时 add_done_callback 会在当前线程同步触发。
        future.add_done_callback(lambda done, _key=key: self._forget(_key, done))
        return self.task_state(set_id, task_type)

    def task_state(self, set_id: str, task_type: str) -> dict[str, Any]:
        """读取任务状态：进行中返回进度，终态返回结果或错误。"""
        try:
            record = self.repository.get_task(set_id, task_type)
        except KeyError:
            raise ComboKitNotFound(f"任务不存在：{task_type}") from None
        status = str(record.get("status") or STATUS_QUEUED)
        payload = record.get("result_json")
        payload = payload if isinstance(payload, dict) else {}
        finished = status in TERMINAL_STATUSES
        return {
            "task_id": str(record.get("task_id") or ""),
            "set_id": set_id,
            "task_type": task_type,
            "status": status,
            "progress": None if finished else (payload.get("progress") or None),
            "result": payload if finished and status == STATUS_COMPLETED else None,
            "error_kind": str(record.get("error_kind") or ""),
            "error_message": str(record.get("error_message") or ""),
            "attempt_count": int(record.get("attempt_count") or 0),
            "started_at": record.get("started_at"),
            "finished_at": record.get("finished_at"),
        }

    # ---- 内部实现 ----

    def _run(self, key: tuple[str, str], job: TaskJob, context: contextvars.Context) -> None:
        set_id, task_type = key
        self._write(set_id, task_type, {"status": STATUS_RUNNING, "started_at": _now()})

        def report(progress: dict[str, Any]) -> None:
            self._write(
                set_id,
                task_type,
                {"result_json": json.dumps({"progress": progress}, ensure_ascii=False)},
            )

        try:
            result = context.run(job, report)
        except ComboKitError as exc:
            self._finish_failed(set_id, task_type, exc.__class__.__name__, str(exc))
            return
        except Exception as exc:  # 后台线程必须吞掉一切异常并落到任务状态，否则前端永久轮询
            LOGGER.exception("combo-kit 任务执行失败：%s/%s", set_id, task_type)
            self._finish_failed(set_id, task_type, exc.__class__.__name__, str(exc)[:500])
            return
        self._write(
            set_id,
            task_type,
            {
                "status": STATUS_COMPLETED,
                "result_json": json.dumps(result, ensure_ascii=False, default=str),
                "error_kind": "",
                "error_message": "",
                "finished_at": _now(),
            },
        )

    def _finish_failed(self, set_id: str, task_type: str, error_kind: str, error_message: str) -> None:
        self._write(
            set_id,
            task_type,
            {
                "status": STATUS_FAILED,
                "result_json": "{}",
                "error_kind": error_kind,
                "error_message": error_message,
                "finished_at": _now(),
            },
        )

    def _write(self, set_id: str, task_type: str, values: dict[str, Any]) -> None:
        """写任务状态：套装可能在任务执行期间被删除，写入失败不应打断 worker。"""
        try:
            self.repository.update_task(set_id, task_type, values)
        except Exception:
            LOGGER.warning("combo-kit 任务状态写入失败：%s/%s", set_id, task_type, exc_info=True)

    def _forget(self, key: tuple[str, str], future: Future[Any]) -> None:
        with self._lock:
            if self._inflight.get(key) is future:
                self._inflight.pop(key, None)

    def _next_attempt(self, set_id: str, task_type: str) -> int:
        try:
            return int(self.repository.get_task(set_id, task_type).get("attempt_count") or 0) + 1
        except (KeyError, TypeError, ValueError):
            return 1

    def _fail_interrupted_tasks(self) -> None:
        """启动清理：上次进程遗留的 queued/running 任务已无执行者，标记中断。"""
        try:
            stale = self.repository.list_active_tasks()
        except Exception:
            LOGGER.warning("combo-kit 未完成任务清理失败", exc_info=True)
            return
        for task in stale:
            self._write(
                str(task.get("set_id") or ""),
                str(task.get("task_type") or ""),
                {
                    "status": STATUS_FAILED,
                    "result_json": "{}",
                    "error_kind": "interrupted",
                    "error_message": "任务已中断（服务重启），请重新发起",
                    "finished_at": _now(),
                },
            )
