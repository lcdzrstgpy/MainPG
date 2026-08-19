from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .db import transaction
from .session import Actor


TEST_GRANT_POINTS = int(os.environ.get("WH_BILLING_TEST_GRANT_POINTS", "10000") or "10000")
GATEWAY_LEGACY_LEASE_SECONDS = 900


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


def feature_reserve_points(feature_key: str) -> int:
    """Compatibility estimate for local preflight before remote rules are loaded.

    The platform reserve endpoint remains authoritative and snapshots its active
    pricing rule.  This value is used only by the legacy desktop router to show
    a conservative preflight estimate while supporting older clients.
    """
    return FEATURE_PRICING.get(feature_key, FeaturePricing(50, 10, 20, 3.0)).reserve_points


def active_pricing(database_path: Path) -> dict[str, Any]:
    """Return the active server rule; callers must never accept a client price."""
    with transaction(database_path) as conn:
        return _pricing_payload(_active_pricing(conn))


def update_active_pricing(
    database_path: Path,
    *,
    payload: dict[str, Any],
    updated_by: str,
) -> dict[str, Any]:
    """Create the next server pricing revision after strict range validation."""
    def whole(name: str, current: int) -> int:
        value = payload.get(name, current)
        if isinstance(value, bool):
            raise HTTPException(status_code=400, detail=f"{name} must be an integer")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{name} must be an integer") from exc

    with transaction(database_path) as conn:
        current = _active_pricing(conn)
        next_rule = {
            "point_unit_scale": 10,
            "points_per_cny": whole("points_per_cny", int(current["points_per_cny"])),
            "text_reserve_units": whole("text_reserve_units", int(current["text_reserve_units"])),
            "text_charge_units": whole("text_charge_units", int(current["text_charge_units"])),
            "image_reserve_units": whole("image_reserve_units", int(current["image_reserve_units"])),
            "image_charge_units": whole("image_charge_units", int(current["image_charge_units"])),
            "min_client_version": str(payload.get("min_client_version", current["min_client_version"]) or "").strip()[:64],
        }
        if next_rule["points_per_cny"] <= 0:
            raise HTTPException(status_code=400, detail="points_per_cny must be positive")
        if not (35 <= next_rule["image_charge_units"] <= 45):
            raise HTTPException(status_code=400, detail="image_charge_units must be between 3.5 and 4.5 points")
        if not 0 <= next_rule["text_charge_units"] <= 10:
            raise HTTPException(status_code=400, detail="text_charge_units must be between 0 and 1 point")
        if next_rule["text_reserve_units"] < next_rule["text_charge_units"] or next_rule["image_reserve_units"] < next_rule["image_charge_units"]:
            raise HTTPException(status_code=400, detail="reserve units cannot be lower than charge units")
        now = _utc_now()
        conn.execute(
            """
            UPDATE billing_pricing_rules
            SET rule_version = rule_version + 1,
                points_per_cny = ?, text_reserve_units = ?, text_charge_units = ?,
                image_reserve_units = ?, image_charge_units = ?,
                min_client_version = ?, effective_at = ?, updated_at = ?, updated_by = ?
            WHERE rule_id = 1
            """,
            (
                next_rule["points_per_cny"], next_rule["text_reserve_units"], next_rule["text_charge_units"],
                next_rule["image_reserve_units"], next_rule["image_charge_units"],
                next_rule["min_client_version"], now, now, updated_by[:160],
            ),
        )
        return _pricing_payload(_active_pricing(conn))


def usage_history(
    database_path: Path,
    *,
    account_id: str,
    cursor: str = "",
    limit: int = 30,
    feature_key: str = "",
    usage_status: str = "",
) -> dict[str, Any]:
    """Read an account-scoped, sanitized consumption ledger.

    Provider keys and prompts are deliberately absent.  A user sees only the
    identifiers and amounts needed to reconcile their own balance.
    """
    page_size = max(1, min(int(limit), 100))
    clauses = ["account_id = ?"]
    params: list[Any] = [account_id]
    if feature_key:
        clauses.append("feature_key = ?")
        params.append(feature_key)
    if usage_status:
        clauses.append("status = ?")
        params.append(usage_status)
    if cursor:
        clauses.append("usage_id < ?")
        params.append(cursor)
    where = " AND ".join(clauses)
    with transaction(database_path) as conn:
        rule = _active_pricing(conn)
        rows = conn.execute(
            f"""
            SELECT usage_id, feature_key, source_ref, reserved_points, charged_points,
                   refunded_points, actual_cost_cny, provider, model, channel,
                   input_tokens, output_tokens, total_tokens, status, error_message,
                   metadata_json, created_at, settled_at
            FROM billing_ai_usage_events
            WHERE {where}
            ORDER BY created_at DESC, usage_id DESC
            LIMIT ?
            """,
            (*params, page_size + 1),
        ).fetchall()
    scale = int(rule["point_unit_scale"])
    has_more = len(rows) > page_size
    result: list[dict[str, Any]] = []
    for row in rows[:page_size]:
        metadata = _safe_usage_metadata(str(row["metadata_json"] or "{}"))
        result.append(
            {
                "usage_id": str(row["usage_id"]),
                "feature_key": str(row["feature_key"]),
                "source_ref": str(row["source_ref"]),
                "reserved_points": _display_points(int(row["reserved_points"]), scale),
                "charged_points": _display_points(int(row["charged_points"]), scale),
                "refunded_points": _display_points(int(row["refunded_points"]), scale),
                "status": str(row["status"]),
                "provider": str(row["provider"]),
                "model": str(row["model"]),
                "channel": str(row["channel"]),
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "total_tokens": int(row["total_tokens"]),
                "error_message": str(row["error_message"] or "")[:300],
                "created_at": str(row["created_at"]),
                "settled_at": str(row["settled_at"] or ""),
                "rule_version": metadata.get("server_pricing", {}).get("rule_version", "legacy"),
                "task": metadata.get("task_id", ""),
            }
        )
    return {
        "ok": True,
        "items": result,
        "next_cursor": str(result[-1]["usage_id"]) if has_more and result else "",
        "has_more": has_more,
        "point_unit_scale": scale,
    }


