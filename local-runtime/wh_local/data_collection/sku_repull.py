"""Background SKU re-pull for daily-selection runs with incomplete variants.

A single collection run may leave many candidates with ``source_variant_records``
empty (detail fetch failed / SKU 未知).  This module runs a per-run background
round that re-fetches each incomplete candidate's 1688 item detail via the same
injected provider and persists the enriched candidate in place.  The user can
read the current round, progress, and cancel the round at any time.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repull_worker_count() -> int:
    """并发补齐的工作线程数，默认 4，可用 WH_SKU_REPULL_WORKERS 覆盖（1-8）。"""
    try:
        return max(1, min(8, int(os.environ.get("WH_SKU_REPULL_WORKERS", "4"))))
    except ValueError:
        return 4


# 单轮只保留最近几条失败摘要：够定位「为什么失败」，又不让 run metadata 膨胀。
_MAX_RECORDED_ERRORS = 3


def _error_summary(error: Mapping[str, str]) -> str:
    code = str(error.get("code") or "").strip()
    message = str(error.get("message") or "").strip()
    return f"{code} {message}".strip() or "未知错误"


def candidate_sku_incomplete(candidate: Any) -> bool:
    """A candidate needs re-pull when no SKU record was captured."""
    return not tuple(getattr(candidate, "source_variant_records", ()))


def incomplete_candidates(run: Any) -> list[Any]:
    """SKU 未捕获且未被用户确认的候选才需要补齐。

    已确认（confirmed）的候选不再自动补齐，避免后台补拉把用户在页面上
    的确认状态冲掉（入库层无状态前进保护，且补拉基于轮次启动时的快照）。
    """
    return [
        candidate
        for candidate in run.candidates
        if candidate_sku_incomplete(candidate) and getattr(candidate, "status", None) != "confirmed"
    ]


def empty_repull_state() -> dict[str, Any]:
    return {
        "status": "idle",
        "round": 0,
        "total": 0,
        "done": 0,
        "succeeded": 0,
        "failed": 0,
        "message": "尚未补齐 SKU",
        "errors": [],
        "updated_at": "",
    }


def _reconcile_completed_run(run: Any, metadata: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Remove recovered detail errors and derive the final persisted run status."""
    unresolved_offer_ids = {
        str(candidate.offer_id)
        for candidate in incomplete_candidates(run)
    }
    detail_errors = metadata.get("detail_errors")
    if isinstance(detail_errors, Mapping):
        resolved_errors = [
            error
            for offer_id, error in detail_errors.items()
            if str(offer_id) not in unresolved_offer_ids
        ]
        metadata["detail_errors"] = {
            offer_id: error
            for offer_id, error in detail_errors.items()
            if str(offer_id) in unresolved_offer_ids
        }
        errors = metadata.get("errors")
        if isinstance(errors, (list, tuple)):
            remaining_errors = list(errors)
            for resolved_error in resolved_errors:
                try:
                    remaining_errors.remove(resolved_error)
                except ValueError:
                    continue
            metadata["errors"] = remaining_errors
    status = getattr(run, "status", "partial")
    if status == "partial" and not unresolved_offer_ids and not metadata.get("errors"):
        status = "completed"
    return metadata, status


