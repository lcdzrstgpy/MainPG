"""FastAPI adapter for persistent whole-shop collection."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .service import DailySelectionActor
from .shop_contracts import ShopBatch, ShopBatchItemPage, ShopBatchPage
from .shop_repository import ActiveShopBatchExists, ShopBatchNotFound, ShopCollectionRepository
from .shop_service import (
    ShopCollectionConflict,
    ShopCollectionInputError,
    ShopCollectionProviderUnavailable,
    ShopCollectionService,
)


class CreateShopBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_input: str = Field(min_length=1, max_length=4096)

    @field_validator("source_input", mode="before")
    @classmethod
    def strip_source(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


@dataclass(frozen=True)
class ShopCollectionRouteDependencies:
    resolve_actor: Callable[..., Any]
    database_path: str | Path
    provider_config_resolver: Callable[[DailySelectionActor], Mapping[str, Any]]
    worker: Any
    repository: ShopCollectionRepository | None = None

    def build_service(self) -> ShopCollectionService:
        return ShopCollectionService(
            repository=self.repository or ShopCollectionRepository(self.database_path),
            provider_config_resolver=self.provider_config_resolver,
            worker=self.worker,
        )


def create_shop_collection_router(dependencies: ShopCollectionRouteDependencies) -> APIRouter:
    router = APIRouter(prefix="/desktop/data-collection/shop-batches", tags=["shop-collection"])
    service = dependencies.build_service()

    def actor_dependency(value: Any = Depends(dependencies.resolve_actor)) -> DailySelectionActor:
        try:
            return DailySelectionActor.model_validate(value)
        except (ValidationError, TypeError, ValueError) as error:
            actor_id = getattr(value, "actor_id", None) or getattr(value, "id", None)
            workspace_id = getattr(value, "workspace_id", None)
            if isinstance(actor_id, str) and actor_id.strip() and isinstance(workspace_id, str) and workspace_id.strip():
                return DailySelectionActor(actor_id=actor_id, workspace_id=workspace_id)
            raise HTTPException(status_code=401, detail="authenticated workspace required") from error

    @router.post("", response_model=ShopBatch, status_code=status.HTTP_202_ACCEPTED)
    def create_batch(request: CreateShopBatchRequest, actor: DailySelectionActor = Depends(actor_dependency)) -> ShopBatch:
        try:
            return service.create_batch(actor=actor, source_input=request.source_input)
        except ShopCollectionInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ActiveShopBatchExists as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ShopCollectionProviderUnavailable as error:
            raise HTTPException(
                status_code=503,
                detail={"code": "PROVIDER_NOT_CONFIGURED", "message": "1688 采集服务暂不可用"},
            ) from error

    @router.get("", response_model=ShopBatchPage)
    def list_batches(
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> ShopBatchPage:
        return service.list_batches(actor=actor, limit=limit, offset=offset)

    @router.get("/{batch_id}", response_model=ShopBatch)
    def get_batch(batch_id: str, actor: DailySelectionActor = Depends(actor_dependency)) -> ShopBatch:
        return _not_found(lambda: service.get_batch(actor=actor, batch_id=batch_id))

    @router.get("/{batch_id}/items", response_model=ShopBatchItemPage)
    def list_items(
        batch_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        actor: DailySelectionActor = Depends(actor_dependency),
    ) -> ShopBatchItemPage:
        return _not_found(lambda: service.list_items(actor=actor, batch_id=batch_id, limit=limit, offset=offset))

    def action(name: str, batch_id: str, actor: DailySelectionActor) -> ShopBatch:
        try:
            return getattr(service, name)(actor=actor, batch_id=batch_id)
        except ShopBatchNotFound as error:
            raise HTTPException(status_code=404, detail="shop collection batch not found") from error
        except ShopCollectionConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post("/{batch_id}/pause", response_model=ShopBatch)
    def pause(batch_id: str, actor: DailySelectionActor = Depends(actor_dependency)) -> ShopBatch:
        return action("pause", batch_id, actor)

    @router.post("/{batch_id}/resume", response_model=ShopBatch)
    def resume(batch_id: str, actor: DailySelectionActor = Depends(actor_dependency)) -> ShopBatch:
        return action("resume", batch_id, actor)

    @router.post("/{batch_id}/cancel", response_model=ShopBatch)
    def cancel(batch_id: str, actor: DailySelectionActor = Depends(actor_dependency)) -> ShopBatch:
        return action("cancel", batch_id, actor)

    @router.post("/{batch_id}/retry-failed", response_model=ShopBatch)
    def retry_failed(batch_id: str, actor: DailySelectionActor = Depends(actor_dependency)) -> ShopBatch:
        return action("retry_failed", batch_id, actor)

    return router


def _not_found(callback: Callable[[], Any]) -> Any:
    try:
        return callback()
    except ShopBatchNotFound as error:
        raise HTTPException(status_code=404, detail="shop collection batch not found") from error
