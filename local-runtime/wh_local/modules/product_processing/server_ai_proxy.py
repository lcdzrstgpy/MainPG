"""Scoped credentials for product-processing calls through the platform gateway."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from ...config import default_config


_REMOTE_TOKEN: ContextVar[str] = ContextVar("product_processing_remote_token", default="")
_USAGE_IDS: ContextVar[dict[str, str]] = ContextVar("product_processing_usage_ids", default={})


@contextmanager
def server_ai_context(token: str, usage_ids: dict[str, str]) -> Iterator[None]:
    token_marker = _REMOTE_TOKEN.set(str(token or "").strip())
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
