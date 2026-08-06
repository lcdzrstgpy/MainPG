"""产品处理 AI 提供方中转配置。

对照原型程序（ecommerce-automation-workbench）的 native_product_engine 配置，
AI 调用统一走 OpenAI 兼容中转：https://api.aicoming.top/v1。

注意：api_key 为用户临时提供，暂时写死便于本地联调；环境变量可覆盖（WH_AI_API_KEY 等）。
"""

from __future__ import annotations

import os
from typing import Any

AI_PROVIDER = "aicoming"
AI_BASE_URL = "https://api.aicoming.top/v1"
AI_API_KEY = "sk-980fea67bd0f1ffe46802653d114be5463e27f2e358267e3"

TEXT_MODEL = "gpt-5.4-mini"
TEXT_MODEL_FALLBACK_ORDER = ("gpt-5.4-mini", "gpt-5.4", "deepseek-v4-pro")

IMAGE_MODEL = "gpt-image-2"
REFERENCE_IMAGE_MODEL = "gpt-image-2-1k"
IMAGE_SIZE = "1024x1024"
IMAGE_QUALITY = "medium"

DEFAULT_AI_TIMEOUT_SECONDS = 60.0


def resolve_ai_provider() -> dict[str, Any]:
    """返回 AI 中转提供方配置（环境变量可覆盖写死的默认值）。"""
    return {
        "provider": AI_PROVIDER,
        "base_url": os.environ.get("WH_AI_BASE_URL", AI_BASE_URL).rstrip("/"),
        "api_key": os.environ.get("WH_AI_API_KEY", AI_API_KEY).strip(),
        "text_model": os.environ.get("WH_AI_TEXT_MODEL", TEXT_MODEL).strip(),
        "text_model_fallback_order": [
            model.strip()
            for model in os.environ.get("WH_AI_TEXT_MODEL_FALLBACK", ",".join(TEXT_MODEL_FALLBACK_ORDER)).split(",")
            if model.strip()
        ],
        "image_model": os.environ.get("WH_AI_IMAGE_MODEL", IMAGE_MODEL).strip(),
        "reference_image_model": os.environ.get("WH_AI_REFERENCE_IMAGE_MODEL", REFERENCE_IMAGE_MODEL).strip(),
        "image_size": os.environ.get("WH_AI_IMAGE_SIZE", IMAGE_SIZE).strip(),
        "image_quality": os.environ.get("WH_AI_IMAGE_QUALITY", IMAGE_QUALITY).strip(),
        "timeout_seconds": DEFAULT_AI_TIMEOUT_SECONDS,
    }


def masked_api_key(api_key: str) -> str:
    """掩码展示 API key，避免明文外泄。"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:3]}...{api_key[-4:]}"


def ai_provider_summary() -> dict[str, Any]:
    """返回可安全暴露给前端的提供方摘要（key 掩码）。"""
    provider = resolve_ai_provider()
    return {
        "provider": provider["provider"],
        "base_url": provider["base_url"],
        "api_key_masked": masked_api_key(provider["api_key"]),
        "api_key_configured": bool(provider["api_key"]),
        "text_model": provider["text_model"],
        "text_model_fallback_order": provider["text_model_fallback_order"],
        "image_model": provider["image_model"],
        "reference_image_model": provider["reference_image_model"],
        "image_size": provider["image_size"],
        "image_quality": provider["image_quality"],
        "enabled": bool(provider["api_key"]),
    }
