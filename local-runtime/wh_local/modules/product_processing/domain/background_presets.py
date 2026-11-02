"""品类背景片段与系统背景模板库。

路径 ①：`BACKDROP_FRAGMENTS` 是直接拼进生图提示词的背景描述（每组 ≤8 词）。
路径 ②：`SYSTEM_BACKGROUNDS` 是系统提供的背景底板（先占位素材，后续替换为实拍图）。

两条路径都按品类取，且选择结果可复现：同样的输入永远得到同一背景，
避免同一批商品每张背景都不同；跨批次再按 shop_id + batch_id 轮换。
"""

from __future__ import annotations

import hashlib
from typing import Any

# 8 组背景片段：拼在 Scene 里替换“背景”那一段，台面与景深保留。
BACKDROP_FRAGMENTS: dict[str, str] = {
    "bd_paper_sweep": "pale gray paper sweep, soft floor transition",
    "bd_plaster_wall": "warm plaster wall with soft window gradient",
    "bd_oak_edge": "light oak table edge against off-white wall",
    "bd_linen_drape": "draped ivory linen backdrop, gentle folds",
    "bd_terrazzo": "warm terrazzo surface, matte finish",
    "bd_frosted_panel": "frosted glass panel with diffused backlight",
    "bd_metal_strip": "brushed metal strip along the top edge",
    "bd_gradient_studio": "clean studio gradient, seamless top-to-bottom",
}

# 品类 → 默认背景片段（与前端 category id 对齐）。
BACKDROP_BY_CATEGORY: dict[str, str] = {
    "home_kitchen": "bd_plaster_wall",
    "apparel_accessories": "bd_linen_drape",
    "pet_supplies": "bd_plaster_wall",
    "office_stationery": "bd_paper_sweep",
    "sports_outdoor": "bd_gradient_studio",
    "auto_accessories": "bd_metal_strip",
    "hand_tools": "bd_oak_edge",
    "passive_tech": "bd_frosted_panel",
    "beauty_tools": "bd_terrazzo",
    "adult_games": "bd_gradient_studio",
    "garden_tools": "bd_oak_edge",
    "general": "bd_paper_sweep",
}

# 品类文本（Temu 类目 + 标题）命中词 → 品类 id；命中不了按 seed 轮换。
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "home_kitchen": ("厨房", "餐具", "收纳", "床品", "抱枕", "香薰", "kitchen", "tableware", "storage", "bedding"),
    "apparel_accessories": ("服装", "连衣裙", "裤", "鞋", "包", "帽", "围巾", "首饰", "apparel", "dress", "shoes", "bag"),
    "pet_supplies": ("宠物", "猫", "狗", "牵引", "窝垫", "pet", "dog", "cat"),
    "office_stationery": ("文具", "笔", "笔记本", "文件夹", "桌面", "stationery", "notebook", "desk"),
    "sports_outdoor": ("瑜伽", "健身", "露营", "钓鱼", "水壶", "sports", "yoga", "camping", "fishing"),
    "auto_accessories": ("车载", "遮阳", "脚垫", "后备箱", "car", "auto", "trunk"),
    "hand_tools": ("扳手", "螺丝刀", "批头", "紧固件", "挂钩", "tool", "wrench", "screwdriver"),
    "passive_tech": ("手机壳", "平板套", "内胆包", "线材", "键盘膜", "phone case", "sleeve", "cable"),
    "beauty_tools": ("化妆", "美甲", "修眉", "剃刀", "梳", "beauty", "makeup", "nail"),
    "adult_games": ("桌游", "扑克", "拼图", "解压", "骰子", "board game", "puzzle", "card"),
    "garden_tools": ("花盆", "修枝", "小铲", "育苗", "浇水", "garden", "planter", "pruner"),
}

