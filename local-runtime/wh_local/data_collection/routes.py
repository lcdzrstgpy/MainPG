"""FastAPI routes registered without importing a host application."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .budget import TaskApiBudget
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

    def build_service(self) -> DailySelectionService:
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
            budget = TaskApiBudget()
        return DailySelectionService(
            repository=repository,
            budget=budget,
            provider_config_resolver=self.provider_config_resolver,
            provider_factory=self.provider_factory,
            image_cache=self.image_cache,
            run_id_factory=self.run_id_factory,
        )


def register_daily_selection_routes(
    router: APIRouter, dependencies: DailySelectionRouteDependencies
) -> None:
    """Register six routes on a host-provided router."""
    service = dependencies.build_service()
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
        # 插件只回传 variant_combinations；这里把它换算成草稿池/导出使用的
        # 标准 SKU 记录（source_variant_records），保证草稿池能看到完整规格。
        "source_variant_records": _plugin_variant_records(product),
    }


def _plugin_variant_records(product: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Derive draft-pool SKU records from plugin ``variant_combinations``.

    1688 采集插件在页面上只产出 ``variant_combinations``（每组含属性组合、
    SKU 货号、价格、库存、规格图），不产出标准 ``source_variant_records``。
    这里把组合换算成与 OneBound 详情一致的记录结构，缺失货号时用
    ``{product_id}:{index}`` 兜底，保证每个规格都可独立展示与编辑。
    """
    combos = product.get("variant_combinations") or product.get("raw_variant_combinations") or []
    if not isinstance(combos, (list, tuple)):
        return []
    product_id = str(product.get("product_id") or product.get("source_product_id") or "").strip()
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, combo in enumerate(combos):
        if not isinstance(combo, Mapping):
            continue
        sku_id = str(
            combo.get("source_sku_id")
            or combo.get("sourceSkuId")
            or combo.get("sku_id")
            or combo.get("sku")
            or ""
        ).strip()
        if not sku_id:
            sku_id = f"{product_id or 'plugin'}:variant-{index}"
        attributes = combo.get("attributes")
        attributes = {str(key): value for key, value in attributes.items()} if isinstance(attributes, Mapping) else {}
        price_cny = _plugin_decimal(combo.get("price"))
        dedupe_key = f"{sku_id}|{price_cny}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        records.append(
            {
                "sku_id": sku_id,
                "attributes": attributes,
                "spec_text": str(combo.get("spec_text") or combo.get("properties_name") or "") or None,
                "image_url": str(combo.get("image_url") or combo.get("imageUrl") or "") or None,
                "price_cny": price_cny,
                "quantity": _plugin_int(combo.get("stock") or combo.get("quantity") or combo.get("inventory")),
            }
        )
    return records


def _plugin_decimal(value: object) -> float | None:
    """Parse a plugin money value (may include currency or a price range)."""
    if value is None:
        return None
    text = str(value).strip().lstrip("¥￥$€ \t\n")
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
            draft_writer.media_assets.materialize_pending,
            workspace_id=workspace_id,
        )


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


__all__ = [
    "CachedDailySelectionImage",
    "DailySelectionActor",
    "DailySelectionRouteDependencies",
    "register_daily_selection_routes",
]
