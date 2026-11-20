"""场景道具追加（兼容垫片）。

⚠️ 本文件是**垫片**，不是原实现。2026-11-20 合入 MiraTide 的货源/利润线时发现
`modules/product_processing/service.py` 引用了 `.domain.scene_prop_presets`，但该
文件在原分支及其它所有远端分支都不存在（作者机器上漏提交）。没有它后端无法启动。

这里只做最小兼容：原样返回提示词结果，即「不做场景道具追加」。视觉提示词其余
部分（panel_props / scene_text / split_prop_shares）不受影响。
拿到原实现后应整体替换本文件并删除此说明。
"""

from __future__ import annotations

from typing import Any


def append_scene_props(
    result: Any,
    category_id: str = "general",
    *,
    title: str = "",
    style: str = "",
) -> Any:
    """原实现会把品类场景零件（台面/配件）追加进提示词分格。

    垫片阶段直接返回入参；函数签名与调用点保持一致，替换真实实现时无需改调用方。
    """
    _ = (category_id, title, style)  # 保持签名可读性，垫片不使用
    return result
