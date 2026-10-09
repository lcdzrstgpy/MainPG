"""Scoped credentials for product-processing calls through the platform gateway."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from ...config import default_config


_REMOTE_TOKEN: ContextVar[str] = ContextVar("product_processing_remote_token", default="")
_USAGE_IDS: ContextVar[dict[str, str]] = ContextVar("product_processing_usage_ids", default={})
# 直连模式：批次冻结时服务端下发的短期密钥（provider 名 → 明文 key），
# 以及当前批次 freeze_id（启动对账/结算用）。明文只在内存上下文里存在。
_GRANTED_KEYS: ContextVar[dict[str, str]] = ContextVar("product_processing_granted_keys", default={})
_FREEZE_ID: ContextVar[str] = ContextVar("product_processing_freeze_id", default="")


@contextmanager
def server_ai_context(
    token: str,
    usage_ids: dict[str, str],
    granted_keys: dict[str, str] | None = None,
    freeze_id: str = "",
) -> Iterator[None]:
    token_marker = _REMOTE_TOKEN.set(str(token or "").strip())
    usage_marker = _USAGE_IDS.set({str(key): str(value) for key, value in usage_ids.items() if value})
    keys_marker = _GRANTED_KEYS.set(
        {str(provider): str(value) for provider, value in (granted_keys or {}).items() if value}
    )
    freeze_marker = _FREEZE_ID.set(str(freeze_id or "").strip())
    try:
        yield
    finally:
        _FREEZE_ID.reset(freeze_marker)
        _GRANTED_KEYS.reset(keys_marker)
        _USAGE_IDS.reset(usage_marker)
        _REMOTE_TOKEN.reset(token_marker)


def remote_token() -> str:
    return _REMOTE_TOKEN.get()


def usage_id(kind: str) -> str:
    return str(_USAGE_IDS.get().get(kind) or "")


def granted_key(provider: str) -> str:
    """Return the granted short-lived provider key for the current batch, if any."""
    return str(_GRANTED_KEYS.get().get(provider) or "")


def batch_freeze_id() -> str:
    return str(_FREEZE_ID.get() or "")


def granted_keys_snapshot() -> dict[str, Any]:
    """Expose current granted keys (used by provider config to build direct clients)."""
    return dict(_GRANTED_KEYS.get())


def gateway_base_url() -> str:
    return default_config().customer_auth_base_url.rstrip("/")
