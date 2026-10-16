"""平台实现注册表：把平台标识映射到具体的生图实现。

运行时按「模型 → 平台」（catalog.provider_for_model）取实现，因此新增平台只需要
在这里登记一次，四个 POD 模块自动跟随。
"""

from __future__ import annotations

from .base import GridImageProvider
from .catalog import DOUBAO_PLATFORM, SUCHUANG_PLATFORM
from .doubao import ARK_RESULT_TIMEOUT_SECONDS, DoubaoSeedreamImageProvider
from .suchuang import (
    SUCHUANG_POLL_INTERVAL_SECONDS,
    SUCHUANG_RESULT_TIMEOUT_SECONDS,
    SuChuangImageProvider,
)


def build_grid_providers(
    *,
    result_timeout_seconds: float = SUCHUANG_RESULT_TIMEOUT_SECONDS,
    poll_interval_seconds: float = SUCHUANG_POLL_INTERVAL_SECONDS,
) -> dict[str, GridImageProvider]:
    """构造本次运行时的平台实现表（每个实例只建一次，随运行时关闭一起丢弃）。"""
    return {
        SUCHUANG_PLATFORM: SuChuangImageProvider(
            result_timeout_seconds=result_timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        ),
        DOUBAO_PLATFORM: DoubaoSeedreamImageProvider(
            result_timeout_seconds=ARK_RESULT_TIMEOUT_SECONDS
        ),
    }


__all__ = ["build_grid_providers"]