def _safe_usage_metadata(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    pricing = value.get("server_pricing")
    if isinstance(pricing, dict):
        result["server_pricing"] = {"rule_version": pricing.get("rule_version", "legacy")}
    for key in ("task_id", "item_id"):
        if isinstance(value.get(key), (str, int)):
            result[key] = value[key]
    return result


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
    quantity = max(1, int(quantity))
    usage_id = f"use_{secrets.token_urlsafe(18)}"
    now = _utc_now()
    with transaction(database_path) as conn:
        pricing = _pricing(conn, feature_key)
        pricing_rule = _active_pricing(conn)
        reserve_points = pricing.reserve_points * quantity
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
        event_metadata = dict(metadata or {})
        event_metadata["server_pricing"] = {
            "rule_version": int(pricing_rule["rule_version"]),
            "point_unit_scale": int(pricing_rule["point_unit_scale"]),
            "reserve_units": int(pricing.reserve_points),
            "charge_units": int(pricing.fixed_charge_points),
        }
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
                json.dumps(event_metadata, ensure_ascii=False, sort_keys=True),
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
        pricing = _pricing_from_event(conn, row)
        quantity = int(row["quantity"])
        fallback_points = pricing.fixed_charge_points * quantity
        min_points = int(row["min_charge_points"])
        # The upstream providers do not expose a reliable per-request cost.
        # Charge the versioned server rule snapshot, never an unverifiable
        # number posted by a desktop client.
        charge_points = max(min_points, fallback_points)
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
    expected_account_id: str | None = None,
    reject_gateway_activity: bool = False,
) -> dict[str, Any]:
    now = _utc_now()
    with transaction(database_path) as conn:
        row = conn.execute(
            "SELECT * FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        if row is None:
            if expected_account_id is not None:
                raise HTTPException(status_code=404, detail="usage event not found")
            return {"usage_id": usage_id, "status": "missing"}
        if expected_account_id is not None and str(row["account_id"]) != str(expected_account_id):
            raise HTTPException(status_code=404, detail="usage event not found")
        if row["status"] != "reserved":
            return dict(row)
        if reject_gateway_activity:
            stale_cutoff = (
                datetime.now(timezone.utc) - timedelta(seconds=GATEWAY_LEGACY_LEASE_SECONDS)
            ).isoformat(timespec="seconds")
            conn.execute(
                """
                UPDATE billing_ai_gateway_requests
                SET status = 'failed', phase = 'lease_expired', response_json = '',
                    lease_expires_at = '', updated_at = ?
                WHERE usage_id = ? AND status = 'in_progress'
                  AND (
                    (lease_expires_at <> '' AND julianday(lease_expires_at) <= julianday(?))
                    OR
                    (lease_expires_at = '' AND julianday(updated_at) <= julianday(?))
                  )
                """,
                (now, usage_id, now, stale_cutoff),
            )
            gateway_statuses = {
                str(gateway_row["status"])
                for gateway_row in conn.execute(
                """
                SELECT status
                FROM billing_ai_gateway_requests
                WHERE usage_id = ?
                """,
                (usage_id,),
                ).fetchall()
            }
            if "in_progress" in gateway_statuses:
                raise HTTPException(
                    status_code=409,
                    detail="provider request is still in progress",
                )
            if "succeeded" in gateway_statuses:
                return _settle_consumed_usage_after_business_failure(
                    conn,
                    row,
                    error_message=error_message,
                    settled_at=now,
                )
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
        settled = conn.execute(
            "SELECT * FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        return dict(settled)


def _settle_consumed_usage_after_business_failure(
    conn: Any,
    row: Any,
    *,
    error_message: str,
    settled_at: str,
) -> dict[str, Any]:
    """Charge a reserved usage when the authoritative gateway already succeeded."""
    pricing = _pricing_from_event(conn, row)
    charge_points = min(
        int(row["reserved_points"]),
        max(int(row["min_charge_points"]), pricing.fixed_charge_points * int(row["quantity"])),
    )
    refund_points = int(row["reserved_points"]) - charge_points
    provider = "wuyin" if str(row["feature_key"]) == "product_processing.image_grid_2k" else "aicoming"
    model = "image_gpt" if provider == "wuyin" else "gpt-5.6-terra"
    conn.execute(
        """
        UPDATE billing_wallets
        SET points_balance = points_balance - ?, locked_points = locked_points - ?,
            version = version + 1, updated_at = ?
        WHERE account_id = ?
        """,
        (charge_points, int(row["reserved_points"]), settled_at, row["account_id"]),
    )
    conn.execute(
        """
        UPDATE billing_ai_usage_events
        SET charged_points = ?, refunded_points = ?, provider = ?, model = ?,
            status = 'succeeded', error_message = ?, settled_at = ?
        WHERE usage_id = ? AND status = 'reserved'
        """,
        (
            charge_points,
            refund_points,
            provider,
            model,
            str(error_message)[:500],
            settled_at,
            row["usage_id"],
        ),
    )
    _append_ledger(
        conn,
        account_id=row["account_id"],
        workspace_id=row["workspace_id"],
        direction="debit",
        points_delta=charge_points,
        source_type="ai_usage",
        source_id=row["usage_id"],
        idempotency_key=f"{row['idempotency_key']}:settle",
        metadata={"feature_key": row["feature_key"], "business_outcome": "failed_after_provider_success"},
    )
    if refund_points:
        _append_ledger(
            conn,
            account_id=row["account_id"],
            workspace_id=row["workspace_id"],
            direction="unlock",
            points_delta=refund_points,
            source_type="ai_usage",
            source_id=row["usage_id"],
            idempotency_key=f"{row['idempotency_key']}:unlock",
            metadata={"feature_key": row["feature_key"]},
        )
    settled = conn.execute(
        "SELECT * FROM billing_ai_usage_events WHERE usage_id = ?",
        (row["usage_id"],),
    ).fetchone()
    return dict(settled)


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


def _active_pricing(conn: Any) -> Any:
    row = conn.execute(
        "SELECT * FROM billing_pricing_rules WHERE rule_id = 1"
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=503, detail="server billing pricing is unavailable")
    return row


def _pricing_payload(rule: Any) -> dict[str, Any]:
    scale = int(rule["point_unit_scale"])
    text_reserve = int(rule["text_reserve_units"])
    text_charge = int(rule["text_charge_units"])
    image_reserve = int(rule["image_reserve_units"])
    image_charge = int(rule["image_charge_units"])
    return {
        "rule_version": int(rule["rule_version"]),
        "currency": "CNY",
        "point_unit_scale": scale,
        "points_per_cny": int(rule["points_per_cny"]),
        "ratio_label": f"1 元 = {int(rule['points_per_cny'])} 积分",
        "product_link": {
            "actual_charge_min_points": _display_points(image_charge, scale),
            "actual_charge_max_points": _display_points(image_charge + text_charge, scale),
            "reserve_max_points": _display_points(image_reserve + text_reserve, scale),
        },
        "features": {
            "product_processing.text": {
                "reserve_units": text_reserve,
                "charge_units": text_charge,
                "reserve_points": _display_points(text_reserve, scale),
                "charge_points": _display_points(text_charge, scale),
            },
            "product_processing.image_grid_2k": {
                "reserve_units": image_reserve,
                "charge_units": image_charge,
                "reserve_points": _display_points(image_reserve, scale),
                "charge_points": _display_points(image_charge, scale),
            },
        },
        "min_client_version": str(rule["min_client_version"] or ""),
        "effective_at": str(rule["effective_at"] or ""),
        "updated_at": str(rule["updated_at"] or ""),
    }


def _display_points(units: int, scale: int = 10) -> int | float:
    value = int(units) / int(scale)
    return int(value) if value.is_integer() else value


def _pricing(conn: Any, feature_key: str) -> FeaturePricing:
    rule = _active_pricing(conn)
    if feature_key == "product_processing.text":
        return FeaturePricing(
            int(rule["text_reserve_units"]),
            int(rule["text_charge_units"]),
            int(rule["text_charge_units"]),
            1.0,
        )
    if feature_key == "product_processing.image_grid_2k":
        return FeaturePricing(
            int(rule["image_reserve_units"]),
            int(rule["image_charge_units"]),
            int(rule["image_charge_units"]),
            1.0,
        )
    legacy = FEATURE_PRICING.get(feature_key, FeaturePricing(50, 10, 20, 3.0))
    return FeaturePricing(
        legacy.reserve_points * 10,
        legacy.min_charge_points * 10,
        legacy.fixed_charge_points * 10,
        legacy.cost_multiplier,
    )


def _pricing_from_event(conn: Any, row: Any) -> FeaturePricing:
    try:
        metadata = json.loads(str(row["metadata_json"] or "{}"))
        snapshot = metadata.get("server_pricing") if isinstance(metadata, dict) else None
        if isinstance(snapshot, dict):
            return FeaturePricing(
                int(snapshot["reserve_units"]),
                int(snapshot["charge_units"]),
                int(snapshot["charge_units"]),
                1.0,
            )
    except (TypeError, ValueError, KeyError):
        pass
    return _pricing(conn, str(row["feature_key"]))


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
