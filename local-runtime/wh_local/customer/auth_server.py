from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import secrets
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request

from ..config import default_config
from ..db import init_db, transaction
from .auth_service import SQLiteCustomerAuthService
from .contracts import CustomerAuthActionResult, CustomerAuthResult


REMOTE_SESSION_TTL = timedelta(hours=12)


def create_auth_app(database_path: Path | None = None) -> FastAPI:
    """Create the standalone platform customer-auth service.

    The local workbench can point WH_LOCAL_CUSTOMER_AUTH_BASE_URL at this app.
    This service owns platform accounts/passwords and returns normalized account
    data plus a remote wh_auth_* token. The local workbench still creates its
    own wh_local_* session for business modules.
    """

    config = default_config()
    db_path = database_path or config.database_path
    init_db(db_path)
    service = SQLiteCustomerAuthService(db_path)

    app = FastAPI(title="W-H Platform Customer Auth Service", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "database_path": str(db_path), "service": "customer-auth"}

    @app.post("/api/customer/register")
    def register(payload: dict[str, Any]) -> dict[str, Any]:
        return _action_response(_call_action(service.register, payload))

    @app.post("/api/customer/login")
    def login(payload: dict[str, Any], request: Request) -> dict[str, Any]:
        try:
            customer = service.login(payload)
            remote_session = _issue_platform_session(
                db_path,
                customer.customer_id,
                user_agent=request.headers.get("user-agent", ""),
                client_ip=request.client.host if request.client else "",
            )
            return {
                "ok": True,
                "token": remote_session["token"],
                "expires_at": remote_session["expires_at"],
                "account": _account_payload(customer),
            }
        except Exception as exc:
            _raise_http_error(exc)

    @app.get("/api/customer/me")
    def me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        token = _bearer_token(authorization)
        account = _account_by_token(db_path, token)
        if account is None:
            raise HTTPException(status_code=401, detail="invalid bearer token")
        return {"ok": True, "account": account}

    @app.post("/api/customer/logout")
    def logout(authorization: str | None = Header(default=None)) -> dict[str, bool]:
        token = _bearer_token(authorization)
        with transaction(db_path) as conn:
            conn.execute(
                """
                UPDATE auth_platform_sessions
                SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at = ''
                """,
                (_utc_now(), _hash_token(token)),
            )
        return {"ok": True}

    @app.post("/api/customer/activate")
    def activate(payload: dict[str, Any]) -> dict[str, Any]:
        return _action_response(_call_action(service.activate, payload))

    @app.post("/api/customer/email-code")
    def email_code(payload: dict[str, Any]) -> dict[str, Any]:
        return _action_response(_call_action(service.email_code, payload))

    @app.post("/api/customer/password-reset")
    def password_reset(payload: dict[str, Any]) -> dict[str, Any]:
        return _action_response(_call_action(service.password_reset, payload))

    return app


def create_default_auth_app() -> FastAPI:
    return create_auth_app()


def _call_action(func: Any, payload: dict[str, Any]) -> CustomerAuthActionResult:
    try:
        return func(payload)
    except Exception as exc:
        _raise_http_error(exc)


def _action_response(result: CustomerAuthActionResult) -> dict[str, Any]:
    response: dict[str, Any] = {"ok": result.ok, "message": result.message}
    if result.raw:
        response["raw"] = result.raw
    return response


def _account_payload(customer: CustomerAuthResult) -> dict[str, Any]:
    return {
        "account_id": customer.customer_id,
        "customer_id": customer.customer_id,
        "username": customer.username,
        "email": customer.email,
        "display_name": customer.username,
        "account_status": customer.account_status or "active",
        "role": customer.role or "operator",
        "workspace_code": customer.workspace_code,
        "workspace_name": customer.workspace_name,
        "workspace": {"code": customer.workspace_code, "name": customer.workspace_name},
        "raw": customer.raw,
    }


def _issue_platform_session(
    database_path: Path,
    account_id: str,
    *,
    user_agent: str = "",
    client_ip: str = "",
) -> dict[str, str]:
    token = f"wh_auth_{secrets.token_urlsafe(32)}"
    session_id = f"auth_sess_{secrets.token_urlsafe(24)}"
    now = _utc_now()
    expires_at = (datetime.now(timezone.utc) + REMOTE_SESSION_TTL).isoformat(timespec="seconds")
    with transaction(database_path) as conn:
        conn.execute(
            """
            INSERT INTO auth_platform_sessions (
                session_id, account_id, token_hash, expires_at, revoked_at,
                last_used_at, created_at, user_agent, client_ip
            )
            VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)
            """,
            (session_id, account_id, _hash_token(token), expires_at, now, now, user_agent, client_ip),
        )
    return {"session_id": session_id, "token": token, "expires_at": expires_at}


def _account_by_token(database_path: Path, token: str) -> dict[str, Any] | None:
    now = _utc_now()
    token_hash = _hash_token(token)
    with transaction(database_path) as conn:
        row = conn.execute(
            """
            SELECT
                a.account_id,
                a.username,
                a.email,
                a.display_name,
                a.role,
                a.account_status,
                w.workspace_code,
                w.workspace_name
            FROM auth_platform_sessions s
            JOIN auth_accounts a ON a.account_id = s.account_id
            LEFT JOIN workspaces w ON w.workspace_id = a.workspace_id
            WHERE s.token_hash = ?
              AND s.revoked_at = ''
              AND s.expires_at > ?
            """,
            (token_hash, now),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE auth_platform_sessions SET last_used_at = ? WHERE token_hash = ?",
            (now, token_hash),
        )
    return {
        "account_id": row["account_id"],
        "customer_id": row["account_id"],
        "username": row["username"],
        "email": row["email"],
        "display_name": row["display_name"],
        "role": row["role"],
        "account_status": row["account_status"],
        "workspace_code": row["workspace_code"] or "",
        "workspace_name": row["workspace_name"] or "",
        "workspace": {"code": row["workspace_code"] or "", "name": row["workspace_name"] or ""},
    }


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return token


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise exc

