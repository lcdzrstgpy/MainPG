"""Transactional SQLite persistence for daily-selection runs and handoffs."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .contracts import (
    DailySelectionCandidate,
    is_sensitive_field,
    redact_sensitive_text,
)
from .handoff import (
    DailySelectionHandoff,
    build_handoff_payload,
    handoff_idempotency_key,
)


_JSON_VALUE = TypeAdapter(Any)


class DailySelectionRunNotFound(PermissionError):
    """Raised without revealing whether a run belongs to another workspace."""


class DailySelectionCandidateNotConfirmable(ValueError):
    """Raised when a candidate is not eligible for downstream processing."""

    def __init__(self, reasons: Mapping[str, str]) -> None:
        self.reasons = dict(reasons)
        super().__init__("one or more candidates cannot be confirmed")


class DailySelectionRunSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    workspace_id: str
    status: str
    candidate_count: int
    created_at: str
    updated_at: str


class DailySelectionRun(DailySelectionRunSummary):
    criteria: Mapping[str, Any] = Field(default_factory=dict)
    metadata: Mapping[str, Any] = Field(default_factory=dict)
    candidates: tuple[DailySelectionCandidate, ...] = ()


class DailySelectionFeedback(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    feedback_id: str
    workspace_id: str
    run_id: str
    candidate_id: str
    reason: str
    details: Mapping[str, Any] = Field(default_factory=dict)
    created_at: str


class DailySelectionRepository:
    """Own the module's five tables without depending on a host application."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        self._database_uri = self._database_path == ":memory:"
        self._connect_target = (
            f"file:daily-selection-{uuid.uuid4().hex}?mode=memory&cache=shared"
            if self._database_uri
            else self._database_path
        )
        self._keeper_connection = self._new_connection() if self._database_uri else None
        self._initialize()

    @property
    def database_path(self) -> str:
        return self._database_path

    def close(self) -> None:
        if self._keeper_connection is not None:
            self._keeper_connection.close()
            self._keeper_connection = None

    def save_run(
        self,
        *,
        workspace_id: str,
        run_id: str,
        status: str,
        candidates: Sequence[DailySelectionCandidate],
        criteria: Mapping[str, Any] | BaseModel | None = None,
        metadata: Mapping[str, Any] | BaseModel | None = None,
        created_at: str | None = None,
    ) -> DailySelectionRun:
        workspace_id = _required_text(workspace_id, "workspace_id")
        run_id = _required_text(run_id, "run_id")
        status = _required_text(status, "status")
        created = created_at or _now()
        candidate_values = tuple(candidates)
        if any(not isinstance(item, DailySelectionCandidate) for item in candidate_values):
            raise TypeError("candidates must contain DailySelectionCandidate values")
        candidate_ids = [item.candidate_id for item in candidate_values]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate_id values must be unique within a run")
        criteria_json = _dump_json(criteria or {})
        metadata_json = _dump_json(metadata or {})

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT 1 FROM daily_selection_runs
                WHERE workspace_id = ? AND run_id = ?
                """,
                (workspace_id, run_id),
            ).fetchone()
            if existing is not None:
                dependent_count = connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM daily_selection_feedback
                         WHERE workspace_id = ? AND run_id = ?)
                      + (SELECT COUNT(*) FROM daily_selection_handoffs
                         WHERE workspace_id = ? AND run_id = ?)
                    """,
                    (workspace_id, run_id, workspace_id, run_id),
                ).fetchone()[0]
                if dependent_count:
                    raise ValueError(
                        "cannot replace a run that already has feedback or handoffs"
                    )
                connection.execute(
                    """
                    DELETE FROM daily_selection_candidates
                    WHERE workspace_id = ? AND run_id = ?
                    """,
                    (workspace_id, run_id),
                )
            connection.execute(
                """
                INSERT INTO daily_selection_runs
                    (workspace_id, run_id, status, criteria_json, metadata_json,
                     candidate_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (workspace_id, run_id) DO UPDATE SET
                    status = excluded.status,
                    criteria_json = excluded.criteria_json,
                    metadata_json = excluded.metadata_json,
                    candidate_count = excluded.candidate_count,
                    updated_at = excluded.updated_at
                """,
                (
                    workspace_id,
                    run_id,
                    status,
                    criteria_json,
                    metadata_json,
                    len(candidate_values),
                    created,
                    created,
                ),
            )
            for item in candidate_values:
                self._upsert_candidate(
                    connection,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    candidate=item,
                    timestamp=created,
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.get_run(workspace_id=workspace_id, run_id=run_id)

    def list_runs(
        self, *, workspace_id: str, limit: int = 20, offset: int = 0
    ) -> tuple[DailySelectionRunSummary, ...]:
        workspace_id = _required_text(workspace_id, "workspace_id")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            rows = connection.execute(
                """
                SELECT run_id, workspace_id, status, candidate_count, created_at, updated_at
                FROM daily_selection_runs
                WHERE workspace_id = ?
                ORDER BY created_at DESC, run_id DESC
                LIMIT ? OFFSET ?
                """,
                (workspace_id, limit, offset),
            ).fetchall()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return tuple(DailySelectionRunSummary(**dict(row)) for row in rows)

    def get_run(self, *, workspace_id: str, run_id: str) -> DailySelectionRun:
        workspace_id = _required_text(workspace_id, "workspace_id")
        run_id = _required_text(run_id, "run_id")
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            row = self._owned_run(connection, workspace_id=workspace_id, run_id=run_id)
            candidate_rows = connection.execute(
                """
                SELECT raw_candidate_json
                FROM daily_selection_candidates
                WHERE workspace_id = ? AND run_id = ?
                ORDER BY rowid
                """,
                (workspace_id, run_id),
            ).fetchall()
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return DailySelectionRun(
            run_id=row["run_id"],
            workspace_id=row["workspace_id"],
            status=row["status"],
            candidate_count=row["candidate_count"],
            criteria=_load_json(row["criteria_json"]),
            metadata=_load_json(row["metadata_json"]),
            candidates=tuple(
                _load_candidate(item["raw_candidate_json"])
                for item in candidate_rows
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def record_feedback(
        self,
        *,
        workspace_id: str,
        run_id: str,
        candidate_id: str,
        reason: str,
        details: Mapping[str, Any] | BaseModel | None = None,
        created_at: str | None = None,
    ) -> DailySelectionFeedback:
        workspace_id = _required_text(workspace_id, "workspace_id")
        run_id = _required_text(run_id, "run_id")
        candidate_id = _required_text(candidate_id, "candidate_id")
        reason = _required_text(reason, "reason")
        timestamp = created_at or _now()
        feedback_id = str(uuid.uuid4())
        details_json = _dump_json(details or {})
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_run(connection, workspace_id=workspace_id, run_id=run_id)
            candidate = _load_candidate(
                self._candidate_json(
                    connection,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    candidate_id=candidate_id,
                )
            )
            self._upsert_candidate(
                connection,
                workspace_id=workspace_id,
                run_id=run_id,
                candidate=candidate.model_copy(update={"status": "rejected"}),
                timestamp=timestamp,
            )
            connection.execute(
                """
                INSERT INTO daily_selection_feedback
                    (feedback_id, workspace_id, run_id, candidate_id, reason,
                     details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    feedback_id,
                    workspace_id,
                    run_id,
                    candidate_id,
                    reason,
                    details_json,
                    timestamp,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return DailySelectionFeedback(
            feedback_id=feedback_id,
            workspace_id=workspace_id,
            run_id=run_id,
            candidate_id=candidate_id,
            reason=reason,
            details=_load_json(details_json),
            created_at=timestamp,
        )

    def confirm_candidates(
        self,
        *,
        workspace_id: str,
        run_id: str,
        candidate_ids: Iterable[str],
        created_at: str | None = None,
    ) -> tuple[DailySelectionHandoff, ...]:
        workspace_id = _required_text(workspace_id, "workspace_id")
        run_id = _required_text(run_id, "run_id")
        normalized_ids = tuple(
            dict.fromkeys(_required_text(item, "candidate_id") for item in candidate_ids)
        )
        if not normalized_ids:
            raise ValueError("candidate_ids must not be empty")
        timestamp = created_at or _now()
        connection = self._connect()
        handoffs: list[DailySelectionHandoff] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._owned_run(connection, workspace_id=workspace_id, run_id=run_id)
            candidates = {
                candidate_id: _load_candidate(
                    self._candidate_json(
                        connection,
                        workspace_id=workspace_id,
                        run_id=run_id,
                        candidate_id=candidate_id,
                    )
                )
                for candidate_id in normalized_ids
            }
            non_confirmable: dict[str, str] = {}
            for candidate_id, candidate in candidates.items():
                handoff_exists = connection.execute(
                    """
                    SELECT 1
                    FROM daily_selection_handoffs
                    WHERE workspace_id = ? AND run_id = ? AND candidate_id = ?
                    """,
                    (workspace_id, run_id, candidate_id),
                ).fetchone() is not None
                if candidate.risk_tags:
                    non_confirmable[candidate_id] = "candidate has risk tags"
                elif candidate.status == "candidate":
                    continue
                elif candidate.status == "confirmed" and handoff_exists:
                    # Confirmation is intentionally replay-safe.  A retry can
                    # resume a failed downstream consume without another user
                    # action, but cannot confirm an arbitrary stale record.
                    continue
                else:
                    non_confirmable[candidate_id] = (
                        f"candidate status {candidate.status!r} is not confirmable"
                    )
            if non_confirmable:
                raise DailySelectionCandidateNotConfirmable(non_confirmable)
            for candidate_id in normalized_ids:
                confirmed = candidates[candidate_id].model_copy(update={"status": "confirmed"})
                self._upsert_candidate(
                    connection,
                    workspace_id=workspace_id,
                    run_id=run_id,
                    candidate=confirmed,
                    timestamp=timestamp,
                )
                idempotency_key = handoff_idempotency_key(
                    workspace_id=workspace_id,
                    run_id=run_id,
                    candidate_id=candidate_id,
                )
                handoff_id = str(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))
                connection.execute(
                    """
                    INSERT OR IGNORE INTO daily_selection_handoffs
                        (handoff_id, run_id, candidate_id, workspace_id,
                         payload_json, status, idempotency_key, created_at)
                    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        handoff_id,
                        run_id,
                        candidate_id,
                        workspace_id,
                        build_handoff_payload(confirmed),
                        idempotency_key,
                        timestamp,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT handoff_id, run_id, candidate_id, workspace_id,
                           payload_json, status, idempotency_key, created_at
                    FROM daily_selection_handoffs
                    WHERE workspace_id = ? AND run_id = ? AND candidate_id = ?
                    """,
                    (workspace_id, run_id, candidate_id),
                ).fetchone()
                handoffs.append(DailySelectionHandoff(**dict(row)))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return tuple(handoffs)

    def mark_handoffs_consumed(
        self,
        *,
        workspace_id: str,
        handoff_ids: Iterable[str],
    ) -> tuple[DailySelectionHandoff, ...]:
        """Acknowledge successful internal draft creation without exposing an ACK API."""
        workspace_id = _required_text(workspace_id, "workspace_id")
        normalized_ids = tuple(
            dict.fromkeys(_required_text(item, "handoff_id") for item in handoff_ids)
        )
        if not normalized_ids:
            raise ValueError("handoff_ids must not be empty")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            handoffs: list[DailySelectionHandoff] = []
            for handoff_id in normalized_ids:
                row = connection.execute(
                    """
                    SELECT handoff_id, run_id, candidate_id, workspace_id,
                           payload_json, status, idempotency_key, created_at
                    FROM daily_selection_handoffs
                    WHERE workspace_id = ? AND handoff_id = ?
                    """,
                    (workspace_id, handoff_id),
                ).fetchone()
                if row is None:
                    raise DailySelectionRunNotFound("daily-selection handoff not found")
                if row["status"] == "failed":
                    raise ValueError("failed handoffs cannot be acknowledged as consumed")
                if row["status"] == "pending":
                    connection.execute(
                        """
                        UPDATE daily_selection_handoffs
                        SET status = 'consumed'
                        WHERE workspace_id = ? AND handoff_id = ? AND status = 'pending'
                        """,
                        (workspace_id, handoff_id),
                    )
                    row = connection.execute(
                        """
                        SELECT handoff_id, run_id, candidate_id, workspace_id,
                               payload_json, status, idempotency_key, created_at
                        FROM daily_selection_handoffs
                        WHERE workspace_id = ? AND handoff_id = ?
                        """,
                        (workspace_id, handoff_id),
                    ).fetchone()
                handoffs.append(DailySelectionHandoff(**dict(row)))
            connection.commit()
            return tuple(handoffs)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        migration = (
            Path(__file__).with_name("migrations") / "001_daily_selection.sql"
        ).read_text(encoding="utf-8")
        connection = self._connect()
        try:
            connection.executescript(migration)
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        return self._new_connection()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._connect_target,
            timeout=10,
            isolation_level=None,
            uri=self._database_uri,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _owned_run(
        connection: sqlite3.Connection, *, workspace_id: str, run_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT run_id, workspace_id, status, criteria_json, metadata_json,
                   candidate_count, created_at, updated_at
            FROM daily_selection_runs
            WHERE workspace_id = ? AND run_id = ?
            """,
            (workspace_id, run_id),
        ).fetchone()
        if row is None:
            raise DailySelectionRunNotFound("daily-selection run not found")
        return row

    @staticmethod
    def _candidate_json(
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
        run_id: str,
        candidate_id: str,
    ) -> str:
        row = connection.execute(
            """
            SELECT raw_candidate_json
            FROM daily_selection_candidates
            WHERE workspace_id = ? AND run_id = ? AND candidate_id = ?
            """,
            (workspace_id, run_id, candidate_id),
        ).fetchone()
        if row is None:
            raise ValueError("candidate does not belong to the selected run")
        return str(row["raw_candidate_json"])

    @staticmethod
    def _upsert_candidate(
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
        run_id: str,
        candidate: DailySelectionCandidate,
        timestamp: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO daily_selection_candidates
                (workspace_id, run_id, candidate_id, offer_id, source_platform,
                 source_url, source_title, main_image_url, price_cny,
                 selection_score, status, raw_candidate_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (workspace_id, run_id, candidate_id) DO UPDATE SET
                offer_id = excluded.offer_id,
                source_platform = excluded.source_platform,
                source_url = excluded.source_url,
                source_title = excluded.source_title,
                main_image_url = excluded.main_image_url,
                price_cny = excluded.price_cny,
                selection_score = excluded.selection_score,
                status = excluded.status,
                raw_candidate_json = excluded.raw_candidate_json,
                updated_at = excluded.updated_at
            """,
            (
                workspace_id,
                run_id,
                candidate.candidate_id,
                candidate.offer_id,
                candidate.source_platform,
                candidate.source_url,
                candidate.source_title,
                candidate.main_image_url,
                None if candidate.price_cny is None else str(candidate.price_cny),
                str(candidate.selection_score),
                candidate.status,
                _dump_candidate(candidate),
                timestamp,
                timestamp,
            ),
        )


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    return _JSON_VALUE.dump_json(_safe_json_value(value)).decode("utf-8")


def _load_json(value: str) -> Any:
    return _JSON_VALUE.validate_json(value)


_DECIMAL_PATHS = "__daily_selection_decimal_paths__"


def _dump_candidate(candidate: DailySelectionCandidate) -> str:
    decimal_paths: list[list[str | int]] = []
    data = _encode_candidate_value(
        candidate.model_dump(mode="python"),
        path=[],
        decimal_paths=decimal_paths,
    )
    data[_DECIMAL_PATHS] = decimal_paths
    return _JSON_VALUE.dump_json(data).decode("utf-8")


def _load_candidate(value: str) -> DailySelectionCandidate:
    data = _load_json(value)
    decimal_paths = data.pop(_DECIMAL_PATHS, [])
    for path in decimal_paths:
        parent = data
        for token in path[:-1]:
            parent = parent[token]
        parent[path[-1]] = Decimal(parent[path[-1]])
    return DailySelectionCandidate.model_validate(data)


def _encode_candidate_value(
    value: Any,
    *,
    path: list[str | int],
    decimal_paths: list[list[str | int]],
) -> Any:
    if isinstance(value, Decimal):
        decimal_paths.append(path)
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _encode_candidate_value(
                item,
                path=[*path, str(key)],
                decimal_paths=decimal_paths,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _encode_candidate_value(
                item,
                path=[*path, index],
                decimal_paths=decimal_paths,
            )
            for index, item in enumerate(value)
        ]
    return value


def _safe_json_value(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("repository JSON cannot contain binary data")
    if isinstance(value, Mapping):
        return {
            str(key): _safe_json_value(item)
            for key, item in value.items()
            if not is_sensitive_field(key)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_json_value(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise ValueError("repository JSON numbers must be finite")
    if isinstance(value, Decimal):
        if value.is_finite():
            return value
        raise ValueError("repository JSON numbers must be finite")
    raise TypeError("repository values must be JSON serializable")
