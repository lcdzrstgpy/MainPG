"""POD call-plan bridge over the shared point wallet and batch freeze ledger.

The bridge stores only identifiers, pricing snapshots and call outcomes. Provider
credentials and remote sessions are deliberately outside this module and must
never be persisted with a POD freeze.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .billing import PIC_UNIT_SCALE, freeze_planned_points, settle_planned_points
from .db import connect, transaction
from .session import Actor


POD_FEATURE_KEYS = ("pod.title", "pod.image")
POD_MAX_CALLS = 200


POD_BILLING_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS billing_pod_freezes (
    freeze_id TEXT PRIMARY KEY,
    rule_version INTEGER NOT NULL,
    plan_hash TEXT NOT NULL,
    title_call_count INTEGER NOT NULL,
    image_call_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (freeze_id) REFERENCES billing_batch_freezes (freeze_id) ON DELETE CASCADE,
    CHECK (title_call_count >= 0),
    CHECK (image_call_count >= 0)
);

CREATE TABLE IF NOT EXISTS billing_pod_calls (
    freeze_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    feature_key TEXT NOT NULL CHECK (feature_key IN ('pod.title', 'pod.image')),
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'success', 'no_return')),
    charge_units INTEGER NOT NULL,
    refund_units INTEGER NOT NULL DEFAULT 0,
    settled_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (freeze_id, call_id),
    UNIQUE (freeze_id, ordinal),
    FOREIGN KEY (freeze_id) REFERENCES billing_pod_freezes (freeze_id) ON DELETE CASCADE,
    CHECK (charge_units >= 0),
    CHECK (refund_units >= 0)
);

CREATE INDEX IF NOT EXISTS idx_billing_pod_calls_freeze_ordinal
    ON billing_pod_calls (freeze_id, ordinal);
"""


def init_pod_billing_schema(database_path: Path) -> None:
    """Forward-only, repeatable schema helper intentionally kept out of db.py."""
    with transaction(database_path) as conn:
        conn.executescript(POD_BILLING_SCHEMA_SQL)


def pod_pricing_items(
    database_path: Path,
    *,
    rule_version: int | None = None,
    require_configured: bool = True,
) -> dict[str, Any]:
    conn = connect(database_path)
    try:
        rule = conn.execute(
            "SELECT rule_version, point_unit_scale, effective_at FROM billing_pricing_rules WHERE rule_id = 1"
        ).fetchone()
        if rule is None:
            raise HTTPException(status_code=503, detail="server billing pricing is unavailable")
        version = int(rule["rule_version"]) if rule_version is None else int(rule_version)
        rows = conn.execute(
            """
            SELECT feature_key, charge_points
            FROM billing_pricing_items
            WHERE rule_version = ? AND feature_key IN ('pod.title', 'pod.image')
            """,
            (version,),
        ).fetchall()
    finally:
        conn.close()
    by_key = {str(row["feature_key"]): int(row["charge_points"]) for row in rows}
    if require_configured and any(key not in by_key for key in POD_FEATURE_KEYS):
        raise HTTPException(status_code=503, detail="POD pricing is not configured")
    return {
        "rule_version": version,
        "point_unit_scale": PIC_UNIT_SCALE,
        "items": {
            key: {
                "charge_units": units,
                "charge_points": _display_points(units),
            }
            for key, units in by_key.items()
            if key in POD_FEATURE_KEYS
        },
        "effective_at": str(rule["effective_at"] or "") if rule_version is None else "",
    }


