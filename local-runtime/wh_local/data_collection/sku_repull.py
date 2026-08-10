"""Background SKU re-pull for daily-selection runs with incomplete variants.

A single collection run may leave many candidates with ``source_variant_records``
empty (detail fetch failed / SKU 未知).  This module runs a per-run background
round that re-fetches each incomplete candidate's 1688 item detail via the same
injected provider and persists the enriched candidate in place.  The user can
read the current round, progress, and cancel the round at any time.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def candidate_sku_incomplete(candidate: Any) -> bool:
    """A candidate needs re-pull when no SKU record was captured."""
    return not tuple(getattr(candidate, "source_variant_records", ()))


def incomplete_candidates(run: Any) -> list[Any]:
    return [candidate for candidate in run.candidates if candidate_sku_incomplete(candidate)]


def empty_repull_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "round": 0,
        "total": 0,
        "done": 0,
        "succeeded": 0,
        "failed": 0,
        "message": "尚未补齐 SKU",
        "updated_at": "",
    }


@dataclass
class SkuRepullJob:
    """In-memory progress for one run's current re-pull round."""

    run_id: str
    workspace_id: str
    round: int
    total: int
    done: int = 0
    succeeded: int = 0
    failed: int = 0
    status: str = "running"  # running / completed / cancelled / failed
    message: str = ""
    cancel_event: threading.Event = field(default_factory=threading.Event)
    updated_at: str = ""

    def to_state(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "round": self.round,
            "total": self.total,
            "done": self.done,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "message": self.message,
            "updated_at": self.updated_at,
        }


class SkuRepullRunner:
    """Owns jobs and executes re-pull rounds without touching HTTP routes."""

    def __init__(
        self,
        *,
        repository: Any,
        provider_config_resolver: Any,
        provider_factory: Any,
        clock: Any = None,
    ) -> None:
        self._repository = repository
        self._provider_config_resolver = provider_config_resolver
        self._provider_factory = provider_factory
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._jobs: dict[tuple[str, str], SkuRepullJob] = {}
        self._lock = threading.Lock()

    def start(
        self,
        *,
        actor: Any,
        run: Any,
        targets: Sequence[Any],
        previous_round: int,
    ) -> dict[str, Any]:
        key = (actor.workspace_id, run.run_id)
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and job.status == "running":
                return job.to_state()
            if not targets:
                job = SkuRepullJob(
                    run_id=run.run_id,
                    workspace_id=actor.workspace_id,
                    round=previous_round + 1,
                    total=0,
                    status="completed",
                    message="本批次没有需要补齐 SKU 的候选",
                    updated_at=_now(),
                )
                self._jobs[key] = job
                return job.to_state()
            job = SkuRepullJob(
                run_id=run.run_id,
                workspace_id=actor.workspace_id,
                round=previous_round + 1,
                total=len(targets),
                message=f"第 {previous_round + 1} 轮补齐进行中",
                updated_at=_now(),
            )
            self._jobs[key] = job
        thread = threading.Thread(
            target=self._execute,
            args=(actor, job, list(targets)),
            name=f"sku-repull-{run.run_id[:8]}",
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
        stored = run.metadata.get("sku_repull")
        return dict(stored) if isinstance(stored, Mapping) else empty_repull_state()

    def cancel(self, *, actor: Any, run: Any) -> dict[str, Any]:
        key = (actor.workspace_id, run.run_id)
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and job.status == "running":
                job.cancel_event.set()
        return self.state(actor=actor, run=run)

    def _execute(self, actor: Any, job: SkuRepullJob, targets: list[Any]) -> None:
        try:
            config = self._provider_config_resolver(actor)
            provider = self._provider_factory(config)
        except Exception:
            job.status = "failed"
            job.message = "1688 采集服务未配置，无法补齐 SKU"
            job.updated_at = _now()
            self._persist(actor, job)
            return
        from .normalizer import enrich_candidate_with_detail

        for candidate in targets:
            if job.cancel_event.is_set():
                break
            try:
                response = provider.get_item_detail(candidate.offer_id)
                if response.error is None:
                    enriched = enrich_candidate_with_detail(
                        candidate, response.response, evidence=response.audit
                    )
                    self._repository.update_candidate(
                        workspace_id=job.workspace_id,
                        run_id=job.run_id,
                        candidate=enriched,
                        timestamp=_now(),
                    )
                    job.succeeded += 1
                else:
                    job.failed += 1
            except Exception:
                job.failed += 1
            finally:
                job.done += 1
                job.updated_at = _now()
        if job.status == "running" and job.cancel_event.is_set():
            # 取消标志可能在某个候选取回期间被设置：以用户请求为准，
            # 本轮标记为已中断而不是已完成。
            job.status = "cancelled"
            job.message = f"第 {job.round} 轮已中断：完成 {job.done}/{job.total}"
            job.updated_at = _now()
        elif job.status == "running":
            job.status = "completed"
            job.message = (
                f"第 {job.round} 轮补齐完成：成功 {job.succeeded}，"
                f"失败 {job.failed}"
            )
            job.updated_at = _now()
        self._persist(actor, job)

    def _persist(self, actor: Any, job: SkuRepullJob) -> None:
        """Record the round outcome in run metadata so it survives restarts."""
        try:
            run = self._repository.get_run(
                workspace_id=job.workspace_id, run_id=job.run_id
            )
            metadata = dict(run.metadata)
            metadata["sku_repull"] = job.to_state()
            self._repository.update_run_metadata(
                workspace_id=job.workspace_id,
                run_id=job.run_id,
                metadata=metadata,
            )
        except Exception:
            # 元数据持久化失败不阻断本轮结果；下一轮 start 仍按内存 job 推进。
            pass