# 20 张系统背景模板；asset 指向前端静态目录，当前为 SVG 占位图。
SYSTEM_BACKGROUNDS: tuple[dict[str, Any], ...] = (
    {"id": "bg_warm_white", "name": "暖白渐变", "style_family": "solid", "color_family": "warm", "categories": (), "usable_for_main": True},
    {"id": "bg_fog_gray", "name": "雾灰渐变", "style_family": "solid", "color_family": "cool", "categories": (), "usable_for_main": True},
    {"id": "bg_oatmeal", "name": "燕麦纸感", "style_family": "solid", "color_family": "warm", "categories": (), "usable_for_main": True},
    {"id": "bg_pale_apricot", "name": "浅杏渐变", "style_family": "solid", "color_family": "warm", "categories": (), "usable_for_main": True},
    {"id": "bg_cool_gray", "name": "冷灰渐变", "style_family": "solid", "color_family": "cool", "categories": (), "usable_for_main": True},
    {"id": "bg_paper_ivory", "name": "象牙纸纹", "style_family": "paper", "color_family": "warm", "categories": (), "usable_for_main": True},
    {"id": "bg_plaster_warm", "name": "暖灰石膏", "style_family": "paper", "color_family": "warm", "categories": ("home_kitchen", "pet_supplies", "general"), "usable_for_main": True},
    {"id": "bg_paper_coarse", "name": "粗纹灰纸", "style_family": "paper", "color_family": "neutral", "categories": ("office_stationery", "general"), "usable_for_main": True},
    {"id": "bg_oak_light", "name": "浅橡木台面", "style_family": "wood_stone", "color_family": "warm", "categories": ("home_kitchen", "hand_tools", "garden_tools"), "usable_for_main": True},
    {"id": "bg_walnut", "name": "胡桃木台面", "style_family": "wood_stone", "color_family": "warm", "categories": ("adult_games", "garden_tools"), "usable_for_main": True},
    {"id": "bg_stone_warm", "name": "暖灰石板", "style_family": "wood_stone", "color_family": "neutral", "categories": ("hand_tools", "sports_outdoor"), "usable_for_main": True},
    {"id": "bg_terrazzo", "name": "暖色水磨石", "style_family": "wood_stone", "color_family": "neutral", "categories": ("beauty_tools", "office_stationery"), "usable_for_main": True},
    {"id": "bg_marble_white", "name": "白大理石", "style_family": "wood_stone", "color_family": "cool", "categories": ("beauty_tools",), "usable_for_main": True},
    {"id": "bg_linen", "name": "米白亚麻", "style_family": "fabric", "color_family": "warm", "categories": ("apparel_accessories",), "usable_for_main": True},
    {"id": "bg_canvas", "name": "棉麻画布", "style_family": "fabric", "color_family": "warm", "categories": ("apparel_accessories", "home_kitchen"), "usable_for_main": True},
    {"id": "bg_velvet", "name": "深灰绿绒面", "style_family": "fabric", "color_family": "cool", "categories": ("beauty_tools", "adult_games"), "usable_for_main": True},
    {"id": "bg_arch", "name": "拱形墙洞", "style_family": "geo", "color_family": "warm", "categories": ("home_kitchen", "beauty_tools"), "usable_for_main": False},
    {"id": "bg_steps", "name": "石质阶梯", "style_family": "geo", "color_family": "neutral", "categories": ("home_kitchen", "passive_tech"), "usable_for_main": False},
    {"id": "bg_pine", "name": "松枝暖灯", "style_family": "festive", "color_family": "festive", "categories": ("adult_games",), "usable_for_main": False},
    {"id": "bg_gift_velvet", "name": "礼盒丝绒", "style_family": "festive", "color_family": "festive", "categories": ("adult_games", "beauty_tools"), "usable_for_main": False},
)

ASSET_PREFIX = "/assets/backgrounds"

