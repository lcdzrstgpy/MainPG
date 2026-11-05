from __future__ import annotations

from pathlib import Path

import pytest

from wh_local.modules.team_store.repository import TeamRepository
from wh_local.modules.team_store.service import TeamStoreService


@pytest.fixture
def service(tmp_path: Path) -> TeamStoreService:
    return TeamStoreService(TeamRepository(tmp_path / "team.sqlite3"))


def _seed(service: TeamStoreService) -> dict[str, str]:
    for account, staff in (("acc-leader", "组长"), ("acc-a", "张三"), ("acc-b", "李四")):
        service.save_profile(account, staff_no=staff)
    store_a = service.register_store("acc-a", name="店A")["store"]["store_code"]
    store_b = service.register_store("acc-b", name="店B")["store"]["store_code"]
    for account, code, gmv in (("acc-a", store_a, 100.0), ("acc-b", store_b, 300.0)):
        service.submit_rollup(
            account,
            week_start="2026-10-19",
            submitted_at="2026-10-24T10:00:00+08:00",
            stores=[
                {
                    "store_code": code,
                    "days": [
                        {
                            "stat_date": "2026-10-20",
                            "source_updated_at": "2026-10-20T23:00:00+08:00",
                            "order_count": 2,
                            "gmv_cny": gmv,
                            "profit_cny": gmv / 2,
                        }
                    ],
                }
            ],
        )
    service._repository.create_group(
        group_id="g-1",
        name="华东组",
        leader_account_id="acc-leader",
        now="2026-10-24T10:00:00+08:00",
    )
    service._repository.add_member(
        group_id="g-1", account_id="acc-a", now="2026-10-24T10:00:00+08:00"
    )
    service._repository.add_member(
        group_id="g-1", account_id="acc-b", now="2026-10-24T10:00:00+08:00"
    )
    return {"store_a": store_a, "store_b": store_b}


def test_scope_includes_members_of_led_groups(service: TeamStoreService) -> None:
    _seed(service)
    scope = service.scope("acc-leader")
    assert scope["is_leader"] is True
    assert set(scope["visible_account_ids"]) == {"acc-leader", "acc-a", "acc-b"}
    assert scope["visible_store_count"] == 2

    # 普通成员只能看到自己
    own = service.scope("acc-a")
    assert own["is_leader"] is False
    assert own["visible_account_ids"] == ["acc-a"]
    assert own["visible_store_count"] == 1


def test_summary_by_shop_and_account(service: TeamStoreService) -> None:
    _seed(service)
    by_shop = service.summary(
        "acc-leader", start="2026-10-19", end="2026-10-25", group_by="shop"
    )
    assert len(by_shop["rows"]) == 2
    assert by_shop["totals"]["gmv_cny"] == pytest.approx(400.0)
    assert by_shop["totals"]["order_count"] == pytest.approx(4)

    by_account = service.summary(
        "acc-leader", start="2026-10-19", end="2026-10-25", group_by="account"
    )
    labels = {row["label"] for row in by_account["rows"]}
    assert labels == {"张三", "李四"}


def test_member_cannot_see_other_accounts(service: TeamStoreService) -> None:
    _seed(service)
    result = service.summary(
        "acc-a", start="2026-10-19", end="2026-10-25", group_by="shop"
    )
    assert len(result["rows"]) == 1
    assert result["totals"]["gmv_cny"] == pytest.approx(100.0)


def test_overview_returns_trend_and_top_stores(service: TeamStoreService) -> None:
    _seed(service)
    overview = service.overview("acc-leader", start="2026-10-19", end="2026-10-25")
    assert len(overview["trend"]) == 1
    assert overview["trend"][0]["key"] == "2026-10-20"
    assert overview["top_stores"][0]["gmv_cny"] == pytest.approx(300.0)
    assert len(overview["stores"]) == 2
    assert overview["totals"]["gmv_cny"] == pytest.approx(400.0)


