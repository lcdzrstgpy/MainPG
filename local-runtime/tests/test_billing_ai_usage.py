from __future__ import annotations

from pathlib import Path

from wh_local.billing import reserve_ai_usage, settle_ai_usage_success
from wh_local.db import init_db, transaction
from wh_local.session import Actor


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
    assert settled["charged_points"] == 30
    assert repeated["usage_id"] == settled["usage_id"]
    with transaction(database_path) as conn:
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (actor.id,),
        ).fetchone()
    assert dict(wallet) == {"points_balance": 970, "locked_points": 0}
