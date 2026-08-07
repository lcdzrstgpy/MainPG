"""产品处理 AI 提供方中转配置。

对照原型程序（ecommerce-automation-workbench）的 native_product_engine 配置，
AI 调用统一走 OpenAI 兼容中转。

优先级：系统配置（BasicSettings DB） > 环境变量 > 硬编码默认值。
系统配置通过"系统配置"板块的 Web UI 进行管理，密钥加密存储在 secret_values 表中。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

AI_PROVIDER = "aicoming"
AI_BASE_URL = "https://api.aicoming.top/v1"
AI_API_KEY = "sk-980fea67bd0f1ffe46802653d114be5463e27f2e358267e3"  # 硬编码兜底

TEXT_MODEL = "gpt-5.6-terra"
# 文本统一走低价档模型，且优先选择支持提示词缓存（⚡缓存）的模型以降低重复前缀成本；
# 禁止降级到高价模型（如 gpt-5.4、deepseek-v4-pro）。
TEXT_MODEL_FALLBACK_ORDER = ("gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.4-mini", "gemini-3.1-flash-lite-antigravity")

IMAGE_MODEL = "gpt-image-2-1k"
REFERENCE_IMAGE_MODEL = "gpt-image-2-1k"
# 图片模型池：同中转多模型之间按请求轮巡（balanced/round_robin），单模型挂掉自动切换。
IMAGE_MODEL_POOL = ("gpt-image-2-1k", "gpt-image-2-2k")
IMAGE_SIZE = "1024x1024"
IMAGE_QUALITY = "medium"

DEFAULT_AI_TIMEOUT_SECONDS = 60.0

# 应用组合根（create_app 中注入），指向 BasicSettings 使用的 SQLite 数据库。
_system_config_db_path: str | None = None


def register_system_config_db_path(db_path: str | Path) -> None:
    """由应用组合根调用，注册系统配置数据库路径。

    仅需在 create_app 中调用一次；调用后 resolve_ai_provider() 会尝试从此 DB
    读取系统配置板块（BasicSettings）保存的 AI 提供方设置。
    """
    global _system_config_db_path
    _system_config_db_path = str(db_path)


def _try_system_runtime_config() -> Any | None:
    """尝试从 BasicSettings 数据库加载 RuntimeSystemConfig，失败返回 None。"""
    db_path = _system_config_db_path
    if not db_path or not Path(db_path).is_file():
        return None
    try:
        from ...modules.basic_settings.service import SystemConfigService
    except Exception:
        return None
    try:
        return SystemConfigService(Path(db_path)).get_runtime_config()
    except Exception:
        return None


def resolve_ai_provider() -> dict[str, Any]:
    """返回 AI 中转提供方配置。

    模型名一律写死（控制成本，防止被系统配置/环境变量切到贵模型）：
    text_model / image_model / reference_image_model / 降级链 / 图片模型池
    均取本模块常量。仅 base_url / api_key 允许被系统配置 > 环境变量覆盖
    （换中转站或换 key 需要）。image_size / image_quality 保留环境变量覆盖。
    """
    sys_cfg = _try_system_runtime_config()

    # 文本 AI（仅 base_url/api_key 可覆盖，模型写死）
    text_base_url = _first_truthy(
        (sys_cfg and sys_cfg.text_ai.base_url),
        os.environ.get("WH_AI_BASE_URL"),
        AI_BASE_URL,
    ).rstrip("/")
    text_api_key = _first_truthy(
        (sys_cfg and sys_cfg.text_ai.api_key),
        os.environ.get("WH_AI_API_KEY"),
        AI_API_KEY,
    ).strip()
    text_model = TEXT_MODEL

    # 图片 AI（仅 api_key 可覆盖，模型写死）
    image_api_key = _first_truthy(
        (sys_cfg and sys_cfg.image_ai.api_key),
        os.environ.get("WH_AI_API_KEY"),
        AI_API_KEY,
    ).strip()
    image_model = IMAGE_MODEL
    reference_image_model = REFERENCE_IMAGE_MODEL

    fallback = list(TEXT_MODEL_FALLBACK_ORDER)

    # 读取 COS 系统配置公开字段（bucket/region），密钥由 _media_config_provider 处理
    sys_cos: dict[str, str] = _try_system_cos_public()

    return {
        "provider": AI_PROVIDER,
        "base_url": text_base_url,
        "api_key": text_api_key,
        "text_model": text_model,
        "text_model_fallback_order": fallback,
        "image_model": image_model,
        "reference_image_model": reference_image_model,
        "image_models": list(IMAGE_MODEL_POOL),
        "image_size": os.environ.get("WH_AI_IMAGE_SIZE", IMAGE_SIZE).strip(),
        "image_quality": os.environ.get("WH_AI_IMAGE_QUALITY", IMAGE_QUALITY).strip(),
        "timeout_seconds": DEFAULT_AI_TIMEOUT_SECONDS,
        # 系统配置附加信息（供 _media_config_provider 使用）
        "_sys_image_ai": (
            {
                "base_url": (sys_cfg.image_ai.base_url or text_base_url).rstrip("/"),
                "api_key": image_api_key,
                "model": image_model,
                "reference_model": reference_image_model,
            }
            if sys_cfg and sys_cfg.image_ai.configured
            else None
        ),
        "_sys_backup_image_ai": (
            {
                "base_url": sys_cfg.backup_image_ai.base_url.rstrip("/"),
                "api_key": sys_cfg.backup_image_ai.api_key,
                "model": sys_cfg.backup_image_ai.model,
                "reference_model": sys_cfg.backup_image_ai.reference_model,
            }
            if sys_cfg and sys_cfg.backup_image_ai.configured
            else None
        ),
        "_sys_limits": dict(sys_cfg.limits) if sys_cfg and sys_cfg.limits else {},
        "_sys_updates": dict(sys_cfg.updates) if sys_cfg and sys_cfg.updates else {},
        "_sys_cos": sys_cos,
    }


def _try_system_cos_public() -> dict[str, str]:
    """从系统配置 DB 读取 COS 公开字段（bucket/region），不包含密钥。"""
    db_path = _system_config_db_path
    if not db_path or not Path(db_path).is_file():
        return {}
    try:
        from ...modules.basic_settings.service import SystemConfigService
    except Exception:
        return {}
    try:
        config = SystemConfigService(Path(db_path))._load_public_config()
        cos = config.get("cos", {}) if isinstance(config, dict) else {}
        return {"bucket": str(cos.get("bucket", "")).strip(), "region": str(cos.get("region", "")).strip()}
    except Exception:
        return {}


def _first_truthy(*values: Any) -> str:
    for v in values:
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


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
