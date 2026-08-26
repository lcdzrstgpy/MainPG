from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

import wh_local.billing as billing_module
from wh_local.billing import (
    batch_freeze_status,
    compute_batch_charge,
    freeze_batch_points,
    pricing_changelog,
    pricing_items,
    release_expired_batch_freezes,
    settle_batch_points,
    update_pricing_items,
    usage_history,
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


def test_retry_premium_batch_settle_charges_extra_per_link(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    freeze = freeze_batch_points(database_path, actor, link_count=1, idempotency_key="freeze:retry-1")

    retried_subitems = [
        {"feature": "title", "status": "success", "retried": True},
        {"feature": "description", "status": "success", "retried": True},
        {"feature": "product_dimensions", "status": "success", "retried": True},
        {"feature": "four_grid", "status": "success", "retried": True},
        {"feature": "detail_images", "status": "success", "retried": True},
    ]
    settled = settle_batch_points(
        database_path,
        freeze["freeze_id"],
        item_results=[{"subitems": retried_subitems}],
        expected_account_id=actor.id,
    )
    # 基础 45 积分（450 单位）+ 重试溢价 10 积分（100 单位）= 55 积分。
    assert settled["charged_points"] == 55
    assert settled["retry_premium_points"] == 10
    assert settled["refunded_points"] == 0
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (actor.id,),
        ).fetchone()
    # 冻结 450 全扣（无退款），额外 100 单位从余额扣：1000 - 450 - 100 = 450。
    assert dict(wallet) == {"points_balance": 450, "locked_points": 0}


def test_retry_premium_not_charged_when_no_retry_marker(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    freeze = freeze_batch_points(database_path, actor, link_count=1, idempotency_key="freeze:noretry-1")
    settled = settle_batch_points(
        database_path,
        freeze["freeze_id"],
        item_results=_full_success_subitems(),
        expected_account_id=actor.id,
    )
    assert settled["charged_points"] == 45
    assert settled["retry_premium_points"] == 0


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


def test_usage_history_links_batch_to_task_id(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    freeze = freeze_batch_points(
        database_path,
        actor,
        link_count=1,
        idempotency_key="batch:task-link-0001",
        task_id="task-42",
    )
    item = usage_history(database_path, account_id=actor.id)["items"][0]
    assert item["usage_id"] == f"batch:{freeze['freeze_id']}"
    assert item["status"] == "frozen"
    # 消费流水携带任务号：用户/客服可据此对应到处理历史，避免「处理中但历史无记录」。
    assert item["task"] == "task-42"

    settle_batch_points(
        database_path,
        freeze["freeze_id"],
        item_results=_full_success_subitems(),
        expected_account_id=actor.id,
    )
    settled = usage_history(database_path, account_id=actor.id)["items"][0]
    assert settled["status"] == "succeeded"
    assert settled["task"] == "task-42"


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


def test_pod_random_profile_persists_prices_and_settles_whole_styles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=2000)
    picks = iter((0, 10))
    monkeypatch.setattr(billing_module.secrets, "randbelow", lambda upper: next(picks))

    first = freeze_batch_points(
        database_path,
        actor,
        link_count=2,
        scope=["title", "four_grid"],
        idempotency_key="pod:batch:random-price-0001",
        billing_profile="pod_random_v1",
    )
    repeated = freeze_batch_points(
        database_path,
        actor,
        link_count=2,
        scope=["title", "four_grid"],
        idempotency_key="pod:batch:random-price-0001",
        billing_profile="pod_random_v1",
    )

    assert first["billing_profile"] == "pod_random_v1"
    assert first["link_prices"] == [40, 50]
    assert first["frozen_points"] == 90
    assert repeated["link_prices"] == [40, 50]
    assert repeated["frozen_points"] == 90

    settled = settle_batch_points(
        database_path,
        first["freeze_id"],
        item_results=[
            {
                "link_idx": 1,
                "subitems": [
                    {"feature": "title", "status": "success"},
                    {"feature": "four_grid", "status": "success"},
                ],
            },
            {
                "link_idx": 2,
                "subitems": [
                    {"feature": "title", "status": "success"},
                    {"feature": "four_grid", "status": "no_return"},
                ],
            },
        ],
        expected_account_id=actor.id,
    )

    assert settled["charged_points"] == 40
    assert settled["refunded_points"] == 50
    status = batch_freeze_status(
        database_path,
        first["freeze_id"],
        expected_account_id=actor.id,
    )
    assert status["billing_profile"] == "pod_random_v1"
    assert status["link_prices"] == [40, 50]

    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (actor.id,),
        ).fetchone()
    # 冻结 90 积分（900 单位）全释放，成功款扣 40 积分（400 单位）：2000 - 400 = 1600。
    assert dict(wallet) == {"points_balance": 1600, "locked_points": 0}


def test_pod_random_profile_charges_when_images_succeed_even_if_title_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    monkeypatch.setattr(billing_module.secrets, "randbelow", lambda upper: 0)
    freeze = freeze_batch_points(
        database_path,
        actor,
        link_count=1,
        scope=["title", "four_grid"],
        idempotency_key="pod:batch:image-success-title-failed-0001",
        billing_profile="pod_random_v1",
    )

    settled = settle_batch_points(
        database_path,
        freeze["freeze_id"],
        item_results=[{
            "link_idx": 1,
            "subitems": [
                {"feature": "title", "status": "no_return"},
                {"feature": "four_grid", "status": "success"},
            ],
        }],
        expected_account_id=actor.id,
    )

    assert settled["charged_points"] == 40
    assert settled["refunded_points"] == 0


def test_pod_random_profile_refunds_title_only_retry_even_when_title_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    monkeypatch.setattr(billing_module.secrets, "randbelow", lambda upper: 0)
    freeze = freeze_batch_points(
        database_path,
        actor,
        link_count=1,
        scope=["title"],
        idempotency_key="pod:retry:title-only-refund-0001",
        billing_profile="pod_random_v1",
    )

    settled = settle_batch_points(
        database_path,
        freeze["freeze_id"],
        item_results=[{
            "link_idx": 1,
            "subitems": [{"feature": "title", "status": "success"}],
        }],
        expected_account_id=actor.id,
    )

    assert settled["charged_points"] == 0
    assert settled["refunded_points"] == 40


def test_usage_history_identifies_pod_batch_charge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    monkeypatch.setattr(billing_module.secrets, "randbelow", lambda upper: 2)
    freeze = freeze_batch_points(
        database_path,
        actor,
        link_count=1,
        scope=["title", "four_grid"],
        idempotency_key="pod:batch:usage-history-0001",
        billing_profile="pod_random_v1",
    )
    settle_batch_points(
        database_path,
        freeze["freeze_id"],
        item_results=[
            {
                "link_idx": 1,
                "subitems": [
                    {"feature": "title", "status": "success"},
                    {"feature": "four_grid", "status": "success"},
                ],
            }
        ],
        expected_account_id=actor.id,
    )

    item = usage_history(database_path, account_id=actor.id)["items"][0]

    assert item["feature_key"] == "pod_customization.batch"
    assert item["billing_profile"] == "pod_random_v1"
    assert item["provider"] == "POD 定制结算"
    assert item["model"] == "1 款创作"
    assert item["rule_version"] == freeze["rule_version"]
    assert item["charged_points"] == 42


def test_pod_random_profile_rejects_duplicate_scope_before_freezing(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)

    with pytest.raises(HTTPException) as exc:
        freeze_batch_points(
            database_path,
            actor,
            link_count=1,
            scope=["title", "title"],
            idempotency_key="pod:batch:duplicate-scope-0001",
            billing_profile="pod_random_v1",
        )

    assert exc.value.status_code == 400
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT locked_points FROM billing_wallets WHERE account_id = ?",
            (actor.id,),
        ).fetchone()
    assert wallet["locked_points"] == 0


