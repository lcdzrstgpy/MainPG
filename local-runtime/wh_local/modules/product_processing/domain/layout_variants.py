"""精选处理的版式结构（骨架）注册表。

一张营销图 = 内容块（标题/短词/药丸/细节卡/编号点）+ 一种结构骨架：

    S1 上下分栏  照片占上部，底部白色信息带
    S2 左右分栏  照片满高占一侧，另一侧信息栏
    S3 满版叠加  照片铺满，标题/标签叠加在照片留白区
    S4 贴边大卡  暖色页面 + 圆角大图卡 + 周围信息

结构只影响本地排版，不影响生图块；同一商品内的多个模板用「打乱后的结构顺序」
依次分配，保证前 4 个模板拿到 4 种不同结构，且同一商品重跑结果稳定。
"""

from __future__ import annotations

import random
from typing import Sequence

SKELETONS: tuple[str, ...] = ("S1", "S2", "S3", "S4")

SKELETON_LABELS: dict[str, str] = {
    "S1": "上下分栏",
    "S2": "左右分栏",
    "S3": "满版叠加",
    "S4": "贴边大卡",
}

# S1 信息带高度（占画布）。实际使用时还会按商品底边自适应下调，避免切商品。
BAND_RATIO: dict[str, float] = {"S1": 0.32, "S2": 0.0, "S3": 0.0, "S4": 0.0}
BAND_MIN_RATIO = 0.26
BAND_MAX_RATIO = 0.38

# 生图时给每个结构预留的构图区（英文，直接拼进生图提示词）。
SKELETON_SPACE: dict[str, str] = {
    "S1": (
        "the product occupies only the upper 60% of the frame, the bottom 35% is clean "
        "empty floor continuing the same surface, no props and nothing else in it"
    ),
    "S2": (
        "the product is centred inside the left 60% of the frame, the right 40% is clean "
        "seamless background with nothing in it"
    ),
    "S3": (
        "the product sits in the lower middle of the frame, the top 28% and the bottom 15% "
        "are clean empty background with nothing in them"
    ),
    "S4": (
        "the product is centred with generous even empty margins on all four sides, "
        "the whole product fully visible and nothing else in the frame"
    ),
}

# 每个模板的信息样式：
# chips=图标短词卡 / pills=纯文字药丸 / cards=带缩略图的卡片 /
# circles=圆形细节 / callouts=编号列表 / none=只有标题。
CONTENT_STYLE: dict[str, str] = {
    "headline_hero": "none",
    "action_proof": "none",
    "icon_grid": "chips",
    "material_strip": "pills",
    "use_cases": "cards",
    "side_rail": "chips",
    "feature_cards": "cards",
    "feature_strip": "circles",
    "callout_ring": "callouts",
    "white_main": "none",
}

# 候选择优时给视觉模型的「结构适配」判据：没有这一条，它会按普通商品图
# 的标准挑一张好看的，却把排版需要的留白裁掉。
JUDGE_HINTS: dict[str, str] = {
    "S1": "the bottom ~35% of the frame must be clean empty floor for a text band",
    "S2": "the right ~40% of the frame must be clean seamless background for an info panel",
    "S3": "the top ~28% and bottom ~15% must be clean background for overlaid text",
    "S4": "the whole product must be visible with even margins so a rounded card crop keeps it intact",
}

# 每个结构最多展示几条内容（大字号优先：宁少勿小）。
MAX_ITEMS: dict[str, int] = {"S1": 4, "S2": 6, "S3": 4, "S4": 4}


def variant_order(seed: int) -> list[str]:
    """按商品种子打乱结构顺序；同一商品稳定，不同商品顺序不同。"""
    order = list(SKELETONS)
    random.Random(int(seed)).shuffle(order)
    return order


def pick_variant(template: str, order: Sequence[str], index: int) -> str:
    """给第 ``index`` 个模板分配结构；白底主图没有文字，固定 S1。"""
    if str(template or "") == "white_main":
        return "S1"
    if not order:
        return "S1"
    return str(order[int(index) % len(order)])


def skeleton_space(skeleton: str) -> str:
    return SKELETON_SPACE.get(str(skeleton or ""), "")


def judge_hint(skeleton: str) -> str:
    return JUDGE_HINTS.get(str(skeleton or ""), "")
