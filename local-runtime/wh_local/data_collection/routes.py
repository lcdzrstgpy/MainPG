"""FastAPI routes registered without importing a host application."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
import re
import threading

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .budget import UnlimitedApiBudget
from .contracts import DailySelectionContractError
from .criteria import DailySelectionCriteriaError
from .repository import (
    DailySelectionCandidateNotConfirmable,
    DailySelectionFeedback,
    DailySelectionRepository,
    DailySelectionRun,
    DailySelectionRunNotFound,
    DailySelectionRunSummary,
)
from .handoff import DailySelectionConfirmResult, DailySelectionHandoff
from .normalizer import sanitize_raw_payload
from .plugin_queue import DataCollectionPluginQueue, PluginCommand
from .progress import (
    DailySelectionProgressTracker,
    DailySelectionTaskNotFound,
    DailySelectionTaskStatus,
)
from .service import (
    CachedDailySelectionImage,
    DailySelectionActor,
    DailySelectionImageAccessDenied,
    DailySelectionImageCache,
    DailySelectionImageNotFound,
    DailySelectionHandoffConsumer,
    DailySelectionProviderUnavailable,
    DailySelectionService,
    ProviderConfigResolver,
    ProviderFactory,
    RunIdFactory,
)
from .plugin_onebound_capture import (
    PluginOneBoundCaptureDependencies,
    PluginOneBoundCaptureService,
    register_plugin_onebound_capture_routes,
)


class DailySelectionFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    details: Mapping[str, Any] = Field(default_factory=dict)

    @field_validator("candidate_id", "reason", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class DailySelectionConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ids: tuple[str, ...] = Field(min_length=1)

    @field_validator("candidate_ids", mode="after")
    @classmethod
    def _normalize_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            dict.fromkeys(value.strip() for value in values if value.strip())
        )
        if not normalized:
            raise ValueError("candidate_ids must contain a non-empty identifier")
        return normalized


class TemuLinkCollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: int = Field(ge=1)
    source_url: str = Field(min_length=1)


class PluginResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str = Field(min_length=1)
    command_id: int = Field(ge=1)
    status: str = Field(pattern="^(sent|running|succeeded|failed)$")
    result: Mapping[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class DailySelectionRouteDependencies:
    """All host-owned adapters required by the daily-selection routes."""

    resolve_actor: Callable[..., Any]
    provider_config_resolver: ProviderConfigResolver
    provider_factory: ProviderFactory
    database_path: str | Path | None = None
    repository: DailySelectionRepository | None = None
    budget: Any | None = None
    image_cache: DailySelectionImageCache | None = None
    run_id_factory: RunIdFactory | None = None
    plugin_queue: DataCollectionPluginQueue | None = None
    plugin_draft_writer: Any | None = None
    handoff_consumer: DailySelectionHandoffConsumer | None = None
    existing_source_refs: Callable[[str], frozenset[str]] | None = None

    def build_service(
        self,
        *,
        existing_source_refs: Callable[[str], frozenset[str]] | None = None,
    ) -> DailySelectionService:
        repository = self.repository
        if repository is None:
            if self.database_path is None:
                raise ValueError("database_path or repository is required")
            repository = DailySelectionRepository(self.database_path)
        budget = self.budget
        if budget is None:
            database_path = self.database_path or repository.database_path
            if str(database_path) == ":memory:":
                raise ValueError("an in-memory database requires an injected budget")
            budget = UnlimitedApiBudget()
        return DailySelectionService(
            repository=repository,
            budget=budget,
            provider_config_resolver=self.provider_config_resolver,
            provider_factory=self.provider_factory,
            image_cache=self.image_cache,
            run_id_factory=self.run_id_factory,
            existing_source_refs=existing_source_refs,
        )


def register_daily_selection_routes(
    router: APIRouter, dependencies: DailySelectionRouteDependencies
) -> None:
    """Register daily-selection routes on a host-provided router."""
    progress_tracker = DailySelectionProgressTracker()
    plugin_queue = dependencies.plugin_queue
    if plugin_queue is None and dependencies.database_path is not None:
        plugin_queue = DataCollectionPluginQueue(dependencies.database_path)
    plugin_draft_writer = dependencies.plugin_draft_writer
    handoff_consumer = dependencies.handoff_consumer
    if plugin_draft_writer is None and dependencies.database_path is not None:
        # This is an adapter only: product processing remains the owner of
        # its draft table, mapping, de-duplication, and raw payload storage.
        from ..modules.product_processing.infrastructure.assets import ProductProcessingAssets
        from ..modules.product_processing.infrastructure.database import create_database
        from ..modules.product_processing.infrastructure.repository import ProductProcessingRepository
        from ..modules.product_processing.service import ProductProcessingService

        database_path = Path(dependencies.database_path).resolve()
        plugin_draft_writer = ProductProcessingService(
            ProductProcessingRepository(create_database(f"sqlite:///{database_path.as_posix()}")),
            ProductProcessingAssets(database_path.parent / "product-processing-assets"),
        )
    if handoff_consumer is None and plugin_draft_writer is not None:
        from ..modules.product_processing.domain.models import DailySelectionHandoffEnvelope

        def consume_handoffs(
            handoffs: tuple[DailySelectionHandoff, ...],
        ) -> Mapping[str, Any]:
            return plugin_draft_writer.consume_daily_selection_handoffs(
                [
                    DailySelectionHandoffEnvelope.model_validate(
                        handoff.model_dump(mode="python")
                    )
                    for handoff in handoffs
                ]
            )

        handoff_consumer = consume_handoffs

    service = dependencies.build_service(
        existing_source_refs=_draft_pool_source_refs(plugin_draft_writer)
    )

    def actor_dependency(
        actor_value: Any = Depends(dependencies.resolve_actor),
    ) -> DailySelectionActor:
        try:
            return DailySelectionActor.model_validate(actor_value)
        except (ValidationError, TypeError, ValueError) as error:
            # The local runtime currently exposes a small host ``Actor`` with
            # an ``id`` attribute rather than this module's workspace contract.
            # Keep that bridge here so the host does not need Demo user models.
            actor_id = getattr(actor_value, "id", None)
            if isinstance(actor_id, str) and actor_id.strip():
                return DailySelectionActor(
                    actor_id=actor_id,
                    workspace_id=actor_id,
                )
            raise HTTPException(status_code=401, detail="authenticated workspace required") from error

    def run_preview_task(
        task_id: str,
        actor: DailySelectionActor,
        request: dict[str, Any],
        cancel_event: threading.Event | None = None,
    ) -> None:
        def report(
            stage: str,
            progress: int,
            completed: int,
            total: int,
            message: str,
        ) -> None:
            progress_tracker.update(
                task_id,
                stage=stage,
                progress=progress,
                completed=completed,
                total=total,
                message=message,
            )

        try:
            run = service.preview(
                actor=actor,
                request=request,
                progress_callback=report,
                cancel_event=cancel_event,
            )
            if run.status == "cancelled":
                # 中断后仍保留已采集的候选，保存为一次可查看的批次。
                progress_tracker.mark_cancelled(task_id, run_id=run.run_id)
                return
            progress_tracker.complete(task_id, run_id=run.run_id)
            # 结果返回后：先处理「空采集自动重试」（0 候选时按同一 criteria
            # 后台重采），再启动 SKU 缺失项的低频后台补齐。
            service.auto_retry_empty_collection(actor=actor, run_id=run.run_id)
            service.auto_start_sku_repull(actor=actor, run_id=run.run_id)
        except (
            DailySelectionCriteriaError,
            DailySelectionContractError,
            DailySelectionProviderUnavailable,
            ValidationError,
            TypeError,
            ValueError,
        ) as error:
            progress_tracker.fail(task_id, error=str(error))
        except Exception:
            progress_tracker.fail(task_id, error="采集任务执行失败，请稍后重试")

    @router.post(
        "/desktop/daily-selection/preview-tasks",
        response_model=DailySelectionTaskStatus,
        status_code=202,
    )
    def start_preview_task(
        background_tasks: BackgroundTasks,
        request: dict[str, Any] = Body(...),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> DailySelectionTaskStatus:
        task = progress_tracker.create(workspace_id=actor.workspace_id)
        cancel_event = progress_tracker.cancel_event_for(
            task.task_id, workspace_id=actor.workspace_id
        )
        background_tasks.add_task(
            run_preview_task, task.task_id, actor, request, cancel_event
        )
        return task

    @router.get(
        "/desktop/daily-selection/preview-tasks/{task_id}",
        response_model=DailySelectionTaskStatus,
    )
    def preview_task_status(
        task_id: str,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> DailySelectionTaskStatus:
        try:
            return progress_tracker.get(task_id, workspace_id=actor.workspace_id)
        except DailySelectionTaskNotFound as error:
            raise HTTPException(status_code=404, detail="daily-selection task not found") from error

    @router.post(
        "/desktop/daily-selection/preview-tasks/{task_id}/cancel",
        response_model=DailySelectionTaskStatus,
    )
    def cancel_preview_task(
        task_id: str,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> DailySelectionTaskStatus:
        try:
            return progress_tracker.cancel(task_id, workspace_id=actor.workspace_id)
        except DailySelectionTaskNotFound as error:
            raise HTTPException(status_code=404, detail="daily-selection task not found") from error

    @router.post(
        "/desktop/daily-selection/preview",
        response_model=DailySelectionRun,
    )
    def preview(
        background_tasks: BackgroundTasks,
        request: dict[str, Any] = Body(...),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> DailySelectionRun:
        try:
            run = service.preview(actor=actor, request=request)
            # 采集预览不写入草稿池：候选需用户在每日选品页确认入池后才会进入。
            # 空采集（0 候选）自动重试后，再启动 SKU 缺失项补齐。
            background_tasks.add_task(service.auto_retry_empty_collection, actor=actor, run_id=run.run_id)
            background_tasks.add_task(service.auto_start_sku_repull, actor=actor, run_id=run.run_id)
            return run
        except (
            DailySelectionCriteriaError,
            DailySelectionContractError,
            ValidationError,
        ) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except DailySelectionProviderUnavailable as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "PROVIDER_NOT_CONFIGURED",
                    "message": "1688 采集服务尚未配置",
                },
            ) from error

    @router.post(
        "/desktop/daily-selection/preview-from-1688-link",
        response_model=DailySelectionRun,
    )
    def preview_from_1688_link(
        background_tasks: BackgroundTasks,
        request: dict[str, Any] = Body(...),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> DailySelectionRun:
        try:
            run = service.preview_from_1688_link(actor=actor, request=request)
            # 采集预览不写入草稿池：候选需用户在每日选品页确认入池后才会进入。
            background_tasks.add_task(service.auto_start_sku_repull, actor=actor, run_id=run.run_id)
            return run
        except (ValueError, DailySelectionCriteriaError, DailySelectionContractError, ValidationError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except DailySelectionProviderUnavailable as error:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "PROVIDER_NOT_CONFIGURED",
                    "message": "1688 采集服务尚未配置",
                },
            ) from error

    @router.post("/desktop/data-collection/plugin-sessions")
    def create_plugin_session(
        capabilities: dict[str, Any] = Body(default_factory=dict),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        if plugin_queue is None:
            raise HTTPException(status_code=503, detail="plugin queue is unavailable")
        return plugin_queue.create_session(
            actor_id=actor.actor_id,
            workspace_id=actor.workspace_id,
            capabilities=capabilities,
        )

    # Compatibility surface for the delivered v0.1.109 browser connector.
    # It is intentionally limited to the same host-authenticated actor and
    # delegates to the module-owned queue rather than Demo workbench tables.
    @router.post("/plugin/connect")
    def connect_legacy_plugin(
        payload: Mapping[str, Any] = Body(default_factory=dict),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        if plugin_queue is None:
            raise HTTPException(status_code=503, detail="plugin queue is unavailable")
        capabilities = payload.get("capabilities")
        return plugin_queue.create_session(
            actor_id=actor.actor_id,
            workspace_id=actor.workspace_id,
            capabilities=capabilities if isinstance(capabilities, Mapping) else {},
        )

    @router.post("/desktop/data-collection/temu-link/collect", response_model=PluginCommand)
    def queue_temu_link(
        request: TemuLinkCollectRequest,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> PluginCommand:
        if plugin_queue is None:
            raise HTTPException(status_code=503, detail="plugin queue is unavailable")
        try:
            return plugin_queue.queue_temu_link(
                actor_id=actor.actor_id,
                workspace_id=actor.workspace_id,
                session_id=request.session_id,
                source_url=request.source_url,
            )
        except PermissionError as error:
            raise HTTPException(status_code=404, detail="plugin session not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/desktop/data-collection/plugin/poll", response_model=list[PluginCommand])
    def poll_plugin(session_token: str = Query(min_length=1), limit: int = Query(default=10, ge=1, le=50)) -> tuple[PluginCommand, ...]:
        if plugin_queue is None:
            raise HTTPException(status_code=503, detail="plugin queue is unavailable")
        try:
            return plugin_queue.poll(session_token, limit=limit)
        except PermissionError as error:
            raise HTTPException(status_code=401, detail="invalid plugin session") from error

    @router.post("/plugin/poll")
    def poll_legacy_plugin(payload: Mapping[str, Any] = Body(...)) -> Mapping[str, Any]:
        if plugin_queue is None:
            raise HTTPException(status_code=503, detail="plugin queue is unavailable")
        session_token = payload.get("session_token")
        if not isinstance(session_token, str) or not session_token:
            raise HTTPException(status_code=422, detail="session_token is required")
        try:
            commands = plugin_queue.poll(session_token, limit=int(payload.get("limit", 10)))
        except PermissionError as error:
            raise HTTPException(status_code=401, detail="invalid plugin session") from error
        return {
            "commands": [
                {
                    "id": command.command_id,
                    "command_type": command.command_type,
                    "payload": command.payload,
                    "status": command.status,
                }
                for command in commands
            ]
        }

    @router.post("/desktop/data-collection/plugin/results", response_model=PluginCommand)
    def receive_plugin_result(
        request: PluginResultRequest,
        background_tasks: BackgroundTasks,
    ) -> PluginCommand:
        if plugin_queue is None:
            raise HTTPException(status_code=503, detail="plugin queue is unavailable")
        try:
            command = plugin_queue.receive_result(
                session_token=request.session_token,
                command_id=request.command_id,
                status=request.status,
                result=request.result,
            )
            _ingest_temu_link_result(command, request.session_token, background_tasks)
            return command
        except PermissionError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/plugin/result")
    def receive_legacy_plugin_result(
        background_tasks: BackgroundTasks,
        payload: Mapping[str, Any] = Body(...),
    ) -> Mapping[str, Any]:
        if plugin_queue is None:
            raise HTTPException(status_code=503, detail="plugin queue is unavailable")
        try:
            session_token = str(payload["session_token"])
            command_id = int(payload["command_id"])
            status = str(payload["status"])
            result = payload.get("result")
            if not isinstance(result, Mapping):
                raise ValueError("result must be an object")
            command = plugin_queue.receive_result(
                session_token=session_token,
                command_id=command_id,
                status=status,
                result=result,
            )
            _ingest_temu_link_result(command, session_token, background_tasks)
        except KeyError as error:
            raise HTTPException(status_code=422, detail=f"missing {error.args[0]}") from error
        except PermissionError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"ok": True}

    @router.post("/plugin/product-capture/draft")
    def save_plugin_product_draft(
        background_tasks: BackgroundTasks,
        payload: Mapping[str, Any] = Body(...),
    ) -> Mapping[str, Any]:
        if plugin_queue is None or plugin_draft_writer is None:
            raise HTTPException(status_code=503, detail="plugin draft storage is unavailable")
        session_token = payload.get("session_token")
        product = payload.get("product")
        if not isinstance(session_token, str) or not session_token.strip():
            raise HTTPException(status_code=422, detail="session_token is required")
        if not isinstance(product, Mapping):
            raise HTTPException(status_code=422, detail="product must be an object")
        try:
            workspace_id = plugin_queue.workspace_for_session(session_token)
        except PermissionError as error:
            raise HTTPException(status_code=401, detail="invalid plugin session") from error
        draft, created = plugin_draft_writer.create_draft(
            _plugin_product_to_draft(product), workspace_id=workspace_id
        )
        _schedule_source_image_sync(plugin_draft_writer, background_tasks, draft, workspace_id)
        return {
            "ok": True,
            "skipped": not created,
            "draft": draft,
            "draft_id": draft["id"],
        }

    @router.post("/plugin/product-capture/drafts/status")
    def plugin_product_draft_status(payload: Mapping[str, Any] = Body(...)) -> Mapping[str, Any]:
        if plugin_queue is None or plugin_draft_writer is None:
            raise HTTPException(status_code=503, detail="plugin draft storage is unavailable")
        session_token = payload.get("session_token")
        source_refs = payload.get("source_refs")
        if not isinstance(session_token, str) or not session_token.strip():
            raise HTTPException(status_code=422, detail="session_token is required")
        if not isinstance(source_refs, list) or not all(isinstance(item, str) for item in source_refs):
            raise HTTPException(status_code=422, detail="source_refs must be a list of strings")
        try:
            workspace_id = plugin_queue.workspace_for_session(session_token)
        except PermissionError as error:
            raise HTTPException(status_code=401, detail="invalid plugin session") from error
        requested = {_canonical_url(item) for item in source_refs if _canonical_url(item)}
        listed = plugin_draft_writer.list_drafts(
            None, 500, 0, summary=True, workspace_id=workspace_id
        )["drafts"]
        return {"drafts": [draft for draft in listed if draft.get("source_ref") in requested]}

    def _ingest_temu_link_result(
        command: PluginCommand,
        session_token: str,
        background_tasks: BackgroundTasks,
    ) -> None:
        if (
            plugin_draft_writer is None
            or command.command_type != "temu_link_capture"
            or command.status != "succeeded"
        ):
            return
        product = command.result.get("product")
        if not isinstance(product, Mapping):
            return
        try:
            draft_payload = _plugin_product_to_draft(product)
        except HTTPException:
            # The plugin result is retained as command diagnostics, but only a
            # complete, usable product is allowed to enter the draft pool.
            return
        workspace_id = plugin_queue.workspace_for_session(session_token)
        draft, _created = plugin_draft_writer.create_draft(
            draft_payload, workspace_id=workspace_id
        )
        _schedule_source_image_sync(plugin_draft_writer, background_tasks, draft, workspace_id)

    @router.get("/desktop/data-collection/plugin-commands/{command_id}", response_model=PluginCommand)
    def get_plugin_command(command_id: int, actor: DailySelectionActor = Depends(actor_dependency)) -> PluginCommand:
        if plugin_queue is None:
            raise HTTPException(status_code=503, detail="plugin queue is unavailable")
        try:
            return plugin_queue.get_command(actor_id=actor.actor_id, workspace_id=actor.workspace_id, command_id=command_id)
        except PermissionError as error:
            raise HTTPException(status_code=404, detail="plugin command not found") from error

    @router.get(
        "/desktop/daily-selection/runs",
        response_model=list[DailySelectionRunSummary],
    )
    def list_runs(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> tuple[DailySelectionRunSummary, ...]:
        return service.list_runs(actor=actor, limit=limit, offset=offset)

    @router.get(
        "/desktop/daily-selection/runs/{run_id}",
        response_model=DailySelectionRun,
    )
    def get_run(
        run_id: str,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> DailySelectionRun:
        try:
            return service.get_run(actor=actor, run_id=run_id)
        except DailySelectionRunNotFound as error:
            raise _run_not_found(error) from error

    @router.post(
        "/desktop/daily-selection/runs/{run_id}/feedback",
        response_model=DailySelectionFeedback,
    )
    def feedback(
        run_id: str,
        request: DailySelectionFeedbackRequest,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> DailySelectionFeedback:
        try:
            return service.record_feedback(
                actor=actor,
                run_id=run_id,
                candidate_id=request.candidate_id,
                reason=request.reason,
                details=request.details,
            )
        except DailySelectionRunNotFound as error:
            raise _run_not_found(error) from error
        except (DailySelectionContractError, ValueError, TypeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/desktop/daily-selection/runs/{run_id}/confirm",
        response_model=DailySelectionConfirmResult,
    )
    def confirm(
        background_tasks: BackgroundTasks,
        run_id: str,
        request: DailySelectionConfirmRequest,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> DailySelectionConfirmResult:
        try:
            handoffs = service.confirm_candidates(
                actor=actor,
                run_id=run_id,
                candidate_ids=request.candidate_ids,
            )
            if handoff_consumer is None:
                # The handoff is durable and remains pending; a host may inject
                # a dedicated consumer when product processing is deployed.
                return DailySelectionConfirmResult(
                    handoffs=handoffs,
                    selected_count=len(handoffs),
                    created_count=0,
                    replayed_count=0,
                    pending_count=len(handoffs),
                )
            try:
                consumed = handoff_consumer(handoffs)
            except Exception as error:
                # Confirmation has already been committed.  Do not expose a
                # downstream stack trace or discard the durable pending record;
                # the user can retry the same idempotent confirmation later.
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "PRODUCT_PROCESSING_UNAVAILABLE",
                        "message": "产品处理服务暂不可用，确认记录已保留，稍后可重试",
                    },
                ) from error
            # 确认即入池：V1 草稿调度来源图同步，V2 草稿调度统一资产物化。
            drafts = consumed.get("drafts") or []
            for draft in drafts:
                if int(draft.get("media_contract_version") or 1) >= 2:
                    continue
                _schedule_source_image_sync(
                    plugin_draft_writer, background_tasks, draft, actor.workspace_id
                )
            if any(int(d.get("media_contract_version") or 1) >= 2 for d in drafts):
                _schedule_media_materialization(
                    plugin_draft_writer, background_tasks, actor.workspace_id
                )
            acknowledged = service.mark_handoffs_consumed(actor=actor, handoffs=handoffs)
            return DailySelectionConfirmResult(
                handoffs=acknowledged,
                selected_count=len(handoffs),
                created_count=int(consumed.get("created") or 0),
                replayed_count=int(consumed.get("replayed") or 0),
                pending_count=0,
            )
        except DailySelectionRunNotFound as error:
            raise _run_not_found(error) from error
        except DailySelectionCandidateNotConfirmable as error:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CANDIDATE_NOT_CONFIRMABLE",
                    "message": "候选商品当前不可确认入库",
                    "candidates": error.reasons,
                },
            ) from error
        except (DailySelectionContractError, ValueError, TypeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/desktop/daily-selection/runs/{run_id}/sku-repull/start",
        response_model=dict[str, Any],
    )
    def sku_repull_start(
        run_id: str,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            return service.start_sku_repull(actor=actor, run_id=run_id)
        except DailySelectionRunNotFound as error:
            raise _run_not_found(error) from error

    @router.get(
        "/desktop/daily-selection/runs/{run_id}/sku-repull/state",
        response_model=dict[str, Any],
    )
    def sku_repull_state(
        run_id: str,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            return service.get_sku_repull_state(actor=actor, run_id=run_id)
        except DailySelectionRunNotFound as error:
            raise _run_not_found(error) from error

    @router.post(
        "/desktop/daily-selection/runs/{run_id}/sku-repull/cancel",
        response_model=dict[str, Any],
    )
    def sku_repull_cancel(
        run_id: str,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            return service.cancel_sku_repull(actor=actor, run_id=run_id)
        except DailySelectionRunNotFound as error:
            raise _run_not_found(error) from error

    @router.get(
        "/desktop/daily-selection/runs/{run_id}/collection-retry/state",
        response_model=dict[str, Any],
    )
    def collection_retry_state(
        run_id: str,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            return service.get_collection_retry_state(actor=actor, run_id=run_id)
        except DailySelectionRunNotFound as error:
            raise _run_not_found(error) from error

    @router.get("/desktop/daily-selection/image")
    def image(
        run_id: str = Query(min_length=1),
        url: str = Query(min_length=1),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> Response:
        try:
            cached = service.get_image(actor=actor, run_id=run_id, url=url)
        except DailySelectionRunNotFound as error:
            raise _run_not_found(error) from error
        except DailySelectionImageNotFound as error:
            raise HTTPException(status_code=404, detail="daily-selection image not found") from error
        except DailySelectionImageAccessDenied as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        return Response(
            content=cached.content,
            media_type=cached.media_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )


def _run_not_found(error: BaseException) -> HTTPException:
    return HTTPException(status_code=404, detail="daily-selection run not found")


def _plugin_product_to_draft(product: Mapping[str, Any]) -> dict[str, Any]:
    platform = str(product.get("platform") or "temu").strip().casefold() or "temu"
    product_id = str(product.get("product_id") or product.get("source_product_id") or "").strip()
    source_ref = _canonical_url(
        str(product.get("product_link") or product.get("link") or product.get("url") or "")
    )
    title = str(product.get("title") or product.get("product_name") or "").strip()
    if not source_ref or not title:
        raise HTTPException(status_code=422, detail="product title and product_link are required")
    sanitized_product = dict(sanitize_raw_payload(product))
    for key in ("product_link", "link", "url"):
        value = sanitized_product.get(key)
        if isinstance(value, str):
            sanitized_product[key] = _canonical_url(value)
    captured_fields = sanitized_product.get("captured_fields")
    if isinstance(captured_fields, Mapping):
        sanitized_captured_fields = dict(captured_fields)
        capture_url = sanitized_captured_fields.get("capture_url")
        if isinstance(capture_url, str):
            sanitized_captured_fields["capture_url"] = _canonical_url(capture_url)
        sanitized_product["captured_fields"] = sanitized_captured_fields
    images = product.get("image_urls") or product.get("product_image_urls") or []
    source_image_urls = [
        str(value).strip()
        for value in images
        if isinstance(value, str) and _canonical_url(value)
    ] if isinstance(images, (list, tuple)) else []
    image_url = str(
        product.get("image_url") or product.get("imageUrl") or (source_image_urls[0] if source_image_urls else "")
    ).strip()
    candidate_id = f"plugin:{platform}:{product_id or source_ref}"
    weight_text, package_info_text = _plugin_physical_evidence(product)
    source_attributes = _plugin_source_attributes(product)
    source_variant_records = _plugin_variant_records(product, fallback_image_url=image_url)
    shipping_package_records = _plugin_shipping_package_records(product, source_variant_records)
    return {
        **sanitized_product,
        "source_type": "web_manual_capture",
        "source_platform": platform,
        "candidate_id": candidate_id,
        "source_ref": source_ref,
        "product_name": title,
        "title": title,
        "image_url": image_url,
        "source_image_urls": source_image_urls,
        "declared_price": product.get("price"),
        "sku": str(product.get("sku") or "").strip() or None,
        "weight_text": weight_text,
        "package_info_text": package_info_text,
        # Preserve the visible source parameter table under the canonical key
        # consumed by product processing. Explicit table values must beat AI.
        "source_attributes": source_attributes,
        # 插件只回传 variant_combinations；这里把它换算成草稿池/导出使用的
        # 标准 SKU 记录（source_variant_records），保证草稿池能看到完整规格。
        "source_variant_records": source_variant_records,
        # 1688 商品属性中的“商品件重尺”是 SKU 包装物流证据，不是商品本体尺寸。
        # 它保留为逐 SKU 的结构化记录，供预检和导出消费，绝不写入 package_info_text。
        "shipping_package_records": shipping_package_records,
    }


_PLUGIN_WEIGHT_VALUE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(g|克|kg|千克|公斤)?", re.IGNORECASE
)
_PLUGIN_SIZE_TRIPLE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*[xX*×]\s*(\d+(?:\.\d+)?)\s*(cm|mm|厘米|毫米)?",
    re.IGNORECASE,
)
# 「长30宽20高10cm」这类无分隔符的显式轴文本。
_PLUGIN_SIZE_AXISED = re.compile(
    r"长(?:度)?[^\d]{0,4}(\d+(?:\.\d+)?)[^\d]{0,4}宽(?:度)?[^\d]{0,4}(\d+(?:\.\d+)?)[^\d]{0,4}高(?:度)?[^\d]{0,4}(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# 插件回传商品顶层的重量系字段（Temu/1688 详情数据里字段名不稳定）。
_PLUGIN_WEIGHT_KEY_RE = re.compile(r"(重量|毛重|净重|单重|克重|weight|gross|net)", re.IGNORECASE)
_PLUGIN_WEIGHT_TOP_KEYS = (
    # Selected-SKU evidence must win over generic page-level weight fields.
    "employee_action_weight_text", "employee_action_weight_kg",
    "weight_text", "weight_kg", "weight", "item_weight", "itemWeight",
    "gross_weight", "net_weight", "packaging_weight", "package_weight",
    "重量", "毛重", "净重", "克重",
)
_PLUGIN_WEIGHT_KG_KEYS = frozenset({"employee_action_weight_kg", "weight_kg"})


def _plugin_source_attributes(product: Mapping[str, Any]) -> dict[str, Any]:
    """Return the extension's visible parameter table as canonical attributes."""

    candidates: list[Any] = [product.get("source_attributes")]
    for container_key in ("employee_action_validation", "captured_fields", "raw_payload"):
        container = product.get(container_key)
        if isinstance(container, Mapping):
            candidates.extend(
                (
                    container.get("source_attributes"),
                    container.get("source_attribute_pairs"),
                    container.get("source_attribute_table"),
                )
            )
    candidates.extend((product.get("source_attribute_pairs"), product.get("source_attribute_table")))

    attributes: dict[str, Any] = {}
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            for key, value in candidate.items():
                name = str(key or "").strip()
                if name and value not in (None, ""):
                    attributes.setdefault(name, value)
        elif isinstance(candidate, (list, tuple)):
            for item in candidate:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("name") or item.get("key") or item.get("label") or "").strip()
                value = item.get("value")
                if name and value not in (None, ""):
                    attributes.setdefault(name, value)
    return attributes


