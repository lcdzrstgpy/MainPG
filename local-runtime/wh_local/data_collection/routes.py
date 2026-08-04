"""FastAPI routes registered without importing a host application."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .budget import SQLiteDailyApiBudget
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
            budget = SQLiteDailyApiBudget(database_path)
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

    def actor_dependency(
        actor_value: Any = Depends(dependencies.resolve_actor),
    ) -> DailySelectionActor:
        try:
            return DailySelectionActor.model_validate(actor_value)
        except (ValidationError, TypeError, ValueError) as error:
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


__all__ = [
    "CachedDailySelectionImage",
    "DailySelectionActor",
    "DailySelectionRouteDependencies",
    "register_daily_selection_routes",
]
