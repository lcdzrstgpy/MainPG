"""FastAPI routes registered without importing a host application."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .budget import TaskApiBudget
from .contracts import DailySelectionContractError
from .criteria import DailySelectionCriteriaError
from .repository import (
    DailySelectionFeedback,
    DailySelectionRepository,
    DailySelectionRun,
    DailySelectionRunNotFound,
    DailySelectionRunSummary,
)
from .handoff import DailySelectionHandoff
from .plugin_queue import DataCollectionPluginQueue, PluginCommand
from .service import (
    CachedDailySelectionImage,
    DailySelectionActor,
    DailySelectionImageAccessDenied,
    DailySelectionImageCache,
    DailySelectionImageNotFound,
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
    status: str = Field(pattern="^(running|succeeded|failed)$")
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
        request: dict[str, Any] = Body(...),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> DailySelectionRun:
        try:
            return service.preview(actor=actor, request=request)
        except (
            DailySelectionCriteriaError,
            DailySelectionContractError,
            ValidationError,
        ) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post(
        "/desktop/daily-selection/preview-from-1688-link",
        response_model=DailySelectionRun,
    )
    def preview_from_1688_link(
        request: dict[str, Any] = Body(...),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> DailySelectionRun:
        try:
            return service.preview_from_1688_link(actor=actor, request=request)
        except (ValueError, DailySelectionCriteriaError, DailySelectionContractError, ValidationError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

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
    def receive_plugin_result(request: PluginResultRequest) -> PluginCommand:
        if plugin_queue is None:
            raise HTTPException(status_code=503, detail="plugin queue is unavailable")
        try:
            return plugin_queue.receive_result(
                session_token=request.session_token,
                command_id=request.command_id,
                status=request.status,
                result=request.result,
            )
        except PermissionError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.post("/plugin/result")
    def receive_legacy_plugin_result(payload: Mapping[str, Any] = Body(...)) -> Mapping[str, Any]:
        if plugin_queue is None:
            raise HTTPException(status_code=503, detail="plugin queue is unavailable")
        try:
            session_token = str(payload["session_token"])
            command_id = int(payload["command_id"])
            status = str(payload["status"])
            result = payload.get("result")
            if not isinstance(result, Mapping):
                raise ValueError("result must be an object")
            plugin_queue.receive_result(
                session_token=session_token,
                command_id=command_id,
                status=status,
                result=result,
            )
        except KeyError as error:
            raise HTTPException(status_code=422, detail=f"missing {error.args[0]}") from error
        except PermissionError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"ok": True}

    @router.post("/plugin/product-capture/draft")
    def save_plugin_product_draft(payload: Mapping[str, Any] = Body(...)) -> Mapping[str, Any]:
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
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> tuple[DailySelectionRunSummary, ...]:
        return service.list_runs(actor=actor)

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
        response_model=list[DailySelectionHandoff],
    )
    def confirm(
        run_id: str,
        request: DailySelectionConfirmRequest,
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> tuple[DailySelectionHandoff, ...]:
        try:
            return service.confirm_candidates(
                actor=actor,
                run_id=run_id,
                candidate_ids=request.candidate_ids,
            )
        except DailySelectionRunNotFound as error:
            raise _run_not_found(error) from error
        except (DailySelectionContractError, ValueError, TypeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

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
    sanitized_product = dict(product)
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
    image_url = str(
        product.get("image_url") or product.get("imageUrl") or (images[0] if isinstance(images, list) and images else "")
    ).strip()
    candidate_id = f"plugin:{platform}:{product_id or source_ref}"
    return {
        **sanitized_product,
        "source_type": "plugin_capture",
        "candidate_id": candidate_id,
        "source_ref": source_ref,
        "product_name": title,
        "title": title,
        "image_url": image_url,
        "declared_price": product.get("price"),
        "sku": str(product.get("sku") or "").strip() or None,
    }


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
