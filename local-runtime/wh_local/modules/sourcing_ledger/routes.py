"""货源与利润台账 HTTP 路由（/api/sourcing-ledger）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field

from .service import SourcingLedgerService


class SourcePayload(BaseModel):
    source_url: str = ""
    source_title: str = ""
    shop_name: str = ""
    main_image_url: str = ""
    price_cny: str = ""
    moq: int = Field(default=1, ge=1)
    domestic_freight_cny: str = ""
    weight_kg: str = ""
    note: str = ""
    offer_id: str = ""


class ConfirmMatchPayload(BaseModel):
    market_skc_id: str
    site: str = ""
    skc: str
    market_skc_code: str = ""
    product_title: str = ""


class LibraryPayload(BaseModel):
    site: str = ""
    source_id: str = ""


class ImagePayload(BaseModel):
    image_url: str = ""


class WeightsPayload(BaseModel):
    skcs: list[str] = Field(default_factory=list)
    weight_kg: str = ""


class DeleteItemsPayload(BaseModel):
    skcs: list[str] = Field(default_factory=list)


class SitePayload(BaseModel):
    site_code: str = ""


class PriceSelectionPayload(BaseModel):
    sku_ref: str = ""


class StorePayload(BaseModel):
    store_name: str = ""


class BatchStorePayload(BaseModel):
    skcs: list[str] = Field(default_factory=list)
    store_name: str = ""


def create_sourcing_ledger_router(service: SourcingLedgerService) -> APIRouter:
    router = APIRouter(prefix="/sourcing-ledger", tags=["sourcing_ledger"])

    def workspace_id(value: str = Header(default="local", alias="X-Workspace-ID")) -> str:
        normalized = str(value or "local").strip()
        if not normalized:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "workspace id must not be empty")
        return normalized

    @router.get("/sites")
    def list_sites(workspace: str = Header(default="local", alias="X-Workspace-ID")) -> dict[str, Any]:
        return {"sites": service.sites(workspace_id(workspace))}

    @router.get("/items")
    def list_items(
        status_filter: str = Query(default="", alias="status"),
        site: str = Query(default=""),
        keyword: str = Query(default=""),
        batch: str = Query(default=""),
        store: str = Query(default=""),
        limit: int = Query(default=200, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return service.list_items(
            workspace_id(workspace),
            status=status_filter,
            site=site,
            keyword=keyword,
            batch=batch,
            store=store,
            limit=limit,
            offset=offset,
        )

    @router.post("/items/delete")
    def delete_items(
        payload: DeleteItemsPayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.delete_items(workspace_id(workspace), payload.skcs)
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.get("/items/{skc}")
    def item_detail(
        skc: str,
        site: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.item_detail(workspace_id(workspace), skc, site=site)
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    @router.post("/items/{skc}/site")
    def set_item_site(
        skc: str,
        payload: SitePayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.set_item_site(workspace_id(workspace), skc, payload.site_code)
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.post("/items/{skc}/store")
    def set_item_store(
        skc: str,
        payload: StorePayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.set_item_store(workspace_id(workspace), skc, payload.store_name)
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.post("/items/{skc}/price-selection")
    def set_price_selection(
        skc: str,
        payload: PriceSelectionPayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.set_price_selection(workspace_id(workspace), skc, payload.sku_ref)
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.delete("/items/{skc}/declared/{row_id}")
    def delete_declared_price(
        skc: str,
        row_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.delete_declared_price(workspace_id(workspace), skc, row_id)
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.post("/items/{skc}/sources")
    def create_source(
        skc: str,
        payload: SourcePayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.save_source(
                workspace_id(workspace), skc, payload.model_dump()
            )
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.patch("/items/{skc}/sources/{source_id}")
    def update_source(
        skc: str,
        source_id: str,
        payload: SourcePayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.save_source(
                workspace_id(workspace), skc, payload.model_dump(), source_id=source_id
            )
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.delete("/items/{skc}/sources/{source_id}")
    def delete_source(
        skc: str,
        source_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return service.remove_source(workspace_id(workspace), skc, source_id)

    @router.post("/items/{skc}/sources/{source_id}/primary")
    def set_primary_source(
        skc: str,
        source_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return service.set_primary_source(workspace_id(workspace), skc, source_id)

    @router.post("/items/{skc}/sources/{source_id}/images")
    def add_source_image(
        skc: str,
        source_id: str,
        payload: ImagePayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.add_source_image(
                workspace_id(workspace), skc, source_id, payload.image_url
            )
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.delete("/items/{skc}/sources/{source_id}/images/{image_id}")
    def remove_source_image(
        skc: str,
        source_id: str,
        image_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return service.remove_source_image(workspace_id(workspace), skc, source_id, image_id)

    @router.post("/items/{skc}/sources/{source_id}/main-image")
    def set_source_main_image(
        skc: str,
        source_id: str,
        payload: ImagePayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.set_source_main_image(
                workspace_id(workspace), skc, source_id, payload.image_url
            )
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.delete("/items/{skc}/sources/{source_id}/main-image")
    def remove_source_main_image(
        skc: str,
        source_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.clear_source_main_image(workspace_id(workspace), skc, source_id)
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    @router.post("/weights")
    def set_weights(
        payload: WeightsPayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.set_source_weights(
                workspace_id(workspace), payload.skcs, payload.weight_kg
            )
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.post("/stores")
    def set_stores(
        payload: BatchStorePayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.set_items_store(
                workspace_id(workspace), payload.skcs, payload.store_name
            )
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error

    @router.post("/items/{skc}/library")
    def enter_library(
        skc: str,
        payload: LibraryPayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.enter_library(
                workspace_id(workspace), skc, site=payload.site, source_id=payload.source_id
            )
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

    @router.delete("/items/{skc}/library")
    def leave_library(
        skc: str,
        site: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.leave_library(workspace_id(workspace), skc, site=site)
        except ValueError as error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error

    @router.get("/matches/pending")
    def pending_matches(
        limit: int = Query(default=200, ge=1, le=500),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        return {"items": service.pending_matches(workspace_id(workspace), limit=limit)}

    @router.post("/matches/confirm")
    def confirm_match(
        payload: ConfirmMatchPayload,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.confirm_match(
                workspace_id=workspace_id(workspace),
                market_skc_id=payload.market_skc_id,
                site=payload.site,
                skc=payload.skc,
                market_skc_code=payload.market_skc_code,
                product_title=payload.product_title,
            )
        except LookupError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    return router
