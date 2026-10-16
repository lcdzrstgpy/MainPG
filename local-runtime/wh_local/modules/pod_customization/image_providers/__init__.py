"""POD 生图平台实现（速创 / 豆包）。

四个 POD 模块共用同一套生图底座，差异全部收敛在本包内：

- ``catalog``：三个可选模型 → 上游实现/provider key 的映射（对外只有模型，没有"平台"）；
- ``base``：平台无关的请求/结果结构与 host 协议；
- ``suchuang``：速创（无印）异步生图；
- ``doubao``：豆包 Seedream（火山方舟）同步生图，参考图 base64 内嵌；
- ``registry``：平台标识 → 实现的注册表。
"""

from __future__ import annotations

from .base import (
    GridImageProvider,
    GridImageRequest,
    GridImageResult,
    GridProviderHost,
    GridReference,
    image_data_url,
    suffix_for_content_type,
)
from .catalog import (
    DOUBAO_PLATFORM,
    PLATFORM_GRANT_PROVIDERS,
    POD_IMAGE_MODEL_DEFAULT,
    POD_IMAGE_MODELS,
    SUCHUANG_PLATFORM,
    grant_provider_for_model,
    model_choices,
    normalize_model,
    provider_for_model,
)
from .doubao import DoubaoSeedreamImageProvider, ark_size
from .registry import build_grid_providers
from .suchuang import SUCHUANG_RESULT_TIMEOUT_SECONDS, SuChuangImageProvider, suchuang_size

__all__ = [
    "DOUBAO_PLATFORM",
    "DoubaoSeedreamImageProvider",
    "GridImageProvider",
    "GridImageRequest",
    "GridImageResult",
    "GridProviderHost",
    "GridReference",
    "PLATFORM_GRANT_PROVIDERS",
    "POD_IMAGE_MODEL_DEFAULT",
    "POD_IMAGE_MODELS",
    "SUCHUANG_PLATFORM",
    "SUCHUANG_RESULT_TIMEOUT_SECONDS",
    "SuChuangImageProvider",
    "ark_size",
    "build_grid_providers",
    "grant_provider_for_model",
    "image_data_url",
    "model_choices",
    "normalize_model",
    "provider_for_model",
    "suchuang_size",
    "suffix_for_content_type",
]
