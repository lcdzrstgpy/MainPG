"""新手引导配置：把板块下的子任务与步骤持久化到 workbench_settings。

对外只暴露 :func:`create_router`。
"""

from __future__ import annotations

from .router import create_router

__all__ = ["create_router"]
