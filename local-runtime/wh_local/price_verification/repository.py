"""Workspace-isolated SQLite persistence for price-verification snapshots."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
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


class QuoteDecisionRecord(_Record):
    decision_id: str
    workspace_id: str
    quote_run_id: str
    quote_key: str
    decision: str
    decided_by: str
    note: str
    revision: int
    decided_at: str


class SourcingRunQuoteRecord(_Record):
    workspace_id: str
    sourcing_run_id: str
    quote_run_id: str
    quote_key: str
    official_link_url: str
    main_image_url: str
    selected_price_cny: str
    snapshot: Mapping[str, Any] = Field(default_factory=dict)
    created_at: str


class QuoteCaptureBatchRecord(_Record):
    batch_id: str
    workspace_id: str
    name: str
    is_current: bool
    created_by: str
    created_at: str
    updated_at: str
    chunk_count: int = 0
    quote_count: int = 0
    skc_count: int = 0
    snapshot_count: int = 0


class PrescreenSettingsRecord(_Record):
    workspace_id: str
    min_adjusted_price_cny: str | None = None
    updated_at: str = ""
    updated_by: str = ""


class QuoteCaptureChunkRecord(_Record):
    chunk_id: str
    workspace_id: str
    batch_id: str
    content_sha256: str
    page_url: str
    item_count: int
    capture: Mapping[str, Any] = Field(default_factory=dict)
    items: tuple[Mapping[str, Any], ...] = ()
    captured_at: str
    created_at: str


class QuoteCaptureBatchSnapshotRecord(_Record):
    workspace_id: str
    batch_id: str
    revision: int
    quote_run_id: str
    created_at: str


class BatchSelectionRecord(_Record):
    id: int
    workspace_id: str
    batch_id: str
    skc_id: str
    quote_keys: tuple[str, ...] = ()
    product_title: str = ""
    main_image_url: str = ""
    official_link_url: str = ""
    site: str = ""
    source_confidence: str = ""
    authenticity_status: str = ""
    sku_prices: tuple[Mapping[str, Any], ...] = ()
    original_min: str | None = None
    original_max: str | None = None
    adjusted_min: str | None = None
    adjusted_max: str | None = None
    max_candidates: int = 10
    status: str = "pending"
    created_at: str = ""
    updated_at: str = ""


class SkcSourceLinkRecord(_Record):
    """One retained 1688 offer linked to a Temu SKC for dropshipping lookup."""

    id: int
    workspace_id: str
    batch_id: str
    skc_id: str
    offer_id: str
    source_url: str
    source_title: str = ""
    main_image_url: str = ""
    price_cny: str | None = None
    weight_kg: str | None = None
    moq: str | None = None
    domestic_freight_cny: str | None = None
    source_decision: str = ""
    note: str = ""
    status: str = "active"
    created_at: str = ""
    updated_at: str = ""
    # 关联时快照的 Temu 侧上下文：覆盖式重新采集会清空 batch_selections，
    # 但已关联货源长期保留，STEP 04 展示与利润核算需要这些字段兜底。
    product_title: str = ""
    site: str = ""
    selling_price: str | None = None


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
        migrations = Path(__file__).with_name("migrations")
        with self._connect() as connection:
            for migration in sorted(migrations.glob("*.sql")):
                connection.executescript(migration.read_text(encoding="utf-8"))
            # SQLite has no "ADD COLUMN IF NOT EXISTS", so guard column adds here.
            _ensure_column(connection, "price_verification_skc_source_links", "product_title", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "price_verification_skc_source_links", "site", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(connection, "price_verification_skc_source_links", "selling_price", "TEXT")
            _ensure_column(connection, "price_verification_skc_source_links", "weight_kg", "TEXT")
            connection.execute(
                """UPDATE price_verification_skc_source_links AS link
                SET product_title = COALESCE((
                        SELECT s.product_title FROM price_verification_batch_selections AS s
                        WHERE s.workspace_id = link.workspace_id
                          AND s.batch_id = link.batch_id
                          AND s.skc_id = link.skc_id), ''),
                    site = COALESCE((
                        SELECT s.site FROM price_verification_batch_selections AS s
                        WHERE s.workspace_id = link.workspace_id
                          AND s.batch_id = link.batch_id
                          AND s.skc_id = link.skc_id), ''),
                    selling_price = COALESCE((
                        SELECT s.adjusted_min FROM price_verification_batch_selections AS s
                        WHERE s.workspace_id = link.workspace_id
                          AND s.batch_id = link.batch_id
                          AND s.skc_id = link.skc_id), link.selling_price)
                WHERE link.site = '' OR link.product_title = '' OR link.selling_price IS NULL"""
            )

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

    def create_quote_capture_batch(
        self,
        *,
        workspace_id: str,
        name: str,
        created_by: str,
        make_current: bool = True,
        batch_id: str | None = None,
    ) -> QuoteCaptureBatchRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        name = _required_text(name, "name")
        created_by = _required_text(created_by, "created_by")
        batch_id = batch_id or _new_id()
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if make_current:
                    connection.execute(
                        "UPDATE price_verification_quote_capture_batches SET is_current = 0, updated_at = ? WHERE workspace_id = ?",
                        (now, workspace_id),
                    )
                connection.execute(
                    """INSERT INTO price_verification_quote_capture_batches
                    (batch_id, workspace_id, name, is_current, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (batch_id, workspace_id, name, int(make_current), created_by, now, now),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_quote_capture_batch(workspace_id=workspace_id, batch_id=batch_id)

    def get_quote_capture_batch(
        self, *, workspace_id: str, batch_id: str
    ) -> QuoteCaptureBatchRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        with self._connect() as connection:
            row = self._owned_row_in(
                connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id
            )
            counts = connection.execute(
                """SELECT
                    (SELECT COUNT(*) FROM price_verification_quote_capture_chunks
                     WHERE workspace_id = ? AND batch_id = ?) AS chunk_count,
                    (SELECT COALESCE(SUM(item_count), 0) FROM price_verification_quote_capture_chunks
                     WHERE workspace_id = ? AND batch_id = ?) AS quote_count,
                    (SELECT COUNT(*) FROM price_verification_quote_capture_batch_snapshots
                     WHERE workspace_id = ? AND batch_id = ?) AS snapshot_count,
                    (SELECT COUNT(DISTINCT json_extract(je.value, '$.skc_id'))
                     FROM price_verification_quote_capture_chunks c, json_each(c.items_json) je
                     WHERE c.workspace_id = ? AND c.batch_id = ?) AS skc_count""",
                (workspace_id, batch_id, workspace_id, batch_id, workspace_id, batch_id, workspace_id, batch_id),
            ).fetchone()
        values = dict(row)
        values["is_current"] = bool(values["is_current"])
        values.update(dict(counts))
        return QuoteCaptureBatchRecord(**values)

    def list_quote_capture_batches(self, *, workspace_id: str) -> tuple[QuoteCaptureBatchRecord, ...]:
        workspace_id = _required_text(workspace_id, "workspace_id")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT b.*, COUNT(DISTINCT c.chunk_id) AS chunk_count,
                          (SELECT COALESCE(SUM(chunk.item_count), 0)
                           FROM price_verification_quote_capture_chunks chunk
                           WHERE chunk.workspace_id = b.workspace_id AND chunk.batch_id = b.batch_id) AS quote_count,
                          (SELECT COUNT(DISTINCT json_extract(je.value, '$.skc_id'))
                           FROM price_verification_quote_capture_chunks c, json_each(c.items_json) je
                           WHERE c.workspace_id = b.workspace_id AND c.batch_id = b.batch_id) AS skc_count,
                          COUNT(DISTINCT s.revision) AS snapshot_count
                FROM price_verification_quote_capture_batches b
                LEFT JOIN price_verification_quote_capture_chunks c
                    ON c.workspace_id = b.workspace_id AND c.batch_id = b.batch_id
                LEFT JOIN price_verification_quote_capture_batch_snapshots s
                    ON s.workspace_id = b.workspace_id AND s.batch_id = b.batch_id
                WHERE b.workspace_id = ?
                GROUP BY b.batch_id
                ORDER BY b.is_current DESC, b.updated_at DESC, b.batch_id DESC""",
                (workspace_id,),
            ).fetchall()
        return tuple(_capture_batch_record(row) for row in rows)

    def capture_batches_revision(self, *, workspace_id: str) -> str:
        """轻量变更指纹：最近一次核价批次写入/更新/采集的时间（ISO 字符串，字典序即时间序）。

        供前端轮询检测插件采集/核价确认产生的新数据，避免频繁拉全量列表。
        """
        workspace_id = _required_text(workspace_id, "workspace_id")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT MAX(updated_at) FROM price_verification_quote_capture_batches WHERE workspace_id = ?""",
                (workspace_id,),
            ).fetchone()
        value = row[0] if row else None
        return str(value) if value else ""

    def get_current_quote_capture_batch(self, *, workspace_id: str) -> QuoteCaptureBatchRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT batch_id FROM price_verification_quote_capture_batches
                WHERE workspace_id = ? AND is_current = 1""",
                (workspace_id,),
            ).fetchone()
        if row is None:
            raise PriceVerificationNotFound("current capture batch not found")
        return self.get_quote_capture_batch(workspace_id=workspace_id, batch_id=str(row["batch_id"]))

    def activate_quote_capture_batch(
        self, *, workspace_id: str, batch_id: str
    ) -> QuoteCaptureBatchRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_row_in(
                    connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id
                )
                connection.execute(
                    "UPDATE price_verification_quote_capture_batches SET is_current = 0, updated_at = ? WHERE workspace_id = ?",
                    (now, workspace_id),
                )
                connection.execute(
                    """UPDATE price_verification_quote_capture_batches
                    SET is_current = 1, updated_at = ? WHERE workspace_id = ? AND batch_id = ?""",
                    (now, workspace_id, batch_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_quote_capture_batch(workspace_id=workspace_id, batch_id=batch_id)

    def ensure_current_capture_batch(
        self, *, workspace_id: str, created_by: str
    ) -> QuoteCaptureBatchRecord:
        """Return the active capture batch, auto-creating one when none exists.

        Batch management is hidden from the workbench UI; a single default
        batch per workspace is kept alive so plugin captures always land
        somewhere without a manual "create batch" step.
        """
        workspace_id = _required_text(workspace_id, "workspace_id")
        created_by = _required_text(created_by, "created_by")
        try:
            return self.get_current_quote_capture_batch(workspace_id=workspace_id)
        except PriceVerificationNotFound:
            return self.create_quote_capture_batch(
                workspace_id=workspace_id,
                name="默认核价批次",
                created_by=created_by,
                make_current=True,
            )

    def get_prescreen_settings(
        self, *, workspace_id: str
    ) -> PrescreenSettingsRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT workspace_id, min_adjusted_price_cny, updated_at, updated_by
                FROM price_verification_prescreen_settings
                WHERE workspace_id = ?""",
                (workspace_id,),
            ).fetchone()
        if row is None:
            return PrescreenSettingsRecord(workspace_id=workspace_id)
        return PrescreenSettingsRecord(**dict(row))

    def set_prescreen_settings(
        self,
        *,
        workspace_id: str,
        min_adjusted_price_cny: str | None,
        updated_by: str,
    ) -> PrescreenSettingsRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        updated_by = _required_text(updated_by, "updated_by")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """INSERT INTO price_verification_prescreen_settings
                    (workspace_id, min_adjusted_price_cny, updated_at, updated_by)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(workspace_id) DO UPDATE SET
                        min_adjusted_price_cny = excluded.min_adjusted_price_cny,
                        updated_at = excluded.updated_at,
                        updated_by = excluded.updated_by""",
                    (workspace_id, min_adjusted_price_cny, now, updated_by),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_prescreen_settings(workspace_id=workspace_id)

    def clear_capture_batch_quotes(
        self, *, workspace_id: str, batch_id: str
    ) -> None:
        """Drop all captured quote chunks and staged selections of one batch.

        New plugin captures replace the previous batch content instead of
        accumulating, so the review panels always show only the latest 50
        captured rows.
        """
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """DELETE FROM price_verification_batch_selections
                    WHERE workspace_id = ? AND batch_id = ?""",
                    (workspace_id, batch_id),
                )
                connection.execute(
                    """DELETE FROM price_verification_batch_sourcing_sessions
                    WHERE workspace_id = ? AND batch_id = ?""",
                    (workspace_id, batch_id),
                )
                connection.execute(
                    """DELETE FROM price_verification_quote_capture_chunks
                    WHERE workspace_id = ? AND batch_id = ?""",
                    (workspace_id, batch_id),
                )
                connection.execute(
                    """UPDATE price_verification_quote_capture_batches
                    SET updated_at = ? WHERE workspace_id = ? AND batch_id = ?""",
                    (now, workspace_id, batch_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def append_quote_capture_chunk(
        self,
        *,
        workspace_id: str,
        batch_id: str,
        content_sha256: str,
        page_url: str,
        capture: Mapping[str, Any],
        items: Sequence[Mapping[str, Any]],
        captured_at: str,
    ) -> QuoteCaptureChunkRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        content_sha256 = _digest(content_sha256, "content_sha256")
        captured_at = _required_text(captured_at, "captured_at")
        if not isinstance(page_url, str):
            raise PriceVerificationContractError("page_url must be text")
        snapshots = _snapshot_rows(items, key_name="quote_key")
        if not snapshots:
            raise PriceVerificationContractError("one capture page must contain at least one quote row")
        if _capture_skc_group_count(snapshots) > 500:
            raise PriceVerificationContractError("one capture page must contain no more than 500 SKC groups")
        if len(snapshots) > 5_000:
            raise PriceVerificationContractError("one capture page contains too many SKU quote rows")
        if not isinstance(capture, Mapping):
            raise PriceVerificationContractError("capture must be a mapping")
        chunk_id = _new_id()
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_row_in(
                    connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id
                )
                existing = connection.execute(
                    """SELECT chunk_id FROM price_verification_quote_capture_chunks
                    WHERE workspace_id = ? AND batch_id = ? AND content_sha256 = ?""",
                    (workspace_id, batch_id, content_sha256),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """INSERT INTO price_verification_quote_capture_chunks
                        (chunk_id, workspace_id, batch_id, content_sha256, page_url, item_count,
                         capture_json, items_json, captured_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            chunk_id, workspace_id, batch_id, content_sha256, page_url.strip(), len(snapshots),
                            safe_json_dumps(capture), safe_json_dumps([json.loads(value) for _, value in snapshots]),
                            captured_at, now,
                        ),
                    )
                    connection.execute(
                        """UPDATE price_verification_quote_capture_batches SET updated_at = ?
                        WHERE workspace_id = ? AND batch_id = ?""",
                        (now, workspace_id, batch_id),
                    )
                else:
                    chunk_id = str(existing["chunk_id"])
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_quote_capture_chunk(workspace_id=workspace_id, chunk_id=chunk_id)

    def get_quote_capture_chunk(
        self, *, workspace_id: str, chunk_id: str
    ) -> QuoteCaptureChunkRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        chunk_id = _required_text(chunk_id, "chunk_id")
        with self._connect() as connection:
            row = self._owned_row_in(
                connection, "price_verification_quote_capture_chunks", workspace_id, "chunk_id", chunk_id
            )
        return _capture_chunk_record(row)

    def list_quote_capture_chunks(
        self, *, workspace_id: str, batch_id: str
    ) -> tuple[QuoteCaptureChunkRecord, ...]:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        with self._connect() as connection:
            self._owned_row_in(
                connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id
            )
            rows = connection.execute(
                """SELECT * FROM price_verification_quote_capture_chunks
                WHERE workspace_id = ? AND batch_id = ? ORDER BY created_at ASC, chunk_id ASC""",
                (workspace_id, batch_id),
            ).fetchall()
        return tuple(_capture_chunk_record(row) for row in rows)

    def remove_capture_chunk_quote_items(
        self, *, workspace_id: str, batch_id: str, quote_keys: Sequence[str]
    ) -> int:
        """Remove quote rows whose quote_key is in the target set from the
        batch's capture chunks; chunks that become empty are dropped."""
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        targets = {str(key).strip() for key in quote_keys if str(key).strip()}
        if not targets:
            return 0
        now = _now()
        removed = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_row_in(
                    connection,
                    "price_verification_quote_capture_batches",
                    workspace_id,
                    "batch_id",
                    batch_id,
                )
                rows = connection.execute(
                    """SELECT chunk_id, items_json, item_count
                    FROM price_verification_quote_capture_chunks
                    WHERE workspace_id = ? AND batch_id = ?""",
                    (workspace_id, batch_id),
                ).fetchall()
                for row in rows:
                    items = json.loads(row["items_json"]) if row["items_json"] else []
                    kept = [
                        item for item in items
                        if str((item or {}).get("quote_key") or "").strip() not in targets
                    ]
                    if len(kept) == len(items):
                        continue
                    removed += len(items) - len(kept)
                    if kept:
                        connection.execute(
                            """UPDATE price_verification_quote_capture_chunks
                            SET items_json = ?, item_count = ?
                            WHERE chunk_id = ?""",
                            (json.dumps(kept, ensure_ascii=False), len(kept), row["chunk_id"]),
                        )
                    else:
                        connection.execute(
                            """DELETE FROM price_verification_quote_capture_chunks
                            WHERE chunk_id = ?""",
                            (row["chunk_id"],),
                        )
                connection.execute(
                    """UPDATE price_verification_quote_capture_batches
                    SET updated_at = ? WHERE workspace_id = ? AND batch_id = ?""",
                    (now, workspace_id, batch_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return removed

    def clear_capture_chunks(self, *, workspace_id: str, batch_id: str) -> int:
        """Remove every capture chunk (and thus every quote row) of a batch,
        returning how many quote rows were removed."""
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        now = _now()
        removed = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_row_in(
                    connection,
                    "price_verification_quote_capture_batches",
                    workspace_id,
                    "batch_id",
                    batch_id,
                )
                row = connection.execute(
                    """SELECT COALESCE(SUM(item_count), 0) AS total
                    FROM price_verification_quote_capture_chunks
                    WHERE workspace_id = ? AND batch_id = ?""",
                    (workspace_id, batch_id),
                ).fetchone()
                if row is not None:
                    removed = int(row["total"] or 0)
                connection.execute(
                    """DELETE FROM price_verification_quote_capture_chunks
                    WHERE workspace_id = ? AND batch_id = ?""",
                    (workspace_id, batch_id),
                )
                connection.execute(
                    """UPDATE price_verification_quote_capture_batches
                    SET updated_at = ? WHERE workspace_id = ? AND batch_id = ?""",
                    (now, workspace_id, batch_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return removed

    def record_quote_capture_batch_snapshot(
        self, *, workspace_id: str, batch_id: str, quote_run_id: str
    ) -> QuoteCaptureBatchSnapshotRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        quote_run_id = _required_text(quote_run_id, "quote_run_id")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_row_in(
                    connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id
                )
                self._owned_row_in(
                    connection, "price_verification_quote_runs", workspace_id, "run_id", quote_run_id
                )
                row = connection.execute(
                    """SELECT COALESCE(MAX(revision), 0) AS revision
                    FROM price_verification_quote_capture_batch_snapshots
                    WHERE workspace_id = ? AND batch_id = ?""",
                    (workspace_id, batch_id),
                ).fetchone()
                revision = int(row["revision"]) + 1
                connection.execute(
                    """INSERT INTO price_verification_quote_capture_batch_snapshots
                    (workspace_id, batch_id, revision, quote_run_id, created_at)
                    VALUES (?, ?, ?, ?, ?)""",
                    (workspace_id, batch_id, revision, quote_run_id, now),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return QuoteCaptureBatchSnapshotRecord(
            workspace_id=workspace_id, batch_id=batch_id, revision=revision,
            quote_run_id=quote_run_id, created_at=now,
        )

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

    def upsert_batch_selection(
        self,
        *,
        workspace_id: str,
        batch_id: str,
        skc_id: str,
        quote_keys: Sequence[str],
        product_title: str,
        main_image_url: str,
        official_link_url: str,
        site: str,
        source_confidence: str,
        authenticity_status: str,
        sku_prices: Sequence[Mapping[str, Any]],
        original_min: str | None,
        original_max: str | None,
        adjusted_min: str | None,
        adjusted_max: str | None,
        max_candidates: int = 10,
        now: str,
    ) -> BatchSelectionRecord:
        """Add or refresh one SKC row in the pending review list.

        A first-time (or previously deleted) SKC joins as ``pending``; an
        existing row keeps its current review status while its content snapshot
        is refreshed with the latest captured evidence.
        """
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        skc_id = _required_text(skc_id, "skc_id")
        values = {
            "workspace_id": workspace_id,
            "batch_id": batch_id,
            "skc_id": skc_id,
            "quote_keys": tuple(_required_text(key, "quote_key") for key in quote_keys),
            "product_title": _text(product_title),
            "main_image_url": _text(main_image_url),
            "official_link_url": _text(official_link_url),
            "site": _text(site),
            "source_confidence": _text(source_confidence),
            "authenticity_status": _text(authenticity_status),
            "sku_prices": tuple(_json_safe(item) for item in sku_prices),
            "original_min": _nullable_text(original_min),
            "original_max": _nullable_text(original_max),
            "adjusted_min": _nullable_text(adjusted_min),
            "adjusted_max": _nullable_text(adjusted_max),
            "max_candidates": max(1, min(int(max_candidates), 100)),
            "now": now,
        }
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_row_in(
                    connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id
                )
                row = connection.execute(
                    """SELECT * FROM price_verification_batch_selections
                    WHERE workspace_id = ? AND batch_id = ? AND skc_id = ?""",
                    (workspace_id, batch_id, skc_id),
                ).fetchone()
                if row is None:
                    connection.execute(
                        """INSERT INTO price_verification_batch_selections
                        (workspace_id, batch_id, skc_id, quote_keys_json, product_title,
                         main_image_url, official_link_url, site, source_confidence,
                         authenticity_status, sku_prices_json, original_min, original_max,
                         adjusted_min, adjusted_max, max_candidates, status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                        (
                            values["workspace_id"], values["batch_id"], values["skc_id"],
                            json.dumps(values["quote_keys"], ensure_ascii=False),
                            values["product_title"], values["main_image_url"],
                            values["official_link_url"], values["site"],
                            values["source_confidence"], values["authenticity_status"],
                            json.dumps(values["sku_prices"], ensure_ascii=False),
                            values["original_min"], values["original_max"],
                            values["adjusted_min"], values["adjusted_max"],
                            values["max_candidates"],
                            values["now"], values["now"],
                        ),
                    )
                else:
                    if row["status"] == "deleted":
                        status = "pending"
                    else:
                        status = row["status"]
                    connection.execute(
                        """UPDATE price_verification_batch_selections
                        SET quote_keys_json = ?, product_title = ?, main_image_url = ?,
                            official_link_url = ?, site = ?, source_confidence = ?,
                            authenticity_status = ?, sku_prices_json = ?, original_min = ?,
                            original_max = ?, adjusted_min = ?, adjusted_max = ?,
                            max_candidates = ?, status = ?, updated_at = ?
                        WHERE workspace_id = ? AND batch_id = ? AND skc_id = ?""",
                        (
                            json.dumps(values["quote_keys"], ensure_ascii=False),
                            values["product_title"], values["main_image_url"],
                            values["official_link_url"], values["site"],
                            values["source_confidence"], values["authenticity_status"],
                            json.dumps(values["sku_prices"], ensure_ascii=False),
                            values["original_min"], values["original_max"],
                            values["adjusted_min"], values["adjusted_max"],
                            values["max_candidates"],
                            status, values["now"],
                            workspace_id, batch_id, skc_id,
                        ),
                    )
                saved = connection.execute(
                    """SELECT * FROM price_verification_batch_selections
                    WHERE workspace_id = ? AND batch_id = ? AND skc_id = ?""",
                    (workspace_id, batch_id, skc_id),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return _batch_selection_record(saved)

    def replace_batch_selection_scope(
        self, *, workspace_id: str, batch_id: str, skc_ids: Sequence[str]
    ) -> None:
        """Keep only the SKCs selected in the latest STEP 02 confirmation."""
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        selected = tuple(dict.fromkeys(_required_text(value, "skc_id") for value in skc_ids))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_row_in(
                    connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id
                )
                if selected:
                    placeholders = ",".join("?" for _ in selected)
                    connection.execute(
                        f"""DELETE FROM price_verification_batch_selections
                        WHERE workspace_id = ? AND batch_id = ? AND skc_id NOT IN ({placeholders})""",
                        (workspace_id, batch_id, *selected),
                    )
                else:
                    connection.execute(
                        """DELETE FROM price_verification_batch_selections
                        WHERE workspace_id = ? AND batch_id = ?""",
                        (workspace_id, batch_id),
                    )
                connection.execute(
                    """DELETE FROM price_verification_batch_sourcing_sessions
                    WHERE workspace_id = ? AND batch_id = ?""",
                    (workspace_id, batch_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def save_batch_sourcing_session(
        self,
        *,
        workspace_id: str,
        batch_id: str,
        selected_skc_ids: Sequence[str],
        unresolved_skc_ids: Sequence[str],
        matched_products: Sequence[Mapping[str, Any]],
        preview: Mapping[str, Any] | None = None,
        selected_candidates: Sequence[Mapping[str, Any]] | None = None,
    ) -> Mapping[str, Any]:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        now = _now()
        selected = [_required_text(value, "skc_id") for value in dict.fromkeys(selected_skc_ids)]
        unresolved = [_required_text(value, "skc_id") for value in dict.fromkeys(unresolved_skc_ids)]
        products = [_json_safe(dict(value)) for value in matched_products]
        preview_json = json.dumps(_json_safe(dict(preview)), ensure_ascii=False) if preview is not None else None
        candidates = [_json_safe(dict(value)) for value in (selected_candidates or [])]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_row_in(connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id)
                connection.execute(
                    """INSERT INTO price_verification_batch_sourcing_sessions
                    (workspace_id, batch_id, selected_skc_ids_json, unresolved_skc_ids_json,
                     matched_products_json, preview_json, selected_candidates_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(workspace_id, batch_id) DO UPDATE SET
                      selected_skc_ids_json = excluded.selected_skc_ids_json,
                      unresolved_skc_ids_json = excluded.unresolved_skc_ids_json,
                      matched_products_json = excluded.matched_products_json,
                      preview_json = excluded.preview_json,
                      selected_candidates_json = excluded.selected_candidates_json,
                      updated_at = excluded.updated_at""",
                    (workspace_id, batch_id, json.dumps(selected, ensure_ascii=False), json.dumps(unresolved, ensure_ascii=False),
                     json.dumps(products, ensure_ascii=False), preview_json, json.dumps(candidates, ensure_ascii=False), now),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_batch_sourcing_session(workspace_id=workspace_id, batch_id=batch_id) or {}

    def get_batch_sourcing_session(self, *, workspace_id: str, batch_id: str) -> Mapping[str, Any] | None:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        with self._connect() as connection:
            self._owned_row_in(connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id)
            row = connection.execute(
                """SELECT * FROM price_verification_batch_sourcing_sessions
                WHERE workspace_id = ? AND batch_id = ?""", (workspace_id, batch_id)
            ).fetchone()
        if row is None:
            return None
        values = dict(row)
        return {
            "selected_skc_ids": _load_json_list(values["selected_skc_ids_json"]),
            "unresolved_skc_ids": _load_json_list(values["unresolved_skc_ids_json"]),
            "matched_products": _load_snapshot_list(values["matched_products_json"]),
            "preview": _load_snapshot(values["preview_json"]) if values.get("preview_json") else None,
            "selected_candidates": _load_snapshot_list(values["selected_candidates_json"]),
            "updated_at": values["updated_at"],
        }

    def clear_batch_sourcing_results(self, *, workspace_id: str, batch_id: str) -> None:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        with self._connect() as connection:
            connection.execute(
                """UPDATE price_verification_batch_sourcing_sessions
                SET unresolved_skc_ids_json = '[]', preview_json = NULL,
                    selected_candidates_json = '[]', updated_at = ?
                WHERE workspace_id = ? AND batch_id = ?""",
                (_now(), workspace_id, batch_id),
            )

    def list_batch_selections(
        self, *, workspace_id: str, batch_id: str, include_deleted: bool = False
    ) -> tuple[BatchSelectionRecord, ...]:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        with self._connect() as connection:
            self._owned_row_in(
                connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id
            )
            if include_deleted:
                rows = connection.execute(
                    """SELECT * FROM price_verification_batch_selections
                    WHERE workspace_id = ? AND batch_id = ? ORDER BY id ASC""",
                    (workspace_id, batch_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM price_verification_batch_selections
                    WHERE workspace_id = ? AND batch_id = ? AND status != 'deleted'
                    ORDER BY id ASC""",
                    (workspace_id, batch_id),
                ).fetchall()
        return tuple(_batch_selection_record(row) for row in rows)

    def get_batch_selection(self, *, workspace_id: str, selection_id: int) -> BatchSelectionRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        selection_id = int(selection_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM price_verification_batch_selections
                WHERE workspace_id = ? AND id = ?""",
                (workspace_id, selection_id),
            ).fetchone()
            if row is None:
                raise PriceVerificationNotFound("resource not found")
        return _batch_selection_record(row)

    def update_batch_selection_review(
        self,
        *,
        workspace_id: str,
        selection_id: int,
        decision: str,
        max_candidates: int,
        now: str,
    ) -> BatchSelectionRecord:
        """Apply the final human decision on one pending SKC review row."""
        workspace_id = _required_text(workspace_id, "workspace_id")
        selection_id = int(selection_id)
        if decision not in {"retained", "deleted"}:
            raise PriceVerificationContractError("decision must be retained or deleted")
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= 100:
            raise PriceVerificationContractError("max_candidates must be an integer between 1 and 100")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """SELECT * FROM price_verification_batch_selections
                    WHERE workspace_id = ? AND id = ?""",
                    (workspace_id, selection_id),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise PriceVerificationNotFound("resource not found")
                connection.execute(
                    """UPDATE price_verification_batch_selections
                    SET status = ?, max_candidates = ?, updated_at = ?
                    WHERE workspace_id = ? AND id = ?""",
                    (decision, max_candidates, now, workspace_id, selection_id),
                )
                saved = connection.execute(
                    """SELECT * FROM price_verification_batch_selections
                    WHERE workspace_id = ? AND id = ?""",
                    (workspace_id, selection_id),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return _batch_selection_record(saved)


    def get_batch_selection_by_skc(
        self, *, workspace_id: str, batch_id: str, skc_id: str
    ) -> BatchSelectionRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        skc_id = _required_text(skc_id, "skc_id")
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM price_verification_batch_selections
                WHERE workspace_id = ? AND batch_id = ? AND skc_id = ?""",
                (workspace_id, batch_id, skc_id),
            ).fetchone()
            if row is None:
                raise PriceVerificationNotFound("resource not found")
        return _batch_selection_record(row)

    def upsert_skc_source_link(
        self,
        *,
        workspace_id: str,
        batch_id: str,
        skc_id: str,
        offer_id: str,
        source_url: str,
        source_title: str,
        main_image_url: str,
        price_cny: str | None,
        weight_kg: str | None,
        moq: str | None,
        domestic_freight_cny: str | None,
        source_decision: str,
        note: str,
        now: str,
        product_title: str = "",
        site: str = "",
        selling_price: str | None = None,
    ) -> SkcSourceLinkRecord:
        """Link one 1688 offer to an SKC, or revive an existing (possibly removed) link.

        The (workspace_id, skc_id, offer_id) unique constraint makes the write
        idempotent: a second link of the same offer reactivates the row instead
        of duplicating it.  ``product_title`` / ``site`` / ``selling_price``
        snapshot the retained Temu context so STEP 04 keeps rendering site and
        profit even after an 覆盖式 re-capture clears the batch selections.
        """
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        skc_id = _required_text(skc_id, "skc_id")
        offer_id = _required_text(offer_id, "offer_id")
        source_url = _required_text(source_url, "source_url")
        product_title = _text(product_title)
        site = _text(site)
        selling_price = _nullable_text(selling_price)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._owned_row_in(
                    connection, "price_verification_quote_capture_batches", workspace_id, "batch_id", batch_id
                )
                row = connection.execute(
                    """SELECT * FROM price_verification_skc_source_links
                    WHERE workspace_id = ? AND skc_id = ? AND offer_id = ?""",
                    (workspace_id, skc_id, offer_id),
                ).fetchone()
                if row is None:
                    connection.execute(
                        """INSERT INTO price_verification_skc_source_links
                        (workspace_id, batch_id, skc_id, offer_id, source_url, source_title,
                         main_image_url, price_cny, weight_kg, moq, domestic_freight_cny, source_decision,
                         note, status, created_at, updated_at, product_title, site, selling_price)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)""",
                        (
                            workspace_id, batch_id, skc_id, offer_id, source_url,
                            source_title, main_image_url, price_cny, weight_kg, moq,
                            domestic_freight_cny, source_decision, note, now, now,
                            product_title, site, selling_price,
                        ),
                    )
                else:
                    connection.execute(
                        """UPDATE price_verification_skc_source_links
                        SET batch_id = ?, source_url = ?, source_title = ?, main_image_url = ?,
                            price_cny = ?, weight_kg = ?, moq = ?, domestic_freight_cny = ?,
                            source_decision = ?, note = ?, status = 'active', updated_at = ?,
                            product_title = ?, site = ?, selling_price = ?
                        WHERE id = ?""",
                        (
                            batch_id, source_url, source_title, main_image_url, price_cny, weight_kg,
                            moq, domestic_freight_cny, source_decision, note, now,
                            product_title, site, selling_price,
                            row["id"],
                        ),
                    )
                saved = connection.execute(
                    """SELECT * FROM price_verification_skc_source_links
                    WHERE workspace_id = ? AND skc_id = ? AND offer_id = ?""",
                    (workspace_id, skc_id, offer_id),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return _skc_source_link_record(saved)

    def list_skc_source_links(
        self, *, workspace_id: str, batch_id: str, skc_id: str | None = None
    ) -> tuple[SkcSourceLinkRecord, ...]:
        workspace_id = _required_text(workspace_id, "workspace_id")
        batch_id = _required_text(batch_id, "batch_id")
        if skc_id is not None:
            skc_id = _required_text(skc_id, "skc_id")
        with self._connect() as connection:
            if skc_id:
                rows = connection.execute(
                    """SELECT * FROM price_verification_skc_source_links
                    WHERE workspace_id = ? AND batch_id = ? AND skc_id = ? AND status = 'active'
                    ORDER BY created_at""",
                    (workspace_id, batch_id, skc_id),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT * FROM price_verification_skc_source_links
                    WHERE workspace_id = ? AND batch_id = ? AND status = 'active'
                    ORDER BY skc_id, created_at""",
                    (workspace_id, batch_id),
                ).fetchall()
        return tuple(_skc_source_link_record(row) for row in rows)

    def list_active_skc_source_links_for_skcs(
        self, *, workspace_id: str, skc_ids: Sequence[str]
    ) -> tuple[SkcSourceLinkRecord, ...]:
        """Return durable 1688 details for the selected SKCs across their batches."""
        workspace_id = _required_text(workspace_id, "workspace_id")
        selected = tuple(dict.fromkeys(_required_text(skc_id, "skc_id") for skc_id in skc_ids))
        if not selected:
            return ()
        placeholders = ",".join("?" for _ in selected)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM price_verification_skc_source_links
                WHERE workspace_id = ? AND status = 'active' AND skc_id IN ({placeholders})
                ORDER BY skc_id, updated_at DESC""",
                (workspace_id, *selected),
            ).fetchall()
        return tuple(_skc_source_link_record(row) for row in rows)

    def active_skc_link_targets(
        self, workspace_id: str | None = None
    ) -> tuple[tuple[str, str, str], ...]:
        """Distinct (workspace_id, batch_id, skc_id) triples with active source links.

        Used to backfill the product library for already-associated SKCs.
        """
        with self._connect() as connection:
            if workspace_id:
                rows = connection.execute(
                    """SELECT DISTINCT workspace_id, batch_id, skc_id
                    FROM price_verification_skc_source_links
                    WHERE workspace_id = ? AND status = 'active'""",
                    (workspace_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """SELECT DISTINCT workspace_id, batch_id, skc_id
                    FROM price_verification_skc_source_links
                    WHERE status = 'active'""",
                ).fetchall()
        return tuple((str(row[0]), str(row[1]), str(row[2])) for row in rows)

    def soft_remove_skc_source_link(
        self, *, workspace_id: str, link_id: int, now: str
    ) -> SkcSourceLinkRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        link_id = int(link_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """SELECT * FROM price_verification_skc_source_links
                    WHERE workspace_id = ? AND id = ?""",
                    (workspace_id, link_id),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise PriceVerificationNotFound("resource not found")
                if row["status"] != "removed":
                    connection.execute(
                        """UPDATE price_verification_skc_source_links
                        SET status = 'removed', updated_at = ?
                        WHERE id = ?""",
                        (now, link_id),
                    )
                saved = connection.execute(
                    """SELECT * FROM price_verification_skc_source_links
                    WHERE workspace_id = ? AND id = ?""",
                    (workspace_id, link_id),
                ).fetchone()
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return _skc_source_link_record(saved)

    def soft_remove_skc_source_links_for_skc(
        self, *, workspace_id: str, skc_id: str, now: str
    ) -> int:
        """Deactivate every durable 1688 association for one deleted product SKC."""
        workspace_id = _required_text(workspace_id, "workspace_id")
        skc_id = _required_text(skc_id, "skc_id")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(
                    """UPDATE price_verification_skc_source_links
                    SET status = 'removed', updated_at = ?
                    WHERE workspace_id = ? AND skc_id = ? AND status = 'active'""",
                    (now, workspace_id, skc_id),
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return max(0, int(cursor.rowcount))


    def record_quote_decision(
        self,
        *,
        workspace_id: str,
        quote_run_id: str,
        quote_key: str,
        decision: str,
        decided_by: str,
        note: str = "",
    ) -> QuoteDecisionRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        quote_run_id = _required_text(quote_run_id, "quote_run_id")
        quote_key = _required_text(quote_key, "quote_key")
        decided_by = _required_text(decided_by, "decided_by")
        if decision not in {"retained", "rejected"}:
            raise PriceVerificationContractError("decision must be retained or rejected")
        if not isinstance(note, str):
            raise PriceVerificationContractError("note must be text")
        now = _now()
        decision_id = _new_id()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            quote = connection.execute(
                """SELECT 1 FROM price_verification_quote_items
                WHERE workspace_id = ? AND run_id = ? AND quote_key = ?""",
                (workspace_id, quote_run_id, quote_key),
            ).fetchone()
            if quote is None:
                connection.rollback()
                raise PriceVerificationNotFound("resource not found")
            row = connection.execute(
                """SELECT COALESCE(MAX(revision), 0) AS revision
                FROM price_verification_quote_decisions
                WHERE workspace_id = ? AND quote_run_id = ? AND quote_key = ?""",
                (workspace_id, quote_run_id, quote_key),
            ).fetchone()
            revision = int(row["revision"]) + 1
            connection.execute(
                """INSERT INTO price_verification_quote_decisions
                (decision_id, workspace_id, quote_run_id, quote_key, decision,
                 decided_by, note, revision, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    workspace_id,
                    quote_run_id,
                    quote_key,
                    decision,
                    decided_by,
                    note.strip(),
                    revision,
                    now,
                ),
            )
            record = connection.execute(
                "SELECT * FROM price_verification_quote_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            connection.commit()
        return QuoteDecisionRecord(**dict(record))

    def list_current_quote_decisions(
        self, *, workspace_id: str, quote_run_id: str
    ) -> tuple[QuoteDecisionRecord, ...]:
        workspace_id = _required_text(workspace_id, "workspace_id")
        quote_run_id = _required_text(quote_run_id, "quote_run_id")
        with self._connect() as connection:
            self._owned_row_in(
                connection,
                "price_verification_quote_runs",
                workspace_id,
                "run_id",
                quote_run_id,
            )
            rows = connection.execute(
                """SELECT d.* FROM price_verification_quote_decisions d
                WHERE d.workspace_id = ? AND d.quote_run_id = ?
                  AND d.revision = (
                    SELECT MAX(current.revision)
                    FROM price_verification_quote_decisions current
                    WHERE current.workspace_id = d.workspace_id
                      AND current.quote_run_id = d.quote_run_id
                      AND current.quote_key = d.quote_key
                  )
                ORDER BY d.quote_key""",
                (workspace_id, quote_run_id),
            ).fetchall()
        return tuple(QuoteDecisionRecord(**dict(row)) for row in rows)

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
        source_quotes: Sequence[Mapping[str, Any]] = (),
    ) -> SourcingRunRecord:
        workspace_id = _required_text(workspace_id, "workspace_id")
        quote_run_id = _required_text(quote_run_id, "quote_run_id")
        source_mode = _required_text(source_mode, "source_mode")
        status = _required_text(status, "status")
        snapshots = _source_candidate_rows(candidates)
        frozen_quotes = _source_quote_rows(source_quotes)
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
                connection.executemany(
                    """INSERT INTO price_verification_sourcing_run_quotes
                    (workspace_id, sourcing_run_id, quote_run_id, quote_key,
                     official_link_url, main_image_url, selected_price_cny,
                     snapshot_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        (
                            workspace_id,
                            run_id,
                            quote_run_id,
                            quote_key,
                            official_link_url,
                            main_image_url,
                            selected_price_cny,
                            serialized,
                            now,
                        )
                        for (
                            quote_key,
                            official_link_url,
                            main_image_url,
                            selected_price_cny,
                            serialized,
                        ) in frozen_quotes
                    ],
                )
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return self.get_sourcing_run(workspace_id=workspace_id, run_id=run_id)

    def list_sourcing_run_quotes(
        self, *, workspace_id: str, sourcing_run_id: str
    ) -> tuple[SourcingRunQuoteRecord, ...]:
        workspace_id = _required_text(workspace_id, "workspace_id")
        sourcing_run_id = _required_text(sourcing_run_id, "sourcing_run_id")
        with self._connect() as connection:
            self._owned_row_in(
                connection,
                "price_verification_sourcing_runs",
                workspace_id,
                "run_id",
                sourcing_run_id,
            )
            rows = connection.execute(
                """SELECT * FROM price_verification_sourcing_run_quotes
                WHERE workspace_id = ? AND sourcing_run_id = ?
                ORDER BY quote_key""",
                (workspace_id, sourcing_run_id),
            ).fetchall()
        records: list[SourcingRunQuoteRecord] = []
        for row in rows:
            values = dict(row)
            values["snapshot"] = _load_snapshot(values.pop("snapshot_json"))
            records.append(SourcingRunQuoteRecord(**values))
        return tuple(records)

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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """Add a column only when missing; SQLite lacks ``ADD COLUMN IF NOT EXISTS``."""
    existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _new_id() -> str:
    return uuid.uuid4().hex


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _nullable_text(value: object) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return str(value).strip()


def _json_safe(value: object) -> object:
    """Coerce JSON-unfriendly scalars (Decimal) into strings for persistence."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if value is None:
        return None
    return str(value)


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


def _capture_skc_group_count(snapshots: Sequence[tuple[str, str]]) -> int:
    """Count capture-page groups by SKC, keeping ungrouped rows conservative."""
    groups: set[str] = set()
    for quote_key, serialized in snapshots:
        try:
            item = json.loads(serialized)
        except json.JSONDecodeError as error:  # pragma: no cover - guarded by safe_json_dumps
            raise PriceVerificationContractError("invalid quote snapshot") from error
        skc_id = str(item.get("skc_id") or "").strip() if isinstance(item, Mapping) else ""
        groups.add(f"skc:{skc_id}" if skc_id else f"row:{quote_key}")
    return len(groups)


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


def _source_quote_rows(
    source_quotes: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, str, str, str, str], ...]:
    rows: list[tuple[str, str, str, str, str]] = []
    seen: set[str] = set()
    for source_quote in source_quotes:
        if not isinstance(source_quote, Mapping):
            raise TypeError("source quote entries must be mappings")
        quote_key = _required_text(source_quote.get("quote_key"), "quote_key")
        if quote_key in seen:
            raise ValueError("quote_key values must be unique within a sourcing run")
        seen.add(quote_key)
        official_link_url = _required_text(
            source_quote.get("official_link_url"), "official_link_url"
        )
        main_image_url = _required_text(source_quote.get("main_image_url"), "main_image_url")
        selected_price_cny = _required_text(
            source_quote.get("selected_price_cny"), "selected_price_cny"
        )
        rows.append(
            (
                quote_key,
                official_link_url,
                main_image_url,
                selected_price_cny,
                safe_json_dumps(source_quote),
            )
        )
    return tuple(rows)


def _load_snapshot(value: str) -> Mapping[str, Any]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, Mapping) else {}


def _load_snapshot_list(value: str) -> tuple[Mapping[str, Any], ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return ()
    return tuple(item for item in parsed if isinstance(item, Mapping))


def _capture_batch_record(row: sqlite3.Row) -> QuoteCaptureBatchRecord:
    values = dict(row)
    values["is_current"] = bool(values["is_current"])
    return QuoteCaptureBatchRecord(**values)


def _batch_selection_record(row: sqlite3.Row) -> BatchSelectionRecord:
    values = dict(row)
    values["quote_keys"] = _load_json_list(values.pop("quote_keys_json"))
    values["sku_prices"] = _load_snapshot_list(values.pop("sku_prices_json"))
    return BatchSelectionRecord(**values)


def _skc_source_link_record(row: sqlite3.Row) -> SkcSourceLinkRecord:
    return SkcSourceLinkRecord(**dict(row))


def _load_json_list(value: str) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed if isinstance(item, str))


def _capture_chunk_record(row: sqlite3.Row) -> QuoteCaptureChunkRecord:
    values = dict(row)
    values["capture"] = _load_snapshot(values.pop("capture_json"))
    values["items"] = _load_snapshot_list(values.pop("items_json"))
    return QuoteCaptureChunkRecord(**values)


def _session_record(row: sqlite3.Row) -> PluginSessionRecord:
    values = dict(row)
    values["capabilities"] = _load_snapshot(values.pop("capabilities_json"))
    return PluginSessionRecord(**values)


def _command_record(row: sqlite3.Row) -> PluginCommandRecord:
    values = dict(row)
    values["payload"] = _load_snapshot(values.pop("payload_json"))
    values["result"] = _load_snapshot(values.pop("result_json"))
    return PluginCommandRecord(**values)
