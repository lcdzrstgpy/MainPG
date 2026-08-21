from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from wh_local.billing import (
    batch_freeze_status,
    compute_batch_charge,
    freeze_batch_points,
    pricing_changelog,
    pricing_items,
    release_expired_batch_freezes,
    settle_batch_points,
    update_pricing_items,
)
from wh_local.db import init_db, transaction
from wh_local.session import Actor


def _service_account(database_path: Path, balance: int = 10000) -> Actor:
    init_db(database_path)
    actor = Actor(id="batch-user", username="batch-user", role="operator")
    with transaction(database_path) as conn:
        conn.execute(
            "INSERT INTO auth_accounts (account_id, username) VALUES (?, ?)",
            (actor.id, actor.username),
        )
        conn.execute(
            "INSERT INTO billing_wallets (account_id, workspace_id, points_balance) VALUES (?, ?, ?)",
            (actor.id, actor.workspace_id, balance),
        )
    return actor


def _full_success_subitems() -> list[dict[str, Any]]:
    return [
        {"subitems": [{"feature": "title", "status": "success"},
                       {"feature": "description", "status": "success"},
                       {"feature": "product_dimensions", "status": "success"},
                       {"feature": "four_grid", "status": "success"},
                       {"feature": "detail_images", "status": "success"}]}
    ]


def test_pricing_items_seeded_with_defaults(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    init_db(database_path)
    pricing = pricing_items(database_path)
    assert pricing["rule_version"] >= 1
    assert pricing["max_charge_per_link"] == 45
    assert set(pricing["items"].keys()) == {
        "title", "description", "product_dimensions", "four_grid", "detail_images",
    }
    assert pricing["items"]["four_grid"]["charge_points"] == 12


def test_freeze_and_full_success_settle(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    freeze = freeze_batch_points(
        database_path,
        actor,
        link_count=1,
        idempotency_key="freeze:task-1:links-1",
    )
    assert freeze["status"] == "frozen"
    assert freeze["frozen_points"] == 45

    settled = settle_batch_points(
        database_path,
        freeze["freeze_id"],
        item_results=_full_success_subitems(),
        expected_account_id=actor.id,
    )
    assert settled["status"] == "settled"
    assert settled["charged_points"] == 45
    assert settled["refunded_points"] == 0
    assert not settled["already_settled"]

    # Idempotent re-settle returns the stored result.
    repeated = settle_batch_points(
        database_path,
        freeze["freeze_id"],
        item_results=_full_success_subitems(),
        expected_account_id=actor.id,
    )
    assert repeated["already_settled"] is True
    assert repeated["charged_points"] == 45

    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (actor.id,),
        ).fetchone()
    assert dict(wallet) == {"points_balance": 550, "locked_points": 0}


def test_settle_with_intercept_refunds_half_and_no_return_full(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    freeze = freeze_batch_points(database_path, actor, link_count=1, idempotency_key="freeze:intercept-1")
    settled = settle_batch_points(
        database_path,
        freeze["freeze_id"],
        item_results=[
            {"subitems": [
                {"feature": "title", "status": "intercept"},        # 8 -> charge 4 refund 4
                {"feature": "description", "status": "no_return"},  # 8 -> refund 8
                {"feature": "product_dimensions", "status": "success"},  # 7
                {"feature": "four_grid", "status": "no_return"},    # 12 -> refund 12
                {"feature": "detail_images", "status": "success"},  # 10
            ]}
        ],
        expected_account_id=actor.id,
    )
    # charge = 4 + 0 + 7 + 0 + 10 = 21 ; refund = 4 + 8 + 12 = 24
    assert settled["charged_points"] == 21
    assert settled["refunded_points"] == 24


def test_freeze_rejects_insufficient_balance(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=40)
    with pytest.raises(HTTPException) as exc:
        freeze_batch_points(database_path, actor, link_count=1, idempotency_key="freeze:insufficient-1")
    assert exc.value.status_code == 402


def test_ttl_release_frees_locked_points(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    freeze = freeze_batch_points(database_path, actor, link_count=1, idempotency_key="freeze:ttl-1")
    # Force expiry into the past by directly updating the expires_at column.
    with transaction(database_path) as conn:
        conn.execute(
            "UPDATE billing_batch_freezes SET expires_at = '2020-01-01T00:00:00' WHERE freeze_id = ?",
            (freeze["freeze_id"],),
        )
    released = release_expired_batch_freezes(database_path)
    assert released == 1
    status = batch_freeze_status(database_path, freeze["freeze_id"], expected_account_id=actor.id)
    assert status["status"] == "released"
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (actor.id,),
        ).fetchone()
    assert dict(wallet) == {"points_balance": 1000, "locked_points": 0}


def test_update_pricing_items_bumps_version_and_writes_changelog(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    init_db(database_path)
    before = pricing_items(database_path)
    updated = update_pricing_items(
        database_path,
        items={
            "title": {"charge_points": 9},
            "description": {"charge_points": 8},
            "product_dimensions": {"charge_points": 6},
            "four_grid": {"charge_points": 12},
            "detail_images": {"charge_points": 10},
        },
        updated_by="admin-1",
        change_reason="adjust title pricing",
    )
    assert updated["rule_version"] == before["rule_version"] + 1
    assert updated["items"]["title"]["charge_points"] == 9
    assert updated["max_charge_units_per_link"] == 450

    log = pricing_changelog(database_path, limit=10)
    assert len(log) == 1
    assert log[0]["changed_by"] == "admin-1"
    assert log[0]["change_reason"] == "adjust title pricing"
    assert log[0]["before"]["rule_version"] == before["rule_version"]
    assert log[0]["after"]["rule_version"] == updated["rule_version"]

    # Total must stay within 35..45.
    with pytest.raises(HTTPException) as exc:
        update_pricing_items(
            database_path,
            items={
                "title": {"charge_points": 20},
                "description": {"charge_points": 20},
                "product_dimensions": {"charge_points": 20},
                "four_grid": {"charge_points": 20},
                "detail_images": {"charge_points": 20},
            },
            updated_by="admin-1",
            change_reason="invalid total",
        )
    assert exc.value.status_code == 400


def test_compute_batch_charge_rejects_unknown_feature(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    init_db(database_path)
    with pytest.raises(HTTPException) as exc:
        compute_batch_charge(
            database_path,
            rule_version=None,
            item_results=[{"feature": "video", "status": "success"}],
        )
    assert exc.value.status_code == 400


def test_freeze_is_idempotent_by_idempotency_key(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    first = freeze_batch_points(database_path, actor, link_count=1, idempotency_key="freeze:idem-1")
    second = freeze_batch_points(database_path, actor, link_count=1, idempotency_key="freeze:idem-1")
    assert first["freeze_id"] == second["freeze_id"]
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT locked_points FROM billing_wallets WHERE account_id = ?",
            (actor.id,),
        ).fetchone()
    # Only locked once (450 units = 45 points).
    assert wallet["locked_points"] == 450
