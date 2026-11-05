from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from wh_local.data_collection.plugin_queue import (
    TEMU_CAPTURE_ALL,
    DataCollectionPluginQueue,
)
from wh_local.modules.order_profit.repository import OrderProfitRepository
from wh_local.modules.order_profit.service import OrderProfitService


def _ingest_order(service: OrderProfitService) -> None:
    items = [
        {
            "parent_order_sn": "PO-COST",
            "sub_order_sn": "SUB-1",
            "sku_id": "SKU-1",
            "quantity": 1,
            "gmv": 100,
            "gmv_cny": 100,
            "gmv_currency": "CNY",
            "shop_name": "北美一号",
        },
        {
            "parent_order_sn": "PO-COST",
            "sub_order_sn": "SUB-2",
            "sku_id": "SKU-2",
            "quantity": 2,
            "gmv": 200,
            "gmv_cny": 200,
            "gmv_currency": "CNY",
            "shop_name": "北美一号",
        },
    ]
    service._ingest(
        "default",
        SimpleNamespace(command_id=1),
        {
            "ok": True,
            "capture_mode": "order_list",
            "items": items,
            "capturedAt": "2026-10-27T00:00:00Z",
        },
    )


def test_update_order_cost_distributes_by_quantity(tmp_path: Path) -> None:
    repository = OrderProfitRepository(tmp_path / "op.sqlite3")
    service = OrderProfitService(repository)
    _ingest_order(service)

    result = service.update_order_cost("default", "PO-COST", purchase_cost_cny=90)
    assert result["updated"] == 2

    rows = {row["sku_id"]: row for row in repository.list_items_by_parent("default", "PO-COST")}
    # 按件数 1:2 分摊 90 元
    assert rows["SKU-1"]["manual_cost_cny"] == pytest.approx(30)
    assert rows["SKU-2"]["manual_cost_cny"] == pytest.approx(60)
    assert rows["SKU-1"]["purchase_cost_cny"] == pytest.approx(30)
    assert rows["SKU-1"]["cost_source"] == "manual"
    # 利润按新成本重算：100 - 佣金5 - 成本30
    assert rows["SKU-1"]["profit_estimate_cny"] == pytest.approx(65)


def test_update_order_cost_can_be_cleared(tmp_path: Path) -> None:
    repository = OrderProfitRepository(tmp_path / "op.sqlite3")
    service = OrderProfitService(repository)
    _ingest_order(service)
    service.update_order_cost("default", "PO-COST", purchase_cost_cny=90)

    service.update_order_cost("default", "PO-COST", purchase_cost_cny=None)
    rows = repository.list_items_by_parent("default", "PO-COST")
    assert all(row["manual_cost_cny"] is None for row in rows)
    assert all(row["purchase_cost_cny"] is None for row in rows)


def test_update_order_cost_rejects_negative(tmp_path: Path) -> None:
    repository = OrderProfitRepository(tmp_path / "op.sqlite3")
    service = OrderProfitService(repository)
    _ingest_order(service)
    with pytest.raises(ValueError):
        service.update_order_cost("default", "PO-COST", purchase_cost_cny=-1)


def test_progress_accumulates_steps(tmp_path: Path) -> None:
    queue = DataCollectionPluginQueue(tmp_path / "queue.sqlite3")
    session = queue.create_session(
        actor_id="local-demo-admin",
        workspace_id="default",
        capabilities={TEMU_CAPTURE_ALL: True},
    )
    service = OrderProfitService(
        OrderProfitRepository(tmp_path / "op.sqlite3"), plugin_queue=queue
    )
    command = queue.queue_command(
        actor_id="local-demo-admin",
        workspace_id="default",
        session_id=session["session_id"],
        command_type=TEMU_CAPTURE_ALL,
        payload={},
        idempotency_key="progress-1",
    )

    service.report_progress(
        "default",
        command.command_id,
        {"key": "store", "state": "running", "label": "店铺首页", "step": 1, "step_total": 3},
    )
    service.report_progress(
        "default",
        command.command_id,
        {
            "key": "store",
            "state": "ok",
            "label": "店铺首页",
            "detail": "待发货 12 · 在售 111",
            "step": 1,
            "step_total": 3,
            "percent": 33,
        },
    )
    service.report_progress(
        "default",
        command.command_id,
        {"key": "traffic", "state": "running", "label": "商品流量", "step": 2, "step_total": 3, "percent": 33},
    )

    status = service.capture_status("default", command.command_id)
    steps = status["progress"]["steps"]
    assert [step["key"] for step in steps] == ["store", "traffic"]
    assert steps[0]["state"] == "ok"
    assert steps[0]["detail"] == "待发货 12 · 在售 111"
    assert steps[1]["state"] == "running"