def update_pod_pricing_items(
    database_path: Path,
    *,
    items: dict[str, Any],
    updated_by: str,
    change_reason: str,
) -> dict[str, Any]:
    normalized: dict[str, int] = {}
    for feature_key in POD_FEATURE_KEYS:
        value = items.get(feature_key)
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail=f"missing pricing for feature {feature_key}")
        try:
            points = float(value.get("charge_points"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"charge_points for {feature_key} must be numeric",
            ) from exc
        units = int(round(points * PIC_UNIT_SCALE))
        if units < 0 or units > 100_000:
            raise HTTPException(status_code=400, detail=f"invalid pricing for feature {feature_key}")
        normalized[feature_key] = units

    reason = str(change_reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="change_reason is required")
    before = pod_pricing_items(database_path, require_configured=False)
    with transaction(database_path) as conn:
        rule = conn.execute(
            "SELECT rule_version FROM billing_pricing_rules WHERE rule_id = 1"
        ).fetchone()
        if rule is None:
            raise HTTPException(status_code=503, detail="server billing pricing is unavailable")
        current_version = int(rule["rule_version"])
        next_version = current_version + 1
        current_rows = conn.execute(
            """
            SELECT feature_key, charge_points, intercept_refund_ratio, no_return_refund_ratio
            FROM billing_pricing_items WHERE rule_version = ?
            """,
            (current_version,),
        ).fetchall()
        carried = {
            str(row["feature_key"]): (
                int(row["charge_points"]),
                float(row["intercept_refund_ratio"]),
                float(row["no_return_refund_ratio"]),
            )
            for row in current_rows
        }
        for key, units in normalized.items():
            carried[key] = (units, 0.0, 1.0)
        now = _utc_now()
        conn.execute(
            """
            UPDATE billing_pricing_rules
            SET rule_version = ?, effective_at = ?, updated_at = ?, updated_by = ?
            WHERE rule_id = 1
            """,
            (next_version, now, now, str(updated_by or "system")[:160]),
        )
        for feature_key, (units, intercept, no_return) in carried.items():
            conn.execute(
                """
                INSERT INTO billing_pricing_items (
                    rule_version, feature_key, charge_points,
                    intercept_refund_ratio, no_return_refund_ratio, effective_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (next_version, feature_key, units, intercept, no_return, now),
            )
        after = {
            "rule_version": next_version,
            "point_unit_scale": PIC_UNIT_SCALE,
            "items": {
                key: {
                    "charge_units": units,
                    "charge_points": _display_points(units),
                }
                for key, units in normalized.items()
            },
            "effective_at": now,
        }
        conn.execute(
            """
            INSERT INTO billing_pricing_changelog (
                rule_version, changed_by, change_reason, before_json, after_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                next_version,
                str(updated_by or "system")[:160],
                reason[:500],
                json.dumps(before, ensure_ascii=False, sort_keys=True),
                json.dumps(after, ensure_ascii=False, sort_keys=True),
            ),
        )
    return after


def freeze_pod_points(
    database_path: Path,
    actor: Actor,
    *,
    calls: Any,
    title_call_count: Any,
    image_call_count: Any,
    idempotency_key: str,
) -> dict[str, Any]:
    normalized_calls, title_count, image_count = _normalize_plan(
        calls,
        title_call_count=title_call_count,
        image_call_count=image_call_count,
    )
    idem = str(idempotency_key or "").strip()
    if not 16 <= len(idem) <= 200:
        raise HTTPException(status_code=400, detail="idempotency_key length must be 16..200")
    freeze_id = _pod_freeze_id(actor.id, idem)
    plan_hash = _plan_hash(normalized_calls, title_count, image_count)
    pricing = pod_pricing_items(database_path)
    price_by_feature = {
        key: int(value["charge_units"])
        for key, value in pricing["items"].items()
    }
    frozen_units = sum(price_by_feature[item["feature"]] for item in normalized_calls)
    freeze = freeze_planned_points(
        database_path,
        actor,
        frozen_units=frozen_units,
        item_count=len(normalized_calls),
        scope=list(POD_FEATURE_KEYS),
        idempotency_key=freeze_id,
        source_type="pod_freeze",
        persist_plan=lambda conn, persisted_freeze_id: _persist_pod_plan(
            conn,
            persisted_freeze_id,
            rule_version=int(pricing["rule_version"]),
            plan_hash=plan_hash,
            title_count=title_count,
            image_count=image_count,
            calls=normalized_calls,
            price_by_feature=price_by_feature,
        ),
        validate_existing=lambda conn, existing: _validate_existing_pod_plan(
            conn,
            existing,
            plan_hash=plan_hash,
        ),
    )
    if freeze["already_frozen"]:
        result = pod_freeze_status(
            database_path,
            freeze_id,
            expected_account_id=actor.id,
        )
        result["already_frozen"] = True
        return result
    return {
        **freeze,
        "rule_version": int(pricing["rule_version"]),
        "title_call_count": title_count,
        "image_call_count": image_count,
        "calls": normalized_calls,
    }


def _validate_existing_pod_plan(
    conn: Any,
    existing: Any,
    *,
    plan_hash: str,
) -> None:
    stored = conn.execute(
        "SELECT plan_hash FROM billing_pod_freezes WHERE freeze_id = ?",
        (str(existing["freeze_id"]),),
    ).fetchone()
    if stored is None or str(stored["plan_hash"]) != plan_hash:
        raise HTTPException(
            status_code=409,
            detail="idempotency key was already used for another POD plan",
        )
    if (
        str(existing["status"]) != "frozen"
        or str(existing["expires_at"] or "") <= _utc_now()
    ):
        raise HTTPException(status_code=409, detail="POD freeze is no longer active")


def _persist_pod_plan(
    conn: Any,
    freeze_id: str,
    *,
    rule_version: int,
    plan_hash: str,
    title_count: int,
    image_count: int,
    calls: list[dict[str, str]],
    price_by_feature: dict[str, int],
) -> None:
    """Persist the POD plan inside the wallet lock transaction."""
    conn.execute(
        """
        INSERT INTO billing_pod_freezes (
            freeze_id, rule_version, plan_hash, title_call_count, image_call_count
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (freeze_id, rule_version, plan_hash, title_count, image_count),
    )
    for ordinal, call in enumerate(calls):
        conn.execute(
            """
            INSERT INTO billing_pod_calls (
                freeze_id, call_id, feature_key, ordinal, charge_units
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                freeze_id,
                call["call_id"],
                call["feature"],
                ordinal,
                price_by_feature[call["feature"]],
            ),
        )


def settle_pod_points(
    database_path: Path,
    freeze_id: str,
    *,
    item_results: Any,
    expected_account_id: str,
) -> dict[str, Any]:
    status = pod_freeze_status(database_path, freeze_id, expected_account_id=expected_account_id)
    if status["status"] == "settled":
        return settle_planned_points(
            database_path,
            freeze_id,
            expected_account_id=expected_account_id,
            charge_units=0,
            refund_units=0,
            source_type="pod_settle",
        )
    if not isinstance(item_results, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    expected = {
        (str(item["call_id"]), str(item["feature"])): item
        for item in status["calls"]
    }
    supplied: dict[tuple[str, str], str] = {}
    for value in item_results:
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail="settlement items must be objects")
        call_id = str(value.get("call_id") or "").strip()
        feature = str(value.get("feature") or "").strip()
        outcome = str(value.get("status") or "").strip()
        identity = (call_id, feature)
        if identity in supplied or outcome not in {"success", "no_return"}:
            raise HTTPException(status_code=400, detail="invalid POD settlement item")
        supplied[identity] = outcome
    if set(supplied) != set(expected):
        raise HTTPException(status_code=400, detail="settlement calls must exactly match the frozen plan")

    charge_units = 0
    refund_units = 0
    for identity, plan_item in expected.items():
        units = int(plan_item["charge_units"])
        if supplied[identity] == "success":
            charge_units += units
        else:
            refund_units += units
    result = settle_planned_points(
        database_path,
        freeze_id,
        expected_account_id=expected_account_id,
        charge_units=charge_units,
        refund_units=refund_units,
        source_type="pod_settle",
        metadata={"rule_version": int(status["rule_version"]), "call_count": len(expected)},
        persist_settlement=lambda conn, persisted_freeze_id: _persist_pod_settlement(
            conn,
            persisted_freeze_id,
            supplied=supplied,
            expected=expected,
        ),
    )
    return result


def _persist_pod_settlement(
    conn: Any,
    freeze_id: str,
    *,
    supplied: dict[tuple[str, str], str],
    expected: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Persist call outcomes inside the wallet settlement transaction."""
    for (call_id, feature), outcome in supplied.items():
        units = int(expected[(call_id, feature)]["charge_units"])
        conn.execute(
            """
            UPDATE billing_pod_calls
            SET status = ?, refund_units = ?, settled_at = datetime('now')
            WHERE freeze_id = ? AND call_id = ? AND feature_key = ?
            """,
            (outcome, units if outcome == "no_return" else 0, freeze_id, call_id, feature),
        )


def pod_freeze_status(
    database_path: Path,
    freeze_id: str,
    *,
    expected_account_id: str,
) -> dict[str, Any]:
    with transaction(database_path) as conn:
        row = conn.execute(
            """
            SELECT b.*, p.rule_version, p.title_call_count, p.image_call_count, p.plan_hash
            FROM billing_batch_freezes b
            JOIN billing_pod_freezes p ON p.freeze_id = b.freeze_id
            WHERE b.freeze_id = ? AND b.account_id = ?
            """,
            (str(freeze_id), str(expected_account_id)),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="POD freeze not found")
        calls = conn.execute(
            """
            SELECT call_id, feature_key, status, charge_units, refund_units
            FROM billing_pod_calls WHERE freeze_id = ? ORDER BY ordinal
            """,
            (str(freeze_id),),
        ).fetchall()
    return {
        "freeze_id": str(row["freeze_id"]),
        "status": str(row["status"]),
        "rule_version": int(row["rule_version"]),
        "frozen_points": _display_points(int(row["frozen_points"])),
        "charged_points": _display_points(int(row["charged_points"])),
        "refunded_points": _display_points(int(row["refunded_points"])),
        "title_call_count": int(row["title_call_count"]),
        "image_call_count": int(row["image_call_count"]),
        "expires_at": str(row["expires_at"] or ""),
        "settled_at": str(row["settled_at"] or ""),
        "calls": [
            {
                "call_id": str(call["call_id"]),
                "feature": str(call["feature_key"]),
                "status": str(call["status"]),
                "charge_units": int(call["charge_units"]),
                "charge_points": _display_points(int(call["charge_units"])),
                "refund_units": int(call["refund_units"]),
                "refund_points": _display_points(int(call["refund_units"])),
            }
            for call in calls
        ],
    }


def pod_grantable_freeze_status(
    database_path: Path,
    freeze_id: str,
    *,
    expected_account_id: str,
) -> dict[str, Any]:
    """Check ownership, state and expiry under the same write lock."""
    with transaction(database_path) as conn:
        row = conn.execute(
            """
            SELECT status, expires_at FROM billing_batch_freezes
            WHERE freeze_id = ? AND account_id = ?
            """,
            (str(freeze_id), str(expected_account_id)),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="POD freeze not found")
        if (
            str(row["status"]) != "frozen"
            or str(row["expires_at"] or "") <= _utc_now()
        ):
            raise HTTPException(status_code=409, detail="POD freeze is no longer active")
    return pod_freeze_status(
        database_path,
        freeze_id,
        expected_account_id=expected_account_id,
    )


def _normalize_plan(
    calls: Any,
    *,
    title_call_count: Any,
    image_call_count: Any,
) -> tuple[list[dict[str, str]], int, int]:
    try:
        title_count = int(title_call_count)
        image_count = int(image_call_count)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="POD call counts are required") from exc
    if title_count < 0 or image_count < 0 or not 1 <= title_count + image_count <= POD_MAX_CALLS:
        raise HTTPException(status_code=400, detail="POD total call count must be 1..200")
    if not isinstance(calls, list) or len(calls) != title_count + image_count:
        raise HTTPException(status_code=400, detail="POD calls do not match declared counts")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    counts = {key: 0 for key in POD_FEATURE_KEYS}
    for value in calls:
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail="POD calls must be objects")
        call_id = str(value.get("call_id") or "").strip()
        feature = str(value.get("feature") or "").strip()
        if not 8 <= len(call_id) <= 200 or call_id in seen:
            raise HTTPException(status_code=400, detail="POD call_id must be unique and 8..200 characters")
        if feature not in POD_FEATURE_KEYS:
            raise HTTPException(status_code=400, detail="unsupported POD feature")
        seen.add(call_id)
        counts[feature] += 1
        normalized.append({"call_id": call_id, "feature": feature})
    if counts["pod.title"] != title_count or counts["pod.image"] != image_count:
        raise HTTPException(status_code=400, detail="POD calls do not match declared counts")
    return normalized, title_count, image_count


def _plan_hash(calls: list[dict[str, str]], title_count: int, image_count: int) -> str:
    canonical = json.dumps(
        {
            "title_call_count": title_count,
            "image_call_count": image_count,
            "calls": calls,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _pod_freeze_id(account_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(
        f"pod\x00{account_id}\x00{idempotency_key}".encode("utf-8")
    ).hexdigest()
    return f"pod_frz_{digest[:40]}"


def _display_points(units: int) -> int | float:
    value = int(units) / PIC_UNIT_SCALE
    return int(value) if value.is_integer() else value


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
