from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from wh_local.modules.order_profit.repository import OrderProfitRepository
from wh_local.modules.order_profit.service import OrderProfitService
from wh_local.modules.team_store.sync import build_payload, submit_week, week_bounds


class FakeRollupClient:
    configured = True

    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def submit_rollup(self, token: str, payload: dict) -> dict:
        self.payloads.append(payload)
        return {"ok": True, "accepted": 3, "created": 3, "updated": 0, "stale": 0}


def _repository(tmp_path: Path) -> OrderProfitRepository:
    repository = OrderProfitRepository(tmp_path / "order_profit.sqlite3")
    repository.upsert_shop(
        "default",
        "北美一号",
        store_code="20261026-01",
        code_status="official",
    )
    repository.upsert_shop("default", "欧洲二号")
    return repository


def _ingest(
    repository: OrderProfitRepository,
    *,
    shop: str,
    parent: str,
    sub: str,
    sku: str,
    order_time: str,
    gmv: float,
    command_id: int,
) -> None:
    service = OrderProfitService(repository)
    items = [
        {
            "parent_order_sn": parent,
            "sub_order_sn": sub,
            "sku_id": sku,
            "shop_name": shop,
            "order_time": order_time,
            "gmv": gmv,
            "gmv_cny": gmv,
            "gmv_currency": "CNY",
            "quantity": 1,
        }
    ]
    service._ingest(
        "default",
        SimpleNamespace(command_id=command_id),
        {
            "ok": True,
            "capture_mode": "order_list",
            "items": items,
            "summary": {"order_count": 1, "sub_order_count": 1, "item_count": 1},
            "capturedAt": "2026-10-24T00:00:00Z",
        },
    )


def test_week_bounds_returns_monday_to_sunday() -> None:
    monday, sunday = week_bounds("2026-10-22")
    assert monday.isoformat() == "2026-10-19"
    assert sunday.isoformat() == "2026-10-25"


def test_build_payload_groups_days_by_shop(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _ingest(
        repository,
        shop="北美一号",
        parent="PO-1",
        sub="SUB-1",
        sku="SKU-1",
        order_time="2026-10-20 10:00:00",
        gmv=100,
        command_id=1,
    )
    payload = build_payload(repository, "default", week_start="2026-10-19")

    assert payload["week_start"] == "2026-10-19"
    assert len(payload["stores"]) == 1  # 只有拿到正式码的店铺上报
    store = payload["stores"][0]
    assert store["store_code"] == "20261026-01"
    assert store["days"][0]["stat_date"] == "2026-10-20"
    assert store["days"][0]["order_count"] == 1
    assert store["days"][0]["gmv_cny"] == pytest.approx(100)
    # 本地临时码的店铺单独列出来提醒
    assert payload["pending_local_shops"][0]["name"] == "欧洲二号"
    # 明细随汇总一起上传，团队看板据此展示订单/财务等页面
    records = store["records"]
    assert any(
        item["kind"] == "order" and item["payload"]["parent_order_sn"] == "PO-1"
        for item in records
    )
    assert payload["record_count"] == len(records) >= 1


def test_build_payload_includes_history_days(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _ingest(
        repository,
        shop="北美一号",
        parent="PO-OLD",
        sub="SUB-OLD",
        sku="SKU-OLD",
        order_time="2026-09-01 10:00:00",
        gmv=42,
        command_id=9,
    )
    payload = build_payload(repository, "default", week_start="2026-10-19")
    dates = [row["stat_date"] for row in payload["stores"][0]["days"]]
    # 以前日期的汇总也要一起上传，否则团队看板的趋势图只有本周
    assert "2026-09-01" in dates


def test_duplicate_orders_detected_across_shops(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.upsert_shop(
        "default",
        "欧洲二号",
        store_code="20261027-01",
        code_status="official",
    )
    for index, shop in enumerate(("北美一号", "欧洲二号"), start=1):
        _ingest(
            repository,
            shop=shop,
            parent="PO-DUP",
            sub="SUB-DUP",
            sku="SKU-DUP",
            order_time="2026-10-21 09:00:00",
            gmv=50,
            command_id=index,
        )

    duplicates = repository.duplicate_orders("default")
    assert len(duplicates) == 1
    assert duplicates[0]["dup_count"] == 1

    payload = build_payload(repository, "default", week_start="2026-10-19")
    by_code = {store["store_code"]: store for store in payload["stores"]}
    assert by_code["20261026-01"]["dup_orders"]["count"] == 1
    assert by_code["20261026-01"]["dup_orders"]["other_shops"] == ["欧洲二号"]


def test_submit_week_posts_payload(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _ingest(
        repository,
        shop="北美一号",
        parent="PO-9",
        sub="SUB-9",
        sku="SKU-9",
        order_time="2026-10-22 12:00:00",
        gmv=88,
        command_id=3,
    )
    client = FakeRollupClient()
    result = submit_week(
        repository,
        "default",
        client=client,
        token="remote-token",
        week_start="2026-10-19",
    )
    assert result["ok"] is True
    assert result["week_start"] == "2026-10-19"
    assert client.payloads[0]["stores"][0]["store_code"] == "20261026-01"


def test_submit_week_without_official_store_returns_hint(tmp_path: Path) -> None:
    repository = OrderProfitRepository(tmp_path / "empty.sqlite3")
    repository.upsert_shop("default", "只有临时码")
    result = submit_week(
        repository,
        "default",
        client=FakeRollupClient(),
        token="remote-token",
    )
    assert result["ok"] is False
    assert result["reason"] == "no_official_store"
    assert "同步店铺码" in result["status_text"]
