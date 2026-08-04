from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from fastapi import Header, HTTPException

from .config import default_config
from .customer.db_store import SQLiteCustomerSessionStore


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


def actor_from_authorization(authorization: str | None = Header(default=None)) -> Actor:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    config = default_config()

    if token == config.dev_admin_token:
        return Actor(id="local-demo-admin", username="local-demo", role="admin")

    try:
        session = SQLiteCustomerSessionStore(config.database_path).get_session(token)
    except sqlite3.Error as exc:
        raise HTTPException(status_code=401, detail="invalid bearer token") from exc
    if session is None:
        raise HTTPException(status_code=401, detail="invalid bearer token")

    return Actor(
        id=session.user_id,
        username=session.username,
        role=session.role,
        workspace_id=session.workspace_code or "default",
        workspace_code=session.workspace_code,
        workspace_name=session.workspace_name,
    )


def daily_selection_actor_from_authorization(authorization: str | None = Header(default=None)) -> dict[str, str]:
    actor = actor_from_authorization(authorization)
    return {"actor_id": actor.id, "workspace_id": actor.workspace_id}


def require_admin(actor: Actor) -> None:
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="admin permission required")
