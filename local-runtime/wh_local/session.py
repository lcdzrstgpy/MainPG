from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import sqlite3

from fastapi import Header, HTTPException

from .config import default_config
from .customer.db_store import SQLiteCustomerSessionStore
from .db import connect

logger = logging.getLogger("wh_local.session")


@dataclass(frozen=True)
class Actor:
    id: str
    username: str
    role: str
    workspace_id: str = "default"
    workspace_code: str = "local-demo"
    workspace_name: str = "本地演示工作区"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def actor_id(self) -> str:
        return self.id


def actor_from_bearer_token(token: str, database_path: Path | None = None) -> Actor:
    config = default_config()

    if token == config.dev_admin_token:
        return Actor(id="local-demo-admin", username="local-demo", role="admin")

    db_path = database_path or config.database_path
    try:
        session = SQLiteCustomerSessionStore(db_path).get_session(token)
    except sqlite3.Error as exc:
        # 数据库暂时不可用（如写锁竞争）≠ 令牌无效：返回 503 而不是 401，
        # 避免前端把临时故障误判为登录态失效而清空会话跳登录页。
        logger.warning("authentication database unavailable: %r", exc)
        raise HTTPException(status_code=503, detail="authentication database unavailable") from exc
    if session is None:
        raise HTTPException(status_code=401, detail="invalid bearer token")

    return Actor(
        id=session.user_id,
        username=session.username,
        role=session.role,
        workspace_id=session.workspace_id or session.workspace_code or "default",
        workspace_code=session.workspace_code,
        workspace_name=session.workspace_name,
    )


def actor_from_authorization(authorization: str | None = Header(default=None)) -> Actor:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    return actor_from_bearer_token(token)


def daily_selection_actor_from_authorization(authorization: str | None = Header(default=None)) -> dict[str, str]:
    actor = actor_from_authorization(authorization)
    return {"actor_id": actor.id, "workspace_id": actor.workspace_id}


def require_admin(actor: Actor) -> None:
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="admin permission required")


def actor_has_permission(actor: Actor, permission_key: str, database_path: Path | None = None) -> bool:
    """Return whether the actor has a fine-grained permission.

    Admin remains a safe shortcut for local development, while the database
    tables provide the canonical permission registry for module integration.
    """

    if actor.is_admin:
        return True
    config = default_config()
    db_path = database_path or config.database_path
    try:
        conn = connect(db_path)
        try:
            override = conn.execute(
                """
                SELECT effect
                FROM user_permission_overrides
                WHERE user_id = ?
                  AND workspace_id = ?
                  AND permission_key = ?
                """,
                (actor.id, actor.workspace_id, permission_key),
            ).fetchone()
            if override is not None:
                return override["effect"] == "allow"
            row = conn.execute(
                """
                SELECT 1
                FROM role_permissions
                WHERE role = ?
                  AND permission_key = ?
                """,
                (actor.role, permission_key),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def require_permission(actor: Actor, permission_key: str, database_path: Path | None = None) -> None:
    if not actor_has_permission(actor, permission_key, database_path):
        raise HTTPException(status_code=403, detail=f"permission required: {permission_key}")
