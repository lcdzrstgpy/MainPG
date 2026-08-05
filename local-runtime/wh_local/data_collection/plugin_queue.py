"""Minimal browser-plugin command queue owned solely by data collection.

It intentionally mirrors the useful Demo flow (queue, poll, result) without
depending on that application's users, sessions, or business tables.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .normalizer import sanitize_raw_payload


TEMU_LINK_CAPTURE = "temu_link_capture"
_TERMINAL = frozenset({"succeeded", "failed"})
_ACTIVE_WINDOW = timedelta(minutes=10)


class PluginCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: int
    command_type: str
    payload: Mapping[str, Any]
    status: str
    result: Mapping[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class DataCollectionPluginQueue:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._initialize()

    def create_session(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        capabilities: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO data_collection_plugin_sessions
                (actor_id, workspace_id, session_token, capabilities_json, status, created_at, last_seen_at)
                VALUES (?, ?, ?, ?, 'connected', ?, ?)""",
                (actor_id, workspace_id, token, _dump(capabilities or {}), now, now),
            )
        return {"session_id": int(cur.lastrowid), "session_token": token, "status": "connected"}

    def queue_temu_link(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        session_id: int,
        source_url: str,
    ) -> PluginCommand:
        if not _is_temu_product_url(source_url):
            raise ValueError("source_url must be a public temu.com product URL")
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT actor_id, workspace_id, last_seen_at "
                "FROM data_collection_plugin_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None or row["actor_id"] != actor_id or row["workspace_id"] != workspace_id:
                raise PermissionError("plugin session not found")
            if not _active(row["last_seen_at"]):
                raise ValueError("plugin session is offline")
            cur = conn.execute(
                """INSERT INTO data_collection_plugin_commands
                (session_id, command_type, payload_json, status, result_json, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', '{}', ?, ?)""",
                (session_id, TEMU_LINK_CAPTURE, _dump({"source_url": source_url}), now, now),
            )
            return self._command(conn, int(cur.lastrowid))

    def poll(self, session_token: str, *, limit: int = 10) -> tuple[PluginCommand, ...]:
        now = _now()
        with self._connect() as conn:
            session = self._session(conn, session_token)
            rows = conn.execute(
                """SELECT id FROM data_collection_plugin_commands
                WHERE session_id = ? AND status = 'queued' ORDER BY id LIMIT ?""",
                (session["id"], max(1, min(limit, 50))),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                marks = ",".join("?" for _ in ids)
                conn.execute(f"UPDATE data_collection_plugin_commands SET status = 'sent', updated_at = ? WHERE id IN ({marks})", (now, *ids))
            conn.execute("UPDATE data_collection_plugin_sessions SET last_seen_at = ?, status = 'connected' WHERE id = ?", (now, session["id"]))
            return tuple(self._command(conn, command_id) for command_id in ids)

    def receive_result(
        self,
        *,
        session_token: str,
        command_id: int,
        status: str,
        result: Mapping[str, Any],
    ) -> PluginCommand:
        if status not in {"running", *tuple(_TERMINAL)}:
            raise ValueError("unsupported command status")
        now = _now()
        with self._connect() as conn:
            session = self._session(conn, session_token)
            command = conn.execute(
                "SELECT status FROM data_collection_plugin_commands "
                "WHERE id = ? AND session_id = ?",
                (command_id, session["id"]),
            ).fetchone()
            if command is None:
                raise PermissionError("plugin command not found")
            if command["status"] in _TERMINAL:
                return self._command(conn, command_id)
            conn.execute(
                "UPDATE data_collection_plugin_commands SET status = ?, result_json = ?, updated_at = ? WHERE id = ?",
                (status, _dump(result), now, command_id),
            )
            conn.execute("UPDATE data_collection_plugin_sessions SET last_seen_at = ?, status = 'connected' WHERE id = ?", (now, session["id"]))
            return self._command(conn, command_id)

    def get_command(self, *, actor_id: str, workspace_id: str, command_id: int) -> PluginCommand:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT c.id FROM data_collection_plugin_commands c
                JOIN data_collection_plugin_sessions s ON s.id = c.session_id
                WHERE c.id = ? AND s.actor_id = ? AND s.workspace_id = ?""",
                (command_id, actor_id, workspace_id),
            ).fetchone()
            if row is None:
                raise PermissionError("plugin command not found")
            return self._command(conn, int(row["id"]))

    def _initialize(self) -> None:
        migration = Path(__file__).with_name("migrations") / "002_data_collection_plugin_queue.sql"
        with self._connect() as conn:
            conn.executescript(migration.read_text(encoding="utf-8"))

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._database_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _session(conn: sqlite3.Connection, token: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT id, last_seen_at FROM data_collection_plugin_sessions "
            "WHERE session_token = ?",
            (token,),
        ).fetchone()
        if row is None:
            raise PermissionError("invalid plugin session")
        return row

    @staticmethod
    def _command(conn: sqlite3.Connection, command_id: int) -> PluginCommand:
        row = conn.execute(
            "SELECT id, command_type, payload_json, status, result_json, created_at, updated_at "
            "FROM data_collection_plugin_commands WHERE id = ?",
            (command_id,),
        ).fetchone()
        if row is None:
            raise ValueError("plugin command not found")
        return PluginCommand(
            command_id=row["id"],
            command_type=row["command_type"],
            payload=_load(row["payload_json"]),
            status=row["status"],
            result=_load(row["result_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _active(value: str) -> bool:
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(value) <= _ACTIVE_WINDOW
    except ValueError:
        return False


def _dump(value: Mapping[str, Any]) -> str:
    safe = sanitize_raw_payload(value)
    return json.dumps(safe, ensure_ascii=False, separators=(",", ":"))


def _load(value: str) -> Mapping[str, Any]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, Mapping) else {}


def _is_temu_product_url(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").casefold()
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.path)
        and (hostname == "temu.com" or hostname.endswith(".temu.com"))
    )
