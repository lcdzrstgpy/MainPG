"""SQLite-backed daily API-call accounting for the selection collector."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def credential_fingerprint(credentials: Mapping[str, Any] | str) -> str:
    """Return a stable one-way digest without retaining provider credentials."""
    if isinstance(credentials, Mapping):
        payload = json.dumps(dict(credentials), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    elif isinstance(credentials, str):
        payload = credentials
    else:
        raise TypeError("credentials must be a mapping or string")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_credential_fingerprint(value: object) -> bool:
    return isinstance(value, str) and _SHA256_HEX.fullmatch(value.casefold()) is not None


@dataclass(frozen=True)
class BudgetState:
    allowed: bool
    workspace_id: str
    provider_fingerprint: str
    shanghai_date: str
    api_calls_limit: int
    api_calls_used: int
    api_calls_remaining: int
    reservation_granted: bool = False


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
        provider_fingerprint = _provider_fingerprint(provider_fingerprint)
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
            reservation_granted = used + api_calls <= limit
            if row is None:
                connection.execute(
                    """
                    INSERT INTO daily_selection_api_budget
                        (workspace_id, provider_fingerprint, shanghai_date, api_calls_limit, api_calls_used)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (workspace_id, provider_fingerprint, usage_date, limit, used + api_calls if reservation_granted else used),
                )
            else:
                connection.execute(
                    """
                    UPDATE daily_selection_api_budget
                    SET api_calls_limit = ?, api_calls_used = ?
                    WHERE workspace_id = ? AND provider_fingerprint = ? AND shanghai_date = ?
                    """,
                    (limit, used + api_calls if reservation_granted else used, workspace_id, provider_fingerprint, usage_date),
                )
            connection.commit()
            final_used = used + api_calls if reservation_granted else used
            remaining = max(limit - final_used, 0)
            return BudgetState(
                allowed=remaining > 0,
                workspace_id=workspace_id,
                provider_fingerprint=provider_fingerprint,
                shanghai_date=usage_date,
                api_calls_limit=limit,
                api_calls_used=final_used,
                api_calls_remaining=remaining,
                reservation_granted=reservation_granted,
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
        provider_fingerprint = _provider_fingerprint(provider_fingerprint)
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
        remaining = max(limit - used, 0)
        return BudgetState(remaining > 0, workspace_id, provider_fingerprint, usage_date, limit, used, remaining)

    def release(
        self,
        *,
        workspace_id: str,
        provider_fingerprint: str,
        max_api_calls: int,
        api_calls: int,
        now: datetime | None = None,
    ) -> BudgetState:
        """Return unused pre-reserved slots after a short-circuited operation."""
        workspace_id = _required_text(workspace_id, "workspace_id")
        provider_fingerprint = _provider_fingerprint(provider_fingerprint)
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
                raise ValueError("cannot release an unreserved API-call budget")
            limit, used = min(int(row[0]), max_api_calls), int(row[1])
            final_used = max(used - api_calls, 0)
            connection.execute(
                """
                UPDATE daily_selection_api_budget
                SET api_calls_limit = ?, api_calls_used = ?
                WHERE workspace_id = ? AND provider_fingerprint = ? AND shanghai_date = ?
                """,
                (limit, final_used, workspace_id, provider_fingerprint, usage_date),
            )
            connection.commit()
            remaining = max(limit - final_used, 0)
            return BudgetState(remaining > 0, workspace_id, provider_fingerprint, usage_date, limit, final_used, remaining)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

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


class TaskApiBudget:
    """In-memory API-call guard scoped to one collection request.

    It deliberately has no SQLite ledger: provider quotas are owned by the
    provider, while ``max_api_calls`` only caps one local collection task.
    Context variables keep concurrent FastAPI requests isolated.
    """

    def __init__(self) -> None:
        self._used: ContextVar[int] = ContextVar("data_collection_task_api_calls_used", default=0)

    def start(self) -> None:
        self._used.set(0)

    def reserve(
        self,
        *,
        workspace_id: str,
        provider_fingerprint: str,
        max_api_calls: int,
        api_calls: int = 1,
        now: datetime | None = None,
    ) -> BudgetState:
        _positive_int(max_api_calls, "max_api_calls")
        _positive_int(api_calls, "api_calls")
        used = self._used.get()
        granted = used + api_calls <= max_api_calls
        if granted:
            used += api_calls
            self._used.set(used)
        return self._state(workspace_id, provider_fingerprint, max_api_calls, used, granted, now)

    def state(
        self,
        *,
        workspace_id: str,
        provider_fingerprint: str,
        max_api_calls: int,
        now: datetime | None = None,
    ) -> BudgetState:
        _positive_int(max_api_calls, "max_api_calls")
        return self._state(workspace_id, provider_fingerprint, max_api_calls, self._used.get(), True, now)

    def release(
        self,
        *,
        workspace_id: str,
        provider_fingerprint: str,
        max_api_calls: int,
        api_calls: int,
        now: datetime | None = None,
    ) -> BudgetState:
        _positive_int(api_calls, "api_calls")
        used = max(self._used.get() - api_calls, 0)
        self._used.set(used)
        return self._state(workspace_id, provider_fingerprint, max_api_calls, used, True, now)

    @staticmethod
    def _state(
        workspace_id: str,
        provider_fingerprint: str,
        limit: int,
        used: int,
        granted: bool,
        now: datetime | None,
    ) -> BudgetState:
        return BudgetState(
            allowed=used < limit,
            reservation_granted=granted,
            workspace_id=_required_text(workspace_id, "workspace_id"),
            provider_fingerprint=_provider_fingerprint(provider_fingerprint),
            shanghai_date=_shanghai_date(now),
            api_calls_limit=limit,
            api_calls_used=used,
            api_calls_remaining=max(limit - used, 0),
        )

def _shanghai_date(now: datetime | None) -> str:
    instant = now or datetime.now(SHANGHAI)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=SHANGHAI)
    return instant.astimezone(SHANGHAI).date().isoformat()


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _provider_fingerprint(value: object) -> str:
    fingerprint = _required_text(value, "provider_fingerprint").casefold()
    if not is_credential_fingerprint(fingerprint):
        raise ValueError("provider_fingerprint must be a SHA-256 hexadecimal digest")
    return fingerprint


def _positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
