# -*- coding: utf-8 -*-
"""后台「调账原因」必须能到用户手里，且不能顺带泄露管理员身份。

历史问题：wh-admin 的 /api/billing/{account_id}/adjust 把管理员填的 reason 写进
metadata_json，但 point_ledger_history 的 SELECT 从不取 metadata_json，
客户端只能按 source_type 显示写死的「管理员划拨」——用户的理由是白填的。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from wh_local.billing import _append_ledger, _ledger_user_note, point_ledger_history
from wh_local.db import init_db, transaction


def _seed(tmp_path: Path) -> Path:
    db = tmp_path / "workbench.sqlite3"
    init_db(db)
    with transaction(db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workspaces (workspace_id, workspace_code, workspace_name) "
            "VALUES ('default', 'default', '默认工作区')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO auth_accounts (account_id, username, workspace_id) "
            "VALUES ('acct_1', 'tester', 'default')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO billing_wallets (account_id, workspace_id, points_balance, locked_points, version) "
            "VALUES ('acct_1', 'default', 0, 0, 0)"
        )
        _append_ledger(
            conn,
            account_id="acct_1",
            workspace_id="default",
            direction="credit",
            points_delta=100,
            source_type="admin_adjustment",
            source_id="admin:Dai123",
            idempotency_key="admin-adjust:k1",
            metadata={"reason": "扣费补偿", "admin": "Dai123", "client_ip": "222.94.140.187"},
        )
        _append_ledger(
            conn,
            account_id="acct_1",
            workspace_id="default",
            direction="credit",
            points_delta=100,
            source_type="daily_extra_claim",
            source_id="daily:2026-10-19",
            idempotency_key="daily:k1",
            metadata={"claim_points": 100, "pool": "plan"},
        )
    return db


def test_admin_reason_reaches_the_client(tmp_path: Path) -> None:
    db = _seed(tmp_path)
    history = point_ledger_history(db, account_id="acct_1")
    by_source = {item["source_type"]: item for item in history["items"]}
    assert by_source["admin_adjustment"]["note"] == "扣费补偿"
    # 非划拨来源不该冒备注出来
    assert by_source["daily_extra_claim"]["note"] == ""


def test_note_never_leaks_admin_identity_or_ip(tmp_path: Path) -> None:
    """note 只能带 reason。

    注意：``source_id`` 形如 ``admin:<用户名>``，是**既有**字段（本次未改），
    它本来就把管理员用户名带到了客户端；这里只保证新加的 note 不再扩大暴露面，
    并且 metadata 里的 admin / client_ip 一个都不能变成独立字段。
    """
    db = _seed(tmp_path)
    history = point_ledger_history(db, account_id="acct_1")
    item = next(i for i in history["items"] if i["source_type"] == "admin_adjustment")
    assert item["note"] == "扣费补偿"
    assert "222.94.140.187" not in json.dumps(item, ensure_ascii=False), "管理员出口 IP 不能透给终端用户"
    assert "client_ip" not in item
    assert set(item) == {
        "entry_id",
        "points_delta",
        "balance_after",
        "source_type",
        "source_id",
        "created_at",
        "note",
    }


@pytest.mark.parametrize(
    ("source_type", "metadata", "expected"),
    [
        ("admin_adjustment", {"reason": "  补偿  "}, "补偿"),
        ("test_grant", {"reason": "联调"}, "联调"),
        ("payment_alipay", {"reason": "不该出现"}, ""),
        ("admin_adjustment", {}, ""),
        ("admin_adjustment", {"reason": None}, ""),
    ],
)
def test_ledger_user_note_rules(source_type: str, metadata: dict, expected: str) -> None:
    assert _ledger_user_note(source_type, json.dumps(metadata, ensure_ascii=False)) == expected


def test_ledger_user_note_survives_broken_metadata() -> None:
    assert _ledger_user_note("admin_adjustment", "not-json") == ""
    assert _ledger_user_note("admin_adjustment", "") == ""
    assert _ledger_user_note("admin_adjustment", '["list"]') == ""
    long_reason = "长" * 500
    assert len(_ledger_user_note("admin_adjustment", json.dumps({"reason": long_reason}))) == 120
