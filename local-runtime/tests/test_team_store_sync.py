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
    # 本账号所有店铺都上报：没拿到正式码的店铺带着本地临时码一起提交
    assert len(payload["stores"]) == 2
    by_code = {store["store_code"]: store for store in payload["stores"]}
    store = by_code["20261026-01"]
    assert store["store_name"] == "北美一号"
    assert store["days"][0]["stat_date"] == "2026-10-20"
    assert store["days"][0]["order_count"] == 1
    assert store["days"][0]["gmv_cny"] == pytest.approx(100)
    assert [code for code in by_code if code.startswith("LOCAL-")]
    assert payload["pending_local_shops"] == []
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


def test_submit_week_submits_shops_that_only_have_local_code(tmp_path: Path) -> None:
    """只有本地临时码的店铺也要提交：补码失败时按现有码上报，不再整批跳过。"""
    repository = OrderProfitRepository(tmp_path / "empty.sqlite3")
    repository.upsert_shop("default", "只有临时码")
    client = FakeRollupClient()
    result = submit_week(
        repository,
        "default",
        client=client,
        token="remote-token",
    )
    assert result["ok"] is True
    assert result["stores"][0]["name"] == "只有临时码"
    assert client.payloads[0]["stores"][0]["store_code"].startswith("LOCAL-")


def test_validation_error_detail_is_surfaced() -> None:
    """团队服务的 422 校验错误是列表，必须把具体字段讲出来而不是只说「返回 422」。"""
    import io
    import json
    import urllib.error

    from wh_local.modules.team_store import client as client_module

    body = json.dumps(
        {
            "detail": [
                {
                    "loc": ["body", "batches"],
                    "msg": "Extra inputs are not permitted",
                    "type": "extra_forbidden",
                }
            ]
        }
    ).encode("utf-8")
    error = urllib.error.HTTPError("http://team.local/rollup/submit", 422, "Unprocessable", {}, io.BytesIO(body))
    assert client_module._error_detail(error) == "batches: Extra inputs are not permitted"


def test_build_preview_lists_every_shop_of_the_account(tmp_path: Path) -> None:
    """提交确认弹窗要能看到本账号全部店铺，而不只是拿到正式码的那几家。"""
    from wh_local.modules.team_store.sync import build_preview

    repository = _repository(tmp_path)
    _ingest(
        repository,
        shop="北美一号",
        parent="PO-P",
        sub="SUB-P",
        sku="SKU-P",
        order_time="2026-10-20 10:00:00",
        gmv=66,
        command_id=5,
    )
    preview = build_preview(repository, "default", week_start="2026-10-19")

    names = [store["name"] for store in preview["stores"]]
    assert names == ["北美一号", "欧洲二号"]
    assert preview["week_start"] == "2026-10-19"
    assert preview["week_end"] == "2026-10-25"
    assert preview["stores"][0]["day_count"] == 1
    assert preview["missing_code_shops"] == []
    assert preview["day_count"] == 1
