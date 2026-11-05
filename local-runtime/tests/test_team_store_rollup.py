from __future__ import annotations

from pathlib import Path

import pytest

from wh_local.modules.team_store.dedupe import (
    FLAG_SAME_ACCOUNT_DUP_ORDER,
    FLAG_SAME_STORE_MULTI_ACCOUNT,
)
from wh_local.modules.team_store.repository import TeamRepository
from wh_local.modules.team_store.service import TeamStoreService


@pytest.fixture
def repo(tmp_path: Path) -> TeamRepository:
    return TeamRepository(tmp_path / "team.sqlite3")


@pytest.fixture
def service(repo: TeamRepository) -> TeamStoreService:
    return TeamStoreService(repo)


def _open_stores(service: TeamStoreService) -> str:
    service.save_profile("acc-1", staff_no="张三")
    service.save_profile("acc-2", staff_no="李四")
    return service.register_store("acc-1", name="北美一号")["store"]["store_code"]


def _day(stat_date: str, updated: str, **metrics) -> dict:
    return {"stat_date": stat_date, "source_updated_at": updated, **metrics}


def test_submit_creates_daily_rows(
    service: TeamStoreService, repo: TeamRepository
) -> None:
    code = _open_stores(service)
    result = service.submit_rollup(
        "acc-1",
        week_start="2026-10-19",
        submitted_at="2026-10-24T10:00:00+08:00",
        stores=[
            {
                "store_code": code,
                "days": [
                    _day("2026-10-20", "2026-10-20T23:00:00+08:00", order_count=12, gmv_cny=880.5),
                    _day("2026-10-21", "2026-10-21T23:00:00+08:00", order_count=9, gmv_cny=640),
                ],
            }
        ],
    )
    assert result["created"] == 2
    assert result["accepted"] == 2
    row = repo.get_store_daily(code, "2026-10-20")
    assert row is not None
    assert row["order_count"] == 12
    assert row["gmv_cny"] == pytest.approx(880.5)
    assert row["source_account_id"] == "acc-1"


def test_submit_prefers_newer_source_and_marks_stale(
    service: TeamStoreService, repo: TeamRepository
) -> None:
    code = _open_stores(service)
    base = {"week_start": "2026-10-19", "submitted_at": "2026-10-24T10:00:00+08:00"}

    service.submit_rollup(
        "acc-1",
        **base,
        stores=[{"store_code": code, "days": [_day("2026-10-20", "2026-10-20T10:00:00+08:00", order_count=5)]}],
    )
    updated = service.submit_rollup(
        "acc-1",
        **base,
        stores=[{"store_code": code, "days": [_day("2026-10-20", "2026-10-21T10:00:00+08:00", order_count=8)]}],
    )
    assert updated["updated"] == 1
    assert repo.get_store_daily(code, "2026-10-20")["order_count"] == 8

    stale = service.submit_rollup(
        "acc-1",
        **base,
        stores=[{"store_code": code, "days": [_day("2026-10-20", "2026-10-19T10:00:00+08:00", order_count=1)]}],
    )
    assert stale["stale"] == 1
    assert repo.get_store_daily(code, "2026-10-20")["order_count"] == 8


def test_other_account_write_flags_multi_account(
    service: TeamStoreService, repo: TeamRepository
) -> None:
    code = _open_stores(service)
    # acc-2 绑同名店会变成协助账号
    assert service.register_store("acc-2", name="北美一号")["store"]["role"] == "helper"

    result = service.submit_rollup(
        "acc-2",
        week_start="2026-10-19",
        submitted_at="2026-10-24T10:00:00+08:00",
        stores=[{"store_code": code, "days": [_day("2026-10-20", "2026-10-20T23:00:00+08:00", order_count=3)]}],
    )
    assert result["created"] == 1
    assert result["flags"][FLAG_SAME_STORE_MULTI_ACCOUNT] == 1
    kinds = {flag["kind"] for flag in repo.list_flags("acc-2")}
    assert FLAG_SAME_STORE_MULTI_ACCOUNT in kinds


def test_dup_orders_create_flag(service: TeamStoreService, repo: TeamRepository) -> None:
    code = _open_stores(service)
    result = service.submit_rollup(
        "acc-1",
        week_start="2026-10-19",
        submitted_at="2026-10-24T10:00:00+08:00",
        stores=[
            {
                "store_code": code,
                "days": [_day("2026-10-20", "2026-10-20T23:00:00+08:00", order_count=3)],
                "dup_orders": {"count": 2, "samples": ["a" * 40, "b" * 40], "other_store_code": "20261026-02"},
            }
        ],
    )
    assert result["flags"][FLAG_SAME_ACCOUNT_DUP_ORDER] == 2
    flags = [flag for flag in repo.list_flags("acc-1") if flag["kind"] == FLAG_SAME_ACCOUNT_DUP_ORDER]
    assert len(flags) == 1
    assert '"count": 2' in flags[0]["detail"]


def test_unbound_store_is_skipped(service: TeamStoreService) -> None:
    _open_stores(service)
    result = service.submit_rollup(
        "acc-1",
        week_start="2026-10-19",
        submitted_at="2026-10-24T10:00:00+08:00",
        stores=[{"store_code": "20990101-01", "days": [_day("2026-10-20", "")]}],
    )
    assert result["accepted"] == 0
    assert result["skipped"][0]["reason"] == "store_not_bound"


def test_submission_is_one_per_week(service: TeamStoreService) -> None:
    code = _open_stores(service)
    payload = {
        "week_start": "2026-10-19",
        "submitted_at": "2026-10-24T10:00:00+08:00",
        "stores": [{"store_code": code, "days": [_day("2026-10-20", "2026-10-20T23:00:00+08:00")]}],
    }
    service.submit_rollup("acc-1", **payload)
    service.submit_rollup("acc-1", **payload)
    submissions = service.list_submissions("acc-1")["submissions"]
    assert len(submissions) == 1
    assert submissions[0]["week_start"] == "2026-10-19"


def test_week_start_snaps_to_monday(service: TeamStoreService) -> None:
    code = _open_stores(service)
    result = service.submit_rollup(
        "acc-1",
        week_start="2026-10-22",  # 周四
        submitted_at="2026-10-24T10:00:00+08:00",
        stores=[{"store_code": code, "days": []}],
    )
    assert result["submission"]["week_start"] == "2026-10-19"


def test_capture_batches_are_saved_with_submission(service: TeamStoreService) -> None:
    code = _open_stores(service)
    result = service.submit_rollup(
        "acc-1",
        week_start="2026-10-19",
        submitted_at="2026-10-24T10:00:00+08:00",
        stores=[{"store_code": code, "days": []}],
        batches=[
            {
                "batch_id": "20261028-01",
                "capture_mode": "order_list",
                "order_count": 12,
                "sub_order_count": 14,
                "item_count": 20,
                "captured_at": "2026-10-28T09:22:57Z",
            }
        ],
    )
    assert result["batches"] == 1
    rows = service.list_batches("acc-1")["batches"]
    assert rows[0]["batch_id"] == "20261028-01"
    assert rows[0]["order_count"] == 12

    # 再次提交会整账号替换，不会累积重复批次
    service.submit_rollup("acc-1", week_start="2026-10-19", stores=[], batches=[])
    assert service.list_batches("acc-1")["batches"] == []
