"""产品处理 AI 的服务端托管路由配置。

桌面端只携带当前客户会话和已预留的 usage id；文本与图片上游凭据均由
平台服务持有。BasicSettings 仍可提供非 AI 密钥配置（例如 COS 发布），但
不得把已保存的上游 AI key 接回产品处理调用链。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .server_ai_proxy import granted_keys_snapshot

AI_PROVIDER = "aicoming"
# 默认中转站：用户通过 https://station-88.aicoming.top/ 购买 key（运营转售模式）。
# 若用户在"系统配置"里填了 base_url 或设置 WH_AI_BASE_URL，则以用户配置为准。
AI_BASE_URL = "server-managed"
IMAGE_AI_BASE_URL = "https://api.wuyinkeji.com"
# 不再内置共享密钥：产品处理 AI 为转售模式，每个用户使用自己在系统配置中填写的 key；
# 未配置时 engine/status 明确显示 ai_configured=false，任务对 AI 环节给出可读报错，不静默透传。
AI_API_KEY = ""

TEXT_MODEL = "gpt-5.6-terra"
# 文本统一走低价档模型，且优先选择支持提示词缓存（⚡缓存）的模型以降低重复前缀成本；
# 禁止降级到高价模型（如 gpt-5.4、deepseek-v4-pro）。
TEXT_MODEL_FALLBACK_ORDER = ("gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.4-mini", "gemini-3.1-flash-lite-antigravity")

IMAGE_MODEL = "image_gpt"
REFERENCE_IMAGE_MODEL = "image_gpt"
REFERENCE_IMAGE_MODEL_1K = "gpt-image-2-1k"
# 产品处理出图优先质感：默认固定使用 gpt-image-2-2k，避免 balanced 轮巡回 1k。
IMAGE_MODEL_POOL = ("image_gpt",)
IMAGE_SIZE = "2048x2048"
REFERENCE_IMAGE_SIZE_1K = "1024x1024"
IMAGE_QUALITY = "medium"
# 精品模式固定一次 4K 四宫格，再在本地无降采样拆为四张约 2048×2048 轮播图。
PREMIUM_IMAGE_MODEL = "gpt-image-2-4k"
PREMIUM_IMAGE_SIZE = "4096x4096"

# 运行时模型通常先返回，再由调用方做结构化校验；慢模型不能被客户端过早取消。
# 文本单候选最多等 5 分钟，整条降级链最多 6 分钟，避免四个候选串行等待 20 分钟。
# 2K 参考图编辑实测可超过 3 分钟，因此单次图片请求保留 10 分钟。
DEFAULT_AI_TIMEOUT_SECONDS = 300.0
TEXT_AI_TIMEOUT_SECONDS = 300.0
TEXT_AI_TOTAL_TIMEOUT_SECONDS = 360.0
IMAGE_AI_TIMEOUT_SECONDS = 600.0

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
        return SystemConfigService(Path(db_path)).get_product_processing_runtime_config()
    except Exception:
        return None


def resolve_ai_provider() -> dict[str, Any]:
    """返回不含上游凭据的服务端托管 AI 配置与固定计费模型。"""
    sys_cfg = _try_system_runtime_config()

    # Product-processing upstream credentials belong to the platform server.
    # The desktop carries only its authenticated platform session.
    text_base_url = AI_BASE_URL
    text_api_key = ""
    text_model = TEXT_MODEL

    # 图片 AI（仅 api_key 可覆盖，模型写死）
    image_model = _first_truthy(
        os.environ.get("WH_IMAGE_AI_MODEL"),
        (sys_cfg and sys_cfg.image_ai.model),
        IMAGE_MODEL,
    )
    reference_image_model = REFERENCE_IMAGE_MODEL
    premium_image_model = PREMIUM_IMAGE_MODEL
    premium_image_size = PREMIUM_IMAGE_SIZE

    fallback = list(TEXT_MODEL_FALLBACK_ORDER)

    # 直连模式：批次冻结时服务端下发短期密钥。有 wuyin key 时图片直接打无印
    # 上游（media._request_wuyin_image 已存在），有 ark key 时文本/识图由
    # doubao_ark 直连火山方舟。无下发密钥时保持服务端托管（灰度双轨）。
    granted = granted_keys_snapshot()
    direct_wuyin_key = str(granted.get("wuyin") or "").strip()
    direct_mode = bool(direct_wuyin_key or granted.get("ark"))

    # 解密后的 COS 配置仅作为后端内部运行时数据传给图片发布器；安全摘要不会回显它。
    sys_cos: dict[str, str] = (
        {
            "bucket": str(getattr(sys_cfg.cos, "bucket", "")).strip(),
            "region": str(getattr(sys_cfg.cos, "region", "")).strip(),
            "secret_id": str(getattr(sys_cfg.cos, "secret_id", "")).strip(),
            "secret_key": str(getattr(sys_cfg.cos, "secret_key", "")).strip(),
        }
        if sys_cfg
        else _try_system_cos_public()
    )

    return {
        "provider": AI_PROVIDER,
        "base_url": text_base_url,
        "api_key": text_api_key,
        "direct_mode": direct_mode,
        "text_model": text_model,
        "text_model_fallback_order": fallback,
        "image_model": image_model,
        "reference_image_model": reference_image_model,
        "reference_image_model_1k": REFERENCE_IMAGE_MODEL_1K,
        "reference_image_size_1k": REFERENCE_IMAGE_SIZE_1K,
        "image_models": list(IMAGE_MODEL_POOL),
        "image_size": os.environ.get("WH_AI_IMAGE_SIZE", IMAGE_SIZE).strip(),
        "image_quality": os.environ.get("WH_AI_IMAGE_QUALITY", IMAGE_QUALITY).strip(),
        # 精品模式 4K 四宫格配置（环境变量可覆盖，便于运营按中转实际能力调整）
        "premium_image_model": os.environ.get("WH_AI_PREMIUM_IMAGE_MODEL", premium_image_model).strip(),
        "premium_image_size": os.environ.get("WH_AI_PREMIUM_IMAGE_SIZE", premium_image_size).strip(),
        "timeout_seconds": DEFAULT_AI_TIMEOUT_SECONDS,
        "text_timeout_seconds": TEXT_AI_TIMEOUT_SECONDS,
        "text_total_timeout_seconds": TEXT_AI_TOTAL_TIMEOUT_SECONDS,
        "image_timeout_seconds": IMAGE_AI_TIMEOUT_SECONDS,
        # 系统配置附加信息（供 _media_config_provider 使用）
        "_sys_image_ai": {
            "base_url": IMAGE_AI_BASE_URL if direct_wuyin_key else "server-managed-wuyin",
            "api_key": direct_wuyin_key if direct_wuyin_key else "server-managed",
            "model": image_model,
            "reference_model": reference_image_model,
            "reference_model_1k": REFERENCE_IMAGE_MODEL_1K,
            "reference_size_1k": REFERENCE_IMAGE_SIZE_1K,
        },
        # Product processing may only use the billed server-managed route.
        # Never expose decrypted desktop backup-provider credentials here.
        "_sys_backup_image_ai": None,
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
        "api_key_masked": "server-managed",
        "api_key_configured": True,
        "text_model": provider["text_model"],
        "text_model_fallback_order": provider["text_model_fallback_order"],
        "image_model": provider["image_model"],
        "reference_image_model": provider["reference_image_model"],
        "reference_image_model_1k": provider["reference_image_model_1k"],
        "reference_image_size_1k": provider["reference_image_size_1k"],
        "image_size": provider["image_size"],
        "image_quality": provider["image_quality"],
        "enabled": True,
    }