def test_overview_all_dates_ignores_range(service: TeamStoreService) -> None:
    _seed(service)
    # 起止为空 = 所有日期
    overview = service.overview("acc-leader", start="", end="")
    assert overview["totals"]["gmv_cny"] == pytest.approx(400.0)
    # 回显实际有数据的首尾日期
    assert overview["start"] == "2026-10-20"
    assert overview["end"] == "2026-10-20"

    empty = service.overview("acc-leader", start="2025-01-01", end="2025-01-31")
    assert empty["totals"]["gmv_cny"] == pytest.approx(0.0)


def test_overview_excludes_hidden_stores(service: TeamStoreService) -> None:
    codes = _seed(service)
    overview = service.overview(
        "acc-leader", start="", end="", exclude=[codes["store_b"]]
    )
    assert overview["totals"]["gmv_cny"] == pytest.approx(100.0)
    assert [item["store_code"] for item in overview["stores"]] == [codes["store_a"]]
    # 筛选面板仍然拿到全部店铺，方便再勾回来
    assert len(overview["all_stores"]) == 2


def test_trends_returns_store_series_and_totals(service: TeamStoreService) -> None:
    codes = _seed(service)
    data = service.trends("acc-leader", start="", end="")
    assert data["dates"] == ["2026-10-20"]
    assert len(data["series"]) == 2
    assert data["totals"][0]["gmv_cny"] == pytest.approx(400.0)
    # 每条曲线都有当天的点
    assert all(len(item["points"]) == 1 for item in data["series"])

    filtered = service.trends("acc-leader", start="", end="", exclude=[codes["store_b"]])
    assert len(filtered["series"]) == 1
    assert filtered["totals"][0]["gmv_cny"] == pytest.approx(100.0)


def test_delete_store_removes_data_and_checks_owner(service: TeamStoreService) -> None:
    codes = _seed(service)
    # 别人不能删自己名下的店
    with pytest.raises(PermissionError):
        service.delete_store("acc-b", codes["store_a"])

    service.submit_rollup(
        "acc-a",
        week_start="2026-10-19",
        submitted_at="2026-10-24T10:00:00+08:00",
        stores=[{
            "store_code": codes["store_a"],
            "days": [],
            "records": [{
                "kind": "order",
                "key": "PO-DEL|SO|SKU",
                "stat_date": "2026-10-20",
                "payload": {"parent_order_sn": "PO-DEL"},
            }],
        }],
    )
    assert service.records("acc-a", kinds=["order"])["count"] == 1

    result = service.delete_store("acc-a", codes["store_a"])
    assert result["ok"] is True
    assert result["deleted"]["team_stores"] == 1
    # 汇总、明细都跟着清掉
    assert service.records("acc-a", kinds=["order"])["count"] == 0
    assert codes["store_a"] not in [
        store["store_code"] for store in service.list_stores("acc-a")["stores"]
    ]


def test_account_links_require_approval(service: TeamStoreService) -> None:
    _seed(service)
    # 发起申请：未同意前看不到对方数据
    requested = service.add_link("acc-a", "acc-b")
    assert requested["pending_out_count"] == 1
    assert requested["linked_count"] == 1  # 待同意也占名额
    assert service.scope("acc-a")["visible_store_count"] == 1
    assert service.links("acc-b")["pending_in_count"] == 1

    # 被关联方同意后生效
    accepted = service.accept_link("acc-b", "acc-a")
    assert accepted["linked_by_count"] == 1
    assert accepted["linked_by"][0]["account_id"] == "acc-a"
    assert "acc-b" in service.scope("acc-a")["visible_account_ids"]
    assert service.scope("acc-a")["visible_store_count"] == 2

    # 重复申请幂等；不能关联自己
    assert service.add_link("acc-a", "acc-b")["linked_count"] == 1
    with pytest.raises(ValueError):
        service.add_link("acc-a", "acc-a")

    # 最多 3 个（已关联 + 待同意）
    for index in range(4):
        service.save_profile(f"acc-extra{index}", staff_no=f"临时{index}")
    service.add_link("acc-a", "acc-extra0")
    service.add_link("acc-a", "acc-extra1")
    with pytest.raises(ValueError):
        service.add_link("acc-a", "acc-extra2")

    # 拒绝申请：不生效
    service.add_link("acc-b", "acc-extra2")
    assert service.links("acc-extra2")["pending_in_count"] == 1
    service.reject_link("acc-extra2", "acc-b")
    assert service.links("acc-extra2")["pending_in_count"] == 0
    assert "acc-extra2" not in service.scope("acc-b")["visible_account_ids"]

    # 移除已关联后范围恢复
    service.remove_link("acc-a", "acc-b")
    assert service.scope("acc-a")["visible_store_count"] == 1
    assert service.links("acc-b")["linked_by_count"] == 0


