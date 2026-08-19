from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class CustomerAuthError(RuntimeError):
    """Base error for customer account operations."""


class CustomerAuthUnavailable(CustomerAuthError):
    """Raised when the remote customer-auth service is missing or unreachable."""


class CustomerAuthRejected(CustomerAuthError):
    """A validated client-side representation of a remote 4xx rejection."""

    def __init__(self, status_code: int, message: str):
        validated_status = int(status_code)
        if not 400 <= validated_status < 500:
            raise ValueError("customer auth rejection status must be a 4xx code")
        self.status_code = validated_status
        self.message = str(message)
        super().__init__(self.message)


class CustomerBillingProtocolError(CustomerAuthError):
    """Raised when a remote billing response violates its public contract."""

    def __init__(self) -> None:
        super().__init__("remote billing service returned an invalid response")


@dataclass(frozen=True)
class CustomerAuthResult:
    """Normalized successful login response from the platform auth service."""

    customer_id: str
    username: str
    email: str = ""
    account_status: str = "active"
    login_status: str = "offline"
    remote_token: str = ""
    remote_expires_at: str = ""
    role: str = "admin"
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
    role: str = "admin"
    workspace_id: str = "default"
    workspace_code: str = ""
    workspace_name: str = ""
    remote_token: str = ""
