from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class CustomerAuthError(RuntimeError):
    """Base error for customer account operations."""


class CustomerAuthUnavailable(CustomerAuthError):
    """Raised when the remote customer-auth service is missing or unreachable."""


class CustomerAuthProtocolError(CustomerAuthUnavailable):
    """Raised when the remote service answers, but the body is not parseable.

    是 ``CustomerAuthUnavailable`` 的子类，所以既有兜底（503 + 固定话术）不变；
    计费链路会先把它识别成协议错误，避免对一个"怎么读都读不出来"的响应白重试 5 轮。
    """


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


class CustomerBillingPermissionError(PermissionError):
    """Raised when the remote billing service rejects the current session."""

    def __init__(self, status_code: int = 401) -> None:
        if type(status_code) is not int or status_code not in {401, 403}:
            raise ValueError("billing permission status must be 401 or 403")
        self.status_code = status_code
        super().__init__("remote billing session was rejected")


class CustomerAuthPermissionError(PermissionError):
    """Raised when a remote *account* action is rejected with HTTP 401/403.

    与 ``CustomerBillingPermissionError`` 的区别在于：账号操作（注册、登录、验证码、
    改密）必须把**上游原文**透给用户。上游的失败原因是「邀请码已过期」「邮箱验证码
    不正确或已过期」这类可执行信息，以前被统一折叠成「remote billing session was
    rejected」，用户在注册页只会看到一句「操作失败，请稍后重试」，完全无从下手。

    计费侧仍沿用固定话术：那里不该把服务端细节透出去。
    """

    def __init__(self, status_code: int = 401, message: str = "") -> None:
        if type(status_code) is not int or status_code not in {401, 403}:
            raise ValueError("auth permission status must be 401 or 403")
        self.status_code = status_code
        super().__init__(str(message or "").strip() or "customer auth request was rejected")


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
