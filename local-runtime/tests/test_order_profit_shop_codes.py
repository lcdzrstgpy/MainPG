from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.modules.order_profit.repository import OrderProfitRepository
from wh_local.modules.order_profit.routes import create_order_profit_router
from wh_local.modules.order_profit.service import (
    OrderProfitService,
    StaffNoRequiredError,
)
from wh_local.modules.team_store.client import TeamApiError


class FakeTeamClient:
    configured = True

    def __init__(self, *, store_code: str = "20261026-01", conflict: str = "") -> None:
        self._store_code = store_code
        self._conflict = conflict
        self.calls: list[dict] = []

    def register_store(self, token: str, *, name: str, platform: str = "temu", mall_id: str = ""):
        self.calls.append({"token": token, "name": name, "mall_id": mall_id})
        return {
            "ok": True,
            "created": True,
            "conflict": self._conflict,
            "store": {"store_code": self._store_code, "store_name": name},
        }


class BrokenTeamClient:
    configured = True

    def __init__(self, error: Exception) -> None:
        self._error = error

    def register_store(self, token: str, **kwargs):
        raise self._error


def _service(tmp_path: Path) -> OrderProfitService:
    return OrderProfitService(OrderProfitRepository(tmp_path / "order_profit.sqlite3"))


def test_bind_shop_without_team_creates_local_code(tmp_path: Path) -> None:
    shop = _service(tmp_path).bind_shop("default", name="北美一号")["shop"]
    assert shop["store_code"].startswith("LOCAL-")
    assert shop["code_status"] == "local"


def test_bind_shop_uses_official_code_from_team(tmp_path: Path) -> None:
    client = FakeTeamClient(store_code="20261026-07")
    result = _service(tmp_path).bind_shop(
        "default",
        name="北美一号",
        team_client=client,
        remote_token="remote-token",
    )
    assert result["shop"]["store_code"] == "20261026-07"
    assert result["shop"]["code_status"] == "official"
    assert result["sync_warning"] == ""
    assert client.calls[0]["token"] == "remote-token"


def test_bind_shop_requires_staff_no(tmp_path: Path) -> None:
    client = BrokenTeamClient(
        TeamApiError("首次创建店铺需要先填写员工标识", status=400, code="staff_no_required")
    )
    with pytest.raises(StaffNoRequiredError):
        _service(tmp_path).bind_shop(
            "default",
            name="北美一号",
            team_client=client,
            remote_token="remote-token",
        )


def test_bind_shop_degrades_when_team_unavailable(tmp_path: Path) -> None:
    client = BrokenTeamClient(TeamApiError("无法连接团队服务：timed out"))
    result = _service(tmp_path).bind_shop(
        "default",
        name="北美一号",
        team_client=client,
        remote_token="remote-token",
    )
    assert result["shop"]["store_code"].startswith("LOCAL-")
    assert "本地临时店铺码" in result["sync_warning"]


def test_same_store_other_account_reports_conflict(tmp_path: Path) -> None:
    client = FakeTeamClient(conflict="other_account")
    result = _service(tmp_path).bind_shop(
        "default",
        name="北美一号",
        team_client=client,
        remote_token="remote-token",
    )
    assert "协助录入" in result["sync_warning"]


def test_sync_shop_code_replaces_local_code(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = service.bind_shop("default", name="北美一号")["shop"]
    assert created["code_status"] == "local"

    synced = service.sync_shop_code(
        "default",
        created["id"],
        team_client=FakeTeamClient(store_code="20261027-01"),
        remote_token="remote-token",
    )
    assert synced["shop"]["store_code"] == "20261027-01"
    assert synced["shop"]["code_status"] == "official"


def test_shop_code_columns_are_added_on_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "order_profit.sqlite3"
    OrderProfitRepository(path)
    # 重复初始化必须幂等：老库升级列不能因为「列已存在」崩掉
    repository = OrderProfitRepository(path)
    assert repository.list_shops("default") == []


def test_route_returns_local_code_without_login(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(create_order_profit_router(_service(tmp_path)), prefix="/api")
    client = TestClient(app)
    response = client.post("/api/order-profit/shops", json={"name": "北美一号"})
    assert response.status_code == 200
    assert response.json()["shop"]["store_code"].startswith("LOCAL-")


def test_route_maps_staff_no_required_to_400(tmp_path: Path) -> None:
    broken = BrokenTeamClient(
        TeamApiError("首次创建店铺需要先填写员工标识", status=400, code="staff_no_required")
    )
    app = FastAPI()
    app.include_router(
        create_order_profit_router(
            _service(tmp_path),
            team_client=broken,
            remote_token_provider=lambda authorization: "remote-token",
        ),
        prefix="/api",
    )
    client = TestClient(app)
    response = client.post("/api/order-profit/shops", json={"name": "北美一号"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "staff_no_required"
