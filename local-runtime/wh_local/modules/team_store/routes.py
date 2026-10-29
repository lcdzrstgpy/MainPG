"""团队服务路由：账号画像与店铺登记。

第 2 步只开放「员工标识 + 店铺登记」两个能力，
上报、汇总和组长查询在后续步骤追加到同一套路由里。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from .auth import AccountResolver
from .dedupe import BEIJING
from .service import (
    StaffNoLocked,
    StaffNoRequired,
    StaffNoTaken,
    TeamStoreService,
)
from .rules import StaffNoError, StoreNameError


def _parse_date(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _range(start: str, end: str) -> tuple[str, str]:
    """默认最近 7 天；起止写反时自动纠正。"""
    today = datetime.now(BEIJING).date()
    begin = _parse_date(start) or (today - timedelta(days=6))
    finish = _parse_date(end) or today
    if finish < begin:
        begin, finish = finish, begin
    return begin.isoformat(), finish.isoformat()


class ProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staff_no: str
    username: str = ""


class StoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    platform: str = "temu"
    mall_id: str = ""


class LinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_account_id: str


class RollupStore(BaseModel):
    model_config = ConfigDict(extra="allow")

    store_code: str
    days: list[dict[str, Any]] = []
    dup_orders: dict[str, Any] = {}
    records: list[dict[str, Any]] = []


class RollupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: str = ""
    submitted_at: str = ""
    stores: list[RollupStore] = []
    batches: list[dict[str, Any]] = []


def _bad_request(error: BaseException) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


def create_team_store_router(
    service: TeamStoreService,
    resolver: AccountResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/team", tags=["team-store"])

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "team-store"}

    def current_account(authorization: str | None) -> str:
        try:
            account_id, username = resolver.resolve_identity(authorization)
        except PermissionError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        # 顺手把登录账号补进画像，别人就能用登录账号发起关联。
        service.note_identity(account_id, username)
        return account_id

    @router.get("/profile")
    def read_profile(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return service.profile(current_account(authorization))

    @router.post("/profile")
    def save_profile(
        request: ProfileRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account_id = current_account(authorization)
        try:
            return service.save_profile(
                account_id,
                staff_no=request.staff_no,
                username=request.username,
            )
        except (StaffNoError, StaffNoLocked, StaffNoTaken) as error:
            raise _bad_request(error) from error

    @router.get("/stores")
    def list_stores(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return service.list_stores(current_account(authorization))

    @router.post("/stores")
    def register_store(
        request: StoreRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account_id = current_account(authorization)
        try:
            return service.register_store(
                account_id,
                name=request.name,
                platform=request.platform,
                mall_id=request.mall_id,
            )
        except (StaffNoRequired, StoreNameError) as error:
            raise _bad_request(error) from error

    @router.delete("/stores/{store_code}")
    def remove_store(
        store_code: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account_id = current_account(authorization)
        try:
            return service.delete_store(account_id, store_code)
        except PermissionError as error:
            raise HTTPException(status_code=403, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @router.get("/links")
    def read_links(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return service.links(current_account(authorization))

    @router.post("/links")
    def create_link(
        request: LinkRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account_id = current_account(authorization)
        try:
            return service.add_link(account_id, request.target_account_id)
        except ValueError as error:
            raise _bad_request(error) from error

    @router.post("/links/{account_id}/accept")
    def accept_link(
        account_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            return service.accept_link(current_account(authorization), account_id)
        except ValueError as error:
            raise _bad_request(error) from error

    @router.post("/links/{account_id}/reject")
    def reject_link(
        account_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return service.reject_link(current_account(authorization), account_id)

    @router.delete("/links/{target_account_id}")
    def delete_link(
        target_account_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return service.remove_link(current_account(authorization), target_account_id)

    @router.get("/flags")
    def list_flags(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return service.list_flags(current_account(authorization))

    @router.post("/rollup/submit")
    def submit_rollup(
        request: RollupRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        account_id = current_account(authorization)
        return service.submit_rollup(
            account_id,
            week_start=request.week_start,
            submitted_at=request.submitted_at,
            stores=[store.model_dump() for store in request.stores],
            batches=request.batches,
        )

    @router.get("/submissions")
    def list_submissions(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return service.list_submissions(current_account(authorization))

    @router.get("/batches")
    def list_batches(
        limit: int = Query(default=50, ge=1, le=200),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return service.list_batches(current_account(authorization), limit=limit)

    @router.get("/scope")
    def read_scope(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return service.scope(current_account(authorization))

    @router.get("/summary")
    def read_summary(
        start: str = Query(default="", alias="from"),
        end: str = Query(default="", alias="to"),
        group_by: str = Query(default="shop"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        begin, finish = _range(start, end)
        return service.summary(
            current_account(authorization),
            start=begin,
            end=finish,
            group_by=group_by,
        )

    @router.get("/overview")
    def read_overview(
        start: str = Query(default="", alias="from"),
        end: str = Query(default="", alias="to"),
        all_time: bool = Query(default=False, alias="all"),
        exclude: str = Query(default="", description="要剔除的店铺码，逗号分隔"),
        top: int = Query(default=8, ge=1, le=50),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        begin, finish = ("", "") if all_time else _range(start, end)
        hidden = [part.strip() for part in str(exclude or "").split(",") if part.strip()]
        return service.overview(
            current_account(authorization),
            start=begin,
            end=finish,
            top=top,
            exclude=hidden,
        )

    @router.get("/trends")
    def read_trends(
        start: str = Query(default="", alias="from"),
        end: str = Query(default="", alias="to"),
        all_time: bool = Query(default=False, alias="all"),
        exclude: str = Query(default="", description="要剔除的店铺码，逗号分隔"),
        top: int = Query(default=6, ge=1, le=20),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        begin, finish = ("", "") if all_time else _range(start, end)
        return service.trends(
            current_account(authorization),
            start=begin,
            end=finish,
            exclude=[part.strip() for part in str(exclude or "").split(",") if part.strip()],
            top=top,
        )

    @router.get("/records")
    def read_records(
        kind: str = Query(default="order", description="明细类型，可逗号分隔"),
        stores: str = Query(default="", description="店铺码，可逗号分隔"),
        exclude: str = Query(default="", description="要剔除的店铺码，逗号分隔"),
        start: str = Query(default="", alias="from"),
        end: str = Query(default="", alias="to"),
        all_time: bool = Query(default=False, alias="all"),
        status: str = Query(default="", description="订单状态筛选"),
        keyword: str = Query(default=""),
        limit: int = Query(default=2000, ge=1, le=10000),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        begin, finish = ("", "") if all_time else _range(start, end)
        return service.records(
            current_account(authorization),
            kinds=[part.strip() for part in str(kind or "").split(",") if part.strip()],
            stores=[part.strip() for part in str(stores or "").split(",") if part.strip()],
            exclude=[part.strip() for part in str(exclude or "").split(",") if part.strip()],
            start=begin,
            end=finish,
            status=status,
            keyword=keyword,
            limit=limit,
        )

    return router


def create_team_app(
    service: TeamStoreService,
    resolver: AccountResolver,
) -> FastAPI:
    app = FastAPI(title="MainPG Team Store Service", version="0.1.0")
    app.include_router(create_team_store_router(service, resolver))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "team-store"}

    return app
