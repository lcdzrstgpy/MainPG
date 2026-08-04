"""SQLite-backed daily API-call accounting for the selection collector."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def credential_fingerprint(credentials: Mapping[str, Any] | str) -> str:
    """Return a stable one-way digest without retaining provider credentials."""
    if isinstance(credentials, Mapping):
        payload = json.dumps(dict(credentials), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    elif isinstance(credentials, str):
        payload = credentials
    else:
        raise TypeError("credentials must be a mapping or string")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BudgetState:
    allowed: bool
    workspace_id: str
    provider_fingerprint: str
    shanghai_date: str
    api_calls_limit: int
    api_calls_used: int
    api_calls_remaining: int


class SQLiteDailyApiBudget:
    """Atomically reserve call slots for a workspace/provider/day ledger key."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        self._initialize()

    def reserve(
        self,
        *,
        workspace_id: str,
        provider_fingerprint: str,
        max_api_calls: int,
        api_calls: int = 1,
        now: datetime | None = None,
    ) -> BudgetState:
        workspace_id = _required_text(workspace_id, "workspace_id")
        provider_fingerprint = _required_text(provider_fingerprint, "provider_fingerprint")
        _positive_int(max_api_calls, "max_api_calls")
        _positive_int(api_calls, "api_calls")
        usage_date = _shanghai_date(now)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT api_calls_limit, api_calls_used
                FROM daily_selection_api_budget
                WHERE workspace_id = ? AND provider_fingerprint = ? AND shanghai_date = ?
                """,
                (workspace_id, provider_fingerprint, usage_date),
            ).fetchone()
            if row is None:
                limit, used = max_api_calls, 0
            else:
                # A later request can tighten, but never loosen, the day's first budget.
                limit, used = min(int(row[0]), max_api_calls), int(row[1])
            allowed = used + api_calls <= limit
            if row is None:
                connection.execute(
                    """
                    INSERT INTO daily_selection_api_budget
                        (workspace_id, provider_fingerprint, shanghai_date, api_calls_limit, api_calls_used)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (workspace_id, provider_fingerprint, usage_date, limit, used + api_calls if allowed else used),
                )
            else:
                connection.execute(
                    """
                    UPDATE daily_selection_api_budget
                    SET api_calls_limit = ?, api_calls_used = ?
                    WHERE workspace_id = ? AND provider_fingerprint = ? AND shanghai_date = ?
                    """,
                    (limit, used + api_calls if allowed else used, workspace_id, provider_fingerprint, usage_date),
                )
            connection.commit()
            final_used = used + api_calls if allowed else used
            return BudgetState(
                allowed=allowed,
                workspace_id=workspace_id,
                provider_fingerprint=provider_fingerprint,
                shanghai_date=usage_date,
                api_calls_limit=limit,
                api_calls_used=final_used,
                api_calls_remaining=limit - final_used,
            )
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def state(
        self,
        *,
        workspace_id: str,
        provider_fingerprint: str,
        max_api_calls: int,
        now: datetime | None = None,
    ) -> BudgetState:
        workspace_id = _required_text(workspace_id, "workspace_id")
        provider_fingerprint = _required_text(provider_fingerprint, "provider_fingerprint")
        _positive_int(max_api_calls, "max_api_calls")
        usage_date = _shanghai_date(now)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT api_calls_limit, api_calls_used
                FROM daily_selection_api_budget
                WHERE workspace_id = ? AND provider_fingerprint = ? AND shanghai_date = ?
                """,
                (workspace_id, provider_fingerprint, usage_date),
            ).fetchone()
        finally:
            connection.close()
        limit, used = (max_api_calls, 0) if row is None else (min(int(row[0]), max_api_calls), int(row[1]))
        return BudgetState(True, workspace_id, provider_fingerprint, usage_date, limit, used, max(limit - used, 0))

    consume = reserve

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_selection_api_budget (
                    workspace_id TEXT NOT NULL,
                    provider_fingerprint TEXT NOT NULL,
                    shanghai_date TEXT NOT NULL,
                    api_calls_limit INTEGER NOT NULL,
                    api_calls_used INTEGER NOT NULL,
                    PRIMARY KEY (workspace_id, provider_fingerprint, shanghai_date)
                )
                """
            )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._database_path, timeout=10, isolation_level=None)


def _shanghai_date(now: datetime | None) -> str:
    instant = now or datetime.now(SHANGHAI)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=SHANGHAI)
    return instant.astimezone(SHANGHAI).date().isoformat()


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
