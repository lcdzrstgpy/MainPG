"""Workspace-isolated SQLite persistence for price-verification snapshots."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .contracts import PluginCommandRequest, PriceVerificationContractError, safe_json_dumps


class PriceVerificationNotFound(LookupError):
    """Resource was not found in the caller's workspace."""


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PairingCodeRecord(_Record):
    pairing_id: str
    workspace_id: str
    code_sha256: str
    expires_at: str
    used_at: str | None = None
    created_at: str


class PluginSessionRecord(_Record):
    session_id: str
    workspace_id: str
    token_sha256: str
    browser: str
    plugin_version: str = ""
    capabilities: Mapping[str, Any] = Field(default_factory=dict)
    status: str
    created_at: str
    last_seen_at: str


class PluginCommandRecord(_Record):
    command_id: str
    workspace_id: str
    session_id: str
    command_type: str
    idempotency_key: str
    payload: Mapping[str, Any] = Field(default_factory=dict)
    result: Mapping[str, Any] = Field(default_factory=dict)
    status: str
    lease_expires_at: str | None = None
    created_at: str
    updated_at: str


class QuoteRunRecord(_Record):
    run_id: str
    workspace_id: str
    command_id: str
    status: str
    item_count: int
    adapter_version: str
    captured_at: str
    created_at: str
    items: tuple[Mapping[str, Any], ...] = ()


class SourcingRunRecord(_Record):
    run_id: str
    workspace_id: str
    quote_run_id: str
    source_mode: str
    status: str
    task_count: int
    candidate_count: int
    created_at: str
    candidates: tuple[Mapping[str, Any], ...] = ()


class ProviderBudgetRecord(_Record):
    workspace_id: str
    credential_fingerprint: str
    shanghai_date: str
    call_limit: int
    used_count: int
    updated_at: str


