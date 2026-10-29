"""订单利润 HTTP 路由（/api/order-profit）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from .service import OrderProfitService, StaffNoRequiredError



class AccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = ""
    alias: str = ""
    phone: str
    password: str = ""
    region: str = "global"
    shop_name: str = ""


class AccountLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | int = ""
    capture: bool = True
    manual: bool = False


class AccountLoginResultRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    ok: bool = False
    shop_name: str = ""
    error: str = ""


class CaptureInputRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str

class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | int
    incremental: bool = False


class ResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | int
    resource: str


class ProgressRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    step: int = 0
    step_total: int = 0
    step_label: str = ""
    detail: str = ""
    percent: int = 0
    key: str = ""
    state: str = ""


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    mode: str = "list"


class ShopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    platform: str = "temu"
    note: str = ""
    activate: bool = True
    mall_id: str = ""


class TeamProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    staff_no: str
    username: str = ""


class TeamLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_account_id: str


class TeamSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_start: str = ""


class OrderCostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_cost_cny: float | None = None
    shop: str = ""


class ActivateShopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shop_id: str


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usd_cny_rate: float = Field(default=7.2, ge=0)
    commission_rate: float = Field(default=0.05, ge=0, le=1)
    head_unit_price_cny: float = Field(default=0, ge=0)


def create_order_profit_router(
    service: OrderProfitService,
    team_client: Any = None,
    remote_token_provider: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/order-profit", tags=["order_profit"])

    def workspace_id(value: str = Header(default="local", alias="X-Workspace-ID")) -> str:
        normalized = str(value or "local").strip()
        if not normalized:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "workspace id must not be empty")
        return normalized

    def remote_token(authorization: str | None) -> str:
        """本地会话里的远端令牌，用于调用团队服务；未登录时返回空串。"""
        if remote_token_provider is None:
            return ""
        try:
            return str(remote_token_provider(authorization) or "")
        except Exception:  # noqa: BLE001 - 取不到就当未登录
            return ""

    def _error(error: BaseException) -> HTTPException:
        if isinstance(error, HTTPException):
            return error
        if isinstance(error, PermissionError):
            return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
        if isinstance(error, LookupError):
            return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
        if isinstance(error, RuntimeError):
            return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error))
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error))

    @router.get("/plugin-sessions")
    def list_sessions(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.list_sessions(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.post("/captures-all")
    def request_capture_all(
        request: SessionRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.request_capture_all(
                workspace_id(workspace),
                session_id=str(request.session_id),
                incremental=bool(request.incremental),
            )
        except BaseException as error:
            raise _error(error) from error

    @router.post("/captures/resource")
    def request_capture_resource(
        request: ResourceRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        """单个页面的「更新」按钮：只采这一块数据。"""
        try:
            return service.request_resource(
                workspace_id(workspace),
                session_id=str(request.session_id),
                resource=request.resource,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.post("/captures/{command_id}/cancel")
    def cancel_capture(
        command_id: int,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.cancel_capture(workspace_id(workspace), command_id)
        except BaseException as error:
            raise _error(error) from error

    @router.post("/captures/{command_id}/progress")
    def report_capture_progress(
        command_id: int,
        request: ProgressRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        """插件在采集过程中上报进度，工作台据此画进度条。"""
        try:
            return service.report_progress(
                workspace_id(workspace), command_id, request.model_dump()
            )
        except BaseException as error:
            raise _error(error) from error

    @router.post("/captures")
    def request_capture(
        request: CaptureRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.request_capture(
                workspace_id(workspace),
                session_id=request.session_id,
                mode=request.mode,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.get("/captures/latest")
    def latest_capture(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.latest_capture(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.post("/import")
    def import_capture(
        payload: dict[str, Any] = Body(...),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        """插件弹窗「同步到工作台」入口；插件连接失败时仍可手动导入。"""
        try:
            return service.import_capture(workspace_id(workspace), payload)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/captures/{command_id}")
    def capture_status(
        command_id: int,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.capture_status(workspace_id(workspace), command_id)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/orders")
    def list_orders(
        month: str = Query(default="", description="单月 YYYY-MM，或逗号分隔的多月"),
        store: str = Query(default=""),
        keyword: str = Query(default=""),
        shop: str = Query(default="", description="店铺名；留空取当前绑定店铺，all 表示全部"),
        limit: int = Query(default=500, ge=1, le=2000),
        offset: int = Query(default=0, ge=0),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            wanted = [part.strip() for part in month.split(",") if part.strip()]
            multi = wanted if len(wanted) > 1 else None
            return service.orders(
                workspace_id(workspace),
                month=wanted[0] if len(wanted) == 1 else "",
                months=multi,
                store=store,
                keyword=keyword,
                shop=shop,
                limit=limit,
                offset=offset,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.patch("/orders/{parent_order_sn}/cost")
    def update_order_cost(
        parent_order_sn: str,
        request: OrderCostRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        """手工填写/清除整单采购成本，按件数分摊到该单的每个 SKU 行。"""
        try:
            return service.update_order_cost(
                workspace_id(workspace),
                parent_order_sn,
                purchase_cost_cny=request.purchase_cost_cny,
                shop=request.shop,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.get("/orders/months")
    def orders_months(
        shop: str = Query(default="", description="店铺名；留空取当前绑定店铺，all 表示全部"),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.order_months(workspace_id(workspace), shop=shop)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/orders/trend")
    def orders_trend(
        months: str = Query(default="", description="逗号分隔的月份，如 2026-08,2026-09；留空取最近半年"),
        store: str = Query(default=""),
        shop: str = Query(default="", description="店铺名；留空取当前绑定店铺，all 表示全部"),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.orders_trend(
                workspace_id(workspace),
                months=[part.strip() for part in months.split(",") if part.strip()],
                store=store,
                shop=shop,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.get("/daily")
    def daily(
        date: str = Query(default=""),
        store: str = Query(default=""),
        keyword: str = Query(default=""),
        head_unit_price: float | None = Query(default=None, ge=0),
        shop: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.daily(
                workspace_id(workspace),
                date=date,
                store=store,
                keyword=keyword,
                head_unit_price=head_unit_price,
                shop=shop,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.get("/stores")
    def stores(
        shop: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.stores(workspace_id(workspace), shop=shop)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/settings")
    def get_settings(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.settings(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.post("/recalculate")
    def recalculate(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.recalculate(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.get("/operations/overview")
    def operations_overview(
        shop: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.overview(workspace_id(workspace), shop=shop)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/operations/unreceived")
    def operations_unreceived(
        store: str = Query(default=""),
        keyword: str = Query(default=""),
        shop: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.unreceived(
                workspace_id(workspace),
                store=store,
                keyword=keyword,
                shop=shop,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.get("/operations/markup")
    def operations_markup(
        month: str = Query(default=""),
        store: str = Query(default=""),
        shop: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.markup(workspace_id(workspace), month=month, store=store, shop=shop)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/operations/link-changes")
    def operations_link_changes(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.link_changes(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.get("/operations/after-sales")
    def operations_after_sales(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.after_sales(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.get("/operations/appeals")
    def operations_appeals(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.appeals(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.get("/operations/traffic-weekly")
    def operations_traffic_weekly(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.traffic_weekly(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.get("/operations/capture-status")
    def operations_capture_status(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.capture_batches(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.post("/finance/import")
    def finance_import(
        payload: dict[str, Any] = Body(...),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.import_finance(workspace_id(workspace), payload)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/finance/summary")
    def finance_summary(
        month: str = Query(default=""),
        shop: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.finance_summary(workspace_id(workspace), month=month, shop=shop)
        except BaseException as error:
            raise _error(error) from error

    @router.post("/store-metrics/import")
    def store_metrics_import(
        payload: dict[str, Any] = Body(...),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.import_store_metrics(workspace_id(workspace), payload)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/store-metrics")
    def store_metrics(
        shop: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.store_metrics(workspace_id(workspace), shop=shop)
        except BaseException as error:
            raise _error(error) from error

    @router.post("/settlement/import")
    def settlement_import(
        payload: dict[str, Any] = Body(...),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.import_settlement(workspace_id(workspace), payload)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/settlement/summary")
    def settlement_summary(
        status_filter: str = Query(default="", alias="status"),
        shop: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.settlement_summary(workspace_id(workspace), status=status_filter, shop=shop)
        except BaseException as error:
            raise _error(error) from error

    @router.post("/traffic/import")
    def traffic_import(
        payload: dict[str, Any] = Body(...),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.import_traffic(workspace_id(workspace), payload)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/traffic/summary")
    def traffic_summary(
        shop: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.traffic_summary(workspace_id(workspace), shop=shop)
        except BaseException as error:
            raise _error(error) from error

    @router.post("/sales/import")
    def sales_import(
        payload: dict[str, Any] = Body(...),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.import_sales(workspace_id(workspace), payload)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/sales/summary")
    def sales_summary(
        shop: str = Query(default=""),
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.sales_summary(workspace_id(workspace), shop=shop)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/shops")
    def list_shops(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.shops(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.post("/shops")
    def bind_shop(
        request: ShopRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            return service.bind_shop(
                workspace_id(workspace),
                name=request.name,
                platform=request.platform,
                note=request.note,
                activate=request.activate,
                mall_id=request.mall_id,
                team_client=team_client,
                remote_token=remote_token(authorization),
            )
        except StaffNoRequiredError as error:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail={"code": "staff_no_required", "message": str(error)},
            ) from error
        except BaseException as error:
            raise _error(error) from error

    @router.post("/shops/{shop_id}/sync-code")
    def sync_shop_code(
        shop_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            return service.sync_shop_code(
                workspace_id(workspace),
                shop_id,
                team_client=team_client,
                remote_token=remote_token(authorization),
            )
        except BaseException as error:
            raise _error(error) from error

    @router.get("/team/profile")
    def team_profile(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        return service.team_profile(
            team_client=team_client,
            remote_token=remote_token(authorization),
        )

    @router.post("/team/profile")
    def save_team_profile(
        request: TeamProfileRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            return service.save_team_profile(
                staff_no=request.staff_no,
                username=request.username,
                team_client=team_client,
                remote_token=remote_token(authorization),
            )
        except BaseException as error:
            raise _error(error) from error

    @router.post("/team/submit")
    def submit_team_rollup(
        request: TeamSubmitRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            return service.submit_team_rollup(
                workspace_id(workspace),
                week_start=request.week_start,
                team_client=team_client,
                remote_token=remote_token(authorization),
            )
        except BaseException as error:
            raise _error(error) from error

    @router.get("/team/submissions")
    def team_submissions(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return service.team_submissions(
            team_client=team_client,
            remote_token=remote_token(authorization),
        )

    @router.get("/team-links")
    def team_links(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return service.team_links(
            team_client=team_client,
            remote_token=remote_token(authorization),
        )

    @router.post("/team-links")
    def request_team_link(
        request: TeamLinkRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            return service.team_link_request(
                request.target_account_id,
                team_client=team_client,
                remote_token=remote_token(authorization),
            )
        except BaseException as error:
            raise _error(error) from error

    @router.post("/team-links/{account_id}/accept")
    def accept_team_link(
        account_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            return service.team_link_accept(
                account_id,
                team_client=team_client,
                remote_token=remote_token(authorization),
            )
        except BaseException as error:
            raise _error(error) from error

    @router.post("/team-links/{account_id}/reject")
    def reject_team_link(
        account_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            return service.team_link_reject(
                account_id,
                team_client=team_client,
                remote_token=remote_token(authorization),
            )
        except BaseException as error:
            raise _error(error) from error

    @router.delete("/team-links/{account_id}")
    def remove_team_link(
        account_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            return service.team_link_remove(
                account_id,
                team_client=team_client,
                remote_token=remote_token(authorization),
            )
        except BaseException as error:
            raise _error(error) from error

    @router.post("/shops/active")
    def activate_shop(
        request: ActivateShopRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.activate_shop(workspace_id(workspace), request.shop_id)
        except BaseException as error:
            raise _error(error) from error

    @router.post("/shops/cleanup")
    def cleanup_shops(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        """清理没有对应店铺账号的店铺（只清理下拉选项）。"""
        try:
            return service.cleanup_unbound_shops(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.get("/shops/active")
    def active_shop(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.active_shop(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.put("/settings")
    def save_settings(
        request: SettingsRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.save_settings(
                workspace_id(workspace),
                usd_cny_rate=request.usd_cny_rate,
                commission_rate=request.commission_rate,
                head_unit_price_cny=request.head_unit_price_cny,
            )
        except BaseException as error:
            raise _error(error) from error


    # ---------- 店铺账号（自动登录采集） ----------

    @router.get("/accounts")
    def list_accounts(
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.accounts(workspace_id(workspace))
        except BaseException as error:
            raise _error(error) from error

    @router.post("/accounts")
    def save_account(
        request: AccountRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.save_account(
                workspace_id(workspace),
                account_id=request.id,
                alias=request.alias,
                phone=request.phone,
                password=request.password,
                region=request.region,
                shop_name=request.shop_name,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.delete("/accounts/{account_id}")
    def delete_account(
        account_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.delete_account(workspace_id(workspace), account_id)
        except BaseException as error:
            raise _error(error) from error

    @router.post("/accounts/{account_id}/login")
    def start_account_login(
        account_id: str,
        request: AccountLoginRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.start_account_login(
                workspace_id(workspace),
                account_id,
                session_id=str(request.session_id or ""),
                capture=request.capture,
                manual=request.manual,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.post("/accounts/{account_id}/data/clear")
    def clear_account_data(
        account_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.clear_account_data(workspace_id(workspace), account_id)
        except BaseException as error:
            raise _error(error) from error

    @router.post("/accounts/{account_id}/capture")
    def start_account_capture(
        account_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.start_account_capture(workspace_id(workspace), account_id)
        except BaseException as error:
            raise _error(error) from error

    @router.post("/accounts/{account_id}/login-result")
    def account_login_result(
        account_id: str,
        request: AccountLoginResultRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.record_login_result(
                workspace_id(workspace),
                account_id,
                ok=bool(request.ok),
                shop_name=request.shop_name,
                error=request.error,
            )
        except BaseException as error:
            raise _error(error) from error

    @router.post("/accounts/{account_id}/browser")
    def open_account_browser(
        account_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.open_account_browser(workspace_id(workspace), account_id)
        except BaseException as error:
            raise _error(error) from error

    @router.delete("/accounts/{account_id}/browser")
    def close_account_browser(
        account_id: str,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.close_account_browser(workspace_id(workspace), account_id)
        except BaseException as error:
            raise _error(error) from error

    @router.get("/captures/{command_id}/credentials")
    def capture_credentials(
        command_id: int,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.account_credentials(workspace_id(workspace), command_id)
        except BaseException as error:
            raise _error(error) from error

    @router.post("/captures/{command_id}/input")
    def submit_capture_input(
        command_id: int,
        request: CaptureInputRequest,
        workspace: str = Header(default="local", alias="X-Workspace-ID"),
    ) -> dict[str, Any]:
        try:
            return service.submit_capture_input(
                workspace_id(workspace), command_id, request.value
            )
        except BaseException as error:
            raise _error(error) from error

    return router
