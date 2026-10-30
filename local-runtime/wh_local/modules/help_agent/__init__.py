"""操作答疑：远端答疑服务（``workbench.haocoming.top/qa``）的同源代理。

答案与知识库都在服务端，本模块不含任何本地 FAQ 数据与匹配逻辑。
对外只暴露 :func:`create_router`。
"""

from __future__ import annotations

from .router import create_router

__all__ = ["create_router"]
