from __future__ import annotations

import re
from typing import Any

from .contracts import CustomerAuthRejected, CustomerAuthUnavailable
from .local_session import LocalSessionService
from .remote_client import CustomerAuthClient


_AUTHORIZATION_VALUE = re.compile(
    r"(?i)(\bauthorization\b\s*[:=]\s*)((?:bearer\s+)?[^\s,;]+)"
)
_CREDENTIAL_VALUE = re.compile(
    r"(?i)(\b(?:remote[\s._-]*token|access[\s._-]*token|session[\s._-]*token|"
    r"ark[\s._-]*api[\s._-]*key|wuyin[\s._-]*api[\s._-]*key|api[\s._-]*key|token)"
    r"\b\s*[:=]\s*)[^\s,;]+"
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")


def _public_account(customer: Any) -> dict[str, Any]:
    """Serialize only fields that belong to the local customer API contract."""
    return {
        "customer_id": customer.customer_id,
        "username": customer.username,
        "email": customer.email,
        "account_status": customer.account_status,
        "login_status": customer.login_status,
        "role": customer.role,
        "workspace_code": customer.workspace_code,
        "workspace_name": customer.workspace_name,
    }


def _public_action(result: Any) -> dict[str, Any]:
    """Do not forward an upstream action's arbitrary ``raw`` envelope."""
    return {"ok": bool(result.ok), "message": str(result.message)}


def _safe_error_detail(value: object) -> str:
    detail = str(value).strip()[:300]
    detail = _AUTHORIZATION_VALUE.sub(
        lambda match: (
            f"{match.group(1)}Bearer [redacted]"
            if match.group(2).lower().startswith("bearer ")
            else f"{match.group(1)}[redacted]"
        ),
        detail,
    )
    detail = _BEARER_VALUE.sub("Bearer [redacted]", detail)
    return _CREDENTIAL_VALUE.sub(lambda match: f"{match.group(1)}[redacted]", detail)


def create_customer_router(remote_auth: CustomerAuthClient, sessions: LocalSessionService):
    """Create FastAPI routes for customer account access.

    The import is intentionally inside the function so the module can still be
    imported in tests or tooling before FastAPI dependencies are installed.
    """

    from fastapi import APIRouter, Header, HTTPException

    router = APIRouter(prefix="/api/customer", tags=["customer-auth"])

    def handle_auth_error(exc: Exception):
        if isinstance(exc, CustomerAuthUnavailable):
            raise HTTPException(status_code=503, detail=_safe_error_detail(exc))
        if isinstance(exc, CustomerAuthRejected):
            raise HTTPException(status_code=exc.status_code, detail=_safe_error_detail(exc.message))
        if isinstance(exc, PermissionError):
            raise HTTPException(status_code=403, detail=_safe_error_detail(exc))
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=_safe_error_detail(exc))
        raise exc

    def bearer_token(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        return authorization.removeprefix("Bearer ").strip()

    @router.post("/login")
    def login(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            customer = remote_auth.login(payload)
            session = sessions.login_customer(customer)
            return {
                "ok": True,
                "user_id": session.user_id,
                "token": session.token,
                "expires_at": session.expires_at,
                "account": _public_account(customer),
            }
        except Exception as exc:
            handle_auth_error(exc)

    @router.post("/register")
    def register(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _public_action(remote_auth.register(payload))
        except Exception as exc:
            handle_auth_error(exc)

    @router.post("/activate")
    def activate(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _public_action(remote_auth.activate(payload))
        except Exception as exc:
            handle_auth_error(exc)

    @router.post("/email-code")
    def email_code(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _public_action(remote_auth.email_code(payload))
        except Exception as exc:
            handle_auth_error(exc)

    @router.post("/password-reset")
    def password_reset(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _public_action(remote_auth.password_reset(payload))
        except Exception as exc:
            handle_auth_error(exc)

    @router.post("/change-password")
    def change_password(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _public_action(remote_auth.change_password(payload))
        except Exception as exc:
            handle_auth_error(exc)

    @router.post("/forgot-password")
    def forgot_password(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _public_action(remote_auth.forgot_password(payload))
        except Exception as exc:
            handle_auth_error(exc)

    @router.post("/reset-password")
    def reset_password(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return _public_action(remote_auth.reset_password(payload))
        except Exception as exc:
            handle_auth_error(exc)

    @router.get("/me")
    def me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        try:
            return sessions.me(bearer_token(authorization))
        except Exception as exc:
            handle_auth_error(exc)

    def remote_token_from_local_session(authorization: str | None) -> str:
        token = bearer_token(authorization)
        session = sessions.store.get_session(token)
        if session is None:
            raise PermissionError("invalid bearer token")
        if not session.remote_token:
            raise CustomerAuthUnavailable("remote customer session is missing")
        return session.remote_token

    @router.get("/billing/summary")
    def billing_summary(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        try:
            if not hasattr(remote_auth, "billing_summary"):
                raise CustomerAuthUnavailable("remote billing service is not configured")
            return remote_auth.billing_summary(remote_token_from_local_session(authorization))
        except Exception as exc:
            handle_auth_error(exc)

    @router.get("/billing/usage")
    def billing_usage_history(
        cursor: str = "",
        limit: int = 30,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            if not hasattr(remote_auth, "billing_usage_history"):
                raise CustomerAuthUnavailable("remote billing history is not configured")
            return remote_auth.billing_usage_history(
                remote_token_from_local_session(authorization),
                cursor=cursor,
                limit=limit,
            )
        except Exception as exc:
            handle_auth_error(exc)

    @router.post("/billing/topup-orders")
    def create_topup_order(payload: dict[str, Any], authorization: str | None = Header(default=None)) -> dict[str, Any]:
        try:
            if not hasattr(remote_auth, "create_topup_order"):
                raise CustomerAuthUnavailable("remote billing service is not configured")
            return remote_auth.create_topup_order(
                remote_token_from_local_session(authorization),
                payload,
            )
        except Exception as exc:
            handle_auth_error(exc)

    @router.post("/logout")
    def logout(authorization: str | None = Header(default=None)) -> dict[str, bool]:
        try:
            token = bearer_token(authorization)
            # 登出时联动撤销云端登录态（单端登录），失败不阻断本地登出。
            try:
                session = sessions.store.get_session(token)
                remote_token = session.remote_token if session is not None else ""
                if remote_token:
                    remote_auth.logout(remote_token)
            except Exception:
                pass
            sessions.logout(token)
            return {"ok": True}
        except Exception as exc:
            handle_auth_error(exc)

    return router
