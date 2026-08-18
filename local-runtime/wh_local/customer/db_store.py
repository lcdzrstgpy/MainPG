from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import secrets
import sqlite3

from ..db import transaction
from .contracts import CustomerAuthResult, LocalSession
from .local_session import CustomerSessionStore


DEFAULT_WORKSPACE_ID = "default"
DEFAULT_WORKSPACE_CODE = "local-demo"
DEFAULT_WORKSPACE_NAME = "本地演示工作区"


class SQLiteCustomerSessionStore(CustomerSessionStore):
    """SQLite-backed customer user/session persistence.

    The frontend receives the plain local token once at login time. The
    database stores only a SHA-256 hash of that token.
    """

    def __init__(self, database_path: Path):
        self.database_path = database_path

    def upsert_customer_user(self, customer: CustomerAuthResult) -> tuple[str, str]:
        user_id = _stable_user_id(customer)
        workspace_code = customer.workspace_code or DEFAULT_WORKSPACE_CODE
        workspace_name = customer.workspace_name or DEFAULT_WORKSPACE_NAME
        now = _utc_now()
        with transaction(self.database_path) as conn:
            workspace_row = conn.execute(
                "SELECT workspace_id FROM workspaces WHERE workspace_code = ?",
                (workspace_code,),
            ).fetchone()
            workspace_id = workspace_row["workspace_id"] if workspace_row else _workspace_id(customer)
            conn.execute(
                """
                INSERT INTO workspaces (
                    workspace_id, workspace_code, workspace_name, status, created_at, updated_at
                )
                VALUES (?, ?, ?, 'active', ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    workspace_code = excluded.workspace_code,
                    workspace_name = excluded.workspace_name,
                    updated_at = excluded.updated_at
                """,
                (workspace_id, workspace_code, workspace_name, now, now),
            )
            conn.execute(
                """
                INSERT INTO customer_users (
                    user_id,
                    remote_customer_id,
                    username,
                    email,
                    role,
                    workspace_id,
                    account_status,
                    remote_session_expires_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    remote_customer_id = excluded.remote_customer_id,
                    username = excluded.username,
                    email = excluded.email,
                    role = excluded.role,
                    workspace_id = excluded.workspace_id,
                    account_status = excluded.account_status,
                    remote_session_expires_at = excluded.remote_session_expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    customer.customer_id,
                    customer.username,
                    customer.email,
                    "admin",
                    workspace_id,
                    customer.account_status or "active",
                    customer.remote_expires_at,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO auth_accounts (
                    account_id,
                    username,
                    email,
                    display_name,
                    role,
                    workspace_id,
                    account_status,
                    email_verified_at,
                    created_at,
                    updated_at,
                    login_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    username = excluded.username,
                    email = excluded.email,
                    display_name = excluded.display_name,
                    role = excluded.role,
                    workspace_id = excluded.workspace_id,
                    account_status = excluded.account_status,
                    updated_at = excluded.updated_at,
                    login_status = excluded.login_status
                """,
                (
                    user_id,
                    customer.username,
                    customer.email,
                    customer.username,
                    "admin",
                    workspace_id,
                    customer.account_status or "active",
                    now,
                    now,
                    customer.login_status or "online",
                ),
            )
        return user_id, workspace_id

    def save_session(self, session: LocalSession, customer: CustomerAuthResult) -> None:
        session_id = f"sess_{secrets.token_urlsafe(24)}"
        now = _utc_now()
        with transaction(self.database_path) as conn:
            conn.execute(
                """
                INSERT INTO customer_sessions (
                    session_id,
                    user_id,
                    token_hash,
                    expires_at,
                    revoked_at,
                    last_used_at,
                    created_at,
                    remote_token
                )
                VALUES (?, ?, ?, ?, '', ?, ?, ?)
                """,
                (session_id, session.user_id, _hash_token(session.token), session.expires_at, now, now, session.remote_token),
            )

    def get_session(self, token: str) -> LocalSession | None:
        token_hash = _hash_token(token)
        now = _utc_now()
        with transaction(self.database_path) as conn:
            row = conn.execute(
                """
                SELECT
                    s.user_id,
                    s.expires_at,
                    s.remote_token,
                    u.username,
                    u.role,
                    u.workspace_id,
                    w.workspace_code,
                    w.workspace_name
                FROM customer_sessions s
                JOIN customer_users u ON u.user_id = s.user_id
                LEFT JOIN workspaces w ON w.workspace_id = u.workspace_id
                WHERE s.token_hash = ?
                  AND s.revoked_at = ''
                  AND s.expires_at > ?
                """,
                (token_hash, now),
            ).fetchone()
            if row is None:
                return None
            session = LocalSession(
                user_id=row["user_id"],
                token=token,
                expires_at=row["expires_at"],
                username=row["username"],
                role=row["role"],
                workspace_id=row["workspace_id"] or DEFAULT_WORKSPACE_ID,
                workspace_code=row["workspace_code"] or "",
                workspace_name=row["workspace_name"] or "",
                remote_token=row["remote_token"] or "",
            )
        # last_used_at 更新放在读事务之外单独短事务执行：同一事务里“先读后写”
        # 会把读快照升级为写锁，WAL 模式下若期间有并发提交会立即抛
        # SQLITE_BUSY_SNAPSHOT（OperationalError database is locked，busy_timeout
        # 不生效），此前被 session.actor_from_bearer_token 误映射成 401。
        # 新事务首条语句直接取写锁，可正常走 busy_timeout 等待。
        try:
            with transaction(self.database_path) as conn:
                conn.execute(
                    "UPDATE customer_sessions SET last_used_at = ? WHERE token_hash = ?",
                    (now, token_hash),
                )
        except sqlite3.Error:
            # last_used_at 仅用于会话活跃度展示，写失败不阻断本次鉴权。
            pass
        return session

    def revoke_session(self, token: str) -> None:
        token_hash = _hash_token(token)
        with transaction(self.database_path) as conn:
            conn.execute(
                """
                UPDATE customer_sessions
                SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at = ''
                """,
                (_utc_now(), token_hash),
            )


def _stable_user_id(customer: CustomerAuthResult) -> str:
    candidate = customer.customer_id or customer.email or customer.username
    safe_candidate = str(candidate or "").strip()
    if safe_candidate:
        return safe_candidate
    return f"cust_{secrets.token_urlsafe(12)}"


def _workspace_id(customer: CustomerAuthResult) -> str:
    value = str(customer.workspace_code or "").strip()
    if value:
        return value
    return DEFAULT_WORKSPACE_ID


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
