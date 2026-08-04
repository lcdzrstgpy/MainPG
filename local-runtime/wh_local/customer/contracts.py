from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class CustomerAuthError(RuntimeError):
    """Base error for customer account operations."""


class CustomerAuthUnavailable(CustomerAuthError):
    """Raised when the remote customer-auth service is missing or unreachable."""


@dataclass(frozen=True)
class CustomerAuthResult:
    """Normalized successful login response from the platform auth service."""

    customer_id: str
    username: str
    email: str = ""
    account_status: str = "active"
    remote_token: str = ""
    remote_expires_at: str = ""
    role: str = "operator"
    workspace_code: str = ""
    workspace_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CustomerAuthActionResult:
    """Normalized non-login action response, e.g. register/email-code/reset."""

    ok: bool = True
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalSession:
    """Local workbench session returned to the frontend after remote auth succeeds."""

    user_id: str
    token: str
    expires_at: str
    username: str
    role: str = "operator"
    workspace_code: str = ""
    workspace_name: str = ""
