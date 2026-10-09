"""操作答疑智能体：纯本地 FAQ 三层匹配，不调用任何 AI 模型。

对外只暴露 :func:`create_router`，其余（matcher / service）为内部实现。
"""

from __future__ import annotations

from .router import create_router

__all__ = ["create_router"]
