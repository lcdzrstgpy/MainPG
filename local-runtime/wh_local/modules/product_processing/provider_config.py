"""产品处理 AI 的客户端直连路由配置。

生图/文本请求一律由客户端直连上游：批次冻结时服务端下发短期密钥（ark/wuyin），
客户端在此解析出直连地址与密钥；批次未下发时回落到客户端内置兜底密钥
（见 direct_credentials），仍无凭据则任务中止，绝不回落到平台托管网关。
BasicSettings 仍可提供非 AI 密钥配置（例如 COS 发布），但不得把已保存的上游
AI key 接回产品处理调用链。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from .server_ai_proxy import granted_image_plan, granted_keys_snapshot

AI_PROVIDER = "aicoming"
# 默认中转站：用户通过 https://station-88.aicoming.top/ 购买 key（运营转售模式）。
# 若用户在"系统配置"里填了 base_url 或设置 WH_AI_BASE_URL，则以用户配置为准。
AI_BASE_URL = "server-managed"
IMAGE_AI_BASE_URL = "https://api.wuyinkeji.com"
# 火山方舟官方生图（Seedream）直连地址：本地联调可用 WH_PP_IMAGE_PROVIDER=ark 强制切换。
ARK_IMAGE_AI_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
# 不再内置共享密钥：产品处理 AI 为转售模式，每个用户使用自己在系统配置中填写的 key；
# 未配置时 engine/status 明确显示 ai_configured=false，任务对 AI 环节给出可读报错，不静默透传。
AI_API_KEY = ""

TEXT_MODEL = "gpt-5.6-terra"
# 文本统一走低价档模型，且优先选择支持提示词缓存（⚡缓存）的模型以降低重复前缀成本；
# 禁止降级到高价模型（如 gpt-5.4、deepseek-v4-pro）。
TEXT_MODEL_FALLBACK_ORDER = ("gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.4-mini", "gemini-3.1-flash-lite-antigravity")

# AI 处理默认生图模型：火山方舟 Seedream 5.0 Flash（参考图直传、无水印）。
DEFAULT_IMAGE_MODEL = "doubao-seedream-5-0-flash-260915"
IMAGE_MODEL = DEFAULT_IMAGE_MODEL
REFERENCE_IMAGE_MODEL = DEFAULT_IMAGE_MODEL
REFERENCE_IMAGE_MODEL_1K = "gpt-image-2-1k"
# 生图模型的客户端白名单：桌面端只允许在这些名字之间切换，其它历史值一律回落默认。
# 上游端点与凭据仍由服务端持有，客户端只传模型名。
IMAGE_MODEL_CHOICES = (
    "doubao-seedream-5-0-flash-260915",
    "image_gpt",
    "image_gpt_2.5",
)
_IMAGE_MODEL_ALIASES = {
    DEFAULT_IMAGE_MODEL: DEFAULT_IMAGE_MODEL,
    "image_gpt": "image_gpt",
    "image_gpt_2.5": "image_gpt_2.5",
    # 旧接口只对应 GPT-Image-2；其中 ``gpt-image-2-2k`` 是旧版默认配置值，
    # 统一迁移到新的默认模型（Seedream 5.0 Flash）。
    "gpt-image-2": "image_gpt",
    "gpt-image-2-1k": "image_gpt",
    "gpt-image-2-2k": DEFAULT_IMAGE_MODEL,
    "gpt-image-2-4k": "image_gpt",
}
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


def registered_system_config_db_path() -> str | None:
    """返回应用组合根注册的系统配置数据库路径。"""
    return _system_config_db_path


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


def parse_image_chain(plan: Mapping[str, Any] | None) -> list[dict[str, str]]:
    """把服务端下发的出图链路策略解析为有序 provider 列表（最多两条）。

    供任务上下文之外的调用方使用（如组合套装在子线程里自建图片处理器时，
    读不到 ``server_ai_context`` 的 ContextVar，只能拿 freeze 响应里的原始策略解析）。
    """
    entries = plan.get("providers") if isinstance(plan, Mapping) else None
    if not isinstance(entries, list):
        return []
    chain: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, Mapping):
            continue
        base_url = str(item.get("base_url") or "").strip().rstrip("/")
        api_key = str(item.get("api_key") or "").strip()
        model = str(item.get("model") or "").strip()
        if not (base_url and api_key and model):
            continue
        chain.append(
            {
                "role": str(item.get("role") or "").strip() or ("primary" if not chain else "backup"),
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "reference_model": str(item.get("reference_model") or "").strip() or model,
            }
        )
        if len(chain) == 2:
            break
    return chain


def image_chain_supplied(plan: Mapping[str, Any] | None) -> bool:
    """服务端策略里是否带出了可用出图链路。

    供任务启动时判定「是否仍要求 wuyin 短期密钥」：策略已指定链路（例如故障期整体
    切到中转）时，服务端本就不会再下发 wuyin，此时不应因缺少 wuyin 而中止任务。
    """
    return bool(parse_image_chain(plan))


def _image_chain_from_plan() -> list[dict[str, str]]:
    """当前任务上下文的出图链路（服务端下发）。

    契约（服务端批次冻结响应 ``freeze.image_plan``）::

        {"mode": "primary|relay|auto",
         "providers": [{"role", "base_url", "api_key", "model", "reference_model"}, ...]}

    **链路开关与切换权完全在服务器**：服务端已按当前生效模式把要用的链路按顺序排好
    （只用无印 / 只用中转 / 无印优先+中转兜底），客户端只按顺序对接——第 1 条作主链路、
    第 2 条作备用兜底（对应 media._providers 的 primary / backup 两段）。

    未下发（旧服务端）或字段不全时返回空列表，客户端沿用内置默认链路（无印直连），
    行为与切直连后完全一致。
    """
    return parse_image_chain(granted_image_plan())


def resolve_ai_provider() -> dict[str, Any]:
    """返回不含上游凭据的服务端托管 AI 配置与固定计费模型。"""
    sys_cfg = _try_system_runtime_config()

    # Product-processing upstream credentials belong to the platform server.
    # The desktop carries only its authenticated platform session.
    text_base_url = AI_BASE_URL
    text_api_key = ""
    text_model = TEXT_MODEL

    # 本地联调覆盖：显式指定链路时优先于服务端 image_plan（用于临时切链路）。
    local_chain = local_image_chain()

    # 当前选择的生图模型：本地覆盖 > 环境变量 > 系统配置 > 内置默认（Seedream）。
    selected_model = normalize_image_model(
        _first_truthy(
            os.environ.get("WH_IMAGE_AI_MODEL"),
            (sys_cfg and sys_cfg.image_ai.model),
            IMAGE_MODEL,
        )
    )

    # 客户端固定直连：批次冻结时服务端下发短期密钥（ark/wuyin）。
    # granted_keys_snapshot 已把批次未下发的 provider 用客户端内置兜底密钥补齐。
    granted = granted_keys_snapshot()
    direct_wuyin_key = str(granted.get("wuyin") or "").strip()
    direct_ark_key = str(granted.get("ark") or "").strip()
    direct_mode = True

    # 出图链路：本地覆盖 > 服务端下发策略 > 按当前模型推导的默认链路。
    image_chain = _image_chain_from_plan()
    primary_chain = (
        local_chain
        or (image_chain[0] if image_chain else None)
        or _chain_for_model(selected_model)
        or _chain_for_model(DEFAULT_IMAGE_MODEL)
    )
    backup_chain = None if local_chain else (image_chain[1] if len(image_chain) > 1 else None)
    chain_provider = str((primary_chain or {}).get("provider") or "").strip()
    if not chain_provider:
        chain_base = str((primary_chain or {}).get("base_url") or "").lower()
        chain_provider = "ark" if "ark.cn-beijing.volces.com" in chain_base else "wuyin"
    chain_api_key = str((primary_chain or {}).get("api_key") or "").strip()
    if not chain_api_key:
        chain_api_key = direct_ark_key if chain_provider == "ark" else direct_wuyin_key

    image_model = str((primary_chain or {}).get("model") or selected_model)
    # 参考图编辑与主生图必须用同一个模型，否则切换后两条链路走不同上游。
    reference_image_model = str((primary_chain or {}).get("reference_model") or image_model)
    premium_image_model = image_model if chain_provider == "ark" else PREMIUM_IMAGE_MODEL
    premium_image_size = PREMIUM_IMAGE_SIZE

    fallback = list(TEXT_MODEL_FALLBACK_ORDER)

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
        "provider": "ark" if chain_provider == "ark" else AI_PROVIDER,
        "base_url": text_base_url,
        "api_key": text_api_key,
        "direct_mode": direct_mode,
        "text_model": text_model,
        "text_model_fallback_order": fallback,
        "image_model": image_model,
        "reference_image_model": reference_image_model,
        "reference_image_model_1k": REFERENCE_IMAGE_MODEL_1K,
        "reference_image_size_1k": REFERENCE_IMAGE_SIZE_1K,
        # 模型池必须跟着下发策略走：media._providers 优先读 image_models，若这里仍写死
        # 内置模型名，服务端下发的 model（如切到无印 nanoBanana）会被静默忽略。
        "image_models": [primary_chain["model"]] if primary_chain else [image_model],
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
            # 主链路：服务端下发了策略就照它走（含故障期切到中转的情况）；
            # 未下发时用无印直连默认值。
            "base_url": (primary_chain or {}).get("base_url") or IMAGE_AI_BASE_URL,
            # 真实请求只发生在任务上下文（server_ai_context 内），使用冻结下发的
            # 短期密钥；无任务上下文（如启动自检 engine_status）时用占位符，仅为让
            # 图片能力报告为已配置，绝不会发起真实请求。既未下发、也无链路策略的
            # 任务会在 service._execute_task_direct 中止，不回落到平台托管网关。
            "api_key": chain_api_key or "direct-pending",
            "model": (primary_chain or {}).get("model") or image_model,
            "reference_model": (primary_chain or {}).get("reference_model") or reference_image_model,
            "reference_model_1k": REFERENCE_IMAGE_MODEL_1K,
            "reference_size_1k": REFERENCE_IMAGE_SIZE_1K,
        },
        # 备用链路：仅当服务端策略里带了第二条链路（无印优先 + 中转兜底）时才存在。
        # 用户侧不携带任何链路凭据，也不自行切换。
        "_sys_backup_image_ai": (
            {
                "base_url": backup_chain["base_url"],
                "api_key": backup_chain["api_key"],
                "model": backup_chain["model"],
                "reference_model": backup_chain["reference_model"],
            }
            if backup_chain
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


def normalize_image_model(value: Any) -> str:
    """把任意来源的图片模型名收敛到客户端白名单，未知值回到默认 image_gpt。"""
    return _IMAGE_MODEL_ALIASES.get(str(value or "").strip().lower(), IMAGE_MODEL)


def local_image_chain() -> dict[str, str] | None:
    """本地联调用的生图链路覆盖（不设置环境变量时返回 None）。

    正常运行时出图链路由服务端 ``freeze.image_plan`` 下发；本地想单独验证某个模型
    （例如切到火山方舟 Seedream 5.0 Flash）时，用环境变量强制指定：

    - ``WH_PP_IMAGE_PROVIDER``：``ark`` / ``wuyin``（可选，按模型/地址自动推断）
    - ``WH_PP_IMAGE_MODEL``：模型名，例如 ``doubao-seedream-5-0-flash-260915``
    - ``WH_PP_IMAGE_BASE_URL``：上游地址（默认按 provider 取方舟/无印）
    - ``WH_PP_IMAGE_API_KEY``：显式 key；不填时在任务内复用冻结下发的 provider key
    """
    provider = str(os.environ.get("WH_PP_IMAGE_PROVIDER", "")).strip().lower()
    model = str(os.environ.get("WH_PP_IMAGE_MODEL", "")).strip()
    base_url = str(os.environ.get("WH_PP_IMAGE_BASE_URL", "")).strip().rstrip("/")
    api_key = str(os.environ.get("WH_PP_IMAGE_API_KEY", "")).strip()
    if not provider and not model and not base_url:
        return None
    if provider not in {"ark", "wuyin"}:
        provider = (
            "ark"
            if "seedream" in model.lower() or "ark" in base_url.lower()
            else "wuyin"
        )
    if not model:
        model = IMAGE_MODEL
    if not base_url:
        base_url = ARK_IMAGE_AI_BASE_URL if provider == "ark" else IMAGE_AI_BASE_URL
    return {
        "role": "primary",
        "provider": provider,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "reference_model": model,
    }


def _chain_for_model(model: str) -> dict[str, str] | None:
    """按模型名推导默认直连链路：Seedream → 方舟，GPT-Image → 无印。"""
    selected = str(model or "").strip()
    if selected == DEFAULT_IMAGE_MODEL:
        return {
            "role": "primary",
            "provider": "ark",
            "base_url": ARK_IMAGE_AI_BASE_URL,
            "api_key": "",
            "model": selected,
            "reference_model": selected,
        }
    if selected in {"image_gpt", "image_gpt_2.5"}:
        return {
            "role": "primary",
            "provider": "wuyin",
            "base_url": IMAGE_AI_BASE_URL,
            "api_key": "",
            "model": selected,
            "reference_model": selected,
        }
    return None


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
