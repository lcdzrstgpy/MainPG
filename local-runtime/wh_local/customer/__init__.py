"""Customer registration/login module skeleton.

This package owns the local side of the platform account flow:

remote customer auth -> local session -> workspace-scoped business modules.
"""

from .contracts import (
    CustomerAuthActionResult,
    CustomerAuthError,
    CustomerAuthResult,
    CustomerAuthUnavailable,
    LocalSession,
)
from .local_session import LocalSessionService, MemoryCustomerSessionStore
from .remote_client import CustomerAuthClient

__all__ = [
    "CustomerAuthActionResult",
    "CustomerAuthClient",
    "CustomerAuthError",
    "CustomerAuthResult",
    "CustomerAuthUnavailable",
    "LocalSession",
    "LocalSessionService",
    "MemoryCustomerSessionStore",
]