def test_note_identity_creates_profile_for_login_only_account(
    service: TeamStoreService,
) -> None:
    """只登录过团队看板、没登记员工标识的账号，也要能被按登录账号关联。"""
    service.note_identity("acc-guest", "717")
    assert service.links("acc-guest")["username"] == "717"
    assert service.links("acc-guest")["staff_no"] == ""
    # 只读过的账号也能被搜索到
    assert service.links("acc-guest")["pending_in_count"] == 0


def test_account_link_accepts_username_or_staff_no(service: TeamStoreService) -> None:
    _seed(service)
    # 登录账号补写进画像后，可以按登录账号发起关联
    service.note_identity("acc-b", "20223824")
    assert service.links("acc-b")["username"] == "20223824"
    pending = service.add_link("acc-a", "20223824")
    assert pending["pending_out_count"] == 1
    assert pending["pending_out"][0]["account_id"] == "acc-b"
    service.remove_link("acc-a", "acc-b")

    # 员工标识同样可用；填自己时给出明确提示
    assert service.add_link("acc-a", "李四")["pending_out"][0]["account_id"] == "acc-b"
    with pytest.raises(ValueError, match="不能关联自己的账号"):
        service.add_link("acc-a", "张三")
    with pytest.raises(ValueError, match="账号不存在"):
        service.add_link("acc-a", "查无此人")


def test_records_upload_and_query(service: TeamStoreService) -> None:
    codes = _seed(service)

    def submit(records: list[dict]) -> None:
        service.submit_rollup(
            "acc-a",
            week_start="2026-10-19",
            submitted_at="2026-10-24T10:00:00+08:00",
            stores=[{"store_code": codes["store_a"], "days": [], "records": records}],
        )

    submit(
        [
            {
                "kind": "order",
                "key": "PO-1|SO-1|SKU-1",
                "stat_date": "2026-10-20",
                "payload": {
                    "parent_order_sn": "PO-1",
                    "order_status": "已发货",
                    "gmv_cny": 12.5,
                    "goods_name": "Pony",
                },
            },
            {
                "kind": "finance",
                "key": "2026-10-20|广告|3|CNY|推广",
                "stat_date": "2026-10-20",
                "payload": {"entry_type": "广告", "amount": 3},
            },
        ]
    )

    orders = service.records("acc-a", kinds=["order"])
    assert orders["count"] == 1
    assert orders["records"][0]["payload"]["gmv_cny"] == 12.5
    assert service.records("acc-a", kinds=["order"], status="已发货")["count"] == 1
    assert service.records("acc-a", kinds=["order"], status="已取消")["count"] == 0
    assert service.records("acc-a", kinds=["order"], keyword="po-1")["count"] == 1
    assert service.records("acc-a", kinds=["finance"])["count"] == 1
    # 别的账号看不到这家店的明细
    assert service.records("acc-b", kinds=["order"])["count"] == 0

    # 再次提交时整店替换：只留新上传的那一条订单
    submit(
        [
            {
                "kind": "order",
                "key": "PO-2|SO-2|SKU-2",
                "stat_date": "2026-10-21",
                "payload": {"parent_order_sn": "PO-2", "order_status": "待发货"},
            }
        ]
    )
    orders = service.records("acc-a", kinds=["order"])
    assert orders["count"] == 1
    assert orders["records"][0]["payload"]["parent_order_sn"] == "PO-2"
    assert service.records("acc-a", kinds=["finance"])["count"] == 0


