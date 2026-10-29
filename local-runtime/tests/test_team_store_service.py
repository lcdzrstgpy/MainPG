from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wh_local.modules.team_store.auth import AccountResolver, _pick_account_id
from wh_local.modules.team_store.dedupe import FLAG_SAME_STORE_MULTI_ACCOUNT
from wh_local.modules.team_store.repository import TeamRepository
from wh_local.modules.team_store.routes import create_team_app
from wh_local.modules.team_store.rules import parse_store_code
from wh_local.modules.team_store.service import (
    StaffNoLocked,
    StaffNoRequired,
    StaffNoTaken,
    TeamStoreService,
)


class FakeResolver:
    def __init__(self, account_id: str = "acc-1") -> None:
        self.account_id = account_id

    def resolve(self, authorization: str | None) -> str:
        if not self.account_id:
            raise PermissionError("登录已过期")
        return self.account_id

    def resolve_identity(self, authorization: str | None) -> tuple[str, str]:
        return self.resolve(authorization), ""


@pytest.fixture
def service(tmp_path: Path) -> TeamStoreService:
    return TeamStoreService(TeamRepository(tmp_path / "team.sqlite3"))


def _client(service: TeamStoreService, resolver: FakeResolver) -> TestClient:
    return TestClient(create_team_app(service, resolver))


def test_store_requires_staff_no_first(service: TeamStoreService) -> None:
    client = _client(service, FakeResolver("acc-1"))
    response = client.post("/api/team/stores", json={"name": "北美一号"})
    assert response.status_code == 400
    assert "员工标识" in response.json()["detail"]


def test_profile_then_create_store_issues_code(service: TeamStoreService) -> None:
    client = _client(service, FakeResolver("acc-1"))
    profile = client.post("/api/team/profile", json={"staff_no": "张三"})
    assert profile.status_code == 200
    assert profile.json()["profile"]["staff_no"] == "张三"

    created = client.post("/api/team/stores", json={"name": "Bluqevix-US"})
    assert created.status_code == 200
    body = created.json()
    assert body["created"] is True
    assert body["conflict"] == ""
    store = body["store"]
    assert store["role"] == "owner"
    assert store["store_name"] == "Bluqevix-US"
    assert parse_store_code(store["store_code"])[0] == date.today()


def test_same_name_same_account_reuses_store(service: TeamStoreService) -> None:
    service.save_profile("acc-1", staff_no="张三")
    first = service.register_store("acc-1", name="北美一号")
    second = service.register_store("acc-1", name=" 北美一号 ")
    assert second["created"] is False
    assert second["store"]["store_code"] == first["store"]["store_code"]
    # 不能重复发号
    assert len(service.list_stores("acc-1")["stores"]) == 1


def test_same_name_other_account_becomes_helper_with_flag(
    service: TeamStoreService,
) -> None:
    service.save_profile("acc-1", staff_no="张三")
    service.save_profile("acc-2", staff_no="李四")
    owner_store = service.register_store("acc-1", name="北美一号")

    result = service.register_store("acc-2", name="北美一号")
    assert result["created"] is False
    assert result["conflict"] == "other_account"
    assert result["store"]["store_code"] == owner_store["store"]["store_code"]
    assert result["store"]["role"] == "helper"
    assert result["store"]["owner_account_id"] == "acc-1"

    # 不新建店铺，只挂协助关系
    assert len(service.list_stores("acc-2")["stores"]) == 1
    flags = service.list_flags("acc-2")["flags"]
    assert len(flags) == 1
    assert flags[0]["kind"] == FLAG_SAME_STORE_MULTI_ACCOUNT


def test_store_name_rule_is_enforced(service: TeamStoreService) -> None:
    service.save_profile("acc-1", staff_no="张三")
    with pytest.raises(Exception) as error:
        service.register_store("acc-1", name="一二三四五六七八九十一二三四五六七八九十一")
    assert "汉字" in str(error.value)


def test_staff_no_is_unique_and_locked(service: TeamStoreService) -> None:
    service.save_profile("acc-1", staff_no="张三")
    # 同一账号提交相同标识是幂等的
    assert service.save_profile("acc-1", staff_no="张三")["changed"] is False
    # 同账号改成别的标识不允许
    with pytest.raises(StaffNoLocked):
        service.save_profile("acc-1", staff_no="张三丰")
    # 别的账号不能用已被占用的标识
    with pytest.raises(StaffNoTaken):
        service.save_profile("acc-2", staff_no="张三")


def test_missing_staff_no_raises(service: TeamStoreService) -> None:
    with pytest.raises(StaffNoRequired):
        service.register_store("acc-1", name="北美一号")


def test_routes_return_401_without_login(service: TeamStoreService) -> None:
    client = _client(service, FakeResolver(""))
    assert client.get("/api/team/stores").status_code == 401


def test_health_endpoint(service: TeamStoreService) -> None:
    client = _client(service, FakeResolver("acc-1"))
    assert client.get("/health").json()["status"] == "ok"


def test_account_resolver_dev_mode() -> None:
    resolver = AccountResolver(dev_account_id="acc-9")
    assert resolver.resolve(None) == "acc-9"


def test_account_resolver_requires_token_in_production() -> None:
    resolver = AccountResolver(auth_base_url="http://127.0.0.1:9")
    with pytest.raises(PermissionError):
        resolver.resolve(None)


def test_pick_account_id_handles_nested_payloads() -> None:
    assert _pick_account_id({"account_id": "a1"}) == "a1"
    assert _pick_account_id({"user": {"customer_id": "c1"}}) == "c1"
    assert _pick_account_id({"data": [{"user_id": "u1"}]}) == "u1"
    assert _pick_account_id({"foo": "bar"}) == ""
