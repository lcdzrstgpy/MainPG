"""Scoped credentials/context for product-processing AI calls (client-direct mode).

产品处理固定客户端直连：批次冻结时服务端下发短期 provider 密钥（ark/wuyin），
AI 请求由客户端直接发往第三方，不再经平台网关转发。本模块只承载任务级上下文
（token、usage 预留、直连密钥、freeze_id）。

为规避「服务端密钥签发异常导致客户端拿不到密钥而中止任务」，批次未下发的
provider 密钥会回落客户端内置兜底密钥（见 direct_credentials）；是否可用最终由
服务端冻结（余额/积分门控）决定。回落只发生在本模块的读取层，绝不回落到托管网关。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

from .direct_credentials import builtin_provider_key


_REMOTE_TOKEN: ContextVar[str] = ContextVar("product_processing_remote_token", default="")
_USAGE_IDS: ContextVar[dict[str, str]] = ContextVar("product_processing_usage_ids", default={})
# 直连模式：批次冻结时服务端下发的短期密钥（provider 名 → 明文 key），
# 以及当前批次 freeze_id（启动对账/结算用）。明文只在内存上下文里存在。
_GRANTED_KEYS: ContextVar[dict[str, str]] = ContextVar("product_processing_granted_keys", default={})
_FREEZE_ID: ContextVar[str] = ContextVar("product_processing_freeze_id", default="")
# 出图链路生效策略：由服务端在批次冻结响应里下发（见 _image_provider_plan）。
# 客户端只负责对接，不自行决定走哪条链路、也不携带任何链路凭据。
_IMAGE_PLAN: ContextVar[dict[str, Any]] = ContextVar("product_processing_image_plan", default={})


@contextmanager
def server_ai_context(
    token: str,
    usage_ids: dict[str, str],
    granted_keys: dict[str, str] | None = None,
    freeze_id: str = "",
    image_plan: Mapping[str, Any] | None = None,
) -> Iterator[None]:
    token_marker = _REMOTE_TOKEN.set(str(token or "").strip())
    usage_marker = _USAGE_IDS.set({str(key): str(value) for key, value in usage_ids.items() if value})
    keys_marker = _GRANTED_KEYS.set(
        {str(provider): str(value) for provider, value in (granted_keys or {}).items() if value}
    )
    freeze_marker = _FREEZE_ID.set(str(freeze_id or "").strip())
    plan_marker = _IMAGE_PLAN.set(dict(image_plan) if isinstance(image_plan, Mapping) else {})
    try:
        yield
    finally:
        _IMAGE_PLAN.reset(plan_marker)
        _FREEZE_ID.reset(freeze_marker)
        _GRANTED_KEYS.reset(keys_marker)
        _USAGE_IDS.reset(usage_marker)
        _REMOTE_TOKEN.reset(token_marker)


def remote_token() -> str:
    return _REMOTE_TOKEN.get()


def usage_id(kind: str) -> str:
    return str(_USAGE_IDS.get().get(kind) or "")


def granted_key(provider: str) -> str:
    """Return the granted short-lived provider key, falling back to the built-in client key.

    批次下发的短期密钥优先；未下发时回落客户端内置兜底密钥（可能为空）。
    """
    return str(_GRANTED_KEYS.get().get(provider) or "") or builtin_provider_key(provider)


def batch_freeze_id() -> str:
    return str(_FREEZE_ID.get() or "")


def parse_image_plan(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """从批次冻结响应里取出服务端下发的出图链路策略。

    缺失或格式不符一律视为「未下发」——此时客户端沿用内置默认链路（无印直连），
    与旧服务端保持兼容。出图链路的开关与切换权完全在服务器（运维改服务器配置 +
    重启即生效），客户端不自行判断。
    """
    plan = payload.get("image_plan") if isinstance(payload, Mapping) else None
    return dict(plan) if isinstance(plan, Mapping) else {}


def granted_image_plan() -> dict[str, Any]:
    """当前任务上下文里的出图链路策略（未下发时为空 dict）。"""
    plan = _IMAGE_PLAN.get()
    return dict(plan) if isinstance(plan, Mapping) else {}


def merge_builtin_keys(granted: dict[str, str] | None) -> dict[str, str]:
    """把批次未下发的 provider 用客户端内置兜底密钥补齐（补齐后用于缺密钥判定与上下文）。"""
    merged = {str(provider): str(value) for provider, value in (granted or {}).items() if value}
    for provider in ("ark", "wuyin"):
        if not merged.get(provider):
            builtin = builtin_provider_key(provider)
            if builtin:
                merged[provider] = builtin
    return merged


def granted_keys_snapshot() -> dict[str, Any]:
    """Expose current granted keys merged with built-in fallbacks (used to build direct clients)."""
    return merge_builtin_keys(_GRANTED_KEYS.get())