def _plugin_combos(product: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """插件变种组合列表（variant_combinations / raw_variant_combinations）。"""
    combined = [
        *(product.get("variant_combinations") or []),
        *(product.get("raw_variant_combinations") or []),
    ]
    return [item for item in combined if isinstance(item, Mapping)]


_PACKAGE_RECORD_NUMBER_KEYS = ("length_cm", "width_cm", "height_cm", "weight_g")
_PACKAGE_SPEC_NORMALIZE_RE = re.compile(r"[\s\-_，,;；:：/\\|（）()\[\]【】{}<>《》'\"`]+")
_PACKAGE_TERMINAL_PARENS_RE = re.compile(r"(?:\(|（)([^()（）]+)(?:\)|）)\s*$")


def _normalized_package_specification(value: Any) -> str:
    """Generate a stable, punctuation-insensitive fallback key for a 1688 spec."""
    return _PACKAGE_SPEC_NORMALIZE_RE.sub("", str(value or "").strip()).casefold()


def _positive_package_number(value: Any) -> int | float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return int(number) if number.is_integer() else number


def _package_attribute_combination_from_text(value: Any) -> str:
    pairs: list[str] = []
    for part in re.split(r"[;；|｜、，,]", str(value or "")):
        name, separator, raw_value = part.partition(":")
        if not separator:
            name, separator, raw_value = part.partition("：")
        normalized_name = _normalized_package_specification(name)
        normalized_value = _normalized_package_specification(raw_value)
        if normalized_name and normalized_value:
            pairs.append(f"{normalized_name}:{normalized_value}")
    return "|".join(sorted(pairs)) if pairs else ""


def _package_attribute_values_from_text(value: Any) -> str:
    values: list[str] = []
    for part in re.split(r"[;；|｜、，,]", str(value or "")):
        _name, separator, raw_value = part.partition(":")
        if not separator:
            _name, separator, raw_value = part.partition("：")
        normalized = _normalized_package_specification(raw_value)
        if normalized:
            values.append(normalized)
    return "|".join(sorted(values)) if values else ""


def _package_terminal_parenthesized_values(value: Any) -> str:
    """Read only the final complete parenthesized SKU value(s), never substrings.

    1688 often displays a full product name such as ``201...（黑色）`` rather
    than ``颜色：黑色``. The final wrapper is an exact SKU-value candidate; a
    different value such as ``深红色`` remains distinct from ``红色``.
    """
    match = _PACKAGE_TERMINAL_PARENS_RE.search(str(value or ""))
    if not match:
        return ""
    values = [
        _normalized_package_specification(part)
        for part in re.split(r"[;；|｜、，,/]", match.group(1))
    ]
    return "|".join(sorted(value for value in values if value))


def _strict_package_variant_keys(record: Mapping[str, Any]) -> set[str]:
    """Mirror the plugin's exact spec/attribute keys; never use substrings."""
    keys: set[str] = set()
    specification = str(record.get("spec_text") or record.get("specification") or "")
    normalized_specification = _normalized_package_specification(specification)
    if normalized_specification:
        keys.add(f"spec:{normalized_specification}")
    text_attributes = _package_attribute_combination_from_text(specification)
    if text_attributes:
        keys.add(f"attributes:{text_attributes}")
    text_values = _package_attribute_values_from_text(specification)
    if text_values:
        keys.add(f"attribute_values:{text_values}")
    terminal_values = _package_terminal_parenthesized_values(specification)
    if terminal_values:
        keys.add(f"attribute_values:{terminal_values}")
    attributes = record.get("attributes") or {}
    if isinstance(attributes, Mapping):
        entries = [
            (_normalized_package_specification(name), _normalized_package_specification(value))
            for name, value in attributes.items()
        ]
        entries = [(name, value) for name, value in entries if name and value]
        if entries:
            keys.add("attributes:" + "|".join(sorted(f"{name}:{value}" for name, value in entries)))
            keys.add("attribute_values:" + "|".join(sorted(value for _name, value in entries)))
    return keys


def _package_variant_match(
    package_record: Mapping[str, Any],
    variants: list[dict[str, Any]],
    used_variant_keys: set[str],
) -> dict[str, Any] | None:
    """Match one package row to one source SKU: stable ids first, visible spec second."""
    # The plugin has already made a deliberately conservative no-match decision.
    # Never reinterpret it on the server (for example 深红色 must not bind 红色).
    if str(package_record.get("match_status") or "").strip().casefold() == "unmatched":
        return None
    explicit_ids = {
        str(package_record.get(key) or "").strip()
        for key in ("variant_key", "variant_sku_id", "source_sku_id", "sku_id")
    }
    explicit_ids.discard("")
    if explicit_ids:
        for variant in variants:
            key = str(variant.get("sku_id") or "").strip()
            source_key = str(variant.get("source_sku_id") or "").strip()
            if key not in used_variant_keys and explicit_ids.intersection({key, source_key}):
                return variant

    package_keys = _strict_package_variant_keys(package_record)
    if not package_keys:
        return None
    candidates: list[dict[str, Any]] = []
    for variant in variants:
        variant_key = str(variant.get("sku_id") or "").strip()
        if not variant_key or variant_key in used_variant_keys:
            continue
        # Exact normalized spec or exact complete attribute combination only.
        if _strict_package_variant_keys(variant).intersection(package_keys):
            candidates.append(variant)
    return candidates[0] if len(candidates) == 1 else None


def _plugin_shipping_package_records(
    product: Mapping[str, Any], variants: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate 1688 商品件重尺 rows and bind them one-to-one to source variants.

    Rows that cannot be matched remain visible to the operator, but deliberately
    have no variant key and therefore cannot affect SKU export.
    """
    raw_records = product.get("shipping_package_records") or []
    if not isinstance(raw_records, (list, tuple)):
        return []
    normalized: list[dict[str, Any]] = []
    used_variant_keys: set[str] = set()
    used_record_keys: set[str] = set()
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, Mapping):
            continue
        values = {key: _positive_package_number(raw.get(key)) for key in _PACKAGE_RECORD_NUMBER_KEYS}
        if any(value is None for value in values.values()):
            continue
        specification = str(raw.get("specification") or raw.get("spec_text") or "").strip()
        if not specification:
            continue
        base_record_key = _normalized_package_specification(raw.get("variant_key")) or _normalized_package_specification(specification) or f"package-{index}"
        record_key = base_record_key
        suffix = 2
        while record_key in used_record_keys:
            record_key = f"{base_record_key}#{suffix}"
            suffix += 1
        record: dict[str, Any] = {
            "record_key": record_key,
            "specification": specification,
            **values,
            "source": "1688_product_pack_info",
        }
        record["variant_key"] = record["record_key"]
        volume = _positive_package_number(raw.get("volume_cm3"))
        if volume is not None:
            record["volume_cm3"] = volume
        variant = _package_variant_match(raw, variants, used_variant_keys)
        if variant is None:
            record["match_status"] = "unmatched"
        else:
            variant_sku_id = str(variant.get("sku_id") or "").strip()
            record["record_key"] = variant_sku_id
            record["variant_key"] = variant_sku_id
            record["variant_sku_id"] = variant_sku_id
            record["match_status"] = "matched"
            used_variant_keys.add(variant_sku_id)
            # Keep a source copy on the SKU so result transformations that only
            # preserve source_variant_records still retain the package evidence.
            variant["shipping_package"] = dict(record)
        used_record_keys.add(str(record["record_key"]))
        normalized.append(record)
    return normalized


def _plugin_physical_evidence(
    product: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    """从插件回传商品提取重量/尺寸原始证据，归一到草稿池标准字段。

    插件在页面详情数据（detailData / skuInfoMap 等）里能抓到商品件重/包装重量，
    字段名不稳定（weight_text / weight_kg / weight / itemWeight / 毛重…），也
    可能只出现在 SKU 组合里。这里统一收集第一份可用证据并归一成
    ``weight_text`` / ``package_info_text``，让下游
    ``_extract_deterministic_size`` 能确定性解析而非走 AI 估值。
    """
    # #productPackInfo 的“选中 SKU 件重”只服务于物流包装表。它既不是
    # 商品级重量，也不能参与商品本体尺寸推导。
    if _is_1688_selected_package_weight(product):
        return None, None
    weight_text: str | None = None
    # Older extension versions nested the selected-SKU evidence here.
    employee_action = product.get("employee_action_validation")
    if isinstance(employee_action, Mapping):
        employee_text = str(employee_action.get("weight_text") or "").strip()
        employee_kg = employee_action.get("weight_kg")
        if employee_text:
            weight_text = employee_text[:80]
        elif employee_kg not in (None, ""):
            match = _PLUGIN_WEIGHT_VALUE.search(str(employee_kg).strip())
            if match and float(match.group(1)) > 0:
                weight_text = f"重量 {match.group(1)}kg"
    for key in _PLUGIN_WEIGHT_TOP_KEYS:
        if weight_text is not None:
            break
        value = product.get(key)
        if value in (None, ""):
            continue
        text = str(value).strip()
        if not text:
            continue
        if key == "weight_text":
            weight_text = text[:80]
            break
        if key in _PLUGIN_WEIGHT_KG_KEYS:
            match = _PLUGIN_WEIGHT_VALUE.search(text)
            if match and float(match.group(1)) > 0:
                weight_text = f"重量 {match.group(1)}kg"
            break
        match = _PLUGIN_WEIGHT_VALUE.fullmatch(text)
        if match and float(match.group(1)) > 0:
            # 保留原始数字文本（25 而非 25.0），单位缺失时按克约定。
            weight_text = f"重量 {match.group(1)}{(match.group(2) or '').casefold() or 'g'}"
        break
    if weight_text is None:
        # A labelled parameter-table value is authoritative structured evidence.
        for key, value in _plugin_source_attributes(product).items():
            if not _PLUGIN_WEIGHT_KEY_RE.search(str(key)):
                continue
            match = _PLUGIN_WEIGHT_VALUE.search(str(value).strip())
            if match and float(match.group(1)) > 0:
                unit = (match.group(2) or "g").casefold()
                weight_text = f"重量 {match.group(1)}{unit}"
                break
    if weight_text is None:
        # Newer extensions already send weighted canonical variant records.
        records = product.get("source_variant_records") or []
        if isinstance(records, (list, tuple)):
            ordered = [item for item in records if isinstance(item, Mapping) and item.get("selected")]
            ordered.extend(item for item in records if isinstance(item, Mapping) and not item.get("selected"))
            for record in ordered:
                record_text = str(record.get("weight_text") or "").strip()
                record_kg = record.get("weight_kg")
                if record_text:
                    weight_text = record_text[:80]
                    break
                if record_kg not in (None, ""):
                    match = _PLUGIN_WEIGHT_VALUE.search(str(record_kg).strip())
                    if match and float(match.group(1)) > 0:
                        weight_text = f"重量 {match.group(1)}kg"
                        break
    if weight_text is None:
        # SKU 组合兜底：插件详情数据把件重放在 skuInfoMap 的每个 SKU 里。
        for combo in _plugin_combos(product):
            combo_text = str(combo.get("weight_text") or "").strip()
            if combo_text:
                weight_text = combo_text[:80]
                break
            combo_kg = combo.get("weight_kg")
            if combo_kg in (None, ""):
                continue
            match = _PLUGIN_WEIGHT_VALUE.search(str(combo_kg).strip())
            if match and float(match.group(1)) > 0:
                weight_text = f"重量 {match.group(1)}kg"
                break
    return weight_text, _plugin_size_evidence(product)


def _is_1688_selected_package_weight(product: Mapping[str, Any]) -> bool:
    source = str(product.get("weight_source") or "").strip()
    if source == "1688_product_pack_info_selected_sku":
        return True
    employee_action = product.get("employee_action_validation")
    return isinstance(employee_action, Mapping) and str(
        employee_action.get("weight_source") or ""
    ).strip() == "1688_product_pack_info_selected_sku"


def _plugin_size_evidence(product: Mapping[str, Any]) -> str | None:
    """提取尺寸/包装文本（长x宽x高），优先商品级，其次 SKU 属性值。"""
    package_text = str(
        product.get("package_info_text") or product.get("package_info") or ""
    ).strip()
    for source in (
        [str(combo.get("spec_text") or "") for combo in _plugin_combos(product)]
        + [str(value) for value in product.values() if isinstance(value, str)]
    ):
        if not source:
            continue
        match = _PLUGIN_SIZE_TRIPLE.search(source)
        if match:
            unit = (match.group(4) or "cm").casefold()
            return f"{match.group(1)}x{match.group(2)}x{match.group(3)}{unit}"
        match = _PLUGIN_SIZE_AXISED.search(source)
        if match:
            return f"{match.group(1)}x{match.group(2)}x{match.group(3)}cm"
    return package_text or None


_NOISE_DIMENSION_RE = re.compile(
    r"数量|库存|起批|已选|价格|运费|快递|包邮|件数|优惠|小计|合计|剩余|remain|stock|quantity|count|price|freight|shipping",
    re.I,
)
_DIMENSION_SYNONYMS: dict[str, str] = {
    "颜色": "颜色",
    "color": "颜色",
    "colour": "颜色",
    "尺码": "尺码",
    "尺寸": "尺码",
    "大小": "尺码",
    "size": "尺码",
    "容量": "容量",
    "capacity": "容量",
    "款式": "款式",
    "型号": "款式",
    "style": "款式",
    "model": "款式",
    "规格": "规格",
    "spec": "规格",
    "包装": "包装",
    "套装": "套装",
    "pack": "包装",
}


def _canonical_dimension_name(name: object) -> str:
    """把插件/平台的规格维度名归一到中文字段名，识别不了时保留原名。"""
    raw = str(name or "").strip().lower()
    if not raw:
        return "规格"
    normalized = _DIMENSION_SYNONYMS.get(raw)
    if normalized is not None:
        return normalized
    if "颜色" in raw or "colour" in raw or "color" in raw:
        return "颜色"
    if any(token in raw for token in ("尺码", "尺寸", "大小")) or "size" in raw:
        return "尺码"
    if "容量" in raw or "capacity" in raw:
        return "容量"
    if "款式" in raw or "型号" in raw or "style" in raw or "model" in raw:
        return "款式"
    if "规格" in raw or "spec" in raw:
        return "规格"
    return str(name or "规格").strip()


def _plugin_group_values(group: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把插件规格组的 values 归一成 ``{value, image_url, sku}`` 条目。"""
    values = group.get("values") or []
    if not isinstance(values, (list, tuple)):
        return []
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, Mapping):
            value = str(item.get("value") or item.get("text") or item.get("name") or "").strip()
            image_url = str(item.get("image_url") or item.get("imageUrl") or "").strip()
            sku = str(item.get("source_sku_id") or item.get("sku") or "").strip()
        else:
            value = str(item).strip()
            image_url = ""
            sku = ""
        if not value or len(value) > 80:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"value": value, "image_url": image_url, "sku": sku})
    return cleaned


