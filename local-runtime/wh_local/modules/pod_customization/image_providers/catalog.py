"""POD 生图模型目录。

四个 POD 模块（全定制 / 半定制 / 爆款复刻 / 爆款裂变）共用同一套生图底座，
可选的模型就三个：

- 速创（无印）：``image_gpt``、``image_gpt_2.5``
- 豆包 Seedream（火山方舟）：``doubao-seedream-5-0-flash-260915``

模型名全局唯一，所以对外只有一个「模型」下拉，没有任何"平台"配置项；
运行时不看配置里的平台，直接由模型名反查该走哪条上游实现、要哪个 provider key。
平台常量（``SUCHUANG_PLATFORM`` / ``DOUBAO_PLATFORM``）只是内部实现标识，
用于给两套完全不同的协议选实现，不对外暴露。

本模块刻意不依赖 basic_settings / 媒体栈，``basic_settings.service`` 可以安全地
在模块级导入，不会形成环。
"""

from __future__ import annotations

from typing import Any

SUCHUANG_PLATFORM = "suchuang"
DOUBAO_PLATFORM = "doubao"

# (模型值, 展示名, 平台实现)
POD_IMAGE_MODELS: tuple[tuple[str, str, str], ...] = (
    ("image_gpt", "GPT-Image-2（现行）", SUCHUANG_PLATFORM),
    ("image_gpt_2.5", "GPT-Image-2.5（新版）", SUCHUANG_PLATFORM),
    ("doubao-seedream-5-0-flash-260915", "Seedream 5.0 Flash（豆包）", DOUBAO_PLATFORM),
)

# POD 默认走 2.5（产品侧要求；AI处理 的默认仍是 1.0，两者互不影响）。
POD_IMAGE_MODEL_DEFAULT = "image_gpt_2.5"

# 平台 → 生图时需要冻结下发的 provider key 名（速创=wuyin，豆包=ark）。
# 冻结/重冻结由远端按画像下发密钥，客户端只能按当前模型声明用哪一个 key，
# 不能把平台写死成 wuyin，否则切到豆包后整条链路会卡在「授权不可用」。
PLATFORM_GRANT_PROVIDERS: dict[str, str] = {
    SUCHUANG_PLATFORM: "wuyin",
    DOUBAO_PLATFORM: "ark",
}

_PLATFORM_BY_MODEL: dict[str, str] = {
    model: platform for model, _label, platform in POD_IMAGE_MODELS
}


def model_choices() -> list[dict[str, str]]:
    """前端渲染用的模型清单（不含任何凭据）。"""
    return [{"value": model, "label": label} for model, label, _platform in POD_IMAGE_MODELS]


def normalize_model(model: Any) -> str:
    """把模型名收敛到目录内的合法值，未知值回落到默认模型。"""
    candidate = str(model or "").strip()
    return candidate if candidate in _PLATFORM_BY_MODEL else POD_IMAGE_MODEL_DEFAULT


def provider_for_model(model: Any) -> str:
    """由模型名反查平台实现；未知模型回落到默认平台（速创）。"""
    return _PLATFORM_BY_MODEL.get(str(model or "").strip(), SUCHUANG_PLATFORM)


def grant_provider_for_model(model: Any) -> str:
    """当前模型所属平台在冻结里需要的 provider key 名（速创=wuyin，豆包=ark）。"""
    platform = provider_for_model(model)
    return PLATFORM_GRANT_PROVIDERS.get(platform, PLATFORM_GRANT_PROVIDERS[SUCHUANG_PLATFORM])
