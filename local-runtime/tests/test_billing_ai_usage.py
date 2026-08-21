from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import HTTPException

from wh_local.billing import reserve_ai_usage, settle_ai_usage_success
from wh_local.db import init_db, transaction
from wh_local.session import Actor


def _wallet_with(database_path: Path, actor: Actor, balance: int) -> None:
    with transaction(database_path) as conn:
        conn.execute(
            "INSERT INTO auth_accounts (account_id, username) VALUES (?, ?)",
            (actor.id, actor.username),
        )
        conn.execute(
            """
            INSERT INTO billing_wallets (account_id, workspace_id, points_balance)
            VALUES (?, ?, ?)
            """,
            (actor.id, actor.workspace_id, balance),
        )


def test_ai_usage_reservation_and_settlement_work_on_fresh_database(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    init_db(database_path)
    actor = Actor(id="billing-user", username="billing-user", role="operator")

    with transaction(database_path) as conn:
        conn.execute(
            "INSERT INTO auth_accounts (account_id, username) VALUES (?, ?)",
            (actor.id, actor.username),
        )
        conn.execute(
            """
            INSERT INTO billing_wallets (account_id, workspace_id, points_balance)
            VALUES (?, ?, 1000)
            """,
            (actor.id, actor.workspace_id),
        )

    reserved = reserve_ai_usage(
        database_path,
        actor,
        feature_key="product_processing.text",
        idempotency_key="task:1:item:1:text",
    )
    settled = settle_ai_usage_success(database_path, reserved["usage_id"])
    repeated = settle_ai_usage_success(database_path, reserved["usage_id"])

    assert settled["status"] == "succeeded"
    # Internal storage uses tenths of a point.  Text is 5 displayed points
    # (50 units); a text-plus-image link settles at 40 displayed points.
    assert settled["charged_points"] == 50
    assert repeated["usage_id"] == settled["usage_id"]
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (actor.id,),
        ).fetchone()
    assert dict(wallet) == {"points_balance": 950, "locked_points": 0}


def test_concurrent_reservations_cannot_overspend_wallet(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    init_db(database_path)
    actor = Actor(id="concurrent-user", username="concurrent-user", role="operator")
    with transaction(database_path) as conn:
        conn.execute("INSERT INTO auth_accounts (account_id, username) VALUES (?, ?)", (actor.id, actor.username))
        conn.execute(
            # One default image reservation is 400 internal units; two callers
            # must not both reserve against this exact balance.
            "INSERT INTO billing_wallets (account_id, workspace_id, points_balance) VALUES (?, ?, 400)",
            (actor.id, actor.workspace_id),
        )

    barrier = threading.Barrier(2)

    def reserve(index: int):
        barrier.wait()
        return reserve_ai_usage(
            database_path,
            actor,
            feature_key="product_processing.image_grid_2k",
            idempotency_key=f"concurrent-reservation-{index:04d}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve, index) for index in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except HTTPException as exc:
                outcomes.append(exc)

    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert sum(isinstance(value, HTTPException) and value.status_code == 402 for value in outcomes) == 1


def test_retry_premium_applied_only_on_image_usage(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    init_db(database_path)
    actor = Actor(id="retry-user", username="retry-user", role="operator")
    _wallet_with(database_path, actor, balance=1000)

    reserved = reserve_ai_usage(
        database_path,
        actor,
        feature_key="product_processing.image_grid_2k",
        idempotency_key="task:retry:item:1:image",
    )
    settled = settle_ai_usage_success(
        database_path,
        reserved["usage_id"],
        metadata={"billing_retried": True, "task_id": 1, "item_id": 1},
    )
    assert settled["status"] == "succeeded"
    # 350 单位基础费 + 100 单位重试溢价 = 450 单位（45 积分）。
    assert settled["charged_points"] == 450
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (actor.id,),
        ).fetchone()
    assert dict(wallet) == {"points_balance": 550, "locked_points": 0}


def test_retry_premium_skipped_without_marker_or_on_text_usage(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    init_db(database_path)
    actor = Actor(id="retry-text-user", username="retry-text-user", role="operator")
    _wallet_with(database_path, actor, balance=1000)

    # text usage 即使带标记也不加溢价（避免同链接两个 usage 重复计费）。
    reserved = reserve_ai_usage(
        database_path,
        actor,
        feature_key="product_processing.text",
        idempotency_key="task:retry:item:1:text",
    )
    settled = settle_ai_usage_success(
        database_path,
        reserved["usage_id"],
        metadata={"billing_retried": True, "task_id": 1, "item_id": 1},
    )
    assert settled["charged_points"] == 50

    # image usage 不带标记则按正常价结算。
    reserved_image = reserve_ai_usage(
        database_path,
        actor,
        feature_key="product_processing.image_grid_2k",
        idempotency_key="task:retry:item:1:image",
    )
    settled_image = settle_ai_usage_success(database_path, reserved_image["usage_id"])
    assert settled_image["charged_points"] == 350
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance FROM billing_wallets WHERE account_id = ?",
            (actor.id,),
        ).fetchone()
    # 1000 - 50 - 350 = 600
    assert wallet["points_balance"] == 600