def _plugin_combo_attributes(combo: Mapping[str, Any]) -> dict[str, str]:
    attributes = combo.get("attributes")
    if isinstance(attributes, Mapping):
        return {
            _canonical_dimension_name(key): str(value).strip()
            for key, value in attributes.items()
            if str(value).strip()
        }
    return {}


def _plugin_variant_records(
    product: Mapping[str, Any], *, fallback_image_url: str = ""
) -> list[dict[str, Any]]:
    """Derive draft-pool SKU records from plugin ``variant_combinations``.

    1688 采集插件在页面上只产出 ``variant_combinations``（每组含属性组合、
    SKU 货号、价格、库存、规格图），不产出标准 ``source_variant_records``。
    这里把组合换算成与 OneBound 详情一致的记录结构，缺失货号时用
    ``{product_id}:{index}`` 兜底，保证每个规格都可独立展示与编辑。

    TEMU 前端采集存在系统性缺陷：JSON 里的 SKU 模型常把规格拆成逐项
    （每个颜色一条、尺码/数量单独一条），导致变种数明显少于真实规格。
    页面可见的规格维度（``variant_groups``，颜色多值 + 尺码等）才是完整
    维度来源。因此只要规格组可用且比组合更能反映真实规格，就按规格组
    笛卡尔积重建记录，并用组合里的价格/库存/货号回填。
    """
    product_id = str(product.get("product_id") or product.get("source_product_id") or "").strip()
    platform = str(product.get("platform") or product.get("source_platform") or "").strip()
    combos = product.get("variant_combinations") or product.get("raw_variant_combinations") or []
    combos = [item for item in combos if isinstance(item, Mapping)] if isinstance(combos, (list, tuple)) else []
    groups = product.get("variant_groups") or product.get("raw_variant_groups") or []
    groups = [item for item in groups if isinstance(item, Mapping)] if isinstance(groups, (list, tuple)) else []

    combo_records = _plugin_records_from_combos(
        combos,
        product_id,
        platform=platform,
        product_currency=product.get("currency"),
        fallback_image_url=fallback_image_url,
    )
    group_records = _plugin_records_from_groups(groups, combos, product_id)
    if not group_records:
        return combo_records

    combos_dim = max((len(_plugin_combo_attributes(combo)) for combo in combos), default=0)
    distinct_combo_sets = {
        tuple(sorted(_plugin_combo_attributes(combo).items()))
        for combo in combos
        if _plugin_combo_attributes(combo)
    }
    # 规格组更值得信赖：组数比组合维度多（组合丢了尺码等维度），或
    # 组合是“逐项扁平”形态且去重后的变种数不高于规格组笛卡尔积。
    group_ok = (
        len(groups) > combos_dim
        or (len(groups) == combos_dim and len(group_records) >= max(1, len(distinct_combo_sets)))
    )
    if not group_ok:
        return combo_records
    return group_records


