from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import secrets
from typing import Protocol

from .contracts import CustomerAuthResult, LocalSession


SESSION_TTL = timedelta(hours=12)


class CustomerSessionStore(Protocol):
    """Storage boundary for local user/session persistence.

    Replace MemoryCustomerSessionStore with a SQLite/Postgres implementation
    when the shared DB layer is ready.
    """

    def upsert_customer_user(self, customer: CustomerAuthResult) -> tuple[str, str]:
        ...

    def save_session(self, session: LocalSession, customer: CustomerAuthResult) -> None:
        ...

    def get_session(self, token: str) -> LocalSession | None:
        ...

    def revoke_session(self, token: str) -> None:
        ...


class MemoryCustomerSessionStore:
    """Development-only store used before the shared persistence module lands."""

    def __init__(self):
        self.users_by_customer_id: dict[str, dict[str, str]] = {}
        self.sessions_by_token: dict[str, LocalSession] = {}

    def upsert_customer_user(self, customer: CustomerAuthResult) -> tuple[str, str]:
        user_id = customer.customer_id or customer.email or customer.username
        self.users_by_customer_id[user_id] = {
            "user_id": user_id,
            "username": customer.username,
            "email": customer.email,
            "role": customer.role,
            "workspace_id": "default",
            "workspace_code": customer.workspace_code,
            "workspace_name": customer.workspace_name,
        }
        return user_id, "default"

    def save_session(self, session: LocalSession, customer: CustomerAuthResult) -> None:
        self.sessions_by_token[session.token] = session

    def get_session(self, token: str) -> LocalSession | None:
        return self.sessions_by_token.get(token)

    def revoke_session(self, token: str) -> None:
        self.sessions_by_token.pop(token, None)

    def remote_token_for_actor(self, user_id: str, workspace_id: str) -> str:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for session in reversed(tuple(self.sessions_by_token.values())):
            if (
                session.user_id == user_id
                and session.workspace_id == workspace_id
                and session.expires_at > now
            ):
                return session.remote_token
        return ""


class LocalSessionService:
    """Creates local workbench sessions after remote auth succeeds."""

    def __init__(self, store: CustomerSessionStore | None = None):
        self.store = store or MemoryCustomerSessionStore()

    def login_customer(self, customer: CustomerAuthResult) -> LocalSession:
        status = str(customer.account_status or "active").strip().lower()
        if status in {"disabled", "inactive", "locked", "suspended", "deleted"}:
            raise PermissionError("customer account is not active")
        user_id, workspace_id = self.store.upsert_customer_user(customer)
        session = LocalSession(
            user_id=user_id,
            token=f"wh_local_{secrets.token_urlsafe(32)}",
            expires_at=(datetime.now(timezone.utc) + SESSION_TTL).isoformat(timespec="seconds"),
            username=customer.username,
            role=customer.role,
            workspace_id=workspace_id,
            workspace_code=customer.workspace_code,
            workspace_name=customer.workspace_name,
            remote_token=customer.remote_token,
        )
        self.store.save_session(session, customer)
        return session

    def me(self, token: str) -> dict[str, str]:
        session = self.store.get_session(token)
        if session is None:
            raise PermissionError("invalid bearer token")
        payload = asdict(session)
        # remote_token 只用于登出联动，不暴露给前端。
        payload.pop("remote_token", None)
        return payload

    def logout(self, token: str) -> None:
        self.store.revoke_session(token)

    def remote_token_for_actor(self, user_id: str, workspace_id: str) -> str:
        resolver = getattr(self.store, "remote_token_for_actor", None)
        if not callable(resolver):
            return ""
        return str(resolver(user_id, workspace_id) or "")
