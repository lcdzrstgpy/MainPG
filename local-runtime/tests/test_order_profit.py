from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.data_collection.plugin_queue import DataCollectionPluginQueue
from wh_local.modules.order_profit.repository import OrderProfitRepository
from wh_local.modules.order_profit.routes import create_order_profit_router
from wh_local.modules.order_profit.service import OrderProfitService


def _service(tmp_path: Path) -> OrderProfitService:
    return OrderProfitService(OrderProfitRepository(tmp_path / "order_profit.sqlite3"))


def _capture_result() -> dict:
    list_item = {
        "parent_order_sn": "PO-1",
        "sub_order_sn": "SUB-1",
        "sku_id": "SKU-1",
        "skc_id": "SKC-1",
        "skc_code": "SKC-CODE-1",
        "ext_code": "EXT-1",
        "goods_name": "Goods",
        "quantity": 1,
        "tail_freight": 2.7,
        "tail_currency": "USD",
        "tracking_no": "TRACK-1",
        "order_status": "待发货",
        "order_status_code": "2",
        "site": "美国",
        "order_time": "2026-10-18 07:05:16",
        "mall_id": "MALL-1",
        "source": "api",
    }
    detail_item = {
        **list_item,
        "declared_price": 75,
        "declared_currency": "CNY",
        "gmv": 75,
        "gmv_currency": "CNY",
        "sales_payback": 75,
        "sales_reversal": 0,
        "freight_payback": 20.07,
        "freight_reversal": 0,
        "estimated_income": 95.07,
        "order_payment": 75,
        "second_payment": 0,
        "source": "dom-detail",
    }
    return {
        "ok": True,
        "capture_mode": "order_list",
        "items": [list_item, detail_item],
        "summary": {"order_count": 1, "sub_order_count": 1, "item_count": 1},
        "capturedAt": "2026-10-24T00:00:00Z",
    }


def test_ingest_merges_list_and_detail_and_computes_profit(tmp_path: Path) -> None:
    service = _service(tmp_path)
    batch = service._ingest("default", SimpleNamespace(command_id=101), _capture_result())

    assert batch["order_count"] == 1
    assert batch["sub_order_count"] == 1
    assert batch["item_count"] == 1

    items, total = service._repository.list_items("default")
    assert total == 1
    item = items[0]
    assert item["gmv_cny"] == 75.0
    assert item["declared_price"] == 75.0
    assert item["tail_freight_cny"] == pytest.approx(19.44)
    assert item["sales_payback"] == 75.0
    assert item["profit_estimate_cny"] == pytest.approx(51.81)
    assert item["match_status"] == "needs_cost"

    orders = service.orders("default")
    assert orders["summary"]["order_count"] == 1
    assert orders["summary"]["settled_total"] == pytest.approx(95.07)
    assert orders["summary"]["profit"] == pytest.approx(75.63)
    assert orders["orders"][0]["parent_order_sn"] == "PO-1"

    daily = service.daily("default")
    assert daily["summary"]["date"] == "2026-10-18"
    assert daily["summary"]["profit"] == pytest.approx(51.81)
    assert daily["items"][0]["missing"]["purchase_cost"] is True
    assert daily["warnings"][0]["type"] == "purchase"


