from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from wh_local.billing import (
    bind_customer_station,
    get_customer_station_binding,
    station_rebate_settlement_sweep,
)

# auth_server 为服务器侧模块、未随仓库分发（c053f438c 已移除）；缺失时本组用例整体跳过。
pytest.importorskip("wh_local.customer.auth_server")
from wh_local.customer.auth_server import create_auth_app
from wh_local.db import transaction

from test_customer_billing import _EMAIL_CODE_SECRET, _register_and_login


def _client_and_headers(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path, dict[str, str]]:
    database_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    client = TestClient(create_auth_app(database_path))
    token = _register_and_login(client, database_path)
    return client, database_path, {"Authorization": f"Bearer {token}"}


def _fake_partner_detail(station_code: str, *, name: str = "", rate: float = 100.0) -> dict:
    return {
        "ok": True,
        "station_code": station_code,
        "station_name": name or f"{station_code}中转站",
        "tiers": [{"amount_cents": 1_000, "rate": rate}],
    }


def test_first_binding_then_rebind_is_rate_limited_for_seven_days(tmp_path: Path, monkeypatch) -> None:
    _, db_path, _ = _client_and_headers(tmp_path, monkeypatch)
    with transaction(db_path) as conn:
        first = bind_customer_station(
            conn,
            account_id="acct-bind-1",
            station_code="AA01",
            station_name="甲站",
            now="2026-01-01T00:00:00+00:00",
        )
        assert first["bound"] is True
        assert first["change_count"] == 0
        assert first["can_change"] is False

        # 同站重复提交幂等：不消耗次数、不重置冷却起点。
        again = bind_customer_station(
            conn,
            account_id="acct-bind-1",
            station_code="AA01",
            now="2026-01-02T00:00:00+00:00",
        )
        assert again["change_count"] == 0
        assert again["station_code"] == "AA01"

        # 冷却窗口内换到别的中转站被拒绝。
        with pytest.raises(HTTPException) as excinfo:
            bind_customer_station(
                conn,
                account_id="acct-bind-1",
                station_code="BB02",
                now="2026-01-05T00:00:00+00:00",
            )
        assert excinfo.value.status_code == 409

        # 满 7 天后放行，并累计换绑次数。
        switched = bind_customer_station(
            conn,
            account_id="acct-bind-1",
            station_code="BB02",
            station_name="乙站",
            now="2026-01-08T00:00:00+00:00",
        )
        assert switched["station_code"] == "BB02"
        assert switched["station_name"] == "乙站"
        assert switched["change_count"] == 1


def test_unbound_account_reports_bindable_view(tmp_path: Path, monkeypatch) -> None:
    _, db_path, _ = _client_and_headers(tmp_path, monkeypatch)
    with transaction(db_path) as conn:
        view = get_customer_station_binding(conn, account_id="acct-never-bound")
    assert view["bound"] is False
    assert view["station_code"] == ""
    assert view["can_change"] is True
    assert view["next_change_at"] == ""


def test_topup_with_station_code_binds_account_and_status_route_exposes_it(
    tmp_path: Path, monkeypatch
) -> None:
    client, db_path, headers = _client_and_headers(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "wh_local.customer.auth_server._station_partner_detail",
        lambda code: _fake_partner_detail(code, name="甲站"),
    )

    before = client.get("/api/customer/billing/station-binding", headers=headers)
    assert before.status_code == 200
    assert before.json()["binding"]["bound"] is False

    created = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": "station:AA01:1000",
            "idempotency_key": "station-bind-order-0001",
        },
        headers=headers,
    )
    assert created.status_code == 200

    after = client.get("/api/customer/billing/station-binding", headers=headers)
    binding = after.json()["binding"]
    assert binding["bound"] is True
    assert binding["station_code"] == "AA01"
    assert binding["station_name"] == "甲站"
    assert binding["change_count"] == 0

    # 冷却窗口内下单到另一个中转站：绑定被拒绝，且不会生成指向新站点的订单。
    blocked = client.post(
        "/api/customer/billing/topup-orders",
        json={
            "provider": "alipay",
            "package_id": "station:BB02:1000",
            "idempotency_key": "station-bind-order-0002",
        },
        headers=headers,
    )
    assert blocked.status_code == 409

    with transaction(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM billing_payment_orders WHERE station_code='BB02'"
        ).fetchone()["n"]
    assert count == 0


def test_confirm_station_binding_route_persists_and_enforces_cooldown(
    tmp_path: Path, monkeypatch
) -> None:
    client, _, headers = _client_and_headers(tmp_path, monkeypatch)
    valid_codes = {"AA01", "BB02"}

    def fake_detail(code: str) -> dict:
        if code not in valid_codes:
            raise HTTPException(status_code=404, detail="该中转编号不是合作中的中转站")
        return _fake_partner_detail(code, name=f"{code}站")

    monkeypatch.setattr("wh_local.customer.auth_server._station_partner_detail", fake_detail)

    invalid = client.post(
        "/api/customer/billing/station-binding",
        json={"station_code": "NOPE"},
        headers=headers,
    )
    assert invalid.status_code == 404

    created = client.post(
        "/api/customer/billing/station-binding",
        json={"station_code": "AA01"},
        headers=headers,
    )
    assert created.status_code == 200
    binding = created.json()["binding"]
    assert binding["bound"] is True
    assert binding["station_code"] == "AA01"
    assert binding["station_name"] == "AA01站"
    assert binding["change_count"] == 0

    # 冷却窗口内确认换到另一个中转站被拒绝。
    blocked = client.post(
        "/api/customer/billing/station-binding",
        json={"station_code": "BB02"},
        headers=headers,
    )
    assert blocked.status_code == 409

    # 同站重复确认幂等，不消耗换绑次数。
    again = client.post(
        "/api/customer/billing/station-binding",
        json={"station_code": "AA01"},
        headers=headers,
    )
    assert again.status_code == 200
    assert again.json()["binding"]["change_count"] == 0


def test_station_rebate_settlement_sweep_settles_only_due_rows(tmp_path: Path, monkeypatch) -> None:
    _, db_path, _ = _client_and_headers(tmp_path, monkeypatch)
    with transaction(db_path) as conn:
        conn.execute(
            "INSERT INTO station_rebate_ledger (station_code, order_id, out_trade_no, account_id,"
            " package_id, amount_cents, tier_rate, rebate_cents, status, accrued_at, settle_due_at)"
            " VALUES ('AA01','ord-due','t1','acct-1','station:AA01:1000',1000,100.0,50,'accrued',"
            " '2000-01-01T00:00:00+00:00','2000-01-08T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO station_rebate_ledger (station_code, order_id, out_trade_no, account_id,"
            " package_id, amount_cents, tier_rate, rebate_cents, status, accrued_at, settle_due_at)"
            " VALUES ('AA01','ord-future','t2','acct-1','station:AA01:1000',1000,100.0,50,'accrued',"
            " '2026-01-01T00:00:00+00:00','2999-01-01T00:00:00+00:00')"
        )

    moved = station_rebate_settlement_sweep(db_path)
    assert moved == 1

    with transaction(db_path) as conn:
        rows = {
            row["order_id"]: row["status"]
            for row in conn.execute(
                "SELECT order_id, status FROM station_rebate_ledger"
            ).fetchall()
        }
    assert rows["ord-due"] == "settled"
    assert rows["ord-future"] == "accrued"

    # 幂等：再扫一次不再结转任何行。
    assert station_rebate_settlement_sweep(db_path) == 0
