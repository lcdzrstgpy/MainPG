"""成本桥接 HTTP 路由（/api/bridge）。

前端只访问本地 runtime；服务器地址、共享密钥与客户端标识都在本地补齐。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from ...session import Actor, actor_from_authorization
from .client import BridgeApiClient, BridgeApiError


class BindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str = Field(min_length=1)
    password: str = Field(min_length=1)


class CostItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skc_id: str
    unit_cost: float = Field(ge=0)
    currency: str = "CNY"


class CostsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str
    items: list[CostItem] = Field(default_factory=list)


def create_bridge_router(client: BridgeApiClient) -> APIRouter:
    router = APIRouter(prefix="/bridge", tags=["bridge"])

    def _error(error: BaseException) -> HTTPException:
        if isinstance(error, HTTPException):
            return error
        if isinstance(error, BridgeApiError):
            code = (
                error.status
                if 400 <= error.status < 600
                else status.HTTP_502_BAD_GATEWAY
            )
            return HTTPException(code, str(error))
        return HTTPException(status.HTTP_502_BAD_GATEWAY, str(error))

    @router.get("/status")
    def bridge_status() -> dict[str, Any]:
        return {"configured": client.configured, "client_id": client.client_id}

    @router.get("/bindings")
    def bindings() -> dict[str, Any]:
        try:
            return client.bindings()
        except BaseException as error:
            raise _error(error) from error

    @router.post("/bindings")
    def bind(
        request: BindRequest,
        actor: Actor = Depends(actor_from_authorization),
    ) -> dict[str, Any]:
        try:
            # 认领记录展示的是用户登录账号，而不是设备标识。
            return client.bind(request.phone, request.password, label=actor.username)
        except BaseException as error:
            raise _error(error) from error

    @router.delete("/bindings/{account_id}")
    def unbind(account_id: str) -> dict[str, Any]:
        try:
            return client.unbind(account_id)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/items")
    def items(
        account_ids: str = Query(default=""),
        status_filter: str = Query(default="", alias="status"),
        date_from: str = Query(default=""),
        date_to: str = Query(default=""),
        keyword: str = Query(default=""),
        missing_cost: int = Query(default=0),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            return client.items(
                account_ids=account_ids,
                status=status_filter,
                date_from=date_from,
                date_to=date_to,
                keyword=keyword,
                missing_cost=missing_cost,
                page=page,
                page_size=page_size,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.get("/costs")
    def costs() -> dict[str, Any]:
        try:
            return client.costs()
        except BaseException as error:
            raise _error(error) from error

    @router.post("/costs")
    def save_costs(request: CostsRequest) -> dict[str, Any]:
        try:
            return client.save_costs(
                request.account_id,
                [item.model_dump() for item in request.items],
            )
        except BaseException as error:
            raise _error(error) from error

    return router