@dataclass
class SkuRepullJob:
    """In-memory progress for one run's current re-pull round."""

    run_id: str
    workspace_id: str
    round: int
    total: int
    platform: str = "1688"
    done: int = 0
    succeeded: int = 0
    failed: int = 0
    status: str = "running"  # running / completed / cancelled / failed
    message: str = ""
    # 最近几条失败摘要（offer_id + 上游错误码/消息），用于「为什么失败」的诊断；
    # 只保留少量，避免 run metadata 无限膨胀。
    errors: list[dict[str, str]] = field(default_factory=list)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    progress_lock: threading.Lock = field(default_factory=threading.Lock)
    updated_at: str = ""

    def to_state(self) -> dict[str, Any]:
        with self.progress_lock:
            return {
                "status": self.status,
                "round": self.round,
                "total": self.total,
                "done": self.done,
                "succeeded": self.succeeded,
                "failed": self.failed,
                "message": self.message,
                "errors": list(self.errors),
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
                platform=str(run.criteria.get("collection_platform") or "1688")
                if isinstance(getattr(run, "criteria", None), Mapping)
                else "1688",
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
        if not isinstance(stored, Mapping):
            return empty_repull_state()
        state = dict(stored)
        # Runs completed before status reconciliation was introduced may still
        # be persisted as partial. Heal them when their stored round is read.
        if state.get("status") == "completed" and getattr(run, "status", None) == "partial":
            try:
                metadata, status = _reconcile_completed_run(run, dict(run.metadata))
                self._repository.update_run_metadata(
                    workspace_id=actor.workspace_id,
                    run_id=run.run_id,
                    metadata=metadata,
                    status=status,
                )
            except Exception:
                pass
        return state

    def cancel(self, *, actor: Any, run: Any) -> dict[str, Any]:
        key = (actor.workspace_id, run.run_id)
        with self._lock:
            job = self._jobs.get(key)
            if job is not None and job.status == "running":
                job.cancel_event.set()
        return self.state(actor=actor, run=run)

    def _execute(self, actor: Any, job: SkuRepullJob, targets: list[Any]) -> None:
        """线程入口：任何异常都必须把 job 落到终态，不能让它永久停在 running。

        ``running`` 会被 ``start()`` 当成「已有轮次在跑」直接返回，一旦线程异常退出
        而状态没改，用户后续点补齐就再也不会启动，只能重启进程。
        """
        try:
            self._run_round(actor, job, targets)
        except Exception as exc:  # noqa: BLE001 - 线程内兜底，避免僵尸 running
            with job.progress_lock:
                job.status = "failed"
                job.message = f"第 {job.round} 轮补齐失败：{str(exc)[:160]}"
                job.updated_at = _now()
        finally:
            with job.progress_lock:
                if job.status == "running":  # 理论上不会发生，兜底同异常分支
                    job.status = "failed"
                    job.message = f"第 {job.round} 轮补齐异常结束"
                    job.updated_at = _now()
            self._persist(actor, job)

    def _run_round(self, actor: Any, job: SkuRepullJob, targets: list[Any]) -> None:
        try:
            from .service import _platform_config

            config = _platform_config(self._provider_config_resolver(actor), job.platform)
            provider = self._provider_factory(config)
        except Exception:
            with job.progress_lock:
                job.status = "failed"
                job.message = "采集服务未配置，无法补齐 SKU"
                job.updated_at = _now()
            return
        from .normalizer import enrich_candidate_with_detail

        # 补齐是纯 IO（逐条拉取平台详情），线程池并发可显著缩短轮次时长。
        with ThreadPoolExecutor(
            max_workers=_repull_worker_count(), thread_name_prefix="sku-repull"
        ) as executor:
            futures = {
                executor.submit(
                    self._repull_one,
                    job,
                    provider,
                    enrich_candidate_with_detail,
                    candidate,
                ): candidate
                for candidate in targets
            }
            for _ in as_completed(futures):
                if job.cancel_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    break
        with job.progress_lock:
            if job.cancel_event.is_set():
                # 取消标志可能在某个候选取回期间被设置：以用户请求为准，
                # 本轮标记为已中断而不是已完成。
                job.status = "cancelled"
                job.message = f"第 {job.round} 轮已中断：完成 {job.done}/{job.total}"
            else:
                job.status = "completed"
                job.message = (
                    f"第 {job.round} 轮补齐完成：成功 {job.succeeded}，"
                    f"失败 {job.failed}"
                )
                if job.failed and job.errors:
                    job.message += f"（首个失败：{_error_summary(job.errors[0])}）"
            job.updated_at = _now()

    def _repull_one(
        self,
        job: SkuRepullJob,
        provider: Any,
        enrich: Any,
        candidate: Any,
    ) -> None:
        """并发执行单个候选的补齐：拉详情 → 富化 → 落库 → 计数。"""
        if job.cancel_event.is_set():
            return
        try:
            response = provider.get_item_detail(candidate.offer_id)
            if response.error is None:
                enriched = enrich(candidate, response.response, evidence=response.audit)
                self._repository.update_candidate(
                    workspace_id=job.workspace_id,
                    run_id=job.run_id,
                    candidate=enriched,
                    timestamp=_now(),
                )
                with job.progress_lock:
                    job.succeeded += 1
            else:
                self._record_failure(
                    job,
                    offer_id=str(candidate.offer_id),
                    code=str(getattr(response.error, "code", "") or "upstream_failed"),
                    message=str(getattr(response.error, "message", "") or ""),
                )
        except Exception as exc:  # noqa: BLE001
            self._record_failure(
                job,
                offer_id=str(getattr(candidate, "offer_id", "") or ""),
                code="exception",
                message=str(exc),
            )
        finally:
            with job.progress_lock:
                job.done += 1
                job.updated_at = _now()

    @staticmethod
    def _record_failure(job: SkuRepullJob, *, offer_id: str, code: str, message: str) -> None:
        with job.progress_lock:
            job.failed += 1
            if len(job.errors) < _MAX_RECORDED_ERRORS:
                job.errors.append(
                    {
                        "offer_id": offer_id,
                        "code": code[:40],
                        "message": " ".join(message.split())[:160],
                    }
                )

    def _persist(self, actor: Any, job: SkuRepullJob) -> None:
        """Record the round outcome in run metadata so it survives restarts."""
        try:
            run = self._repository.get_run(
                workspace_id=job.workspace_id, run_id=job.run_id
            )
            metadata = dict(run.metadata)
            metadata["sku_repull"] = job.to_state()
            reconciled_status = getattr(run, "status", "partial")
            if job.status == "completed":
                metadata, reconciled_status = _reconcile_completed_run(run, metadata)
            self._repository.update_run_metadata(
                workspace_id=job.workspace_id,
                run_id=job.run_id,
                metadata=metadata,
                status=reconciled_status,
            )
        except Exception:
            # 元数据持久化失败不阻断本轮结果；下一轮 start 仍按内存 job 推进。
            pass