def _plugin_records_from_combos(
    combos: list[Mapping[str, Any]],
    product_id: str,
    *,
    platform: str = "",
    product_currency: Any = None,
    fallback_image_url: str = "",
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    used_sku_ids: dict[str, str] = {}
    for index, combo in enumerate(combos):
        if not isinstance(combo, Mapping):
            continue
        source_sku_id = str(
            combo.get("source_sku_id")
            or combo.get("sourceSkuId")
            or combo.get("sku_id")
            or combo.get("sku")
            or ""
        ).strip()
        attributes = _plugin_combo_attributes(combo)
        attribute_identity = json.dumps(attributes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sku_id = source_sku_id or f"{product_id or 'plugin'}:variant-{index}"
        if sku_id in used_sku_ids and used_sku_ids[sku_id] != attribute_identity:
            suffix = hashlib.sha1(attribute_identity.encode("utf-8")).hexdigest()[:10]
            sku_id = f"{sku_id}:{suffix}"
        used_sku_ids[sku_id] = attribute_identity
        source_price = _plugin_decimal(combo.get("price"))
        source_currency = _plugin_currency(
            combo.get("currency") or product_currency,
            combo.get("price"),
            platform,
        )
        price_cny = source_price if source_currency == "CNY" else None
        dedupe_key = f"{source_sku_id}|{attribute_identity}|{source_price}|{source_currency}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        records.append(
            {
                "sku_id": sku_id,
                "source_sku_id": source_sku_id or None,
                "attributes": attributes,
                "spec_text": str(combo.get("spec_text") or combo.get("properties_name") or "") or None,
                # 尺码、数量等纯文字规格没有单独图片时，SKU 仍代表同一个商品，
                # 因此复用已验证的商品主图。若页面提供了颜色/图案专属图，始终优先它。
                "image_url": str(
                    combo.get("image_url") or combo.get("imageUrl") or fallback_image_url or ""
                ) or None,
                "price_cny": price_cny,
                "source_price": source_price,
                "source_currency": source_currency,
                "quantity": _plugin_int(combo.get("stock") or combo.get("quantity") or combo.get("inventory")),
                "weight_text": str(combo.get("weight_text") or "").strip() or None,
                "weight_kg": combo.get("weight_kg"),
            }
        )
    return records


def _plugin_records_from_groups(
    groups: list[Mapping[str, Any]],
    combos: list[Mapping[str, Any]],
    product_id: str,
) -> list[dict[str, Any]]:
    """按规格组笛卡尔积重建 SKU 记录，并用组合里的价格/库存/货号回填。"""
    axes: list[tuple[str, list[dict[str, Any]]]] = []
    seen_axes: set[str] = set()
    for group in groups:
        name = _canonical_dimension_name(group.get("name") or group.get("source_name") or "")
        if _NOISE_DIMENSION_RE.search(name):
            continue
        values = _plugin_group_values(group)
        if not values:
            continue
        if name in seen_axes:
            continue
        seen_axes.add(name)
        axes.append((name, values))
    if not axes or all(len(values) < 2 for _, values in axes):
        return []
    if len(axes) == 1 and len(axes[0][1]) < 2:
        return []

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    index = 0
    for combination in _dimension_cartesian(axes):
        if len(records) >= 60:
            break
        attributes = {name: value for name, value in combination}
        attr_key = tuple(sorted(attributes.items()))
        matched = _plugin_best_combo_for_attributes(combos, attributes)
        if matched is not None:
            sku_id = str(
                matched.get("source_sku_id")
                or matched.get("sourceSkuId")
                or matched.get("sku_id")
                or matched.get("sku")
                or ""
            ).strip()
            price_cny = _plugin_decimal(matched.get("price"))
            quantity = _plugin_int(matched.get("stock") or matched.get("quantity") or matched.get("inventory"))
            image_url = str(matched.get("image_url") or matched.get("imageUrl") or "").strip() or None
        else:
            sku_id = ""
            price_cny = None
            quantity = None
            image_url = None
        if not sku_id:
            sku_id = f"{product_id or 'plugin'}:variant-{index}"
        if attr_key in seen:
            index += 1
            continue
        seen.add(attr_key)
        spec_parts: list[str] = []
        for axis_index, (name, value) in enumerate(combination):
            axis_values = axes[axis_index][1]
            value_index = next(
                (item_index for item_index, item in enumerate(axis_values) if item["value"] == value),
                axis_index,
            )
            spec_parts.append(f"{axis_index}:{value_index}:{name}:{value}")
        records.append(
            {
                "sku_id": sku_id,
                "attributes": attributes,
                "spec_text": ";".join(spec_parts) or None,
                "image_url": image_url,
                "price_cny": price_cny,
                "quantity": quantity,
            }
        )
        index += 1
    return records


def _dimension_cartesian(axes: list[tuple[str, list[dict[str, Any]]]]):
    """迭代规格组所有取值组合，产出 ``((name, value), ...)``。"""
    result: list[tuple[tuple[str, str], ...]] = [()]
    for name, values in axes:
        result = [
            (*prefix, (name, item["value"]))
            for prefix in result
            for item in values
        ]
    return result


def _plugin_best_combo_for_attributes(
    combos: list[Mapping[str, Any]],
    attributes: Mapping[str, str],
) -> Mapping[str, Any] | None:
    """按属性子集匹配组合，返回覆盖最多的那个（用于回填价格/库存/货号）。"""
    best: Mapping[str, Any] | None = None
    best_score = 0
    target = {_canonical_dimension_name(k): str(v).strip() for k, v in attributes.items()}
    for combo in combos:
        combo_attrs = _plugin_combo_attributes(combo)
        if not combo_attrs:
            continue
        score = sum(
            1
            for name, value in combo_attrs.items()
            if name in target and value.casefold() == target[name].casefold()
        )
        if score > best_score:
            best_score = score
            best = combo
    return best if best_score > 0 else None


def _plugin_currency(value: object, price: object, platform: str) -> str:
    normalized = str(value or "").strip().upper().replace(" ", "")
    aliases = {"RMB": "CNY", "￥": "CNY", "¥": "CNY", "US$": "USD", "$": "USD", "CA$": "CAD", "C$": "CAD"}
    if normalized in aliases:
        return aliases[normalized]
    if normalized in {"CNY", "USD", "CAD", "EUR", "GBP", "AUD"}:
        return normalized
    raw_price = str(price or "").strip().upper()
    if raw_price.startswith(("CA$", "C$", "CAD")):
        return "CAD"
    if raw_price.startswith(("US$", "USD", "$")):
        return "USD"
    if raw_price.startswith(("¥", "￥", "CNY", "RMB")):
        return "CNY"
    return "USD" if platform.lower() == "temu" else "CNY"


def _plugin_decimal(value: object) -> float | None:
    """Parse a plugin money value (may include currency or a price range)."""
    if value is None:
        return None
    text = str(value).strip()
    upper = text.upper()
    for prefix in ("CA$", "US$", "C$", "CAD", "USD", "CNY", "RMB", "EUR", "GBP", "AUD"):
        if upper.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    text = text.lstrip("¥￥$€£ \t\n")
    # 区间价如 "1.5-3.5" 取最小值，与 OneBound 详情价语义一致。
    for separator in ("-", "~", "至", "—"):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
            break
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _plugin_int(value: object) -> int | None:
    if value is None:
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    try:
        return int(digits)
    except (TypeError, ValueError):
        return None


def _schedule_source_image_sync(
    draft_writer: Any,
    background_tasks: BackgroundTasks | None,
    draft: Mapping[str, Any],
    workspace_id: str,
) -> None:
    if background_tasks is not None:
        # The task is scheduled only after create_draft has committed its
        # draft and source-image rows. Failures leave rows retryable.
        background_tasks.add_task(
            draft_writer.sync_draft_source_images,
            int(draft["id"]),
            workspace_id,
        )


def _schedule_media_materialization(
    draft_writer: Any,
    background_tasks: BackgroundTasks | None,
    workspace_id: str,
) -> None:
    if background_tasks is not None:
        # V2 assets are registered inside the same transaction that created the
        # draft, so they are safe to materialize immediately after commit.
        background_tasks.add_task(
            draft_writer.media_assets.materialize_until_idle,
            workspace_id=workspace_id,
        )


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _draft_pool_source_refs(
    plugin_draft_writer: Any,
) -> Callable[[str], frozenset[str]] | None:
    """构造采集去重用的草稿池查询：返回某工作区已入池商品的归一化链接集合。

    草稿池存在即视为「已入池」（含已处理完成），采集完成后据此剔除重复商品。
    查询失败时返回空集（fail-open）：宁可不去重也不让采集流程失败。
    """
    if plugin_draft_writer is None:
        return None

    def resolve(workspace_id: str) -> frozenset[str]:
        try:
            refs: set[str] = set()
            offset = 0
            while True:
                page = plugin_draft_writer.list_drafts(
                    None, 500, offset, summary=True, workspace_id=workspace_id
                )
                drafts = page.get("drafts") or []
                for draft in drafts:
                    ref = _canonical_url(str(draft.get("source_ref") or ""))
                    if ref:
                        refs.add(ref)
                if len(drafts) < 500:
                    break
                offset += 500
            return frozenset(refs)
        except Exception:
            return frozenset()

    return resolve


__all__ = [
    "CachedDailySelectionImage",
    "DailySelectionActor",
    "DailySelectionRouteDependencies",
    "register_daily_selection_routes",
    "PluginOneBoundCaptureDependencies",
    "PluginOneBoundCaptureService",
    "register_plugin_onebound_capture_routes",
]
