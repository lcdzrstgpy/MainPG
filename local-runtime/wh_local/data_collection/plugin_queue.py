"""Minimal browser-plugin command queue owned solely by data collection.

It intentionally mirrors the useful Demo flow (queue, poll, result) without
depending on that application's users, sessions, or business tables.
"""

from __future__ import annotations

import hashlib
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
TEMU_PRICE_QUOTE_DISCOVERY = "temu_price_quote_discovery"
SOURCE_BROWSER_IMAGE_SEARCH = "source_browser_image_search"
TEMU_FLUX_ACCEL = "temu_flux_accel"
TEMU_ORDER_PROFIT_CAPTURE = "temu_order_profit_capture"
TEMU_CAPTURE_ALL = "temu_capture_all"
TEMU_CAPTURE_INCREMENTAL = "temu_capture_incremental"
TEMU_CAPTURE_RESOURCE = "temu_capture_resource"
TEMU_ACCOUNT_LOGIN = "temu_account_login"
ALLOWED_PLUGIN_COMMAND_TYPES = frozenset(
    {
        TEMU_LINK_CAPTURE,
        TEMU_PRICE_QUOTE_DISCOVERY,
        SOURCE_BROWSER_IMAGE_SEARCH,
        TEMU_FLUX_ACCEL,
        TEMU_ORDER_PROFIT_CAPTURE,
        TEMU_CAPTURE_ALL,
        TEMU_CAPTURE_INCREMENTAL,
        TEMU_CAPTURE_RESOURCE,
        TEMU_ACCOUNT_LOGIN,
    }
)
_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
_ACTIVE_WINDOW = timedelta(minutes=10)
_COMMAND_LEASE_WINDOW = timedelta(minutes=15)

# 插件重新连接会拿到新的 session id。旧会话已经停止轮询超过这个时间，
# 就认为它不会再执行队列里的指令，允许把排队中的指令改派给新会话。
_SESSION_STALE_WINDOW = timedelta(seconds=30)
# 下发指令时如果目标会话超过这个时间没轮询（例如插件刚重载），
# 而同账号下已有更新会话在轮询，就直接派给新会话，避免停在「排队中」。
_FRESH_SESSION_WINDOW = timedelta(seconds=10)
_LEGACY_LOCAL_ACTOR_ID = "local-demo-admin"
_LEGACY_LOCAL_WORKSPACE_ID = "default"


class PluginCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command_id: int
    command_type: str
    idempotency_key: str = ""
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
        device_id: str = "",
    ) -> dict[str, Any]:
        now = _now()
        token = secrets.token_urlsafe(32)
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO data_collection_plugin_sessions
                (actor_id, workspace_id, session_token, capabilities_json, status, created_at, last_seen_at, device_id)
                VALUES (?, ?, ?, ?, 'connected', ?, ?, ?)""",
                (actor_id, workspace_id, token, _dump(capabilities or {}), now, now, device_id),
            )
        return {"session_id": int(cur.lastrowid), "session_token": token, "status": "connected"}

    # ---------- 插件设备配对（不依赖工作台页面常驻） ----------

    def create_pair_code(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        ttl_seconds: int = 600,
    ) -> dict[str, Any]:
        """生成一次性配对码，供插件换长期设备凭证。"""
        now = datetime.now(timezone.utc)
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires_at = (now + timedelta(seconds=max(60, int(ttl_seconds)))).isoformat()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM data_collection_plugin_pair_codes WHERE expires_at < ?",
                (now.isoformat(),),
            )
            conn.execute(
                "INSERT OR REPLACE INTO data_collection_plugin_pair_codes "
                "(code, actor_id, workspace_id, expires_at, created_at, used_at) "
                "VALUES (?, ?, ?, ?, ?, '')",
                (code, actor_id, workspace_id, expires_at, now.isoformat()),
            )
        return {"code": code, "expires_at": expires_at, "ttl_seconds": ttl_seconds}

    def pair_device(
        self,
        *,
        code: str,
        name: str = "",
        browser: str = "",
        extension_version: str = "",
    ) -> dict[str, Any]:
        """用配对码换设备凭证；返回的 device_token 只在这里出现一次。"""
        now = datetime.now(timezone.utc)
        text = str(code or "").strip()
        if not text:
            raise ValueError("请填写配对码")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM data_collection_plugin_pair_codes WHERE code = ?",
                (text,),
            ).fetchone()
            if row is None or str(row["used_at"] or ""):
                raise PermissionError("配对码无效或已被使用，请在工作台重新生成")
            try:
                expired = datetime.fromisoformat(str(row["expires_at"])) < now
            except ValueError:
                expired = True
            if expired:
                raise PermissionError("配对码已过期，请在工作台重新生成")
            conn.execute(
                "UPDATE data_collection_plugin_pair_codes SET used_at = ? WHERE code = ?",
                (now.isoformat(), text),
            )
            device_id = "dev_" + secrets.token_hex(6)
            device_token = "pd_" + secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO data_collection_plugin_devices "
                "(device_id, actor_id, workspace_id, name, browser, extension_version, "
                "token_hash, allowed_shops_json, status, created_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 'active', ?, ?)",
                (
                    device_id,
                    str(row["actor_id"]),
                    str(row["workspace_id"]),
                    str(name or "").strip()[:80],
                    str(browser or "").strip()[:40],
                    str(extension_version or "").strip()[:40],
                    _hash_token(device_token),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return {
            "device_id": device_id,
            "device_token": device_token,
            "actor_id": str(row["actor_id"]),
            "workspace_id": str(row["workspace_id"]),
        }

    def device_for_token(self, device_token: str) -> dict[str, Any]:
        """按设备凭证取设备信息（同时刷新最近在线时间）。"""
        token = str(device_token or "").strip()
        if not token:
            raise PermissionError("缺少设备凭证")
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM data_collection_plugin_devices "
                "WHERE token_hash = ? AND status = 'active'",
                (_hash_token(token),),
            ).fetchone()
            if row is None:
                raise PermissionError("设备凭证无效或已解绑，请在插件里重新配对")
            conn.execute(
                "UPDATE data_collection_plugin_devices SET last_seen_at = ? WHERE device_id = ?",
                (now, str(row["device_id"])),
            )
        return _device_row(row, last_seen_at=now)

    def list_devices(self, *, actor_id: str, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM data_collection_plugin_devices "
                "WHERE actor_id = ? AND workspace_id = ? ORDER BY created_at DESC",
                (actor_id, workspace_id),
            ).fetchall()
        return [_device_row(row) for row in rows]

    def set_device_shops(
        self,
        *,
        device_id: str,
        actor_id: str,
        workspace_id: str,
        shops: list[str],
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT device_id FROM data_collection_plugin_devices "
                "WHERE device_id = ? AND actor_id = ? AND workspace_id = ?",
                (device_id, actor_id, workspace_id),
            ).fetchone()
            if row is None:
                raise PermissionError("设备不存在")
            conn.execute(
                "UPDATE data_collection_plugin_devices SET allowed_shops_json = ? WHERE device_id = ?",
                (_dump_list([str(item).strip() for item in shops if str(item).strip()]), device_id),
            )
        return next(
            device
            for device in self.list_devices(actor_id=actor_id, workspace_id=workspace_id)
            if device["device_id"] == device_id
        )

    def revoke_device(self, *, device_id: str, actor_id: str, workspace_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM data_collection_plugin_devices "
                "WHERE device_id = ? AND actor_id = ? AND workspace_id = ?",
                (device_id, actor_id, workspace_id),
            )
        return cur.rowcount > 0


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

    def queue_command(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        session_id: int,
        command_type: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> PluginCommand:
        """Queue one whitelisted read-only command for an owned live session."""
        command_type = _required_text(command_type, "command_type")
        idempotency_key = _required_text(idempotency_key, "idempotency_key")
        if command_type not in ALLOWED_PLUGIN_COMMAND_TYPES:
            raise ValueError("unsupported plugin command type")
        if not isinstance(payload, Mapping):
            raise ValueError("payload must be an object")
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            session = self._owned_session(
                conn,
                actor_id=actor_id,
                workspace_id=workspace_id,
                session_id=session_id,
            )
            if not _active(session["last_seen_at"]):
                conn.rollback()
                raise ValueError("plugin session is offline")
            session = self._prefer_fresh_session(conn, session)
            session_id = int(session["id"])
            capabilities = _load(session["capabilities_json"])
            if capabilities.get(command_type) is not True:
                conn.rollback()
                raise ValueError(f"plugin session does not support {command_type}")
            existing = conn.execute(
                """SELECT command_id FROM data_collection_plugin_command_requests
                WHERE workspace_id = ? AND command_type = ? AND idempotency_key = ?""",
                (workspace_id, command_type, idempotency_key),
            ).fetchone()
            if existing is not None:
                command = self._command(conn, int(existing["command_id"]))
                conn.commit()
                return command
            cur = conn.execute(
                """INSERT INTO data_collection_plugin_commands
                (session_id, command_type, payload_json, status, result_json, created_at, updated_at)
                VALUES (?, ?, ?, 'queued', '{}', ?, ?)""",
                (session_id, command_type, _dump(payload), now, now),
            )
            command_id = int(cur.lastrowid)
            conn.execute(
                """INSERT INTO data_collection_plugin_command_requests
                (workspace_id, command_type, idempotency_key, command_id, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (workspace_id, command_type, idempotency_key, command_id, now),
            )
            command = self._command(conn, command_id)
            conn.commit()
            return command

    def poll(
        self,
        session_token: str,
        *,
        limit: int = 10,
        capabilities: Mapping[str, Any] | None = None,
    ) -> tuple[PluginCommand, ...]:
        now = _now()
        reclaim_before = (datetime.now(timezone.utc) - _COMMAND_LEASE_WINDOW).isoformat()
        with self._connect() as conn:
            # BEGIN IMMEDIATE 串行化「选 queued → 置 sent」：并发 poll（同 session
            # 重试/双开）不经过这里会同时读到同一批 queued 并重复分发执行。
            conn.execute("BEGIN IMMEDIATE")
            try:
                session = self._session(conn, session_token)
                stale_before = (
                    datetime.now(timezone.utc) - _SESSION_STALE_WINDOW
                ).isoformat()
                # 插件重连后会换成新的 session id，之前排在旧会话名下的指令
                # 会永远停在「排队中」，界面表现为一直转圈、没有新的 Temu 页面。
                # 这里把同账号同工作区、旧会话已经不再轮询的排队指令改派给当前会话。
                conn.execute(
                    """UPDATE data_collection_plugin_commands
                    SET session_id = ?, updated_at = ?
                    WHERE status = 'queued'
                      AND session_id IN (
                        SELECT id FROM data_collection_plugin_sessions
                        WHERE actor_id = ? AND workspace_id = ? AND id <> ?
                          AND (last_seen_at = '' OR last_seen_at < ?)
                      )""",
                    (
                        session["id"],
                        now,
                        session["actor_id"],
                        session["workspace_id"],
                        session["id"],
                        stale_before,
                    ),
                )
                # A browser can die after acknowledging a command. Requeue its
                # expired in-flight command so the next poll can finish it.
                conn.execute(
                    """UPDATE data_collection_plugin_commands
                    SET status = 'queued', result_json = '{}', updated_at = ?
                    WHERE session_id = ? AND status IN ('sent', 'running') AND updated_at < ?""",
                    (now, session["id"], reclaim_before),
                )
                # 插件重载/浏览器重开后旧会话不再轮询，排队中的指令会永远没人执行。
                # 把「所属会话已失联」的 queued 指令改派给当前会话，避免一直卡在「排队中」。
                stale_before = (datetime.now(timezone.utc) - _SESSION_STALE_WINDOW).isoformat()
                conn.execute(
                    """UPDATE data_collection_plugin_commands
                    SET session_id = ?, updated_at = ?
                    WHERE status = 'queued' AND session_id IN (
                        SELECT id FROM data_collection_plugin_sessions
                        WHERE workspace_id = ? AND id <> ? AND last_seen_at < ?
                    )""",
                    (session["id"], now, session["workspace_id"], session["id"], stale_before),
                )
                rows = conn.execute(
                    """SELECT id FROM data_collection_plugin_commands
                    WHERE session_id = ? AND status = 'queued' ORDER BY id LIMIT ?""",
                    (session["id"], max(1, min(limit, 50))),
                ).fetchall()
                ids = [int(row["id"]) for row in rows]
                if ids:
                    marks = ",".join("?" for _ in ids)
                    conn.execute(f"UPDATE data_collection_plugin_commands SET status = 'sent', updated_at = ? WHERE id IN ({marks})", (now, *ids))
                # 插件每次轮询都会带上当前 capabilities：会话是复用旧 id 的，
                # 若不在轮询时刷新，插件升级后新增的能力（如 temu_capture_all）
                # 永远不会生效，工作台下发的采集指令会一直被拒。
                if capabilities:
                    conn.execute(
                        "UPDATE data_collection_plugin_sessions "
                        "SET last_seen_at = ?, status = 'connected', capabilities_json = ? "
                        "WHERE id = ?",
                        (now, _dump(dict(capabilities)), session["id"]),
                    )
                else:
                    conn.execute("UPDATE data_collection_plugin_sessions SET last_seen_at = ?, status = 'connected' WHERE id = ?", (now, session["id"]))
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            return tuple(self._command(conn, command_id) for command_id in ids)

    def receive_result(
        self,
        *,
        session_token: str,
        command_id: int,
        status: str,
        result: Mapping[str, Any],
    ) -> PluginCommand:
        # Older browser plugins use `sent` as their acknowledgement payload;
        # normalize it to the persisted in-flight state used by this queue.
        status = "running" if status == "sent" else status
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

    def list_sessions_for_workspace(
        self,
        *,
        workspace_id: str,
        command_type: str = "",
        active_only: bool = True,
    ) -> tuple[Mapping[str, Any], ...]:
        """工作台侧查询：某工作区下所有插件会话（可按能力过滤）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, actor_id, workspace_id, capabilities_json, status, created_at, last_seen_at "
                "FROM data_collection_plugin_sessions WHERE workspace_id = ? "
                "ORDER BY last_seen_at DESC, id DESC",
                (workspace_id,),
            ).fetchall()
        sessions: list[Mapping[str, Any]] = []
        for row in rows:
            capabilities = _load(row["capabilities_json"])
            online = _active(str(row["last_seen_at"]))
            if active_only and not online:
                continue
            if command_type and capabilities.get(command_type) is not True:
                continue
            sessions.append({
                "session_id": int(row["id"]),
                "actor_id": str(row["actor_id"]),
                "workspace_id": str(row["workspace_id"]),
                "capabilities": capabilities,
                "status": str(row["status"]),
                "online": online,
                "last_seen_at": str(row["last_seen_at"]),
            })
        return tuple(sessions)

    def get_command_for_workspace(self, *, workspace_id: str, command_id: int) -> PluginCommand:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT c.id FROM data_collection_plugin_commands c "
                "JOIN data_collection_plugin_sessions s ON s.id = c.session_id "
                "WHERE c.id = ? AND s.workspace_id = ?",
                (int(command_id), workspace_id),
            ).fetchone()
            if row is None:
                raise PermissionError("plugin command not found")
            return self._command(conn, int(row["id"]))

    def cancel_command(self, *, workspace_id: str, command_id: int) -> PluginCommand:
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT c.id, c.status FROM data_collection_plugin_commands c "
                "JOIN data_collection_plugin_sessions s ON s.id = c.session_id "
                "WHERE c.id = ? AND s.workspace_id = ?",
                (int(command_id), workspace_id),
            ).fetchone()
            if row is None:
                raise PermissionError("plugin command not found")
            if str(row["status"]) not in _TERMINAL:
                conn.execute(
                    "UPDATE data_collection_plugin_commands SET status = 'cancelled', updated_at = ? "
                    "WHERE id = ?",
                    (now, int(command_id)),
                )
            return self._command(conn, int(command_id))

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

    def list_sessions(self, *, actor_id: str, workspace_id: str) -> tuple[Mapping[str, Any], ...]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT s.id, s.actor_id, s.workspace_id, s.capabilities_json, s.status,
                s.created_at, s.last_seen_at, s.device_id,
                COALESCE(d.name, '') AS device_name
                FROM data_collection_plugin_sessions s
                LEFT JOIN data_collection_plugin_devices d ON d.device_id = s.device_id
                WHERE s.actor_id = ? AND s.workspace_id = ?
                ORDER BY s.last_seen_at DESC, s.id DESC""",
                (actor_id, workspace_id),
            ).fetchall()
        return tuple(
            {
                "session_id": int(row["id"]),
                "actor_id": str(row["actor_id"]),
                "workspace_id": str(row["workspace_id"]),
                "capabilities": _load(row["capabilities_json"]),
                "status": str(row["status"]),
                "created_at": str(row["created_at"]),
                "last_seen_at": str(row["last_seen_at"]),
                "device_id": str(row["device_id"] or ""),
                "device_name": str(row["device_name"] or ""),
            }
            for row in rows
        )

    def list_sessions_for_workspace(
        self,
        *,
        workspace_id: str,
        command_type: str | None = None,
        active_only: bool = True,
    ) -> tuple[Mapping[str, Any], ...]:
        """订单利润等模块只需要 workspace 维度找插件会话，不依赖登录 actor。"""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT s.id, s.actor_id, s.workspace_id, s.capabilities_json, s.status,
                s.created_at, s.last_seen_at, s.device_id,
                COALESCE(d.name, '') AS device_name
                FROM data_collection_plugin_sessions s
                LEFT JOIN data_collection_plugin_devices d ON d.device_id = s.device_id
                WHERE s.workspace_id = ?
                ORDER BY s.last_seen_at DESC, s.id DESC""",
                (workspace_id,),
            ).fetchall()
        sessions = []
        for row in rows:
            capabilities = _load(row["capabilities_json"])
            online = _active(row["last_seen_at"])
            if active_only and not online:
                continue
            if command_type and capabilities.get(command_type) is not True:
                continue
            sessions.append(
                {
                    "session_id": int(row["id"]),
                    "actor_id": str(row["actor_id"]),
                    "workspace_id": str(row["workspace_id"]),
                    "capabilities": capabilities,
                    "status": str(row["status"]),
                    "online": online,
                    "created_at": str(row["created_at"]),
                    "last_seen_at": str(row["last_seen_at"]),
                    "device_id": str(row["device_id"] or ""),
                    "device_name": str(row["device_name"] or ""),
                }
            )
        return tuple(sessions)

    def get_command_for_workspace(self, *, workspace_id: str, command_id: int) -> PluginCommand:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT c.id FROM data_collection_plugin_commands c
                JOIN data_collection_plugin_sessions s ON s.id = c.session_id
                WHERE c.id = ? AND s.workspace_id = ?""",
                (command_id, workspace_id),
            ).fetchone()
            if row is None:
                raise PermissionError("plugin command not found")
            return self._command(conn, int(row["id"]))

    def cancel_command(self, *, workspace_id: str, command_id: int) -> PluginCommand:
        """终止一条还没结束的采集指令。

        插件在长任务里会定时回查这条指令的状态，看到 cancelled 就自行停下；
        已经结束的指令保持原状，避免把成功结果覆盖成“已取消”。
        """
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                """SELECT c.id, c.status FROM data_collection_plugin_commands c
                JOIN data_collection_plugin_sessions s ON s.id = c.session_id
                WHERE c.id = ? AND s.workspace_id = ?""",
                (int(command_id), workspace_id),
            ).fetchone()
            if row is None:
                raise PermissionError("plugin command not found")
            if str(row["status"]) in _TERMINAL:
                return self._command(conn, int(row["id"]))
            conn.execute(
                "UPDATE data_collection_plugin_commands "
                "SET status = 'cancelled', result_json = ?, updated_at = ? WHERE id = ?",
                (
                    _dump({
                        "ok": False,
                        "cancelled": True,
                        "statusText": "采集已终止",
                    }),
                    now,
                    int(row["id"]),
                ),
            )
            return self._command(conn, int(row["id"]))

    def active_capture_command(self, *, workspace_id: str, session_id: int) -> PluginCommand | None:
        """该会话上还没结束的采集/登录指令（用于阻止重复下发）。"""
        threshold = (
            datetime.now(timezone.utc) - timedelta(minutes=45)
        ).isoformat(timespec="seconds")
        with self._connect() as conn:
            row = conn.execute(
                """SELECT c.id FROM data_collection_plugin_commands c
                JOIN data_collection_plugin_sessions s ON s.id = c.session_id
                WHERE c.session_id = ? AND s.workspace_id = ?
                  AND c.status NOT IN ('succeeded', 'failed', 'cancelled')
                  AND c.command_type IN ('temu_capture_all', 'temu_capture_incremental',
                                         'temu_capture_resource', 'temu_account_login')
                  AND c.created_at >= ?
                ORDER BY c.id DESC LIMIT 1""",
                (int(session_id), workspace_id, threshold),
            ).fetchone()
            if row is None:
                return None
            return self._command(conn, int(row["id"]))

    def claim_connected_legacy_local_session(
        self, *, actor_id: str, workspace_id: str
    ) -> bool:
        """Bind one active legacy local connector to the current workbench user.

        The delivered browser extension was originally paired with the fixed
        local ``dev-admin-token``.  Its session is therefore owned by the
        compatibility ``default`` workspace even after a real operator signs
        in.  On a local single-user runtime, the first authenticated workspace
        opening the plugin-backed module claims the one active compatibility
        connector.  This avoids silently writing a price batch into a different
        workspace while keeping normal, explicitly paired sessions untouched.
        """
        if (
            actor_id == _LEGACY_LOCAL_ACTOR_ID
            and workspace_id == _LEGACY_LOCAL_WORKSPACE_ID
        ):
            return False
        with self._connect() as conn:
            row = conn.execute(
                """SELECT id, last_seen_at
                FROM data_collection_plugin_sessions
                WHERE actor_id = ? AND workspace_id = ? AND status = 'connected'
                ORDER BY last_seen_at DESC, id DESC
                LIMIT 1""",
                (_LEGACY_LOCAL_ACTOR_ID, _LEGACY_LOCAL_WORKSPACE_ID),
            ).fetchone()
            if row is None or not _active(str(row["last_seen_at"])):
                return False
            updated = conn.execute(
                """UPDATE data_collection_plugin_sessions
                SET actor_id = ?, workspace_id = ?
                WHERE id = ? AND actor_id = ? AND workspace_id = ?""",
                (
                    actor_id,
                    workspace_id,
                    int(row["id"]),
                    _LEGACY_LOCAL_ACTOR_ID,
                    _LEGACY_LOCAL_WORKSPACE_ID,
                ),
            )
            return updated.rowcount == 1

    def list_commands(
        self,
        *,
        actor_id: str,
        workspace_id: str,
        command_type: str | None = None,
        limit: int = 20,
    ) -> tuple[PluginCommand, ...]:
        limit = max(1, min(int(limit), 50))
        values: list[Any] = [actor_id, workspace_id]
        clause = ""
        if command_type is not None:
            if command_type not in ALLOWED_PLUGIN_COMMAND_TYPES:
                raise ValueError("unsupported plugin command type")
            clause = " AND c.command_type = ?"
            values.append(command_type)
        values.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT c.id FROM data_collection_plugin_commands c
                JOIN data_collection_plugin_sessions s ON s.id = c.session_id
                WHERE s.actor_id = ? AND s.workspace_id = ?"""
                + clause
                + " ORDER BY c.created_at DESC, c.id DESC LIMIT ?",
                tuple(values),
            ).fetchall()
            return tuple(self._command(conn, int(row["id"])) for row in rows)

    def workspace_for_session(self, session_token: str) -> str:
        """Return the workspace bound to a connected browser session."""
        with self._connect() as conn:
            return str(self._session(conn, session_token)["workspace_id"])

    def session_id_for_token(self, session_token: str) -> int:
        """Return the numeric session id bound to a browser session token."""
        with self._connect() as conn:
            return int(self._session(conn, session_token)["id"])

    def identity_for_session(self, session_token: str) -> Mapping[str, str]:
        """Resolve the authenticated actor/workspace of a live plugin session."""
        with self._connect() as conn:
            session = self._session(conn, session_token)
            if not _active(session["last_seen_at"]):
                raise PermissionError("plugin session is offline")
            return {
                "actor_id": str(session["actor_id"]),
                "workspace_id": str(session["workspace_id"]),
            }

    def _initialize(self) -> None:
        migrations = Path(__file__).with_name("migrations")
        with self._connect() as conn:
            for migration in (
                migrations / "002_data_collection_plugin_queue.sql",
                migrations / "003_plugin_command_requests.sql",
                migrations / "004_plugin_devices.sql",
            ):
                conn.executescript(migration.read_text(encoding="utf-8"))
            columns = {
                str(row["name"])
                for row in conn.execute(
                    "PRAGMA table_info('data_collection_plugin_sessions')"
                ).fetchall()
            }
            if "device_id" not in columns:
                conn.execute(
                    "ALTER TABLE data_collection_plugin_sessions "
                    "ADD COLUMN device_id TEXT NOT NULL DEFAULT ''"
                )

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._database_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _session(conn: sqlite3.Connection, token: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT id, actor_id, workspace_id, last_seen_at FROM data_collection_plugin_sessions "
            "WHERE session_token = ?",
            (token,),
        ).fetchone()
        if row is None:
            raise PermissionError("invalid plugin session")
        return row

    @staticmethod
    def _owned_session(
        conn: sqlite3.Connection,
        *,
        actor_id: str,
        workspace_id: str,
        session_id: int,
    ) -> sqlite3.Row:
        row = conn.execute(
            """SELECT id, actor_id, workspace_id, capabilities_json, last_seen_at
            FROM data_collection_plugin_sessions
            WHERE id = ? AND actor_id = ? AND workspace_id = ?""",
            (session_id, actor_id, workspace_id),
        ).fetchone()
        if row is None:
            raise PermissionError("plugin session not found")
        return row

    def _prefer_fresh_session(self, conn: sqlite3.Connection, session: Any) -> Any:
        """目标会话刚停止轮询（插件升级/重载）时，改派给同账号的最新会话。"""
        try:
            last_seen = datetime.fromisoformat(str(session["last_seen_at"] or ""))
        except (ValueError, KeyError):
            return session
        if last_seen >= datetime.now(timezone.utc) - _FRESH_SESSION_WINDOW:
            return session
        row = conn.execute(
            """SELECT id, actor_id, workspace_id, capabilities_json, last_seen_at
            FROM data_collection_plugin_sessions
            WHERE actor_id = ? AND workspace_id = ? AND id <> ? AND last_seen_at > ?
            ORDER BY last_seen_at DESC LIMIT 1""",
            (
                session["actor_id"],
                session["workspace_id"],
                session["id"],
                last_seen.isoformat(),
            ),
        ).fetchone()
        return dict(row) if row is not None else session

    @staticmethod
    def _command(conn: sqlite3.Connection, command_id: int) -> PluginCommand:
        row = conn.execute(
            """SELECT c.id, c.command_type, c.payload_json, c.status, c.result_json,
            c.created_at, c.updated_at, COALESCE(r.idempotency_key, '') AS idempotency_key
            FROM data_collection_plugin_commands c
            LEFT JOIN data_collection_plugin_command_requests r ON r.command_id = c.id
            WHERE c.id = ?""",
            (command_id,),
        ).fetchone()
        if row is None:
            raise ValueError("plugin command not found")
        return PluginCommand(
            command_id=row["id"],
            command_type=row["command_type"],
            idempotency_key=row["idempotency_key"],
            payload=_load(row["payload_json"]),
            status=row["status"],
            result=_load(row["result_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _device_row(row: sqlite3.Row, *, last_seen_at: str | None = None) -> dict[str, Any]:
    """设备行转响应：隐去 token 哈希，只回展示与归属字段。"""
    return {
        "device_id": str(row["device_id"]),
        "actor_id": str(row["actor_id"]),
        "workspace_id": str(row["workspace_id"]),
        "name": str(row["name"] or ""),
        "browser": str(row["browser"] or ""),
        "extension_version": str(row["extension_version"] or ""),
        "allowed_shops": _load_list(row["allowed_shops_json"]),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "last_seen_at": str(last_seen_at or row["last_seen_at"] or ""),
    }



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


def _dump_list(values: list[str]) -> str:
    return json.dumps([str(item) for item in values], ensure_ascii=False)


def _load_list(value: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item)]


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


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