def test_product_batch_still_uses_current_rule_when_settled(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=1000)
    freeze = freeze_batch_points(
        database_path,
        actor,
        link_count=1,
        idempotency_key="product:settlement-rule-0001",
    )
    assert freeze["frozen_points"] == 45
    update_pricing_items(
        database_path,
        items={
            "title": {"charge_points": 8},
            "description": {"charge_points": 6},
            "product_dimensions": {"charge_points": 7},
            "four_grid": {"charge_points": 12},
            "detail_images": {"charge_points": 10},
        },
        updated_by="admin-1",
        change_reason="verify product billing isolation",
    )

    settled = settle_batch_points(
        database_path,
        freeze["freeze_id"],
        item_results=_full_success_subitems(),
        expected_account_id=actor.id,
    )

    assert settled["charged_points"] == 43


def test_pod_random_settlement_rejects_reordered_link_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "billing.sqlite3"
    actor = _service_account(database_path, balance=2000)
    picks = iter((0, 10))
    monkeypatch.setattr(billing_module.secrets, "randbelow", lambda upper: next(picks))
    freeze = freeze_batch_points(
        database_path,
        actor,
        link_count=2,
        scope=["title", "four_grid"],
        idempotency_key="pod:batch:reordered-links-0001",
        billing_profile="pod_random_v1",
    )

    with pytest.raises(HTTPException) as exc:
        settle_batch_points(
            database_path,
            freeze["freeze_id"],
            item_results=[
                {
                    "link_idx": 2,
                    "subitems": [
                        {"feature": "title", "status": "no_return"},
                        {"feature": "four_grid", "status": "no_return"},
                    ],
                },
                {
                    "link_idx": 1,
                    "subitems": [
                        {"feature": "title", "status": "success"},
                        {"feature": "four_grid", "status": "success"},
                    ],
                },
            ],
            expected_account_id=actor.id,
        )

    assert exc.value.status_code == 400