# 每张底板对应的提示词片段：用户在界面上选定模板时，主图直接用它（图只用于预览）。
_TEMPLATE_PROMPTS: dict[str, str] = {
    "bg_warm_white": "warm white seamless studio sweep",
    "bg_fog_gray": "soft fog gray seamless studio sweep",
    "bg_oatmeal": "oatmeal beige seamless paper backdrop",
    "bg_pale_apricot": "pale apricot warm gradient backdrop",
    "bg_cool_gray": "cool light gray seamless sweep",
    "bg_paper_ivory": "ivory paper backdrop with soft floor transition",
    "bg_plaster_warm": "warm plaster wall with soft window gradient",
    "bg_paper_coarse": "coarse warm gray paper backdrop",
    "bg_oak_light": "light oak tabletop against off-white wall",
    "bg_walnut": "dark walnut tabletop against deep gray wall",
    "bg_stone_warm": "warm gray honed stone surface",
    "bg_terrazzo": "warm terrazzo surface, matte finish",
    "bg_marble_white": "white marble surface with soft veining",
    "bg_linen": "draped ivory linen backdrop",
    "bg_canvas": "natural cotton canvas backdrop",
    "bg_velvet": "deep charcoal green velvet backdrop",
    "bg_arch": "plaster arch niche in a warm white wall",
    "bg_steps": "pale stone display steps",
    "bg_pine": "pine branches and warm bokeh at the edge",
    "bg_gift_velvet": "deep green velvet with gold ribbon bokeh",
}

SYSTEM_BACKGROUNDS = tuple(
    {**item, "prompt": _TEMPLATE_PROMPTS.get(item["id"], "")} for item in SYSTEM_BACKGROUNDS
)


def background_template(template_id: str) -> dict[str, Any] | None:
    """按 id 取模板；找不到返回 None。"""
    target = str(template_id or "").strip()
    return next((item for item in SYSTEM_BACKGROUNDS if item["id"] == target), None)


def background_asset_url(template_id: str, *, ext: str = "jpg") -> str:
    return f"{ASSET_PREFIX}/{template_id}.{ext}"


def _stable_index(seed: str, size: int) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % max(size, 1)


def detect_category_id(category_text: str = "", title: str = "") -> str:
    """从类目文本/标题里认出品类 id；认不出返回 general。"""
    haystack = f"{category_text} {title}".lower()
    best, best_hits = "general", 0
    for category_id, keywords in _CATEGORY_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if keyword.lower() in haystack)
        if hits > best_hits:
            best, best_hits = category_id, hits
    return best


def select_backdrop_fragment(
    category_text: str = "",
    title: str = "",
    *,
    seed: str = "",
    category_id: str = "",
    template_id: str = "",
) -> str:
    """返回一组背景片段文本。

    指定了系统模板就用模板自带的片段（用户在界面上手选的优先）；
    否则按品类取默认，认不出的品类用 seed 稳定轮换。
    """
    preset = background_template(template_id)
    if preset and preset.get("prompt"):
        return str(preset["prompt"])
    resolved = category_id or detect_category_id(category_text, title)
    fragment_id = BACKDROP_BY_CATEGORY.get(resolved)
    if not fragment_id:
        keys = list(BACKDROP_FRAGMENTS)
        fragment_id = keys[_stable_index(seed or f"{category_text}|{title}", len(keys))]
    return BACKDROP_FRAGMENTS[fragment_id]


def pick_system_background(
    *,
    shop_id: str,
    batch_id: str,
    panel: int = 1,
    categories: tuple[str, ...] | list[str] | None = None,
    style_family: str = "",
) -> dict[str, Any] | None:
    """按店铺 + 批次确定系统背景模板：同批稳定，跨批轮换，可按品类/风格过滤。"""
    pool = [item for item in SYSTEM_BACKGROUNDS if item.get("usable_for_main") or panel != 1]
    if categories:
        wanted = set(categories)
        filtered = [item for item in pool if not item["categories"] or wanted & set(item["categories"])]
        pool = filtered or pool
    if style_family:
        filtered = [item for item in pool if item["style_family"] == style_family]
        pool = filtered or pool
    if not pool:
        return None
    index = _stable_index(f"{shop_id}:{batch_id}:{panel}", len(pool))
    return dict(pool[index])