class PriceVerificationRepository:
    """Own all price-verification tables without host routing dependencies."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        self._database_uri = self._database_path == ":memory:"
        self._connect_target = (
            f"file:price-verification-{uuid.uuid4().hex}?mode=memory&cache=shared"
            if self._database_uri
            else self._database_path
        )
        self._keeper_connection = self._new_connection() if self._database_uri else None
        self.initialize()

    @property
    def database_path(self) -> str:
        return self._database_path

    def close(self) -> None:
        if self._keeper_connection is not None:
            self._keeper_connection.close()
            self._keeper_connection = None

    def initialize(self) -> None:
        migration = Path(__file__).with_name("migrations") / "001_price_verification.sql"
        with self._connect() as connection:
            connection.executescript(migration.read_text(encoding="utf-8"))

    def create_pairing_code(
        self,
        *,
        workspace_id: str,
        code_sha256: str,
        expires_at: str,
        pairing_id: str | None = None,
    ) -> PairingCodeRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        code_sha256 = _digest(code_sha256, "code_sha256")
        expires_at = _required_text(expires_at, "expires_at")
        pairing_id = pairing_id or _new_id()
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO price_verification_pairing_codes
                (pairing_id, workspace_id, code_sha256, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (pairing_id, workspace_id, code_sha256, expires_at, now),
            )
        return self.get_pairing_code(workspace_id=workspace_id, pairing_id=pairing_id)

    def get_pairing_code(self, *, workspace_id: str, pairing_id: str) -> PairingCodeRecord:
        row = self._owned_row(
            "price_verification_pairing_codes", workspace_id, "pairing_id", pairing_id
        )
        return PairingCodeRecord(**dict(row))

    def mark_pairing_code_used(
        self, *, workspace_id: str, pairing_id: str, used_at: str | None = None
    ) -> PairingCodeRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        pairing_id = _required_text(pairing_id, "pairing_id")
        with self._connect() as connection:
            self._owned_row_in(connection, "price_verification_pairing_codes", workspace_id, "pairing_id", pairing_id)
            connection.execute(
                """UPDATE price_verification_pairing_codes SET used_at = ?
                WHERE workspace_id = ? AND pairing_id = ? AND used_at IS NULL""",
                (used_at or _now(), workspace_id, pairing_id),
            )
        return self.get_pairing_code(workspace_id=workspace_id, pairing_id=pairing_id)

    def create_plugin_session(
        self,
        *,
        workspace_id: str,
        session_token_hash: str,
        browser: str,
        plugin_version: str = "",
        capabilities: Mapping[str, Any] | None = None,
        session_id: str | None = None,
    ) -> PluginSessionRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        token_sha256 = _digest(session_token_hash, "session_token_hash")
        browser = _required_text(browser, "browser")
        session_id = session_id or _new_id()
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO price_verification_plugin_sessions
                (session_id, workspace_id, token_sha256, browser, plugin_version,
                 capabilities_json, status, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, 'connected', ?, ?)""",
                (
                    session_id,
                    workspace_id,
                    token_sha256,
                    browser,
                    str(plugin_version),
                    safe_json_dumps(capabilities or {}),
                    now,
                    now,
                ),
            )
        return self.get_plugin_session(workspace_id=workspace_id, session_id=session_id)

    def get_plugin_session(
        self, *, workspace_id: str, session_id: str
    ) -> PluginSessionRecord:
        row = self._owned_row(
            "price_verification_plugin_sessions", workspace_id, "session_id", session_id
        )
        return _session_record(row)

    def get_plugin_session_by_token_hash(self, *, session_token_hash: str) -> PluginSessionRecord:
        token_sha256 = _digest(session_token_hash, "session_token_hash")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM price_verification_plugin_sessions WHERE token_sha256 = ?",
                (token_sha256,),
            ).fetchone()
        if row is None:
            raise PriceVerificationNotFound("plugin session not found")
        return _session_record(row)

    def create_command(
        self,
        *,
        workspace_id: str,
        session_id: str,
        request: PluginCommandRequest,
        command_id: str | None = None,
    ) -> PluginCommandRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        session_id = _required_text(session_id, "session_id")
        if not isinstance(request, PluginCommandRequest):
            raise TypeError("request must be PluginCommandRequest")
        command_id = command_id or _new_id()
        now = _now()
        with self._connect() as connection:
            self._owned_row_in(
                connection, "price_verification_plugin_sessions", workspace_id, "session_id", session_id
            )
            existing = connection.execute(
                """SELECT * FROM price_verification_plugin_commands
                WHERE workspace_id = ? AND command_type = ? AND idempotency_key = ?""",
                (workspace_id, request.command_type, request.idempotency_key),
            ).fetchone()
            if existing is not None:
                return _command_record(existing)
            connection.execute(
                """INSERT INTO price_verification_plugin_commands
                (command_id, workspace_id, session_id, command_type, idempotency_key,
                 payload_json, result_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, '{}', 'queued', ?, ?)""",
                (
                    command_id,
                    workspace_id,
                    session_id,
                    request.command_type,
                    request.idempotency_key,
                    safe_json_dumps(request.payload),
                    now,
                    now,
                ),
            )
        return self.get_command(workspace_id=workspace_id, command_id=command_id)

    def get_command(self, *, workspace_id: str, command_id: str) -> PluginCommandRecord:
        row = self._owned_row(
            "price_verification_plugin_commands", workspace_id, "command_id", command_id
        )
        return _command_record(row)

    def create_quote_run(
        self,
        *,
        workspace_id: str,
        command_id: str,
        items: Sequence[Mapping[str, Any]],
        status: str = "succeeded",
        adapter_version: str = "",
        captured_at: str | None = None,
        run_id: str | None = None,
    ) -> QuoteRunRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        command_id = _required_text(command_id, "command_id")
        status = _required_text(status, "status")
        snapshots = _snapshot_rows(items, key_name="quote_key")
        run_id = run_id or _new_id()
        now = _now()
        captured_at = captured_at or now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                command = connection.execute(
                    """SELECT workspace_id FROM price_verification_plugin_commands
                    WHERE command_id = ?""",
                    (command_id,),
                ).fetchone()
                if command is not None and command["workspace_id"] != workspace_id:
                    raise PriceVerificationNotFound("resource not found")
                connection.execute(
                    """INSERT INTO price_verification_quote_runs
                    (run_id, workspace_id, command_id, status, item_count,
                     adapter_version, captured_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, workspace_id, command_id, status, len(snapshots), str(adapter_version), captured_at, now),
                )
                connection.executemany(
                    """INSERT INTO price_verification_quote_items
                    (workspace_id, run_id, quote_key, snapshot_json) VALUES (?, ?, ?, ?)""",
                    [(workspace_id, run_id, key, serialized) for key, serialized in snapshots],
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_quote_run(workspace_id=workspace_id, run_id=run_id)

    def get_quote_run(self, *, workspace_id: str, run_id: str) -> QuoteRunRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        run_id = _required_text(run_id, "run_id")
        with self._connect() as connection:
            row = self._owned_row_in(
                connection, "price_verification_quote_runs", workspace_id, "run_id", run_id
            )
            items = connection.execute(
                """SELECT snapshot_json FROM price_verification_quote_items
                WHERE workspace_id = ? AND run_id = ? ORDER BY quote_key""",
                (workspace_id, run_id),
            ).fetchall()
        return QuoteRunRecord(**dict(row), items=tuple(_load_snapshot(item["snapshot_json"]) for item in items))

    def create_sourcing_run(
        self,
        *,
        workspace_id: str,
        quote_run_id: str,
        candidates: Sequence[Mapping[str, Any]],
        source_mode: str = "browser_image_search",
        status: str = "succeeded",
        task_count: int | None = None,
        run_id: str | None = None,
    ) -> SourcingRunRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        quote_run_id = _required_text(quote_run_id, "quote_run_id")
        source_mode = _required_text(source_mode, "source_mode")
        status = _required_text(status, "status")
        snapshots = _source_candidate_rows(candidates)
        run_id = run_id or _new_id()
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_row_in(
                    connection, "price_verification_quote_runs", workspace_id, "run_id", quote_run_id
                )
                connection.execute(
                    """INSERT INTO price_verification_sourcing_runs
                    (run_id, workspace_id, quote_run_id, source_mode, status, task_count,
                     candidate_count, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (run_id, workspace_id, quote_run_id, source_mode, status,
                     len(snapshots) if task_count is None else int(task_count), len(snapshots), now),
                )
                connection.executemany(
                    """INSERT INTO price_verification_source_candidates
                    (workspace_id, sourcing_run_id, quote_key, candidate_key, snapshot_json)
                    VALUES (?, ?, ?, ?, ?)""",
                    [(workspace_id, run_id, quote_key, candidate_key, serialized)
                     for quote_key, candidate_key, serialized in snapshots],
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_sourcing_run(workspace_id=workspace_id, run_id=run_id)

    def get_sourcing_run(self, *, workspace_id: str, run_id: str) -> SourcingRunRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        run_id = _required_text(run_id, "run_id")
        with self._connect() as connection:
            row = self._owned_row_in(
                connection, "price_verification_sourcing_runs", workspace_id, "run_id", run_id
            )
            candidates = connection.execute(
                """SELECT snapshot_json FROM price_verification_source_candidates
                WHERE workspace_id = ? AND sourcing_run_id = ? ORDER BY quote_key, candidate_key""",
                (workspace_id, run_id),
            ).fetchall()
        return SourcingRunRecord(
            **dict(row), candidates=tuple(_load_snapshot(item["snapshot_json"]) for item in candidates)
        )

    def get_or_create_provider_budget(
        self,
        *,
        workspace_id: str,
        credential_fingerprint: str,
        shanghai_date: str,
        call_limit: int,
    ) -> ProviderBudgetRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        credential_fingerprint = _digest(credential_fingerprint, "credential_fingerprint")
        shanghai_date = _required_text(shanghai_date, "shanghai_date")
        if isinstance(call_limit, bool) or not isinstance(call_limit, int) or call_limit < 1:
            raise ValueError("call_limit must be a positive integer")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO price_verification_provider_budgets
                (workspace_id, credential_fingerprint, shanghai_date, call_limit, used_count, updated_at)
                VALUES (?, ?, ?, ?, 0, ?)
                ON CONFLICT(workspace_id, credential_fingerprint, shanghai_date) DO NOTHING""",
                (workspace_id, credential_fingerprint, shanghai_date, call_limit, now),
            )
            row = connection.execute(
                """SELECT * FROM price_verification_provider_budgets
                WHERE workspace_id = ? AND credential_fingerprint = ? AND shanghai_date = ?""",
                (workspace_id, credential_fingerprint, shanghai_date),
            ).fetchone()
        return ProviderBudgetRecord(**dict(row))

    def _owned_row(self, table: str, workspace_id: str, id_column: str, identifier: str) -> sqlite3.Row:
        with self._connect() as connection:
            return self._owned_row_in(connection, table, workspace_id, id_column, identifier)

    @staticmethod
    def _owned_row_in(
        connection: sqlite3.Connection,
        table: str,
        workspace_id: str,
        id_column: str,
        identifier: str,
    ) -> sqlite3.Row:
        workspace_id = _required_text(workspace_id, "workspace_id")
        identifier = _required_text(identifier, id_column)
        row = connection.execute(
            f"SELECT * FROM {table} WHERE workspace_id = ? AND {id_column} = ?",
            (workspace_id, identifier),
        ).fetchone()
        if row is None:
            raise PriceVerificationNotFound("resource not found")
        return row

    def _new_connection(self) -> sqlite3.Connection:
        if not self._database_uri:
            Path(self._database_path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._connect_target, uri=self._database_uri, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _connect(self) -> sqlite3.Connection:
        return self._new_connection()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _digest(value: object, field_name: str) -> str:
    value = _required_text(value, field_name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.casefold()):
        raise PriceVerificationContractError(f"{field_name} must be a SHA-256 digest")
    return value.casefold()


def _snapshot_rows(items: Sequence[Mapping[str, Any]], *, key_name: str) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            raise TypeError("snapshot entries must be mappings")
        key = str(item.get(key_name) or item.get("sku_key") or item.get("skc_id") or index)
        if not key.strip() or key in seen:
            raise ValueError(f"{key_name} values must be unique within a run")
        seen.add(key)
        rows.append((key, safe_json_dumps(item)))
    return tuple(rows)


def _source_candidate_rows(candidates: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            raise TypeError("candidate entries must be mappings")
        quote_key = str(candidate.get("quote_key") or candidate.get("source_quote_key") or "")
        candidate_key = str(candidate.get("candidate_key") or candidate.get("offer_id") or index)
        if not quote_key.strip() or not candidate_key.strip() or (quote_key, candidate_key) in seen:
            raise ValueError("quote_key and candidate_key must be unique within a sourcing run")
        seen.add((quote_key, candidate_key))
        rows.append((quote_key, candidate_key, safe_json_dumps(candidate)))
    return tuple(rows)


def _load_snapshot(value: str) -> Mapping[str, Any]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, Mapping) else {}


def _session_record(row: sqlite3.Row) -> PluginSessionRecord:
    values = dict(row)
    values["capabilities"] = _load_snapshot(values.pop("capabilities_json"))
    return PluginSessionRecord(**values)


def _command_record(row: sqlite3.Row) -> PluginCommandRecord:
    values = dict(row)
    values["payload"] = _load_snapshot(values.pop("payload_json"))
    values["result"] = _load_snapshot(values.pop("result_json"))
    return PluginCommandRecord(**values)
