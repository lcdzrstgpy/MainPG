from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .db import transaction
from .session import Actor


POINTS_PER_CNY = int(os.environ.get("WH_BILLING_POINTS_PER_CNY", "1000") or "1000")
TEST_GRANT_POINTS = int(os.environ.get("WH_BILLING_TEST_GRANT_POINTS", "10000") or "10000")


@dataclass(frozen=True)
class FeaturePricing:
    reserve_points: int
    min_charge_points: int
    fixed_charge_points: int
    cost_multiplier: float


FEATURE_PRICING: dict[str, FeaturePricing] = {
    "ai_service.chat": FeaturePricing(20, 10, 10, 3.0),
    "ai_service.image": FeaturePricing(800, 200, 599, 3.0),
    "ai_service.pod_group": FeaturePricing(800, 200, 599, 3.0),
    "product_processing.text": FeaturePricing(50, 20, 30, 3.0),
    "product_processing.image_grid_2k": FeaturePricing(650, 200, 599, 3.0),
}


def reserve_ai_usage(
    database_path: Path,
    actor: Actor,
    *,
    feature_key: str,
    idempotency_key: str,
    quantity: int = 1,
    source_ref: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pricing = _pricing(feature_key)
    quantity = max(1, int(quantity))
    reserve_points = pricing.reserve_points * quantity
    usage_id = f"use_{secrets.token_urlsafe(18)}"
    now = _utc_now()
    with transaction(database_path) as conn:
        _ensure_billing_account(conn, actor)
        _ensure_wallet(conn, actor.id, actor.workspace_id)
        existing = conn.execute(
            """
            SELECT * FROM billing_ai_usage_events
            WHERE account_id = ? AND idempotency_key = ?
            """,
            (actor.id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return dict(existing)
        wallet = conn.execute(
            """
            SELECT points_balance, locked_points, manual_frozen_points
            FROM billing_wallets
            WHERE account_id = ?
            """,
            (actor.id,),
        ).fetchone()
        available = (
            int(wallet["points_balance"])
            - int(wallet["locked_points"])
            - int(wallet["manual_frozen_points"])
        )
        if available < reserve_points:
            raise HTTPException(
                status_code=402,
                detail=f"积分不足：需要冻结 {reserve_points}，当前可用 {available}",
            )
        conn.execute(
            """
            UPDATE billing_wallets
            SET locked_points = locked_points + ?, version = version + 1, updated_at = ?
            WHERE account_id = ?
            """,
            (reserve_points, now, actor.id),
        )
        conn.execute(
            """
            INSERT INTO billing_ai_usage_events (
                usage_id, account_id, workspace_id, feature_key, idempotency_key,
                reserved_points, cost_multiplier, min_charge_points, quantity,
                source_ref, status, metadata_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)
            """,
            (
                usage_id,
                actor.id,
                actor.workspace_id,
                feature_key,
                idempotency_key,
                reserve_points,
                pricing.cost_multiplier,
                pricing.min_charge_points * quantity,
                quantity,
                source_ref,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        _append_ledger(
            conn,
            account_id=actor.id,
            workspace_id=actor.workspace_id,
            direction="lock",
            points_delta=reserve_points,
            source_type="ai_usage",
            source_id=usage_id,
            idempotency_key=f"{idempotency_key}:lock",
            metadata={"feature_key": feature_key},
        )
    return {
        "usage_id": usage_id,
        "account_id": actor.id,
        "workspace_id": actor.workspace_id,
        "feature_key": feature_key,
        "reserved_points": reserve_points,
        "status": "reserved",
    }


def settle_ai_usage_success(
    database_path: Path,
    usage_id: str,
    *,
    actual_cost_cny: float = 0.0,
    provider: str = "station",
    provider_key_id: str = "",
    provider_task_id: str = "",
    model: str = "",
    channel: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _utc_now()
    with transaction(database_path) as conn:
        row = conn.execute(
            "SELECT * FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="usage event not found")
        if row["status"] != "reserved":
            return dict(row)
        pricing = _pricing(str(row["feature_key"]))
        quantity = int(row["quantity"])
        cost_points = (
            math.ceil(max(0.0, float(actual_cost_cny)) * POINTS_PER_CNY * float(row["cost_multiplier"]))
            if actual_cost_cny > 0
            else 0
        )
        fallback_points = pricing.fixed_charge_points * quantity
        min_points = int(row["min_charge_points"])
        charge_points = max(min_points, cost_points, fallback_points)
        charge_points = min(charge_points, int(row["reserved_points"]))
        refund_points = int(row["reserved_points"]) - charge_points
        wallet = conn.execute(
            "SELECT points_balance, locked_points FROM billing_wallets WHERE account_id = ?",
            (row["account_id"],),
        ).fetchone()
        if wallet is None:
            raise HTTPException(status_code=409, detail="wallet missing")
        conn.execute(
            """
            UPDATE billing_wallets
            SET points_balance = points_balance - ?,
                locked_points = locked_points - ?,
                version = version + 1,
                updated_at = ?
            WHERE account_id = ?
            """,
            (charge_points, int(row["reserved_points"]), now, row["account_id"]),
        )
        event_metadata = _merge_metadata(row["metadata_json"], metadata or {}, provider_task_id)
        conn.execute(
            """
            UPDATE billing_ai_usage_events
            SET charged_points = ?, refunded_points = ?, actual_cost_cny = ?,
                provider = ?, provider_key_id = ?, model = ?, channel = ?,
                input_tokens = ?, output_tokens = ?, total_tokens = ?,
                source_ref = CASE WHEN ? <> '' THEN ? ELSE source_ref END,
                status = 'succeeded', metadata_json = ?, settled_at = ?
            WHERE usage_id = ?
            """,
            (
                charge_points,
                refund_points,
                float(actual_cost_cny),
                provider,
                provider_key_id,
                model,
                channel,
                int(input_tokens),
                int(output_tokens),
                int(total_tokens),
                provider_task_id,
                provider_task_id,
                json.dumps(event_metadata, ensure_ascii=False, sort_keys=True),
                now,
                usage_id,
            ),
        )
        _append_ledger(
            conn,
            account_id=row["account_id"],
            workspace_id=row["workspace_id"],
            direction="debit",
            points_delta=charge_points,
            source_type="ai_usage",
            source_id=usage_id,
            idempotency_key=f"{row['idempotency_key']}:settle",
            metadata={
                "feature_key": row["feature_key"],
                "actual_cost_cny": actual_cost_cny,
                "provider_task_id": provider_task_id,
                "model": model,
            },
        )
        if refund_points:
            _append_ledger(
                conn,
                account_id=row["account_id"],
                workspace_id=row["workspace_id"],
                direction="unlock",
                points_delta=refund_points,
                source_type="ai_usage",
                source_id=usage_id,
                idempotency_key=f"{row['idempotency_key']}:unlock",
                metadata={"feature_key": row["feature_key"]},
            )
        settled = conn.execute(
            "SELECT * FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
    return dict(settled)


def settle_ai_usage_failure(
    database_path: Path,
    usage_id: str,
    *,
    error_message: str,
) -> None:
    now = _utc_now()
    with transaction(database_path) as conn:
        row = conn.execute(
            "SELECT * FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        if row is None or row["status"] != "reserved":
            return
        conn.execute(
            """
            UPDATE billing_wallets
            SET locked_points = locked_points - ?, version = version + 1, updated_at = ?
            WHERE account_id = ?
            """,
            (int(row["reserved_points"]), now, row["account_id"]),
        )
        conn.execute(
            """
            UPDATE billing_ai_usage_events
            SET refunded_points = reserved_points, status = 'failed',
                error_message = ?, settled_at = ?
            WHERE usage_id = ?
            """,
            (str(error_message)[:500], now, usage_id),
        )
        _append_ledger(
            conn,
            account_id=row["account_id"],
            workspace_id=row["workspace_id"],
            direction="unlock",
            points_delta=int(row["reserved_points"]),
            source_type="ai_usage",
            source_id=usage_id,
            idempotency_key=f"{row['idempotency_key']}:fail-unlock",
            metadata={"error": str(error_message)[:300]},
        )


def grant_test_points(database_path: Path, account_id: str | None = None) -> int:
    """Grant test points to existing accounts without trusting local clients."""
    now = _utc_now()
    targets: list[tuple[str, str, str, str]] = []
    with transaction(database_path) as conn:
        if account_id:
            rows = conn.execute(
                """
                SELECT user_id AS account_id, username, email, workspace_id
                FROM customer_users
                WHERE user_id = ?
                UNION
                SELECT account_id, username, email, workspace_id
                FROM auth_accounts
                WHERE account_id = ?
                """,
                (account_id, account_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT user_id AS account_id, username, email, workspace_id FROM customer_users
                UNION
                SELECT account_id, username, email, workspace_id FROM auth_accounts
                """
            ).fetchall()
        for row in rows:
            targets.append(
                (
                    str(row["account_id"]),
                    str(row["username"] or row["account_id"]),
                    str(row["email"] or ""),
                    str(row["workspace_id"] or "default"),
                )
            )
        for target_id, username, email, workspace_id in targets:
            _ensure_billing_account_values(conn, target_id, username, email, workspace_id)
            _ensure_wallet(conn, target_id, workspace_id)
            wallet = conn.execute(
                "SELECT points_balance FROM billing_wallets WHERE account_id = ?",
                (target_id,),
            ).fetchone()
            if int(wallet["points_balance"]) >= TEST_GRANT_POINTS:
                continue
            delta = TEST_GRANT_POINTS - int(wallet["points_balance"])
            conn.execute(
                """
                UPDATE billing_wallets
                SET points_balance = ?, version = version + 1, updated_at = ?
                WHERE account_id = ?
                """,
                (TEST_GRANT_POINTS, now, target_id),
            )
            _append_ledger(
                conn,
                account_id=target_id,
                workspace_id=workspace_id,
                direction="credit",
                points_delta=delta,
                source_type="test_grant",
                source_id="initial_test_points",
                idempotency_key=f"test-grant:{target_id}:{TEST_GRANT_POINTS}",
                metadata={"reason": "initial test points"},
            )
    return len(targets)


def _pricing(feature_key: str) -> FeaturePricing:
    return FEATURE_PRICING.get(feature_key, FeaturePricing(50, 10, 20, 3.0))


def _ensure_billing_account(conn: Any, actor: Actor) -> None:
    _ensure_billing_account_values(
        conn,
        actor.id,
        actor.username or actor.id,
        "",
        actor.workspace_id or "default",
    )


def _ensure_billing_account_values(
    conn: Any,
    account_id: str,
    username: str,
    email: str,
    workspace_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO workspaces (workspace_id, workspace_code, workspace_name, status)
        VALUES (?, ?, ?, 'active')
        ON CONFLICT(workspace_id) DO NOTHING
        """,
        (workspace_id or "default", workspace_id or "default", workspace_id or "default"),
    )
    conn.execute(
        """
        INSERT INTO auth_accounts (
            account_id, username, email, display_name, role, workspace_id,
            account_status, login_status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, 'operator', ?, 'active', 'offline', ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            username = excluded.username,
            email = CASE WHEN excluded.email <> '' THEN excluded.email ELSE auth_accounts.email END,
            workspace_id = excluded.workspace_id,
            updated_at = excluded.updated_at
        """,
        (account_id, username or account_id, email or "", username or account_id, workspace_id or "default", _utc_now(), _utc_now()),
    )


def _ensure_wallet(conn: Any, account_id: str, workspace_id: str) -> None:
    now = _utc_now()
    conn.execute(
        """
        INSERT INTO billing_wallets (
            account_id, workspace_id, points_balance, locked_points, version, created_at, updated_at
        )
        VALUES (?, ?, 0, 0, 0, ?, ?)
        ON CONFLICT(account_id) DO NOTHING
        """,
        (account_id, workspace_id or "default", now, now),
    )


def _append_ledger(
    conn: Any,
    *,
    account_id: str,
    workspace_id: str,
    direction: str,
    points_delta: int,
    source_type: str,
    source_id: str,
    idempotency_key: str,
    metadata: dict[str, Any],
) -> None:
    existing = conn.execute(
        """
        SELECT 1 FROM billing_point_ledger
        WHERE account_id = ? AND idempotency_key = ?
        """,
        (account_id, idempotency_key),
    ).fetchone()
    if existing is not None:
        return
    wallet = conn.execute(
        "SELECT points_balance, ledger_head_hash FROM billing_wallets WHERE account_id = ?",
        (account_id,),
    ).fetchone()
    balance_after = int(wallet["points_balance"]) if wallet else 0
    previous_hash = str(wallet["ledger_head_hash"] if wallet else "")
    entry_id = f"ledger_{secrets.token_urlsafe(18)}"
    payload = {
        "entry_id": entry_id,
        "account_id": account_id,
        "workspace_id": workspace_id,
        "direction": direction,
        "points_delta": int(points_delta),
        "balance_after": balance_after,
        "source_type": source_type,
        "source_id": source_id,
        "idempotency_key": idempotency_key,
        "previous_hash": previous_hash,
        "metadata": metadata,
    }
    row_hash = _ledger_hash(payload)
    conn.execute(
        """
        INSERT INTO billing_point_ledger (
            entry_id, account_id, workspace_id, direction, points_delta,
            balance_after, source_type, source_id, idempotency_key,
            previous_hash, row_hash, metadata_json, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_id,
            account_id,
            workspace_id,
            direction,
            int(points_delta),
            balance_after,
            source_type,
            source_id,
            idempotency_key,
            previous_hash,
            row_hash,
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            _utc_now(),
        ),
    )
    conn.execute(
        "UPDATE billing_wallets SET ledger_head_hash = ? WHERE account_id = ?",
        (row_hash, account_id),
    )


def _ledger_hash(payload: dict[str, Any]) -> str:
    secret = os.environ.get("WH_BILLING_LEDGER_SECRET", "local-dev-ledger-secret").encode("utf-8")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hmac.new(secret, encoded, hashlib.sha256).hexdigest()


def _merge_metadata(raw: str, update: dict[str, Any], provider_task_id: str) -> dict[str, Any]:
    try:
        base = json.loads(raw or "{}")
    except json.JSONDecodeError:
        base = {}
    if not isinstance(base, dict):
        base = {}
    base.update(update)
    if provider_task_id:
        base["provider_task_id"] = provider_task_id
    return base


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
