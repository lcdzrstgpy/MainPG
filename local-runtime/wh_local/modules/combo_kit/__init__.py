"""商品组合套装独立模块。

与 product_processing / pod_customization 完全隔离：
- 独立数据库表（combo_kit_*）、独立业务流、独立状态机、独立扣费、独立预检。
- 仅复用底层能力：文本模型、生图处理器、蒙版 canvas、上传/OSS 组件。
"""
from __future__ import annotations

from .router import create_combo_kit_router, register_combo_kit_exception_handlers

__all__ = ["create_combo_kit_router", "register_combo_kit_exception_handlers"]
