"""Workspace-isolated SQLite persistence for price-verification snapshots."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    ALLOWED_PLUGIN_COMMAND_TYPES,
    PluginCommandRequest,
    PriceVerificationContractError,
    safe_json_dumps,
)


class PriceVerificationNotFound(LookupError):
    """Resource was not found in the caller's workspace."""


class PairingCodeConsumed(PermissionError):
    """A pairing credential has already been consumed."""


class PairingCodeExpired(PermissionError):
    """A pairing credential has passed its short validity window."""


class PairingCodeWorkspaceNotFound(PriceVerificationNotFound):
    """A pairing code exists but is not owned by the authenticated workspace."""


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

    def consume_pairing_code(self, *, code_sha256: str, now: str) -> PairingCodeRecord:
        """Atomically validate and consume a hashed pairing credential."""
        code_sha256 = _digest(code_sha256, "code_sha256")
        now = _required_text(now, "now")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM price_verification_pairing_codes WHERE code_sha256 = ?",
                (code_sha256,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise PriceVerificationNotFound("pairing code not found")
            if row["used_at"] is not None:
                connection.rollback()
                raise PairingCodeConsumed("pairing code has already been used")
            if str(row["expires_at"]) <= now:
                connection.rollback()
                raise PairingCodeExpired("pairing code has expired")
            if connection.execute(
                """UPDATE price_verification_pairing_codes SET used_at = ?
                WHERE pairing_id = ? AND used_at IS NULL""",
                (now, row["pairing_id"]),
            ).rowcount != 1:
                connection.rollback()
                raise PairingCodeConsumed("pairing code has already been used")
            row = connection.execute(
                "SELECT * FROM price_verification_pairing_codes WHERE pairing_id = ?",
                (row["pairing_id"],),
            ).fetchone()
            connection.commit()
        return PairingCodeRecord(**dict(row))

    def connect_plugin_session(
        self,
        *,
        code_sha256: str,
        session_token_hash: str,
        browser: str,
        capabilities: Mapping[str, Any] | None,
        now: str,
        plugin_version: str = "",
        expected_workspace_id: str | None = None,
        session_id: str | None = None,
    ) -> PluginSessionRecord:
        """Atomically validate a pairing code and create its plugin session.

        ``expected_workspace_id`` is supplied by the authenticated route. A
        mismatch deliberately looks like a missing resource and leaves the
        pairing credential untouched.
        """
        code_sha256 = _digest(code_sha256, "code_sha256")
        token_sha256 = _digest(session_token_hash, "session_token_hash")
        browser = _required_text(browser, "browser")
        now = _required_text(now, "now")
        if expected_workspace_id is not None:
            expected_workspace_id = _required_text(expected_workspace_id, "expected_workspace_id")
        serialized_capabilities = safe_json_dumps(capabilities or {})
        session_id = session_id or _new_id()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            pairing = connection.execute(
                "SELECT * FROM price_verification_pairing_codes WHERE code_sha256 = ?",
                (code_sha256,),
            ).fetchone()
            if pairing is None:
                connection.rollback()
                raise PriceVerificationNotFound("pairing code not found")
            if (
                expected_workspace_id is not None
                and pairing["workspace_id"] != expected_workspace_id
            ):
                connection.rollback()
                raise PairingCodeWorkspaceNotFound("pairing code not found")
            if pairing["used_at"] is not None:
                connection.rollback()
                raise PairingCodeConsumed("pairing code has already been used")
            if str(pairing["expires_at"]) <= now:
                connection.rollback()
                raise PairingCodeExpired("pairing code has expired")
            if connection.execute(
                """UPDATE price_verification_pairing_codes SET used_at = ?
                WHERE pairing_id = ? AND used_at IS NULL""",
                (now, pairing["pairing_id"]),
            ).rowcount != 1:
                connection.rollback()
                raise PairingCodeConsumed("pairing code has already been used")
            connection.execute(
                """INSERT INTO price_verification_plugin_sessions
                (session_id, workspace_id, token_sha256, browser, plugin_version,
                 capabilities_json, status, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?, 'connected', ?, ?)""",
                (
                    session_id,
                    pairing["workspace_id"],
                    token_sha256,
                    browser,
                    str(plugin_version),
                    serialized_capabilities,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM price_verification_plugin_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            connection.commit()
        return _session_record(row)

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

    def list_plugin_sessions(self, *, workspace_id: str) -> tuple[PluginSessionRecord, ...]:
        workspace_id = _required_text(workspace_id, "workspace_id")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM price_verification_plugin_sessions
                WHERE workspace_id = ? ORDER BY last_seen_at DESC, session_id""",
                (workspace_id,),
            ).fetchall()
        return tuple(_session_record(row) for row in rows)

    def touch_plugin_session(
        self, *, workspace_id: str, session_id: str, now: str
    ) -> PluginSessionRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        session_id = _required_text(session_id, "session_id")
        now = _required_text(now, "now")
        with self._connect() as connection:
            self._owned_row_in(
                connection, "price_verification_plugin_sessions", workspace_id, "session_id", session_id
            )
            connection.execute(
                """UPDATE price_verification_plugin_sessions
                SET last_seen_at = ?, status = 'connected' WHERE session_id = ?""",
                (now, session_id),
            )
        return self.get_plugin_session(workspace_id=workspace_id, session_id=session_id)

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

    def lease_plugin_commands(
        self,
        *,
        workspace_id: str,
        session_id: str,
        command_types: Sequence[str],
        now: str,
        lease_expires_at: str,
        limit: int,
    ) -> tuple[PluginCommandRecord, ...]:
        """Lease queued or expired commands owned by one workspace session."""
        workspace_id = _required_text(workspace_id, "workspace_id")
        session_id = _required_text(session_id, "session_id")
        now = _required_text(now, "now")
        lease_expires_at = _required_text(lease_expires_at, "lease_expires_at")
        types = tuple(dict.fromkeys(str(value) for value in command_types))
        if not types or any(value not in ALLOWED_PLUGIN_COMMAND_TYPES for value in types):
            raise PriceVerificationContractError("unsupported plugin command type")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        marks = ",".join("?" for _ in types)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_row_in(
                connection, "price_verification_plugin_sessions", workspace_id, "session_id", session_id
            )
            rows = connection.execute(
                f"""SELECT command_id FROM price_verification_plugin_commands
                WHERE workspace_id = ? AND session_id = ? AND command_type IN ({marks})
                  AND (status = 'queued' OR (status IN ('leased', 'running')
                       AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?))
                ORDER BY created_at, command_id LIMIT ?""",
                (workspace_id, session_id, *types, now, limit),
            ).fetchall()
            command_ids = tuple(str(row["command_id"]) for row in rows)
            if command_ids:
                id_marks = ",".join("?" for _ in command_ids)
                connection.execute(
                    f"""UPDATE price_verification_plugin_commands
                    SET status = 'leased', lease_expires_at = ?, updated_at = ?
                    WHERE command_id IN ({id_marks})""",
                    (lease_expires_at, now, *command_ids),
                )
            connection.execute(
                """UPDATE price_verification_plugin_sessions
                SET last_seen_at = ?, status = 'connected' WHERE session_id = ?""",
                (now, session_id),
            )
            leased = (
                connection.execute(
                    f"""SELECT * FROM price_verification_plugin_commands
                    WHERE command_id IN ({','.join('?' for _ in command_ids)})
                    ORDER BY created_at, command_id""",
                    command_ids,
                ).fetchall()
                if command_ids
                else ()
            )
            connection.commit()
        return tuple(_command_record(row) for row in leased)

    def record_plugin_result(
        self,
        *,
        workspace_id: str,
        session_id: str,
        command_id: str,
        status: str,
        result: Mapping[str, Any],
        now: str,
        lease_expires_at: str | None = None,
    ) -> PluginCommandRecord:
        """Persist a validated result only while the caller owns a live lease."""
        workspace_id = _required_text(workspace_id, "workspace_id")
        session_id = _required_text(session_id, "session_id")
        command_id = _required_text(command_id, "command_id")
        now = _required_text(now, "now")
        if status not in {"running", "succeeded", "failed"}:
            raise PriceVerificationContractError("unsupported command status")
        if status == "running":
            lease_expires_at = _required_text(lease_expires_at, "lease_expires_at")
        elif lease_expires_at is not None:
            raise ValueError("terminal command results cannot retain a lease")
        if not isinstance(result, Mapping):
            raise PriceVerificationContractError("result must be a mapping")
        serialized_result = safe_json_dumps(result)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM price_verification_plugin_commands
                WHERE command_id = ? AND workspace_id = ? AND session_id = ?""",
                (command_id, workspace_id, session_id),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise PriceVerificationNotFound("plugin command not found")
            if row["command_type"] not in ALLOWED_PLUGIN_COMMAND_TYPES:
                connection.rollback()
                raise PriceVerificationContractError("unsupported plugin command type")
            if row["status"] in {"succeeded", "failed"}:
                connection.rollback()
                raise ValueError("command is already complete")
            if row["lease_expires_at"] is None or str(row["lease_expires_at"]) <= now:
                connection.rollback()
                raise ValueError("command lease has expired")
            connection.execute(
                """UPDATE price_verification_plugin_commands
                SET status = ?, result_json = ?, lease_expires_at = ?, updated_at = ?
                WHERE command_id = ?""",
                (status, serialized_result, lease_expires_at, now, command_id),
            )
            connection.execute(
                """UPDATE price_verification_plugin_sessions
                SET last_seen_at = ?, status = 'connected' WHERE session_id = ?""",
                (now, session_id),
            )
            record = connection.execute(
                "SELECT * FROM price_verification_plugin_commands WHERE command_id = ?", (command_id,)
            ).fetchone()
            connection.commit()
        return _command_record(record)

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
