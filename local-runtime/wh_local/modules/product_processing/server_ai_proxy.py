"""Per-item server AI gateway context.

The desktop process must never contain upstream provider keys.  A product task
does however already have the authenticated platform session and its reserved
usage records.  Keep that short-lived state in a context variable while one
item is being processed so the adapters can ask the platform to perform the
actual provider call on behalf of the correct account.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from ...config import default_config


_REMOTE_TOKEN: ContextVar[str] = ContextVar("product_processing_remote_token", default="")
_USAGE_IDS: ContextVar[dict[str, str]] = ContextVar("product_processing_usage_ids", default={})


@contextmanager
def server_ai_context(remote_token: str, usage_ids: dict[str, str]) -> Iterator[None]:
    token_marker = _REMOTE_TOKEN.set(str(remote_token or "").strip())
    usage_marker = _USAGE_IDS.set({str(key): str(value) for key, value in usage_ids.items() if value})
    try:
        yield
    finally:
        _USAGE_IDS.reset(usage_marker)
        _REMOTE_TOKEN.reset(token_marker)


def remote_token() -> str:
    return _REMOTE_TOKEN.get()


def usage_id(kind: str) -> str:
    return str(_USAGE_IDS.get().get(kind) or "")


def gateway_base_url() -> str:
    return default_config().customer_auth_base_url.rstrip("/")


def available() -> bool:
    return bool(gateway_base_url() and remote_token())
