"""SQLite ownership boundary for persistent whole-shop batches."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..db import connect
from .shop_contracts import ShopBatch, ShopBatchItem


class ShopBatchNotFound(LookupError):
    pass


class ActiveShopBatchExists(ValueError):
    pass


class InvalidShopBatchTransition(ValueError):
    pass


class ShopLeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class ShopBatchLease:
    batch: ShopBatch
    lease_owner: str
    lease_token: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self.batch, name)


@dataclass(frozen=True)
class ShopItemLease:
    item: ShopBatchItem
    lease_owner: str
    lease_token: str

    def __getattr__(self, name: str) -> Any:
        return getattr(self.item, name)


_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"resolving", "pausing", "cancelling", "failed"}),
    "resolving": frozenset({"listing", "pausing", "cancelling", "failed"}),
    "listing": frozenset({"enriching", "pausing", "cancelling", "failed"}),
    "enriching": frozenset({"pausing", "cancelling", "completed", "partial", "failed"}),
    "pausing": frozenset({"paused", "cancelling", "failed"}),
    "paused": frozenset({"queued", "cancelling"}),
    "cancelling": frozenset({"cancelled"}),
    "cancelled": frozenset(),
    "completed": frozenset(),
    "partial": frozenset({"queued"}),
    "failed": frozenset({"queued"}),
}


class ShopCollectionRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._initialize()

    def _initialize(self) -> None:
        """Keep the repository usable before the host migration is wired.

        The host still records the forward-only migration markers.  This local
        guard mirrors the existing plugin queue boundary and makes construction
        safe in focused tests and injected hosts.
        """
        migrations = Path(__file__).with_name("migrations")
        with connect(self.database_path) as conn:
            conn.executescript(
                (migrations / "005_shop_collection.sql").read_text(encoding="utf-8")
            )
            for table in ("shop_collection_batches", "shop_collection_items"):
                columns = {
                    str(row["name"])
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if "lease_token" not in columns:
                    conn.execute(
                        f"ALTER TABLE {table} "
                        "ADD COLUMN lease_token TEXT NOT NULL DEFAULT ''"
                    )

    def record_api_call_reservation(
        self,
        *,
        batch_id: str,
        workspace_id: str,
        operation: str,
        reservation_granted: bool,
    ) -> None:
        if operation not in {"item_search_shop", "item_get"}:
            raise ValueError("unsupported shop API operation")
        with connect(self.database_path) as conn:
            conn.execute(
                """INSERT INTO shop_collection_api_calls
                (batch_id, workspace_id, operation, reservation_granted)
                VALUES (?, ?, ?, ?)""",
                (batch_id, workspace_id, operation, int(reservation_granted)),
            )

    def create_batch(
        self, *, batch_id: str, workspace_id: str, actor_id: str, shop_sid: str,
        shop_url: str = "", shop_name: str = "", seed_offer_id: str = "", max_pages: int = 100,
    ) -> ShopBatch:
        try:
            with connect(self.database_path) as conn:
                conn.execute(
                    """INSERT INTO shop_collection_batches
                    (batch_id, workspace_id, actor_id, shop_sid, shop_url, shop_name, seed_offer_id, max_pages)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (batch_id, workspace_id, actor_id, shop_sid, shop_url, shop_name, seed_offer_id, max_pages),
                )
        except sqlite3.IntegrityError as error:
            if "idx_shop_collection_active_shop" in str(error) or "shop_collection_batches.workspace_id, shop_collection_batches.shop_sid" in str(error):
                raise ActiveShopBatchExists("an active batch already exists for this shop") from error
            raise
        return self.get_batch(workspace_id=workspace_id, batch_id=batch_id)

    def resolve_shop_identity(self, *, batch_id: str, shop_sid: str, shop_name: str = "") -> ShopBatch:
        try:
            with connect(self.database_path) as conn:
                cursor = conn.execute(
                    """UPDATE shop_collection_batches SET shop_sid = ?, shop_name = ?, updated_at = datetime('now')
                    WHERE batch_id = ?""",
                    (shop_sid, shop_name, batch_id),
                )
                if not cursor.rowcount:
                    raise ShopBatchNotFound("shop collection batch not found")
        except sqlite3.IntegrityError as error:
            raise ActiveShopBatchExists("an active batch already exists for this shop") from error
        return self.get_batch_internal(batch_id)

    def get_batch(self, *, workspace_id: str, batch_id: str) -> ShopBatch:
        with connect(self.database_path) as conn:
            row = conn.execute(
                "SELECT * FROM shop_collection_batches WHERE workspace_id = ? AND batch_id = ?",
                (workspace_id, batch_id),
            ).fetchone()
        if row is None:
            raise ShopBatchNotFound("shop collection batch not found")
        return _batch(row)

    def get_batch_internal(self, batch_id: str) -> ShopBatch:
        with connect(self.database_path) as conn:
            row = conn.execute("SELECT * FROM shop_collection_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if row is None:
            raise ShopBatchNotFound("shop collection batch not found")
        return _batch(row)

    def list_batches(self, *, workspace_id: str, limit: int, offset: int) -> tuple[ShopBatch, ...]:
        with connect(self.database_path) as conn:
            rows = conn.execute(
                "SELECT * FROM shop_collection_batches WHERE workspace_id = ? ORDER BY created_at DESC, batch_id DESC LIMIT ? OFFSET ?",
                (workspace_id, limit, offset),
            ).fetchall()
        return tuple(_batch(row) for row in rows)

    def count_batches(self, *, workspace_id: str) -> int:
        with connect(self.database_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM shop_collection_batches WHERE workspace_id = ?", (workspace_id,)).fetchone()[0])

    def list_items(self, *, workspace_id: str, batch_id: str, limit: int, offset: int) -> tuple[ShopBatchItem, ...]:
        self.get_batch(workspace_id=workspace_id, batch_id=batch_id)
        with connect(self.database_path) as conn:
            rows = conn.execute(
                "SELECT * FROM shop_collection_items WHERE workspace_id = ? AND batch_id = ? ORDER BY created_at, item_id LIMIT ? OFFSET ?",
                (workspace_id, batch_id, limit, offset),
            ).fetchall()
        return tuple(_item(row) for row in rows)

    def count_items(self, *, workspace_id: str, batch_id: str) -> int:
        self.get_batch(workspace_id=workspace_id, batch_id=batch_id)
        with connect(self.database_path) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM shop_collection_items WHERE workspace_id = ? AND batch_id = ?", (workspace_id, batch_id)).fetchone()[0])

    def transition_batch(
        self,
        batch_id: str,
        status: str,
        *,
        error_code: str = "",
        error_message: str = "",
        expected_statuses: set[str] | frozenset[str] | None = None,
        owner: str | None = None,
        lease_token: str | None = None,
    ) -> ShopBatch:
        with connect(self.database_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM shop_collection_batches WHERE batch_id = ?", (batch_id,)).fetchone()
            if row is None:
                raise ShopBatchNotFound("shop collection batch not found")
            current = str(row["status"])
            if expected_statuses is not None and current not in expected_statuses:
                raise InvalidShopBatchTransition(f"expected batch status {sorted(expected_statuses)}, found {current}")
            if status != current and status not in _TRANSITIONS.get(current, frozenset()):
                raise InvalidShopBatchTransition(f"cannot transition batch from {current} to {status}")
            terminal = status in {"cancelled", "completed", "partial", "failed"}
            fencing = ""
            parameters: list[Any] = [status, error_code, _safe_error(error_message), status, terminal, terminal, terminal, terminal, batch_id, current]
            if owner is not None or lease_token is not None:
                if not owner or not lease_token:
                    raise ValueError("owner and lease_token are required together")
                fencing = " AND lease_owner = ? AND lease_token = ? AND lease_expires_at > ?"
                parameters.extend((owner, lease_token, _now()))
            cursor = conn.execute(
                """UPDATE shop_collection_batches SET status = ?, error_code = ?, error_message = ?,
                started_at = CASE WHEN started_at IS NULL AND ? IN ('resolving','listing','enriching') THEN datetime('now') ELSE started_at END,
                completed_at = CASE WHEN ? THEN datetime('now') ELSE NULL END,
                lease_owner = CASE WHEN ? THEN '' ELSE lease_owner END,
                lease_token = CASE WHEN ? THEN '' ELSE lease_token END,
                lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END,
                updated_at = datetime('now') WHERE batch_id = ? AND status = ?""" + fencing,
                parameters,
            )
            if not cursor.rowcount:
                if fencing:
                    raise ShopLeaseLost("batch lease is no longer owned by this worker")
                raise InvalidShopBatchTransition("batch state changed concurrently")
        return self.get_batch_internal(batch_id)

    def record_shop_page(
        self, *, batch_id: str, page: int, items: Sequence[Mapping[str, Any]],
        has_next: bool, missing_id_count: int = 0,
    ) -> Mapping[str, int]:
        created = duplicates = 0
        missing = max(0, int(missing_id_count))
        seen: set[str] = set()
        with connect(self.database_path) as conn:
            batch = conn.execute("SELECT workspace_id, pages_fetched FROM shop_collection_batches WHERE batch_id = ?", (batch_id,)).fetchone()
            if batch is None:
                raise ShopBatchNotFound("shop collection batch not found")
            for value in items:
                offer_id = str(value.get("offer_id") or "").strip()
                if not offer_id:
                    missing += 1
                    continue
                if offer_id in seen:
                    duplicates += 1
                    continue
                seen.add(offer_id)
                item_id = f"{batch_id}:{offer_id}"
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO shop_collection_items
                    (item_id, batch_id, workspace_id, offer_id, source_url, source_title)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (item_id, batch_id, batch["workspace_id"], offer_id, str(value.get("source_url") or ""), str(value.get("title") or value.get("source_title") or "")),
                )
                if cursor.rowcount:
                    created += 1
                else:
                    duplicates += 1
            conn.execute(
                """UPDATE shop_collection_batches SET
                next_page = MAX(next_page, ?), pages_fetched = MAX(pages_fetched, ?),
                listing_complete = CASE WHEN ? THEN 0 ELSE 1 END,
                discovered_count = discovered_count + ?, duplicate_count = duplicate_count + ?,
                missing_id_count = missing_id_count + ?, updated_at = datetime('now')
                WHERE batch_id = ?""",
                (page + 1, page, int(has_next), created, duplicates, missing, batch_id),
            )
        return {"created": created, "duplicates": duplicates, "missing_ids": missing}

    def claim_pending_items(
        self, *, batch_id: str, owner: str, limit: int, lease_seconds: int
    ) -> tuple[ShopItemLease, ...]:
        now = _now()
        expires = _lease_deadline(lease_seconds)
        with connect(self.database_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """SELECT item_id FROM shop_collection_items WHERE batch_id = ? AND (
                detail_status = 'pending' OR
                (detail_status = 'running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?))
                ) ORDER BY created_at, item_id LIMIT ?""",
                (batch_id, now, limit),
            ).fetchall()
            claims: list[sqlite3.Row] = []
            for row in rows:
                item_id = str(row["item_id"])
                token = uuid.uuid4().hex
                cursor = conn.execute(
                    """UPDATE shop_collection_items SET detail_status = 'running', attempts = attempts + 1,
                    lease_owner = ?, lease_token = ?, lease_expires_at = ?, completed_at = NULL,
                    updated_at = datetime('now') WHERE item_id = ? AND (
                    detail_status = 'pending' OR
                    (detail_status = 'running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?))
                    )""",
                    (owner, token, expires, item_id, now),
                )
                if cursor.rowcount:
                    claimed = conn.execute(
                        "SELECT * FROM shop_collection_items WHERE item_id = ?", (item_id,)
                    ).fetchone()
                    if claimed is not None:
                        claims.append(claimed)
            conn.commit()
        return tuple(_item_lease(row) for row in claims)

    def complete_item(
        self, *, batch_id: str, item_id: str, owner: str, lease_token: str,
        intake_action: str, candidate: Mapping[str, Any],
    ) -> None:
        if intake_action not in {"created", "refreshed", "skipped"}:
            raise ValueError("invalid intake action")
        with connect(self.database_path) as conn:
            cursor = conn.execute(
                """UPDATE shop_collection_items SET detail_status = 'succeeded', intake_action = ?, candidate_json = ?,
                error_code = '', error_message = '', lease_owner = '', lease_token = '', lease_expires_at = NULL,
                completed_at = datetime('now'), updated_at = datetime('now')
                WHERE batch_id = ? AND item_id = ? AND detail_status = 'running'
                AND lease_owner = ? AND lease_token = ? AND lease_expires_at > ?""",
                (intake_action, json.dumps(candidate, ensure_ascii=False, default=str), batch_id, item_id,
                 owner, lease_token, _now()),
            )
            if not cursor.rowcount:
                raise ShopLeaseLost("item lease is no longer owned by this worker")
            conn.execute(
                f"UPDATE shop_collection_batches SET succeeded_count = succeeded_count + 1, {intake_action}_count = {intake_action}_count + 1, updated_at = datetime('now') WHERE batch_id = ?",
                (batch_id,),
            )

    def fail_item(
        self, *, batch_id: str, item_id: str, owner: str, lease_token: str,
        error_code: str, error_message: str,
    ) -> None:
        with connect(self.database_path) as conn:
            cursor = conn.execute(
                """UPDATE shop_collection_items SET detail_status = 'failed', error_code = ?, error_message = ?,
                lease_owner = '', lease_token = '', lease_expires_at = NULL, completed_at = datetime('now'), updated_at = datetime('now')
                WHERE batch_id = ? AND item_id = ? AND detail_status = 'running'
                AND lease_owner = ? AND lease_token = ? AND lease_expires_at > ?""",
                (error_code, _safe_error(error_message), batch_id, item_id, owner, lease_token, _now()),
            )
            if not cursor.rowcount:
                raise ShopLeaseLost("item lease is no longer owned by this worker")
            conn.execute("UPDATE shop_collection_batches SET failed_count = failed_count + 1, updated_at = datetime('now') WHERE batch_id = ?", (batch_id,))

    def release_item(self, *, batch_id: str, item_id: str, owner: str, lease_token: str) -> bool:
        with connect(self.database_path) as conn:
            cursor = conn.execute(
                """UPDATE shop_collection_items SET detail_status = 'pending', lease_owner = '',
                lease_token = '', lease_expires_at = NULL, updated_at = datetime('now')
                WHERE batch_id = ? AND item_id = ? AND detail_status = 'running'
                AND lease_owner = ? AND lease_token = ?""",
                (batch_id, item_id, owner, lease_token),
            )
        return bool(cursor.rowcount)

    def renew_item_lease(
        self, *, batch_id: str, item_id: str, owner: str, lease_token: str,
        lease_seconds: int,
    ) -> bool:
        now = _now()
        with connect(self.database_path) as conn:
            cursor = conn.execute(
                """UPDATE shop_collection_items SET lease_expires_at = ?, updated_at = datetime('now')
                WHERE batch_id = ? AND item_id = ? AND detail_status = 'running'
                AND lease_owner = ? AND lease_token = ? AND lease_expires_at > ?""",
                (_lease_deadline(lease_seconds), batch_id, item_id, owner, lease_token, now),
            )
        return bool(cursor.rowcount)

    def reset_failed_items(self, *, batch_id: str) -> int:
        with connect(self.database_path) as conn:
            cursor = conn.execute(
                """UPDATE shop_collection_items SET detail_status = 'pending', intake_action = 'none',
                error_code = '', error_message = '', lease_owner = '', lease_token = '', lease_expires_at = NULL,
                completed_at = NULL, updated_at = datetime('now')
                WHERE batch_id = ? AND detail_status = 'failed'""",
                (batch_id,),
            )
            conn.execute("UPDATE shop_collection_batches SET failed_count = 0, completed_at = NULL, updated_at = datetime('now') WHERE batch_id = ?", (batch_id,))
            return int(cursor.rowcount)

    def cancel_pending_items(self, *, batch_id: str) -> int:
        with connect(self.database_path) as conn:
            cursor = conn.execute(
                """UPDATE shop_collection_items SET detail_status = 'cancelled', completed_at = datetime('now'), updated_at = datetime('now')
                WHERE batch_id = ? AND detail_status IN ('pending','running')""", (batch_id,)
            )
            return int(cursor.rowcount)

    def has_unfinished_items(self, *, batch_id: str) -> bool:
        with connect(self.database_path) as conn:
            row = conn.execute("SELECT 1 FROM shop_collection_items WHERE batch_id = ? AND detail_status IN ('pending','running') LIMIT 1", (batch_id,)).fetchone()
        return row is not None

    def claim_next_runnable_batch(self, *, owner: str, lease_seconds: int) -> ShopBatchLease | None:
        return self._claim_batch(owner=owner, lease_seconds=lease_seconds)

    def claim_batch(self, *, batch_id: str, owner: str, lease_seconds: int) -> ShopBatchLease | None:
        return self._claim_batch(owner=owner, lease_seconds=lease_seconds, batch_id=batch_id)

    def _claim_batch(
        self, *, owner: str, lease_seconds: int, batch_id: str | None = None
    ) -> ShopBatchLease | None:
        now = _now()
        token = uuid.uuid4().hex
        with connect(self.database_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            identifier = " AND batch_id = ?" if batch_id is not None else ""
            parameters: list[Any] = [now]
            if batch_id is not None:
                parameters.append(batch_id)
            row = conn.execute(
                """SELECT batch_id FROM shop_collection_batches
                WHERE status IN ('queued','resolving','listing','enriching','pausing','cancelling')
                AND (lease_owner = '' OR lease_expires_at IS NULL OR lease_expires_at <= ?)"""
                + identifier + " ORDER BY created_at, batch_id LIMIT 1",
                parameters,
            ).fetchone()
            if row is None:
                return None
            claimed_id = str(row["batch_id"])
            cursor = conn.execute(
                """UPDATE shop_collection_batches SET lease_owner = ?, lease_token = ?, lease_expires_at = ?,
                updated_at = datetime('now') WHERE batch_id = ?
                AND (lease_owner = '' OR lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                (owner, token, _lease_deadline(lease_seconds), claimed_id, now),
            )
            if not cursor.rowcount:
                conn.rollback()
                return None
            claimed = conn.execute(
                "SELECT * FROM shop_collection_batches WHERE batch_id = ?", (claimed_id,)
            ).fetchone()
            conn.commit()
        assert claimed is not None
        return _batch_lease(claimed)

    def renew_batch_lease(
        self, *, batch_id: str, owner: str, lease_token: str, lease_seconds: int
    ) -> bool:
        now = _now()
        with connect(self.database_path) as conn:
            cursor = conn.execute(
                """UPDATE shop_collection_batches SET lease_expires_at = ?, updated_at = datetime('now')
                WHERE batch_id = ? AND lease_owner = ? AND lease_token = ? AND lease_expires_at > ?""",
                (_lease_deadline(lease_seconds), batch_id, owner, lease_token, now),
            )
        return bool(cursor.rowcount)

    def release_batch_lease(self, *, batch_id: str, owner: str, lease_token: str) -> bool:
        with connect(self.database_path) as conn:
            cursor = conn.execute(
                """UPDATE shop_collection_batches SET lease_owner = '', lease_token = '', lease_expires_at = NULL,
                updated_at = datetime('now') WHERE batch_id = ? AND lease_owner = ? AND lease_token = ?""",
                (batch_id, owner, lease_token),
            )
        return bool(cursor.rowcount)

    def next_runnable_batch(self) -> ShopBatch | None:
        with connect(self.database_path) as conn:
            row = conn.execute(
                """SELECT * FROM shop_collection_batches
                WHERE status IN ('queued','resolving','listing','enriching','pausing','cancelling')
                AND (lease_owner = '' OR lease_expires_at IS NULL OR lease_expires_at <= ?)
                ORDER BY created_at, batch_id LIMIT 1""",
                (_now(),),
            ).fetchone()
        return _batch(row) if row is not None else None

    def recover_interrupted_work(self) -> None:
        now = _now()
        with connect(self.database_path) as conn:
            conn.execute(
                """UPDATE shop_collection_items SET detail_status = 'pending', lease_owner = '',
                lease_token = '', lease_expires_at = NULL, updated_at = datetime('now')
                WHERE detail_status = 'running' AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                (now,),
            )
            conn.execute(
                """UPDATE shop_collection_batches SET lease_owner = '', lease_token = '',
                lease_expires_at = NULL, updated_at = datetime('now')
                WHERE status IN ('queued','resolving','listing','enriching','pausing','cancelling')
                AND lease_owner <> '' AND (lease_expires_at IS NULL OR lease_expires_at <= ?)""",
                (now,),
            )


def _batch(row: sqlite3.Row) -> ShopBatch:
    data = dict(row)
    for key in ("lease_owner", "lease_token", "lease_expires_at"):
        data.pop(key, None)
    data["listing_complete"] = bool(data["listing_complete"])
    return ShopBatch.model_validate(data)


def _item(row: sqlite3.Row) -> ShopBatchItem:
    data = dict(row)
    data["candidate"] = json.loads(data.pop("candidate_json") or "{}")
    data["source_url"] = _shop_item_source_url(
        data.get("source_url"), data.get("offer_id")
    )
    for key in ("lease_owner", "lease_token", "lease_expires_at"):
        data.pop(key, None)
    return ShopBatchItem.model_validate(data)


def _shop_item_source_url(source_url: object, offer_id: object) -> str:
    candidate = str(source_url or "").strip()
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return candidate
    normalized_offer_id = str(offer_id or "").strip()
    if normalized_offer_id.isdigit():
        return f"https://detail.1688.com/offer/{normalized_offer_id}.html"
    return ""


def _batch_lease(row: sqlite3.Row) -> ShopBatchLease:
    return ShopBatchLease(
        batch=_batch(row), lease_owner=str(row["lease_owner"]), lease_token=str(row["lease_token"])
    )


def _item_lease(row: sqlite3.Row) -> ShopItemLease:
    return ShopItemLease(
        item=_item(row), lease_owner=str(row["lease_owner"]), lease_token=str(row["lease_token"])
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_deadline(lease_seconds: int) -> str:
    if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or lease_seconds <= 0:
        raise ValueError("lease_seconds must be a positive integer")
    return (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()


def _safe_error(value: object) -> str:
    text = str(value or "")
    for marker in ("api_key", "api_secret", "secret", "token", "authorization", "cookie"):
        if marker.casefold() in text.casefold():
            return "upstream request failed"
    return text[:500]