def test_routes_return_data_and_require_plugin_queue(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._ingest("default", SimpleNamespace(command_id=102), _capture_result())

    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    assert client.get("/api/order-profit/settings", headers=headers).status_code == 200
    orders = client.get("/api/order-profit/orders", headers=headers)
    assert orders.status_code == 200
    assert orders.json()["summary"]["order_count"] == 1
    daily = client.get("/api/order-profit/daily", headers=headers)
    assert daily.status_code == 200
    assert daily.json()["summary"]["order_count"] == 1
    stores = client.get("/api/order-profit/stores", headers=headers)
    assert stores.json()["stores"][0]["id"] == "MALL-1"

    # 没有插件队列时，会话/采集接口返回 503，而不是 500。
    assert client.get("/api/order-profit/plugin-sessions", headers=headers).status_code == 503
    capture = client.post(
        "/api/order-profit/captures",
        headers=headers,
        json={"session_id": "1", "mode": "list"},
    )
    assert capture.status_code == 503


def test_manual_import_route(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    imported = client.post(
        "/api/order-profit/import",
        headers=headers,
        json=_capture_result(),
    )
    assert imported.status_code == 200
    payload = imported.json()
    assert payload["ok"] is True
    assert payload["order_count"] == 1
    assert payload["sub_order_count"] == 1

    latest = client.get("/api/order-profit/captures/latest", headers=headers)
    assert latest.json()["capture"]["command_id"] < 0
    orders = client.get("/api/order-profit/orders", headers=headers)
    assert orders.json()["summary"]["order_count"] == 1


def test_operation_views_and_recalculate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._ingest("default", SimpleNamespace(command_id=103), _capture_result())

    overview = service.overview("default")
    assert overview["counts"]["orders"] == 1
    assert overview["counts"]["unreceived"] == 1
    assert any(todo["view"] == "daily" for todo in overview["todos"])

    unreceived = service.unreceived("default")
    assert unreceived["total"] == 1
    assert unreceived["orders"][0]["parent_order_sn"] == "PO-1"

    markup = service.markup("default", month="2026-10")
    assert markup["summary"]["sku_count"] == 1
    assert markup["items"][0]["declared_price"] == 75.0
    assert markup["items"][0]["status"] == "needs_cost"

    recalculated = service.recalculate("default")
    assert recalculated["updated"] == 1

    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}
    assert client.get("/api/order-profit/operations/overview", headers=headers).status_code == 200
    assert client.get("/api/order-profit/operations/unreceived", headers=headers).status_code == 200
    assert client.get("/api/order-profit/operations/markup", headers=headers).status_code == 200
    assert client.get("/api/order-profit/operations/capture-status", headers=headers).status_code == 200
    assert client.get("/api/order-profit/operations/link-changes", headers=headers).json()["data_source"] == "pending"
    assert client.post("/api/order-profit/recalculate", headers=headers).status_code == 200


def test_finance_and_store_metrics(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    service._ingest("default", SimpleNamespace(command_id=104), _capture_result())
    finance = client.post(
        "/api/order-profit/finance/import",
        headers=headers,
        json={
            "ok": True,
            "shop_name": "Bluqevix",
            "capturedAt": "2026-10-24T02:00:00Z",
            "entries": [
                {"entry_time": "2026-10-20 04:14:39", "entry_type": "支出", "amount": -9.98, "currency": "CNY", "note": "推广服务费"},
                {"entry_time": "2026-10-20 04:12:45", "entry_type": "支出", "amount": -5.07, "currency": "CNY", "note": "推广服务费"},
                {"entry_time": "2026-10-20 04:12:44", "entry_type": "支出", "amount": -0.13, "currency": "CNY", "note": "推广服务费"},
                {"entry_time": "2026-10-20 04:08:54", "entry_type": "结算", "amount": 69.12, "currency": "CNY", "note": "结算批次号 SEMI-BATCH"},
            ],
        },
    )
    assert finance.status_code == 200
    payload = finance.json()
    assert payload["inserted"] == 4
    assert payload["summary"]["ad_cost"] == pytest.approx(15.18)
    assert payload["summary"]["settlement"] == pytest.approx(69.12)

    metrics = client.post(
        "/api/order-profit/store-metrics/import",
        headers=headers,
        json={
            "capturedAt": "2026-10-24T02:00:00Z",
            "metrics": {
                "shop_name": "Bluqevix",
                "pending_shipment": 4,
                "active_products": 608,
                "sales_today": 0,
                "sales_7d": 1,
                "sales_30d": 1,
                "refund_pending": 0,
                "cancel_pending": 0,
                "ship_timeliness": 100,
                "delivery_timeliness": 50,
            },
        },
    )
    assert metrics.status_code == 200
    assert metrics.json()["metrics"]["active_products"] == 608

    overview = client.get("/api/order-profit/operations/overview", headers=headers).json()
    assert overview["counts"]["active_products"] == 608
    assert overview["counts"]["ad_cost"] == pytest.approx(15.18)
    assert overview["store_metrics"]["shop_name"] == "Bluqevix"

    orders = client.get("/api/order-profit/orders?month=2026-10", headers=headers).json()
    assert orders["summary"]["ad_cost"] == pytest.approx(15.18)
    assert orders["summary"]["penalty"] == pytest.approx(0)

    items, _ = service._repository.list_items("default")
    assert items[0]["shop_name"] == "Bluqevix"
    assert client.get("/api/order-profit/finance/summary?month=2026-10", headers=headers).json()["summary"]["ad_cost"] == pytest.approx(15.18)


def test_settlement_import_backfills_order_financials(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    service._ingest("default", SimpleNamespace(command_id=105), _capture_result())

    response = client.post(
        "/api/order-profit/settlement/import",
        headers=headers,
        json={
            "ok": True,
            "status": "pending",
            "capturedAt": "2026-10-24T06:00:00Z",
            "rows": [
                {
                    "parent_order_sn": "PO-1",
                    "currency": "CNY",
                    "sales_payback": 75,
                    "sales_reversal": 0,
                    "freight_payback": 20.07,
                    "freight_reversal": 0,
                    "declared_price": 75,
                    "quantity": 1,
                    "goods_name": "合成商品",
                }
            ],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["written"] == 1
    assert payload["backfilled_items"] == 1
    assert payload["summary"]["total_payback"] == pytest.approx(95.07)

    summary = client.get("/api/order-profit/settlement/summary", headers=headers).json()
    assert summary["pending"]["sales_payback"] == pytest.approx(75)
    assert summary["pending"]["freight_payback"] == pytest.approx(20.07)
    assert summary["rows"][0]["parent_order_sn"] == "PO-1"

    orders = client.get("/api/order-profit/orders?month=2026-10", headers=headers).json()
    order = orders["orders"][0]
    assert order["sales_payback"] == pytest.approx(75)
    assert order["freight_payback"] == pytest.approx(20.07)
    assert order["settled_total"] == pytest.approx(95.07)


def test_traffic_import_keeps_snapshot_period_and_metrics(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    payload = {
        "ok": True,
        "scope": "product",
        "period": "近1日",
        "stat_date": "2026-10-23",
        "shop_name": "Bluqevix",
        "capturedAt": "2026-10-24T08:00:00Z",
        "cards": [
            {"label": "商品曝光量", "value": "107", "change": "9.32%"},
            {"label": "商品点击量", "value": "3", "change": "78.57%"},
            {"label": "支付订单数", "value": "0", "change": "-"},
            {"label": "支付件数", "value": "0", "change": "-"},
            {"label": "点击率", "value": "2.80%", "change": "76.37%"},
            {"label": "点击订单转化率", "value": "0.00%", "change": "-"},
            {"label": "曝光订单转化率", "value": "0.00%", "change": "-"},
        ],
    }
    response = client.post("/api/order-profit/traffic/import", headers=headers, json=payload)
    assert response.status_code == 200
    snapshot = response.json()["snapshot"]
    assert snapshot["exposure"] == 107
    assert snapshot["clicks"] == 3
    assert snapshot["click_rate"] == pytest.approx(2.8)
    assert snapshot["period"] == "近1日"
    assert snapshot["metrics"]["商品曝光量(环比)"] == "9.32%"

    # 同一「范围+周期+统计日期」再采一次只更新快照，不新增行。
    client.post("/api/order-profit/traffic/import", headers=headers, json=payload)
    summary = client.get("/api/order-profit/traffic/summary", headers=headers).json()
    assert summary["product"]["stat_date"] == "2026-10-23"
    assert summary["shop"] is None
    assert len(summary["history"]) == 1


def test_traffic_import_keeps_chart_endpoints(tmp_path: Path) -> None:
    """趋势/渠道图是 canvas，只能把页面接口原始序列一起存进快照。"""
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    payload = {
        "ok": True,
        "scope": "product",
        "period": "近1日",
        "stat_date": "2026-10-28",
        "cards": [
            {"label": "商品曝光量", "value": "425", "change": "3.85%"},
            {"label": "商品点击量", "value": "25", "change": "24.24%"},
        ],
        "charts": [
            {
                "endpoint": "/api/flow/analysis/overview",
                "request": {"timeDimension": "1d"},
                "response": {"result": {"exposure": 425, "clicks": 25}},
                "capturedAt": "2026-10-28T08:00:00Z",
            },
            {
                "endpoint": "/api/flow/analysis/trend",
                "response": {"result": {"list": [{"time": "00:00", "exposure": 18}]}},
                "capturedAt": "2026-10-28T08:00:01Z",
            },
            {"endpoint": "/api/flow/analysis/broken", "response": "not-json"},
        ],
    }
    response = client.post("/api/order-profit/traffic/import", headers=headers, json=payload)
    assert response.status_code == 200
    charts = response.json()["snapshot"]["metrics"]["charts"]
    assert [chart["endpoint"] for chart in charts] == [
        "/api/flow/analysis/overview",
        "/api/flow/analysis/trend",
    ]
    assert charts[0]["request"] == {"timeDimension": "1d"}
    # 再读汇总也应拿到图表数据
    summary = client.get("/api/order-profit/traffic/summary", headers=headers).json()
    assert summary["product"]["metrics"]["charts"][1]["response"]["result"]["list"][0]["time"] == "00:00"


def test_sales_import_keeps_metrics_and_health(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    payload = {
        "ok": True,
        "region": "全球",
        "stat_date": "2026-10-24",
        "updated_at": "2026-10-24 15:13:56",
        "shop_name": "Bluqevix",
        "capturedAt": "2026-10-24T09:00:00Z",
        "metrics": [
            {"label": "今日总销量", "value": "0", "day_change": "-", "week_change": "-"},
            {"label": "库存可售天数", "value": "1032.5", "day_change": "-", "week_change": "48.28%"},
            {"label": "10天库存满足率", "value": "100.00%", "day_change": "-", "week_change": "-"},
        ],
        "health": {"售罄健康分数": "95.00", "待反馈": "0"},
    }
    response = client.post("/api/order-profit/sales/import", headers=headers, json=payload)
    assert response.status_code == 200
    snapshot = response.json()["snapshot"]
    assert snapshot["region"] == "全球"
    assert snapshot["stat_date"] == "2026-10-24"
    assert snapshot["metrics"]["今日总销量"] == "0"
    assert snapshot["metrics"]["库存可售天数(较近7日)"] == "48.28%"
    assert snapshot["health"]["售罄健康分数"] == "95.00"
    assert snapshot["health"]["最近更新时间"] == "2026-10-24 15:13:56"

    client.post("/api/order-profit/sales/import", headers=headers, json=payload)
    summary = client.get("/api/order-profit/sales/summary", headers=headers).json()
    assert summary["latest"]["shop_name"] == "Bluqevix"
    assert len(summary["history"]) == 1


def test_request_capture_all_queues_plugin_command(tmp_path: Path) -> None:
    queue = DataCollectionPluginQueue(tmp_path / "queue.sqlite3")
    session = queue.create_session(
        actor_id="local-demo-admin",
        workspace_id="default",
        capabilities={"temu_order_profit_capture": True, "temu_capture_all": True},
    )
    service = OrderProfitService(
        OrderProfitRepository(tmp_path / "order_profit.sqlite3"),
        plugin_queue=queue,
    )
    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    response = client.post(
        "/api/order-profit/captures-all",
        headers=headers,
        json={"session_id": session["session_id"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["command_type"] == "temu_capture_all"
    command = queue.get_command_for_workspace(
        workspace_id="default", command_id=body["command_id"]
    )
    assert command.command_type == "temu_capture_all"
    assert command.status == "queued"


def test_incremental_and_resource_commands_and_cancel(tmp_path: Path) -> None:
    queue = DataCollectionPluginQueue(tmp_path / "queue.sqlite3")
    session = queue.create_session(
        actor_id="local-demo-admin",
        workspace_id="default",
        capabilities={
            "temu_order_profit_capture": True,
            "temu_capture_all": True,
            "temu_capture_incremental": True,
            "temu_capture_resource": True,
        },
    )
    service = OrderProfitService(
        OrderProfitRepository(tmp_path / "order_profit.sqlite3"),
        plugin_queue=queue,
    )
    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    incremental = client.post(
        "/api/order-profit/captures-all",
        headers=headers,
        json={"session_id": session["session_id"], "incremental": True},
    )
    assert incremental.status_code == 200
    assert incremental.json()["command_type"] == "temu_capture_incremental"
    # 同一会话同一时间只允许一条采集，先停掉上一条再下发下一条。
    client.post(
        f"/api/order-profit/captures/{incremental.json()['command_id']}/cancel",
        headers=headers,
    )

    resource = client.post(
        "/api/order-profit/captures/resource",
        headers=headers,
        json={"session_id": session["session_id"], "resource": "orders"},
    )
    assert resource.status_code == 200
    assert resource.json()["command_type"] == "temu_capture_resource"
    command_id = resource.json()["command_id"]

    progress = client.post(
        f"/api/order-profit/captures/{command_id}/progress",
        headers=headers,
        json={
            "step": 2,
            "step_total": 6,
            "step_label": "对账中心",
            "detail": "第 1 页",
            "percent": 33,
        },
    )
    assert progress.status_code == 200
    assert progress.json()["percent"] == 33
    status_with_progress = client.get(
        f"/api/order-profit/captures/{command_id}", headers=headers
    ).json()
    assert status_with_progress["progress"]["step_label"] == "对账中心"
    assert status_with_progress["progress"]["percent"] == 33

    cancelled = client.post(
        f"/api/order-profit/captures/{command_id}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["cancelled"] is True
    status = client.get(f"/api/order-profit/captures/{command_id}", headers=headers).json()
    assert status["status"] == "cancelled"


def test_shop_binding_attributes_imports(tmp_path: Path) -> None:
    service = _service(tmp_path)
    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    created = client.post(
        "/api/order-profit/shops",
        headers=headers,
        json={"name": "Bluqevix-US", "note": "美国站主店"},
    )
    assert created.status_code == 200
    assert created.json()["active"]["name"] == "Bluqevix-US"

    second = client.post(
        "/api/order-profit/shops",
        headers=headers,
        json={"name": "Bluqevix-EU", "activate": False},
    )
    assert second.json()["active"]["name"] == "Bluqevix-US"
    shops = client.get("/api/order-profit/shops", headers=headers).json()
    assert [shop["name"] for shop in shops["shops"]] == ["Bluqevix-US", "Bluqevix-EU"]

    # 导入订单时不带 shop_name → 归到当前绑定店铺
    imported = client.post("/api/order-profit/import", headers=headers, json=_capture_result())
    assert imported.status_code == 200
    assert imported.json()["shop_name"] == "Bluqevix-US"
    items, _ = service._repository.list_items("default")
    assert {item["shop_name"] for item in items} == {"Bluqevix-US"}

    # 切换当前店铺后，新导入的数据归到新店铺
    eu = next(shop for shop in shops["shops"] if shop["name"] == "Bluqevix-EU")
    switched = client.post(
        "/api/order-profit/shops/active",
        headers=headers,
        json={"shop_id": eu["id"]},
    )
    assert switched.json()["active"]["name"] == "Bluqevix-EU"

    metrics = client.post(
        "/api/order-profit/store-metrics/import",
        headers=headers,
        json={"metrics": {"pending_shipment": 3, "active_products": 608}},
    )
    assert metrics.status_code == 200
    assert metrics.json()["metrics"]["shop_name"] == "Bluqevix-EU"


def test_shop_binding_scopes_reads_and_backfills_unassigned_data(tmp_path: Path) -> None:
    service = _service(tmp_path)
    # 绑定前先采一份数据：此时没有任何店铺名。
    service.import_capture("default", _capture_result())

    service.bind_shop("default", name="Shop-A")
    first = service.orders("default")
    assert first["shop_name"] == "Shop-A"
    assert first["total"] >= 1
    assert {order["shop_name"] for order in first["orders"]} == {"Shop-A"}

    # 切到新店铺后不能再看到上一个店铺的数据。
    service.bind_shop("default", name="Shop-B")
    second = service.orders("default")
    assert second["shop_name"] == "Shop-B"
    assert second["total"] == 0
    assert service.orders("default", shop="all")["total"] >= 1

    # 快照按店铺分别落库，互不覆盖。
    service.import_store_metrics("default", {"metrics": {"pending_shipment": 5}})
    assert service.store_metrics("default")["metrics"]["pending_shipment"] == 5
    assert service.store_metrics("default", shop="Shop-A")["metrics"] is None

    service.import_traffic(
        "default",
        {
            "scope": "product",
            "period": "近1日",
            "stat_date": "2026-10-24",
            "cards": [{"label": "商品曝光量", "value": "120"}],
        },
    )
    assert service.traffic_summary("default")["product"]["exposure"] == 120
    assert service.traffic_summary("default", shop="Shop-A")["product"] is None


def test_plugin_poll_refreshes_session_capabilities(tmp_path: Path) -> None:
    queue = DataCollectionPluginQueue(tmp_path / "queue.sqlite3")
    session = queue.create_session(
        actor_id="local-demo-admin",
        workspace_id="default",
        capabilities={"temu_order_profit_capture": True},
    )
    assert queue.list_sessions_for_workspace(
        workspace_id="default", command_type="temu_capture_all"
    ) == ()

    queue.poll(
        session["session_token"],
        capabilities={"temu_order_profit_capture": True, "temu_capture_all": True},
    )
    sessions = queue.list_sessions_for_workspace(
        workspace_id="default", command_type="temu_capture_all"
    )
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == session["session_id"]


def test_orders_trend_and_multi_month_filter(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._ingest("default", SimpleNamespace(command_id=201), _capture_result())

    august = _capture_result()
    august_item = {
        **august["items"][1],
        "parent_order_sn": "PO-2",
        "sub_order_sn": "SUB-2",
        "order_time": "2026-08-02 10:00:00",
        "order_status": "已签收",
    }
    august["items"] = [august_item]
    service._ingest("default", SimpleNamespace(command_id=202), august)

    app = FastAPI()
    app.include_router(create_order_profit_router(service), prefix="/api")
    client = TestClient(app)
    headers = {"X-Workspace-ID": "default"}

    trend = client.get(
        "/api/order-profit/orders/trend?months=2026-08,2026-10", headers=headers
    ).json()
    assert trend["months"] == ["2026-08", "2026-10"]
    assert trend["available_months"][:2] == ["2026-10", "2026-08"]
    by_month = {point["month"]: point for point in trend["series"]}
    assert by_month["2026-10"]["order_count"] == 1
    assert by_month["2026-10"]["pending"] == 1
    assert by_month["2026-08"]["signed"] == 1
    assert by_month["2026-08"]["sales_payback"] > 0

    both_months = client.get(
        "/api/order-profit/orders?month=2026-08,2026-10", headers=headers
    ).json()
    assert both_months["total"] == 2

    single_month = client.get(
        "/api/order-profit/orders?month=2026-08", headers=headers
    ).json()
    assert single_month["total"] == 1


def test_plugin_queue_workspace_session_and_command(tmp_path: Path) -> None:
    queue = DataCollectionPluginQueue(tmp_path / "queue.sqlite3")
    session = queue.create_session(
        actor_id="local-demo-admin",
        workspace_id="default",
        capabilities={"temu_order_profit_capture": True},
    )
    sessions = queue.list_sessions_for_workspace(
        workspace_id="default",
        command_type="temu_order_profit_capture",
    )
    assert len(sessions) == 1
    assert sessions[0]["online"] is True
    assert sessions[0]["session_id"] == session["session_id"]

    command = queue.queue_command(
        actor_id="local-demo-admin",
        workspace_id="default",
        session_id=session["session_id"],
        command_type="temu_order_profit_capture",
        payload={"mode": "list"},
        idempotency_key="order-profit:test",
    )
    fetched = queue.get_command_for_workspace(
        workspace_id="default", command_id=command.command_id
    )
    assert fetched.command_type == "temu_order_profit_capture"
    assert fetched.status == "queued"


def test_account_capture_requires_bound_shop_and_passes_shop_guard(tmp_path: Path) -> None:
    queue = DataCollectionPluginQueue(tmp_path / "queue.sqlite3")
    queue.create_session(
        actor_id="local-demo-admin",
        workspace_id="default",
        capabilities={
            "temu_order_profit_capture": True,
            "temu_capture_all": True,
            "instance_tag": "acc-tag",
        },
    )
    service = OrderProfitService(
        OrderProfitRepository(tmp_path / "order_profit.sqlite3"),
        plugin_queue=queue,
    )
    account = service.save_account(
        "default", phone="19100000000", password="secret"
    )["account"]
    # 未绑定店铺时不允许采集，避免数据归错店铺。
    with pytest.raises(ValueError):
        service.start_account_capture("default", account["id"])

    service.record_login_result(
        "default", account["id"], ok=True, shop_name="Little Pony Shop"
    )
    service._repository.set_account_instance(
        "default", account["id"], tag="acc-tag", pid=1234
    )

    body = service.start_account_capture("default", account["id"])
    command = queue.get_command_for_workspace(
        workspace_id="default", command_id=body["command_id"]
    )
    assert command.command_type == "temu_capture_all"
    assert command.payload["account_id"] == account["id"]
    # 插件用这个店铺名核对登录店铺，并作为入库归属。
    assert command.payload["shop_name"] == "Little Pony Shop"
    assert service.accounts("default")["accounts"][0]["status"] == "capturing"


def test_batch_id_is_date_plus_sequence(tmp_path: Path) -> None:
    repository = OrderProfitRepository(tmp_path / "order_profit.sqlite3")
    first = repository.create_batch(
        "default", command_id=1, capture_mode="order_list",
        order_count=1, sub_order_count=1, item_count=1,
        captured_at="2026-10-28T00:00:00",
    )
    second = repository.create_batch(
        "default", command_id=2, capture_mode="order_list",
        order_count=1, sub_order_count=1, item_count=1,
        captured_at="2026-10-28T00:00:00",
    )
    assert re.fullmatch(r"\d{8}-01", first["id"]), first["id"]
    assert re.fullmatch(r"\d{8}-02", second["id"]), second["id"]
    assert first["id"][:8] == second["id"][:8]


def test_clear_account_data_keeps_shop_and_binding(tmp_path: Path) -> None:
    service = _service(tmp_path)
    account = service.save_account("default", phone="19100000002", password="x")["account"]
    service.record_login_result(
        "default", account["id"], ok=True, shop_name="Little Pony Shop"
    )
    service.import_capture(
        "default", {**_capture_result(), "shop_name": "Little Pony Shop"}
    )
    repository = service._repository
    _, before = repository.list_items("default", shop="Little Pony Shop")
    assert before > 0

    response = service.clear_account_data("default", account["id"])
    assert response["shop_name"] == "Little Pony Shop"
    assert response["cleared"]["order_profit_items"] == before

    _, after = repository.list_items("default", shop="Little Pony Shop")
    assert after == 0
    # 店铺和账号绑定都保留，只是数据被清空。
    assert [shop["name"] for shop in repository.list_shops("default")] == ["Little Pony Shop"]
    assert service.accounts("default")["accounts"][0]["shop_name"] == "Little Pony Shop"


def test_repository_startup_survives_missing_migrations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """打包漏了 migrations 目录时只能缺表，不能让整个后端启动失败。"""
    from wh_local.modules.order_profit import repository as repository_module

    monkeypatch.setattr(repository_module, "_MIGRATIONS", tmp_path / "missing")
    repo = repository_module.OrderProfitRepository(tmp_path / "order_profit.sqlite3")
    with repo._connect() as conn:  # noqa: SLF001 - 直接断言落库结果
        assert not repo._table_exists(conn, "profit_shops")
        # 与订单利润无关的表仍应建好，保证账号页可用。
        assert repo._table_exists(conn, "store_accounts")
