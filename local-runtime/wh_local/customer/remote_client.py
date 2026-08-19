from __future__ import annotations

import json
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contracts import (
    CustomerAuthActionResult,
    CustomerAuthRejected,
    CustomerAuthResult,
    CustomerAuthUnavailable,
    CustomerBillingPermissionError,
    CustomerBillingProtocolError,
)


_BILLING_MAX_ATTEMPTS = 5
_BILLING_RETRY_DELAYS = (0.2, 0.6, 1.2, 2.0)
_BILLING_REQUEST_GATE = threading.BoundedSemaphore(2)


class CustomerAuthClient:
    """Thin adapter for the remote platform customer-account service.

    Keep this file as the only place that knows the remote response shape.
    When teammates finalize the control-plane contract, update the endpoint
    names or field normalization here instead of spreading that logic across
    business modules.
    """

    def __init__(self, base_url: str = "", *, timeout_seconds: float = 8):
        self.base_url = str(base_url or "").strip().rstrip("/")
        self.timeout_seconds = timeout_seconds

    def configured(self) -> bool:
        return bool(self.base_url)

    def login(self, payload: dict[str, Any]) -> CustomerAuthResult:
        response = self._post("/api/customer/login", payload)
        return normalize_login_response(response, fallback_username=str(payload.get("username") or payload.get("email") or ""))

    def register(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        return normalize_action_response(self._post("/api/customer/register", payload))

    def activate(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        return normalize_action_response(self._post("/api/customer/activate", payload))

    def email_code(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        return normalize_action_response(self._post("/api/customer/email-code", payload))

    def password_reset(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        return normalize_action_response(self._post("/api/customer/password-reset", payload))

    def change_password(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        return normalize_action_response(self._post("/api/customer/change-password", payload))

    def forgot_password(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        return normalize_action_response(self._post("/api/customer/forgot-password", payload))

    def reset_password(self, payload: dict[str, Any]) -> CustomerAuthActionResult:
        return normalize_action_response(self._post("/api/customer/reset-password", payload))

    def logout(self, remote_token: str) -> CustomerAuthActionResult:
        """Revoke a remote wh_auth_* platform session (single-login state)."""
        if not remote_token:
            return CustomerAuthActionResult(ok=True, message="no remote token to revoke")
        return normalize_action_response(
            self._post(
                "/api/customer/logout",
                {},
                headers={"Authorization": f"Bearer {remote_token}"},
            )
        )

    def billing_summary(self, remote_token: str) -> dict[str, Any]:
        if not remote_token:
            raise CustomerBillingPermissionError()
        return self._billing_result(
            self._get,
            "/api/customer/billing/summary",
            headers={"Authorization": f"Bearer {remote_token}"},
        )

    def create_topup_order(self, remote_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not remote_token:
            raise CustomerBillingPermissionError()
        return self._post(
            "/api/customer/billing/topup-orders",
            payload,
            headers={"Authorization": f"Bearer {remote_token}"},
        )

    def reserve_ai_usage(self, remote_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._billing_post("/api/customer/billing/usage/reserve", remote_token, payload)

    def settle_ai_usage_success(self, remote_token: str, usage_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._billing_post(f"/api/customer/billing/usage/{usage_id}/succeed", remote_token, payload)

    def settle_ai_usage_failure(self, remote_token: str, usage_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._billing_post(f"/api/customer/billing/usage/{usage_id}/fail", remote_token, payload)

    def _billing_post(self, path: str, remote_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not remote_token:
            raise CustomerBillingPermissionError()
        return self._billing_result(
            self._post,
            path,
            payload,
            headers={"Authorization": f"Bearer {remote_token}"},
        )

    def _billing_result(self, function, *args, **kwargs) -> dict[str, Any]:
        for attempt in range(_BILLING_MAX_ATTEMPTS):
            try:
                with _BILLING_REQUEST_GATE:
                    response = function(*args, **kwargs)
            except CustomerBillingProtocolError:
                raise
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise CustomerBillingProtocolError() from exc
            except CustomerAuthUnavailable as exc:
                if attempt + 1 >= _BILLING_MAX_ATTEMPTS:
                    raise CustomerAuthUnavailable(
                        "remote billing service is unavailable"
                    ) from exc
                time.sleep(_BILLING_RETRY_DELAYS[attempt])
                continue
            except CustomerAuthRejected as exc:
                status_code = getattr(exc, "status_code", None)
                if type(status_code) is not int or not 400 <= status_code < 500:
                    raise CustomerBillingProtocolError() from exc
                raise CustomerAuthRejected(
                    status_code,
                    "remote billing request was rejected",
                ) from exc
            except CustomerBillingPermissionError:
                raise
            except PermissionError as exc:
                raise CustomerBillingPermissionError() from exc
            if not isinstance(response, dict):
                raise CustomerBillingProtocolError()
            return response
        raise AssertionError("unreachable")

    def _post(self, path: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        if not self.base_url:
            raise CustomerAuthUnavailable("customer auth service is not configured")
        body = json.dumps(payload).encode("utf-8")
        request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if headers:
            request_headers.update(headers)
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            detail = _extract_error_message(exc)
            if exc.code in (401, 403):
                raise PermissionError(detail or f"customer auth service returned HTTP {exc.code}") from exc
            if 400 <= exc.code < 500:
                raise CustomerAuthRejected(
                    exc.code,
                    detail or f"customer auth service rejected the request (HTTP {exc.code})",
                ) from exc
            raise CustomerAuthUnavailable(f"customer auth service returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise CustomerAuthUnavailable(str(getattr(exc, "reason", exc))) from exc

    def _get(self, path: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        if not self.base_url:
            raise CustomerAuthUnavailable("customer auth service is not configured")
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        request = Request(
            f"{self.base_url}{path}",
            headers=request_headers,
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            detail = _extract_error_message(exc)
            if exc.code in (401, 403):
                raise PermissionError(detail or f"customer auth service returned HTTP {exc.code}") from exc
            if 400 <= exc.code < 500:
                raise CustomerAuthRejected(
                    exc.code,
                    detail or f"customer auth service rejected the request (HTTP {exc.code})",
                ) from exc
            raise CustomerAuthUnavailable(f"customer auth service returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise CustomerAuthUnavailable(str(getattr(exc, "reason", exc))) from exc


def _extract_error_message(exc: HTTPError) -> str:
    """Extract a single-line error message from a FastAPI-style error response.

    Remote auth services usually answer with ``{"detail": "..."}``. The detail
    value may itself be a JSON string wrapping another ``detail`` key (e.g. a
    proxied FastAPI exception). Unwrap one level so the surfaced message is not
    double-encoded.
    """
    raw = exc.read().decode("utf-8", errors="replace")
    try:
        body = json.loads(raw or "{}")
    except Exception:
        return raw[:300]
    detail = body.get("detail") or body.get("message") or body.get("error") if isinstance(body, dict) else ""
    if isinstance(detail, dict):
        detail = detail.get("detail") or detail.get("message") or str(detail)
    if isinstance(detail, str):
        try:
            inner = json.loads(detail)
        except Exception:
            inner = None
        if isinstance(inner, dict):
            detail = inner.get("detail") or inner.get("message") or inner.get("error") or str(inner)
        elif inner is not None:
            detail = inner
        detail = str(detail).strip()
    return detail if detail else raw[:300]


def normalize_login_response(payload: dict[str, Any], *, fallback_username: str = "") -> CustomerAuthResult:
    account = payload.get("account") if isinstance(payload.get("account"), dict) else payload
    workspace = account.get("workspace") if isinstance(account.get("workspace"), dict) else {}
    username = _first_text(account, "username", "name", "display_name") or _first_text(account, "email") or fallback_username
    email = _first_text(account, "email", "mail")
    customer_id = _first_text(account, "customer_id", "account_id", "id", "user_id", "sub") or email or username
    if not username:
        raise ValueError("customer auth response is missing username or email")
    return CustomerAuthResult(
        customer_id=customer_id,
        username=username,
        email=email,
        account_status=_first_text(account, "account_status", "status", "state") or "active",
        remote_token=_first_text(payload, "token", "access_token", "session_token") or _first_text(account, "token", "access_token", "session_token"),
        remote_expires_at=_first_text(payload, "expires_at", "session_expires_at") or _first_text(account, "expires_at", "session_expires_at"),
        role=_normalize_role(_first_text(account, "role", "local_role")),
        workspace_code=_first_text(account, "workspace_code", "company_code") or _first_text(workspace, "code"),
        workspace_name=_first_text(account, "workspace_name", "company_name") or _first_text(workspace, "name"),
        raw=payload,
    )


def normalize_action_response(payload: dict[str, Any]) -> CustomerAuthActionResult:
    return CustomerAuthActionResult(
        ok=bool(payload.get("ok", True)),
        message=str(payload.get("message") or payload.get("detail") or ""),
        raw=payload,
    )


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_role(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"operator", "viewer", "editor"}:
        return "operator"
    return "admin"
