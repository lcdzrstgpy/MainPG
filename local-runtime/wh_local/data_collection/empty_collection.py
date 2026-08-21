"""Background auto-retry for daily-selection runs that collected zero candidates.

Upstream 1688/OneBound calls occasionally return an empty result because of
transient API fluctuation.  This module re-runs the collection with the same
criteria in the background and replaces the placeholder run once a later round
produces candidates, so a single fluctuation does not silently sink a batch.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from .collector import DailySelectionCollector
from .criteria import DailySelectionCriteria
from .filtering import filter_and_score_candidates


def empty_collection_retry_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "round": 0,
        "total": 0,
        "message": "",
        "updated_at": "",
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _retry_rounds() -> int:
    """空采集自动重试轮数：WH_DAILY_SELECTION_COLLECT_RETRIES，默认 2，0 关闭。"""
    try:
        return max(0, int(os.environ.get("WH_DAILY_SELECTION_COLLECT_RETRIES", "2")))
    except ValueError:
        return 2


# 相邻两轮重试的间隔：避免与首次波动同一时刻重试仍返回空。
_RETRY_DELAY_SECONDS = 3.0


@dataclass
class EmptyCollectionRetryJob:
    run_id: str
    workspace_id: str
    total: int
    round: int = 0
    status: str = "running"  # running / completed / failed
    message: str = ""
    updated_at: str = ""
    progress_lock: threading.Lock = field(default_factory=threading.Lock)

    def to_state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "round": self.round,
            "total": self.total,
            "message": self.message,
            "updated_at": self.updated_at,
        }


class EmptyCollectionRetryRunner:
    """Owns empty-run retry jobs without touching HTTP routes."""

    def __init__(
        self,
        *,
        repository: Any,
        budget: Any,
        provider_config_resolver: Any,
        provider_factory: Any,
    ) -> None:
        self._repository = repository
        self._budget = budget
        self._provider_config_resolver = provider_config_resolver
        self._provider_factory = provider_factory
        self._jobs: dict[tuple[str, str], EmptyCollectionRetryJob] = {}
        self._lock = threading.Lock()

    def maybe_start(self, *, actor: Any, run: Any) -> dict[str, Any]:
        """当本次采集结果为 0 候选时，自动启动后台重采。

        仅在从未重试过（idle）且确实 0 候选时启动，避免重复消耗 API 调用；
        返回状态供前端轮询展示。
        """
        key = (actor.workspace_id, run.run_id)
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and job.status == "running":
                return job.to_state()
            if run.candidates:
                return empty_collection_retry_state()
        previous = run.metadata.get("collection_retry")
        if isinstance(previous, Mapping) and previous.get("status") not in (None, "idle"):
            return dict(previous)
        max_rounds = _retry_rounds()
        if max_rounds <= 0:
            return empty_collection_retry_state()
        job = EmptyCollectionRetryJob(
            run_id=run.run_id,
            workspace_id=actor.workspace_id,
            total=max_rounds,
            message=f"正在自动重新尝试采集（最多 {max_rounds} 轮）…",
            updated_at=_now(),
        )
        with self._lock:
            self._jobs[key] = job
        thread = threading.Thread(
            target=self._execute,
            args=(actor, job, run),
            name=f"empty-collection-retry-{run.run_id[:8]}",
            daemon=True,
        )
        thread.start()
        return job.to_state()

    def state(self, *, actor: Any, run: Any) -> dict[str, Any]:
        key = (actor.workspace_id, run.run_id)
        with self._lock:
            job = self._jobs.get(key)
            if job is not None:
                return job.to_state()
        previous = run.metadata.get("collection_retry")
        return dict(previous) if isinstance(previous, Mapping) else empty_collection_retry_state()

    def _execute(self, actor: Any, job: EmptyCollectionRetryJob, run: Any) -> None:
        try:
            config = self._provider_config_resolver(actor)
            provider = self._provider_factory(config)
        except Exception:
            with job.progress_lock:
                job.status = "failed"
                job.message = "采集服务暂不可用，自动重试未完成"
                job.updated_at = _now()
            self._persist(actor, job)
            return
        criteria = DailySelectionCriteria.model_validate(dict(run.criteria))
        max_rounds = _retry_rounds()
        for round_index in range(1, max_rounds + 1):
            if round_index > 1:
                time.sleep(_RETRY_DELAY_SECONDS)
            with job.progress_lock:
                job.round = round_index
                job.message = f"正在自动重新尝试采集（第 {round_index} 轮）…"
                job.updated_at = _now()
            collected = None
            candidates: tuple[Any, ...] = ()
            try:
                collected = DailySelectionCollector(
                    workspace_id=actor.workspace_id,
                    provider=provider,
                    budget=self._budget,
                    provider_credentials=config,
                ).collect(criteria)
                filtered = filter_and_score_candidates(
                    tuple(item.candidate for item in collected.candidates), criteria
                )
                candidates = (
                    *filtered.candidates[: criteria.target_count],
                    *filtered.filtered,
                )
            except Exception:
                candidates = ()
            if candidates:
                metadata = dict(run.metadata)
                metadata["collection_retry"] = {
                    "status": "completed",
                    "round": round_index,
                    "total": max_rounds,
                    "message": f"自动重试采集成功：第 {round_index} 轮采到 {len(candidates)} 条",
                    "updated_at": _now(),
                }
                self._repository.replace_run_collection(
                    workspace_id=actor.workspace_id,
                    run_id=run.run_id,
                    status=collected.status if collected is not None else "completed",
                    candidates=candidates,
                    metadata=metadata,
                )
                with job.progress_lock:
                    job.status = "completed"
                    job.message = metadata["collection_retry"]["message"]
                    job.updated_at = _now()
                return
        # 所有重试轮次仍为空
        with job.progress_lock:
            job.status = "failed"
            job.message = (
                f"自动重试 {max_rounds} 轮后仍未采集到商品，可稍后手动重新采集"
            )
            job.updated_at = _now()
        self._persist(actor, job)

    def _persist(self, actor: Any, job: EmptyCollectionRetryJob) -> None:
        try:
            run = self._repository.get_run(
                workspace_id=job.workspace_id, run_id=job.run_id
            )
            metadata = dict(run.metadata)
            metadata["collection_retry"] = job.to_state()
            self._repository.update_run_metadata(
                workspace_id=job.workspace_id,
                run_id=job.run_id,
                metadata=metadata,
            )
        except Exception:
            # 元数据持久化失败不阻断本轮结果；下一轮 maybe_start 仍按内存 job 推进。
            pass
