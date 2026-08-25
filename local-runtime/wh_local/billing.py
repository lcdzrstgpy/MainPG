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
BATCH_BILLING_PROFILE_PRODUCT = "product_processing"
BATCH_BILLING_PROFILE_POD = "pod_random_v1"
# POD 每条款式定价：服务器随机取 40..50 整数积分。
POD_LINK_PRICE_MIN_POINTS = 40
POD_LINK_PRICE_VARIANTS = 11


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
    # Compatibility estimates for the one-link product workflow.  The
    # platform rule remains authoritative; a completed link charges 40 points
    # (text 5 + image 35) and reserves 45 points while it is running.
    "product_processing.text": FeaturePricing(5, 5, 5, 1.0),
    "product_processing.image_grid_2k": FeaturePricing(40, 35, 35, 1.0),
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
        total_charge_units = (
            next_rule["text_charge_units"] + next_rule["image_charge_units"]
        )
        if not 350 <= total_charge_units <= 450:
            raise HTTPException(
                status_code=400,
                detail=(
                    "one product link total charge (text plus image) must be "
                    "between 35 and 45 points"
                ),
            )
        if next_rule["text_charge_units"] < 0 or next_rule["image_charge_units"] < 0:
            raise HTTPException(status_code=400, detail="charge units cannot be negative")
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
        # 直连模式走批量冻结/结算（按链接计费），结算记录在 batch 表而不写 usage_events，
        # 这里把批量结算合并进客户端「消费流水」，与调用级流水统一按时间倒序展示。
        batch_rows = conn.execute(
            """
            SELECT freeze_id, task_id, link_count, frozen_points, charged_points,
                   refunded_points, status, billing_profile, rule_version,
                   created_at, settled_at
            FROM billing_batch_freezes
            WHERE account_id = ?
            ORDER BY created_at DESC, freeze_id DESC
            """,
            (account_id,),
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
                "billing_profile": "ai_usage",
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
    for row in batch_rows:
        raw_status = str(row["status"] or "")
        billing_profile = str(row["billing_profile"] or BATCH_BILLING_PROFILE_PRODUCT)
        is_pod = billing_profile == BATCH_BILLING_PROFILE_POD
        if raw_status == "settled":
            status = "succeeded"
        elif raw_status == "released":
            status = "failed"
        else:
            status = "frozen"
        result.append(
            {
                "usage_id": f"batch:{row['freeze_id']}",
                "feature_key": "pod_customization.batch" if is_pod else "product_processing.batch",
                "billing_profile": billing_profile,
                "source_ref": "",
                "reserved_points": _display_points(int(row["frozen_points"]), scale),
                "charged_points": _display_points(int(row["charged_points"]), scale),
                "refunded_points": _display_points(int(row["refunded_points"]), scale),
                "status": status,
                "provider": "POD 定制结算" if is_pod else "批量链接结算",
                "model": (
                    f"{int(row['link_count'])} 款创作"
                    if is_pod
                    else f"{int(row['link_count'])} 条链接"
                ),
                "channel": "",
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "error_message": "",
                "created_at": str(row["created_at"]),
                "settled_at": str(row["settled_at"] or ""),
                "rule_version": int(row["rule_version"] or 0),
                "task": str(row["task_id"] or ""),
            }
        )
    # 合并后按时间倒序统一排序；批量记录数量有限，分页游标不再精确推进。
    result.sort(key=lambda item: (str(item["created_at"]), str(item["usage_id"])), reverse=True)
    result = result[:page_size]
    return {
        "ok": True,
        "items": result,
        "next_cursor": "",
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
        base_charge_points = max(min_points, fallback_points)
        base_charge_points = min(base_charge_points, int(row["reserved_points"]))
        # 重试溢价：链接发生过 AI 重试/重绘/修复时加收一次（只认图像子项，
        # 避免同链接 text/image 两个 usage 各加一遍）。溢价不在冻结范围内，
        # 直接从余额扣除，不参与 reserved 封顶。
        event_metadata = _merge_metadata(row["metadata_json"], metadata or {}, provider_task_id)
        premium_units = 0
        if (
            str(row["feature_key"]) == RETRY_PREMIUM_FEATURE
            and bool(event_metadata.get("billing_retried"))
        ):
            premium_units = RETRY_PREMIUM_UNITS
        charge_points = base_charge_points + premium_units
        refund_points = int(row["reserved_points"]) - base_charge_points
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
        if premium_units:
            event_metadata["retry_premium_units"] = premium_units
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
                "retry_premium_units": premium_units,
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
    target_units = TEST_GRANT_POINTS * 10
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
            if int(wallet["points_balance"]) >= target_units:
                continue
            delta = target_units - int(wallet["points_balance"])
            conn.execute(
                """
                UPDATE billing_wallets
                SET points_balance = ?, version = version + 1, updated_at = ?
                WHERE account_id = ?
                """,
                (target_units, now, target_id),
            )
            _append_ledger(
                conn,
                account_id=target_id,
                workspace_id=workspace_id,
                direction="credit",
                points_delta=delta,
                source_type="test_grant",
                source_id="initial_test_points",
                idempotency_key=f"test-grant:{target_id}:{target_units}",
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


def settle_payment_order(
    database_path: Path,
    *,
    provider: str,
    out_trade_no: str,
    gateway_transaction_id: str,
    amount_cents: int,
    provider_status: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Credit one verified payment order through the shared wallet ledger.

    The provider callback must be signature-verified before reaching this
    function. This transaction independently rechecks provider, amount and
    state, making duplicate callbacks and mismatched orders harmless.
    """

    normalized_provider = str(provider or "").strip().lower()
    if normalized_provider not in {"alipay", "wechat"}:
        raise HTTPException(status_code=400, detail="unsupported payment provider")
    transaction_id = str(gateway_transaction_id or "").strip()
    if not transaction_id:
        raise HTTPException(status_code=400, detail="payment transaction id is required")
    normalized_order_no = str(out_trade_no or "").strip()
    now = _utc_now()

    with transaction(database_path) as conn:
        order = conn.execute(
            "SELECT * FROM billing_payment_orders WHERE out_trade_no = ?",
            (normalized_order_no,),
        ).fetchone()
        if order is None:
            raise HTTPException(status_code=404, detail="payment order not found")
        if str(order["provider"]) != normalized_provider:
            raise HTTPException(status_code=409, detail="payment provider does not match order")
        if int(order["amount_cents"]) != int(amount_cents):
            raise HTTPException(status_code=409, detail="payment amount does not match order")

        transaction_owner = conn.execute(
            """
            SELECT order_id FROM billing_payment_orders
            WHERE gateway_transaction_id = ? AND gateway_transaction_id <> '' AND order_id <> ?
            """,
            (transaction_id, str(order["order_id"])),
        ).fetchone()
        if transaction_owner is not None:
            raise HTTPException(status_code=409, detail="payment transaction already belongs to another order")

        if str(order["status"]) == "paid":
            if str(order["gateway_transaction_id"] or "") != transaction_id:
                raise HTTPException(status_code=409, detail="payment order transaction does not match")
            return {"already_paid": True, "order": dict(order)}
        if str(order["status"]) != "pending":
            raise HTTPException(status_code=409, detail="payment order is no longer payable")

        account_id = str(order["account_id"])
        workspace_id = str(order["workspace_id"] or "default")
        points = int(order["points"])
        _ensure_wallet(conn, account_id, workspace_id)
        conn.execute(
            """
            UPDATE billing_wallets
            SET points_balance = points_balance + ?, version = version + 1, updated_at = ?
            WHERE account_id = ?
            """,
            (points, now, account_id),
        )
        conn.execute(
            """
            UPDATE billing_payment_orders
            SET status = 'paid', gateway_transaction_id = ?, paid_at = ?, updated_at = ?
            WHERE order_id = ? AND status = 'pending'
            """,
            (transaction_id, now, now, str(order["order_id"])),
        )
        ledger_metadata = {
            "out_trade_no": normalized_order_no,
            "gateway_transaction_id": transaction_id,
            "provider_status": str(provider_status or "")[:64],
            **dict(metadata or {}),
        }
        _append_ledger(
            conn,
            account_id=account_id,
            workspace_id=workspace_id,
            direction="credit",
            points_delta=points,
            source_type=f"payment_{normalized_provider}",
            source_id=str(order["order_id"]),
            idempotency_key=f"payment:{normalized_provider}:{normalized_order_no}:credit",
            metadata=ledger_metadata,
        )
        settled = conn.execute(
            "SELECT * FROM billing_payment_orders WHERE order_id = ?",
            (str(order["order_id"]),),
        ).fetchone()
    return {"already_paid": False, "order": dict(settled)}


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


# ---------------------------------------------------------------------------
# 批次定价引擎：一条产品链接按子项（title/description/product_dimensions/
# four_grid/detail_images）拆分定价，冻结按最大范围预扣，结算按子项明细扣费
# 与退款。定价规则只存服务端并可被管理员干预，每次修改写入审计日志。
# ---------------------------------------------------------------------------

# 定价单位：与 billing_pricing_rules.point_unit_scale 保持一致（10 单位 = 1 积分）。
PIC_UNIT_SCALE = 10
# 子项白名单（顺序即产品处理 scope 的展示顺序）。
SUBITEM_FEATURE_KEYS = (
    "title",
    "description",
    "product_dimensions",
    "four_grid",
    "detail_images",
)
# 冻结按最大范围预扣：默认每子项 charge 之和封顶 45 积分。
DEFAULT_BATCH_FREEZE_PER_LINK = 400  # 固定 40 积分/链接（400 单位）；与子项定价总和联动见 pricing_items
# TTL 兜底：客户端正常结算失败后，超过该天数仍未结算的冻结批次由服务端自动全额释放。
# 主路径已改为客户端任务终态即时结算，此值仅兜底客户端崩溃/永久失联场景（2 天兼顾成本与体验）。
BATCH_FREEZE_TTL_DAYS = 2
# 重试溢价：链接发生过 AI 重试/重绘/修复时，该链接加收 10 积分（100 单位）。
# 语义是「单条链接计一次重试溢价」，不按重试次数累加，也不跨链接共享。
RETRY_PREMIUM_UNITS = 100
# 服务端托管结算只认图像子项携带重试标记，避免 text/image 两个 usage 重复计费。
RETRY_PREMIUM_FEATURE = "product_processing.image_grid_2k"


def pricing_items(database_path: Path, *, rule_version: int | None = None) -> dict[str, Any]:
    """Return the active per-subitem pricing for a given rule version.

    rule_version defaults to the current billing_pricing_rules.rule_version.
    Read-only: uses a plain connection so it can be called inside a settle
    transaction without taking a nested write lock.
    """
    from .db import connect

    conn = connect(database_path)
    try:
        rule = _active_pricing(conn)
        version = int(rule["rule_version"]) if rule_version is None else max(1, int(rule_version))
        rows = conn.execute(
            """
            SELECT feature_key, charge_points, intercept_refund_ratio, no_return_refund_ratio
            FROM billing_pricing_items
            WHERE rule_version = ?
              AND feature_key IN ('title', 'description', 'product_dimensions', 'four_grid', 'detail_images')
            ORDER BY CASE feature_key
                WHEN 'title' THEN 1 WHEN 'description' THEN 2
                WHEN 'product_dimensions' THEN 3 WHEN 'four_grid' THEN 4
                WHEN 'detail_images' THEN 5 ELSE 6 END
            """,
            (version,),
        ).fetchall()
        items: dict[str, Any] = {}
        total_units = 0
        for row in rows:
            charge = int(row["charge_points"])
            total_units += charge
            items[str(row["feature_key"])] = {
                "charge_points": _display_points(charge),
                "charge_units": charge,
                "intercept_refund_ratio": float(row["intercept_refund_ratio"]),
                "no_return_refund_ratio": float(row["no_return_refund_ratio"]),
            }
    finally:
        conn.close()
    return {
        "rule_version": version,
        "point_unit_scale": PIC_UNIT_SCALE,
        "max_charge_per_link": _display_points(total_units),
        "max_charge_units_per_link": total_units,
        "freeze_per_link": _display_points(total_units),
        "freeze_units_per_link": total_units,
        "ttl_days": BATCH_FREEZE_TTL_DAYS,
        "items": items,
        "effective_at": str(rule["effective_at"] or "") if rule_version is None else "",
    }


def update_pricing_items(
    database_path: Path,
    *,
    items: dict[str, Any],
    updated_by: str,
    change_reason: str = "",
) -> dict[str, Any]:
    """Create the next per-subitem pricing revision with a full audit trail.

    ``items`` maps feature_key -> charge_points (points) and optional
    intercept_refund_ratio / no_return_refund_ratio.  All subitems must be
    present; total charge must stay within 35..45 points (350..450 units).
    """
    normalized: dict[str, tuple[int, float, float]] = {}
    for key in SUBITEM_FEATURE_KEYS:
        value = items.get(key)
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail=f"missing pricing for subitem {key}")
        try:
            charge = float(value.get("charge_points", 0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"charge_points for {key} must be numeric") from exc
        charge_units = int(round(charge * PIC_UNIT_SCALE))
        intercept = float(value.get("intercept_refund_ratio", 0.5))
        no_return = float(value.get("no_return_refund_ratio", 1.0))
        if charge_units < 0 or not 0 <= intercept <= 1 or not 0 <= no_return <= 1:
            raise HTTPException(
                status_code=400,
                detail=f"invalid pricing for subitem {key}",
            )
        normalized[key] = (charge_units, intercept, no_return)
    total_units = sum(charge_units for charge_units, _, _ in normalized.values())
    if not 350 <= total_units <= 450:
        raise HTTPException(
            status_code=400,
            detail="one product link total charge must be between 35 and 45 points",
        )
    with transaction(database_path) as conn:
        rule = _active_pricing(conn)
        current_version = int(rule["rule_version"])
        next_version = current_version + 1
        before = pricing_items(database_path, rule_version=current_version)
        now = _utc_now()
        conn.execute(
            """
            UPDATE billing_pricing_rules
            SET rule_version = rule_version + 1, effective_at = ?, updated_at = ?, updated_by = ?
            WHERE rule_id = 1
            """,
            (now, now, str(updated_by or "system")[:160]),
        )
        for key, (charge_units, intercept, no_return) in normalized.items():
            conn.execute(
                """
                INSERT INTO billing_pricing_items (
                    rule_version, feature_key, charge_points,
                    intercept_refund_ratio, no_return_refund_ratio, effective_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (next_version, key, charge_units, intercept, no_return, now),
            )
        # POD pricing shares the global rule version. Product-only revisions
        # must carry configured POD items forward instead of silently making
        # subsequent POD freezes fail closed.
        pod_rows = conn.execute(
            """
            SELECT feature_key, charge_points, intercept_refund_ratio, no_return_refund_ratio
            FROM billing_pricing_items
            WHERE rule_version = ? AND feature_key IN ('pod.title', 'pod.image')
            """,
            (current_version,),
        ).fetchall()
        for row in pod_rows:
            conn.execute(
                """
                INSERT INTO billing_pricing_items (
                    rule_version, feature_key, charge_points,
                    intercept_refund_ratio, no_return_refund_ratio, effective_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    next_version,
                    str(row["feature_key"]),
                    int(row["charge_points"]),
                    float(row["intercept_refund_ratio"]),
                    float(row["no_return_refund_ratio"]),
                    now,
                ),
            )
        after = {
            "rule_version": next_version,
            "point_unit_scale": PIC_UNIT_SCALE,
            "max_charge_units_per_link": total_units,
            "items": {
                key: {
                    "charge_points": _display_points(charge_units),
                    "charge_units": charge_units,
                    "intercept_refund_ratio": intercept,
                    "no_return_refund_ratio": no_return,
                }
                for key, (charge_units, intercept, no_return) in normalized.items()
            },
            "effective_at": now,
        }
        conn.execute(
            """
            INSERT INTO billing_pricing_changelog (
                rule_version, changed_by, change_reason, before_json, after_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                next_version,
                str(updated_by or "system")[:160],
                str(change_reason or "").strip()[:500],
                json.dumps(before, ensure_ascii=False, sort_keys=True),
                json.dumps(after, ensure_ascii=False, sort_keys=True),
            ),
        )
    return after


def pricing_changelog(
    database_path: Path,
    *,
    limit: int = 50,
    cursor_id: int | None = None,
) -> list[dict[str, Any]]:
    """Read the append-only pricing audit trail (newest first)."""
    page_size = max(1, min(int(limit), 200))
    where = ""
    params: list[Any] = []
    if cursor_id is not None:
        where = "WHERE id < ?"
        params = [int(cursor_id)]
    with transaction(database_path) as conn:
        rows = conn.execute(
            f"""
            SELECT id, rule_version, changed_by, change_reason, before_json, after_json, created_at
            FROM billing_pricing_changelog
            {where}
            ORDER BY id DESC
            LIMIT ?
            """,
            (*params, page_size),
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "rule_version": int(row["rule_version"]),
                "changed_by": str(row["changed_by"]),
                "change_reason": str(row["change_reason"]),
                "before": _loads_json(row["before_json"]),
                "after": _loads_json(row["after_json"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]


def _loads_json(raw: Any) -> Any:
    try:
        return json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}


def compute_batch_charge(
    database_path: Path,
    *,
    rule_version: int | None,
    item_results: list[dict[str, str]],
) -> dict[str, Any]:
    """Compute per-subitem charge/refund for one link from client-reported status.

    status: success -> full charge; intercept -> refund ratio; no_return -> full refund.
    The server rule is authoritative; client numbers are never trusted directly.
    """
    pricing = pricing_items(database_path, rule_version=rule_version)
    items = pricing["items"]
    charge_units = 0
    refund_units = 0
    details: list[dict[str, Any]] = []
    # 重试溢价：该链接任一子项带 retried 标记即整条链接加收一次（不按子项重复累加）。
    retried = any(str(result.get("retried") or "").lower() in {"true", "1", "yes"} for result in item_results)
    premium_units = RETRY_PREMIUM_UNITS if retried else 0
    for result in item_results:
        key = str(result.get("feature") or "").strip()
        status = str(result.get("status") or "").strip()
        if key not in items or status not in {"success", "intercept", "no_return"}:
            raise HTTPException(
                status_code=400,
                detail=f"invalid subitem result: feature={key!r} status={status!r}",
            )
        item = items[key]
        units = int(item["charge_units"])
        if status == "success":
            charge_units += units
            item_refund = 0
        elif status == "intercept":
            charge = int(round(units * (1 - float(item["intercept_refund_ratio"]))))
            charge_units += charge
            item_refund = units - charge
        else:  # no_return
            item_refund = units
        refund_units += item_refund
        details.append(
            {
                "feature": key,
                "status": status,
                "charge_points": _display_points(units),
                "charge_units": units,
                "refund_points": _display_points(item_refund),
                "refund_units": item_refund,
            }
        )
    return {
        "rule_version": int(pricing["rule_version"]),
        "charge_units": charge_units,
        "refund_units": refund_units,
        "charge_points": _display_points(charge_units),
        "refund_points": _display_points(refund_units),
        "premium_units": premium_units,
        "premium_points": _display_points(premium_units),
        "details": details,
    }


def freeze_batch_points(
    database_path: Path,
    actor: Actor,
    *,
    link_count: int,
    scope: list[str] | None = None,
    idempotency_key: str = "",
    billing_profile: str = BATCH_BILLING_PROFILE_PRODUCT,
    task_id: str = "",
) -> dict[str, Any]:
    """Reserve batch points (N x freeze_per_link) before the client starts work.

    Returns the freeze record; keys are issued separately by the auth server so
    the billing layer stays free of any credential handling.

    ``task_id`` links the freeze to the client-side processing task so the
    consumption ledger can be reconciled against task history.
    """
    link_count = max(1, int(link_count))
    profile = str(billing_profile or BATCH_BILLING_PROFILE_PRODUCT).strip()
    if profile not in {BATCH_BILLING_PROFILE_PRODUCT, BATCH_BILLING_PROFILE_POD}:
        raise HTTPException(status_code=400, detail="invalid batch billing profile")
    pricing = pricing_items(database_path)
    idem = str(idempotency_key or "").strip()
    normalized_task_id = str(task_id or "").strip()[:64]
    normalized_scope = [str(item) for item in (scope or []) if str(item).strip()]
    with transaction(database_path) as conn:
        _ensure_billing_account(conn, actor)
        _ensure_wallet(conn, actor.id, actor.workspace_id)
        if idem:
            existing = conn.execute(
                """
                SELECT * FROM billing_batch_freezes
                WHERE account_id = ? AND freeze_id = ?
                """,
                (actor.id, idem),
            ).fetchone()
            if existing is not None:
                if str(existing["billing_profile"] or BATCH_BILLING_PROFILE_PRODUCT) != profile:
                    raise HTTPException(status_code=409, detail="batch billing profile conflict")
                return _batch_freeze_response(existing, pricing=pricing, already_frozen=True)
        if profile == BATCH_BILLING_PROFILE_POD:
            allowed_scope = {"title", "four_grid"}
            if (
                not normalized_scope
                or len(set(normalized_scope)) != len(normalized_scope)
                or set(normalized_scope) - allowed_scope
            ):
                raise HTTPException(status_code=400, detail="invalid POD batch billing scope")
            link_price_units = [
                (POD_LINK_PRICE_MIN_POINTS + secrets.randbelow(POD_LINK_PRICE_VARIANTS))
                * PIC_UNIT_SCALE
                for _ in range(link_count)
            ]
            frozen_units = sum(link_price_units)
        else:
            freeze_units_per_link = int(pricing["freeze_units_per_link"])
            link_price_units = []
            frozen_units = freeze_units_per_link * link_count
        frozen_points = _display_points(frozen_units)
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
        if available < frozen_units:
            raise HTTPException(
                status_code=402,
                detail=f"积分不足：需要冻结 {frozen_points}，当前可用 {_display_points(available)}",
            )
        freeze_id = idem or f"frz_{secrets.token_urlsafe(18)}"
        now = _utc_now()
        scope_json = json.dumps(normalized_scope, ensure_ascii=False)
        link_prices_json = json.dumps(link_price_units, ensure_ascii=False)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=BATCH_FREEZE_TTL_DAYS)
        ).isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE billing_wallets
            SET locked_points = locked_points + ?, version = version + 1, updated_at = ?
            WHERE account_id = ?
            """,
            (frozen_units, now, actor.id),
        )
        conn.execute(
            """
            INSERT INTO billing_batch_freezes (
                freeze_id, account_id, workspace_id, task_id, link_count, scope_json,
                frozen_points, status, created_at, expires_at,
                billing_profile, rule_version, link_prices_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'frozen', ?, ?, ?, ?, ?)
            """,
            (
                freeze_id,
                actor.id,
                actor.workspace_id or "default",
                normalized_task_id,
                link_count,
                scope_json,
                frozen_units,
                now,
                expires_at,
                profile,
                int(pricing["rule_version"]),
                link_prices_json,
            ),
        )
        _append_ledger(
            conn,
            account_id=actor.id,
            workspace_id=actor.workspace_id or "default",
            direction="lock",
            points_delta=frozen_units,
            source_type="batch_freeze",
            source_id=freeze_id,
            idempotency_key=f"batch_freeze:{freeze_id}:lock",
            metadata={
                "link_count": link_count,
                "billing_profile": profile,
                "rule_version": int(pricing["rule_version"]),
            },
        )
    return {
        "freeze_id": freeze_id,
        "account_id": actor.id,
        "workspace_id": actor.workspace_id or "default",
        "link_count": link_count,
        "frozen_points": _display_points(frozen_units),
        "freeze_per_link": (
            pricing["freeze_per_link"]
            if profile == BATCH_BILLING_PROFILE_PRODUCT
            else None
        ),
        "rule_version": int(pricing["rule_version"]),
        "billing_profile": profile,
        "link_prices": [_display_points(units) for units in link_price_units],
        "scope": normalized_scope,
        "status": "frozen",
        "expires_at": expires_at,
        "already_frozen": False,
    }


def _batch_freeze_response(
    row: Any,
    *,
    pricing: dict[str, Any],
    already_frozen: bool,
) -> dict[str, Any]:
    profile = str(row["billing_profile"] or BATCH_BILLING_PROFILE_PRODUCT)
    try:
        link_price_units = [int(value) for value in json.loads(str(row["link_prices_json"] or "[]"))]
    except (TypeError, ValueError, json.JSONDecodeError):
        link_price_units = []
    try:
        normalized_scope = [str(value) for value in json.loads(str(row["scope_json"] or "[]"))]
    except (TypeError, ValueError, json.JSONDecodeError):
        normalized_scope = []
    return {
        "freeze_id": str(row["freeze_id"]),
        "account_id": str(row["account_id"]),
        "workspace_id": str(row["workspace_id"] or "default"),
        "link_count": int(row["link_count"]),
        "frozen_points": _display_points(int(row["frozen_points"])),
        "freeze_per_link": (
            pricing["freeze_per_link"]
            if profile == BATCH_BILLING_PROFILE_PRODUCT
            else None
        ),
        "rule_version": int(row["rule_version"] or pricing["rule_version"]),
        "billing_profile": profile,
        "link_prices": [_display_points(units) for units in link_price_units],
        "scope": normalized_scope,
        "status": str(row["status"]),
        "expires_at": str(row["expires_at"] or ""),
        "already_frozen": bool(already_frozen),
    }


def freeze_planned_points(
    database_path: Path,
    actor: Actor,
    *,
    frozen_units: int,
    item_count: int,
    scope: list[str],
    idempotency_key: str,
    source_type: str,
    persist_plan: Any | None = None,
    validate_existing: Any | None = None,
) -> dict[str, Any]:
    """Lock an exact server-computed amount for a versioned call plan.

    This is the shared wallet/ledger primitive used by non-product batch
    bridges. Callers own plan validation and pricing; amounts are integer
    tenths of a point and are never accepted from an HTTP request directly.
    """
    units = int(frozen_units)
    count = int(item_count)
    idem = str(idempotency_key or "").strip()
    if units < 0 or count < 1 or not idem:
        raise HTTPException(status_code=400, detail="invalid planned point freeze")
    with transaction(database_path) as conn:
        _ensure_billing_account(conn, actor)
        _ensure_wallet(conn, actor.id, actor.workspace_id)
        existing = conn.execute(
            """
            SELECT * FROM billing_batch_freezes
            WHERE account_id = ? AND freeze_id = ?
            """,
            (actor.id, idem),
        ).fetchone()
        if existing is not None:
            if validate_existing is not None:
                validate_existing(conn, existing)
            return {
                "freeze_id": str(existing["freeze_id"]),
                "account_id": str(existing["account_id"]),
                "workspace_id": str(existing["workspace_id"]),
                "item_count": int(existing["link_count"]),
                "frozen_points": _display_points(int(existing["frozen_points"])),
                "status": str(existing["status"]),
                "expires_at": str(existing["expires_at"]),
                "already_frozen": True,
            }
        wallet = conn.execute(
            """
            SELECT points_balance, locked_points, manual_frozen_points
            FROM billing_wallets WHERE account_id = ?
            """,
            (actor.id,),
        ).fetchone()
        available = (
            int(wallet["points_balance"])
            - int(wallet["locked_points"])
            - int(wallet["manual_frozen_points"])
        )
        if available < units:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"积分不足：需要冻结 {_display_points(units)}，"
                    f"当前可用 {_display_points(available)}"
                ),
            )
        now = _utc_now()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=BATCH_FREEZE_TTL_DAYS)
        ).isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE billing_wallets
            SET locked_points = locked_points + ?, version = version + 1, updated_at = ?
            WHERE account_id = ?
            """,
            (units, now, actor.id),
        )
        conn.execute(
            """
            INSERT INTO billing_batch_freezes (
                freeze_id, account_id, workspace_id, link_count, scope_json,
                frozen_points, status, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'frozen', ?, ?)
            """,
            (
                idem,
                actor.id,
                actor.workspace_id or "default",
                count,
                json.dumps(scope, ensure_ascii=False),
                units,
                now,
                expires_at,
            ),
        )
        _append_ledger(
            conn,
            account_id=actor.id,
            workspace_id=actor.workspace_id or "default",
            direction="lock",
            points_delta=units,
            source_type=source_type,
            source_id=idem,
            idempotency_key=f"{source_type}:{idem}:lock",
            metadata={"item_count": count},
        )
        if persist_plan is not None:
            persist_plan(conn, idem)
    return {
        "freeze_id": idem,
        "account_id": actor.id,
        "workspace_id": actor.workspace_id or "default",
        "item_count": count,
        "frozen_points": _display_points(units),
        "status": "frozen",
        "expires_at": expires_at,
        "already_frozen": False,
    }


def settle_planned_points(
    database_path: Path,
    freeze_id: str,
    *,
    expected_account_id: str,
    charge_units: int,
    refund_units: int,
    source_type: str,
    metadata: dict[str, Any] | None = None,
    persist_settlement: Any | None = None,
) -> dict[str, Any]:
    """Atomically debit an exact planned charge and release its full lock."""
    charge = int(charge_units)
    refund = int(refund_units)
    if charge < 0 or refund < 0:
        raise HTTPException(status_code=400, detail="invalid planned settlement")
    with transaction(database_path) as conn:
        freeze = conn.execute(
            "SELECT * FROM billing_batch_freezes WHERE freeze_id = ?",
            (freeze_id,),
        ).fetchone()
        if freeze is None or str(freeze["account_id"]) != str(expected_account_id):
            raise HTTPException(status_code=404, detail="POD freeze not found")
        if str(freeze["status"]) == "settled":
            return {
                "freeze_id": freeze_id,
                "status": "settled",
                "charged_points": _display_points(int(freeze["charged_points"])),
                "refunded_points": _display_points(int(freeze["refunded_points"])),
                "already_settled": True,
            }
        if str(freeze["status"]) != "frozen":
            raise HTTPException(status_code=409, detail="POD freeze is no longer active")
        frozen = int(freeze["frozen_points"])
        if charge + refund != frozen:
            raise HTTPException(status_code=400, detail="settlement does not reconcile to frozen points")
        now = _utc_now()
        conn.execute(
            """
            UPDATE billing_wallets
            SET points_balance = points_balance - ?,
                locked_points = locked_points - ?,
                version = version + 1,
                updated_at = ?
            WHERE account_id = ?
            """,
            (charge, frozen, now, expected_account_id),
        )
        conn.execute(
            """
            UPDATE billing_batch_freezes
            SET charged_points = ?, refunded_points = ?, status = 'settled', settled_at = ?
            WHERE freeze_id = ?
            """,
            (charge, refund, now, freeze_id),
        )
        ledger_metadata = dict(metadata or {})
        _append_ledger(
            conn,
            account_id=expected_account_id,
            workspace_id=str(freeze["workspace_id"] or "default"),
            direction="debit",
            points_delta=charge,
            source_type=source_type,
            source_id=freeze_id,
            idempotency_key=f"{source_type}:{freeze_id}:debit",
            metadata=ledger_metadata,
        )
        if refund:
            _append_ledger(
                conn,
                account_id=expected_account_id,
                workspace_id=str(freeze["workspace_id"] or "default"),
                direction="unlock",
                points_delta=refund,
                source_type=source_type,
                source_id=freeze_id,
                idempotency_key=f"{source_type}:{freeze_id}:unlock",
                metadata=ledger_metadata,
            )
        if persist_settlement is not None:
            persist_settlement(conn, freeze_id)
    return {
        "freeze_id": freeze_id,
        "status": "settled",
        "charged_points": _display_points(charge),
        "refunded_points": _display_points(refund),
        "already_settled": False,
    }


def settle_batch_points(
    database_path: Path,
    freeze_id: str,
    *,
    item_results: list[dict[str, Any]],
    expected_account_id: str,
) -> dict[str, Any]:
    """Settle a frozen batch from per-link subitem status reported by the client.

    Computes total charge (success + intercept partial) and refund (no_return
    + intercept partial), debits the wallet and releases the remaining lock.
    Idempotent: a freeze already settled returns its stored result.
    """
    with transaction(database_path) as conn:
        freeze = conn.execute(
            "SELECT * FROM billing_batch_freezes WHERE freeze_id = ?",
            (freeze_id,),
        ).fetchone()
        if freeze is None:
            raise HTTPException(status_code=404, detail="batch freeze not found")
        if str(freeze["account_id"]) != str(expected_account_id):
            raise HTTPException(status_code=404, detail="batch freeze not found")
        if freeze["status"] == "settled":
            return {
                "freeze_id": freeze_id,
                "status": "settled",
                "charged_points": _display_points(int(freeze["charged_points"])),
                "refunded_points": _display_points(int(freeze["refunded_points"])),
                "already_settled": True,
            }
        profile = str(freeze["billing_profile"] or BATCH_BILLING_PROFILE_PRODUCT)
        pricing = pricing_items(database_path, rule_version=None)
        freeze_rule_version = (
            int(freeze["rule_version"] or pricing["rule_version"])
            if profile == BATCH_BILLING_PROFILE_POD
            else int(pricing["rule_version"])
        )
        pod_link_price_units: list[int] = []
        pod_scope: tuple[str, ...] = ()
        if profile == BATCH_BILLING_PROFILE_POD:
            try:
                pod_link_price_units = [
                    int(value)
                    for value in json.loads(str(freeze["link_prices_json"] or "[]"))
                ]
                pod_scope = tuple(
                    str(value)
                    for value in json.loads(str(freeze["scope_json"] or "[]"))
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=409, detail="invalid POD billing snapshot") from exc
            if (
                len(pod_link_price_units) != int(freeze["link_count"])
                or not pod_scope
                or len(set(pod_scope)) != len(pod_scope)
                or set(pod_scope) - {"title", "four_grid"}
            ):
                raise HTTPException(status_code=409, detail="invalid POD billing snapshot")
        if not isinstance(item_results, list):
            raise HTTPException(status_code=400, detail="item_results must be a list")
        frozen_link_count = int(freeze["link_count"] or 1)
        if len(item_results) != frozen_link_count:
            # 风险#20 对策：结算明细条数必须与冻结批次 link_count 一致，
            # 防止客户端多报/漏报子项逃费（漏报部分随锁释放隐式退还）。
            raise HTTPException(
                status_code=400,
                detail=(
                    f"item_results count {len(item_results)} does not match "
                    f"frozen link_count {frozen_link_count}"
                ),
            )
        total_charge_units = 0
        total_refund_units = 0
        total_premium_units = 0
        stored_items: list[dict[str, Any]] = []
        for index, entry in enumerate(item_results, start=1):
            if not isinstance(entry, dict):
                raise HTTPException(status_code=400, detail="item_results entry must be an object")
            if profile == BATCH_BILLING_PROFILE_POD:
                try:
                    supplied_link_index = int(entry.get("link_idx"))
                except (TypeError, ValueError) as exc:
                    raise HTTPException(status_code=400, detail="POD link_idx is required") from exc
                if supplied_link_index != index:
                    raise HTTPException(status_code=400, detail="POD link_idx order mismatch")
            link_results = entry.get("subitems")
            if not isinstance(link_results, list):
                raise HTTPException(status_code=400, detail="subitems must be a list")
            if profile == BATCH_BILLING_PROFILE_POD:
                statuses: dict[str, str] = {}
                for result in link_results:
                    if not isinstance(result, dict):
                        raise HTTPException(status_code=400, detail="invalid POD subitem result")
                    feature = str(result.get("feature") or "").strip()
                    status = str(result.get("status") or "").strip()
                    if feature not in pod_scope or feature in statuses or status not in {"success", "no_return"}:
                        raise HTTPException(status_code=400, detail="invalid POD subitem result")
                    statuses[feature] = status
                if set(statuses) != set(pod_scope):
                    raise HTTPException(status_code=400, detail="POD subitems must match frozen scope")
                link_units = pod_link_price_units[index - 1]
                # 付费重试（超过免费额度后用户确认）：该链接无论成功与否都按款式价
                # 扣费，不再按成败退款；只有未确认的普通重试才按成功/失败结算。
                paid_retry = bool(entry.get("paid_retry") or False)
                if paid_retry or all(statuses[feature] == "success" for feature in pod_scope):
                    total_charge_units += link_units
                else:
                    total_refund_units += link_units
                for feature in pod_scope:
                    stored_items.append((freeze_id, index, feature, statuses[feature]))
            else:
                computed = compute_batch_charge(
                    database_path,
                    rule_version=freeze_rule_version,
                    item_results=[dict(result) for result in link_results if isinstance(result, dict)],
                )
                # 手动付费重试（paid_retry=true）：该链接无论子项成败都按整条链接
                # 全价计费（35-45 积分区间），不退任何子项；审计明细仍保留实际状态。
                if bool(entry.get("paid_retry") or False):
                    full_units = sum(
                        int(detail["charge_units"]) for detail in computed["details"]
                    )
                    computed = {
                        **computed,
                        "charge_units": full_units,
                        "refund_units": 0,
                        "charge_points": _display_points(full_units),
                        "refund_points": 0,
                    }
                total_charge_units += int(computed["charge_units"])
                total_refund_units += int(computed["refund_units"])
                total_premium_units += int(computed.get("premium_units") or 0)
                for detail in computed["details"]:
                    stored_items.append((freeze_id, index, detail["feature"], detail["status"]))
        frozen_units = int(freeze["frozen_points"])
        # 冻结按正常链接封顶（base charge + refund <= frozen）；重试溢价不在冻结范围，
        # 属于额外扣费，直接从余额扣除，避免 500 单位超出 400 单位冻结上限被误拦截。
        if total_charge_units + total_refund_units > frozen_units:
            raise HTTPException(
                status_code=400,
                detail="settle totals exceed the frozen points",
            )
        total_charged_units = total_charge_units + total_premium_units
        # release the unused lock (refund) and debit the charge; wallet stores units.
        conn.execute(
            """
            UPDATE billing_wallets
            SET points_balance = points_balance - ?,
                locked_points = locked_points - ?,
                version = version + 1,
                updated_at = ?
            WHERE account_id = ?
            """,
            (total_charged_units, frozen_units, _utc_now(), expected_account_id),
        )
        now = _utc_now()
        conn.execute(
            """
            UPDATE billing_batch_freezes
            SET charged_points = ?, refunded_points = ?, status = 'settled', settled_at = ?
            WHERE freeze_id = ?
            """,
            (total_charged_units, total_refund_units, now, freeze_id),
        )
        for freeze_key, link_idx, feature_key, status_value in stored_items:
            conn.execute(
                """
                INSERT OR REPLACE INTO billing_batch_items (
                    freeze_id, link_idx, feature_key, status, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (freeze_key, link_idx, feature_key, status_value, now),
            )
        _append_ledger(
            conn,
            account_id=expected_account_id,
            workspace_id=str(freeze["workspace_id"] or "default"),
            direction="debit",
            points_delta=total_charged_units,
            source_type="batch_settle",
            source_id=freeze_id,
            idempotency_key=f"batch_settle:{freeze_id}:debit",
            metadata={
                "rule_version": freeze_rule_version,
                "link_count": int(freeze["link_count"]),
                "billing_profile": profile,
                "retry_premium_units": total_premium_units,
            },
        )
        if total_refund_units:
            _append_ledger(
                conn,
                account_id=expected_account_id,
                workspace_id=str(freeze["workspace_id"] or "default"),
                direction="unlock",
                points_delta=total_refund_units,
                source_type="batch_settle",
                source_id=freeze_id,
                idempotency_key=f"batch_settle:{freeze_id}:unlock",
                metadata={"rule_version": freeze_rule_version, "billing_profile": profile},
            )
    return {
        "freeze_id": freeze_id,
        "status": "settled",
        "charged_points": _display_points(total_charged_units),
        "refunded_points": _display_points(total_refund_units),
        "retry_premium_points": _display_points(total_premium_units),
        "already_settled": False,
    }


def batch_freeze_status(database_path: Path, freeze_id: str, *, expected_account_id: str) -> dict[str, Any]:
    """Return the freeze + settled detail for client startup reconciliation."""
    with transaction(database_path) as conn:
        freeze = conn.execute(
            "SELECT * FROM billing_batch_freezes WHERE freeze_id = ?",
            (freeze_id,),
        ).fetchone()
        if freeze is None or str(freeze["account_id"]) != str(expected_account_id):
            raise HTTPException(status_code=404, detail="batch freeze not found")
        rows = conn.execute(
            """
            SELECT link_idx, feature_key, status FROM billing_batch_items
            WHERE freeze_id = ? ORDER BY link_idx, id
            """,
            (freeze_id,),
        ).fetchall()
        return {
            "freeze_id": freeze_id,
            "status": str(freeze["status"]),
            "link_count": int(freeze["link_count"]),
            "frozen_points": _display_points(int(freeze["frozen_points"])),
            "charged_points": _display_points(int(freeze["charged_points"])),
            "refunded_points": _display_points(int(freeze["refunded_points"])),
            "settled_at": str(freeze["settled_at"] or ""),
            "expires_at": str(freeze["expires_at"] or ""),
            "billing_profile": str(
                freeze["billing_profile"] or BATCH_BILLING_PROFILE_PRODUCT
            ),
            "rule_version": int(freeze["rule_version"] or 0),
            "scope": [
                str(value)
                for value in json.loads(str(freeze["scope_json"] or "[]"))
            ],
            "link_prices": [
                _display_points(int(value))
                for value in json.loads(str(freeze["link_prices_json"] or "[]"))
            ],
            "items": [
                {
                    "link_idx": int(row["link_idx"]),
                    "feature": str(row["feature_key"]),
                    "status": str(row["status"]),
                }
                for row in rows
            ],
        }


def release_expired_batch_freezes(database_path: Path, *, now_iso: str = "") -> int:
    """TTL sweep: release frozen points for batches past their expiry.

    Called periodically by the auth server; returns the number of releases.
    """
    now_iso = now_iso or _utc_now()
    released = 0
    with transaction(database_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM billing_batch_freezes
            WHERE status = 'frozen' AND expires_at <= ?
            """,
            (now_iso,),
        ).fetchall()
        for freeze in rows:
            conn.execute(
                """
                UPDATE billing_batch_freezes
                SET status = 'released', settled_at = ?
                WHERE freeze_id = ?
                """,
                (_utc_now(), freeze["freeze_id"]),
            )
            _append_ledger(
                conn,
                account_id=str(freeze["account_id"]),
                workspace_id=str(freeze["workspace_id"] or "default"),
                direction="unlock",
                points_delta=int(freeze["frozen_points"]),
                source_type="batch_expiry_release",
                source_id=str(freeze["freeze_id"]),
                idempotency_key=f"batch_expiry:{freeze['freeze_id']}:unlock",
                metadata={"link_count": int(freeze["link_count"])},
            )
            conn.execute(
                """
                UPDATE billing_wallets
                SET locked_points = locked_points - ?,
                    version = version + 1,
                    updated_at = ?
                WHERE account_id = ?
                """,
                (int(freeze["frozen_points"]), _utc_now(), str(freeze["account_id"])),
            )
            released += 1
    return released
