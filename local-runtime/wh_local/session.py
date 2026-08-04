from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from .config import default_config


@dataclass(frozen=True)
class Actor:
    id: str
    username: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def actor_from_authorization(authorization: str | None = Header(default=None)) -> Actor:
    # 当前是本地开发版登录保护：前端传 Bearer token，后续可替换为控制平面签发的会话。
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    config = default_config()
    if token != config.dev_admin_token:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return Actor(id="local-demo-admin", username="local-demo", role="admin")


def require_admin(actor: Actor) -> None:
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="admin permission required")
