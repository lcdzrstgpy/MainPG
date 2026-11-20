"""品类场景（Scene）与零件（Set）预设的后端运行时。

数据源唯一保留在前端
``web-frontend/src/modules/product_processing/data/scenePropPresets.ts``，
通过 ``npm run sync:scene-props`` 导出为同目录的 ``scene_props.json``。
本模块只做三件事：

1. 品类归一化（未知品类回落 ``general``）；
2. 主体冲突处理：本体撞件删除 → 材质冲突换件 → 同色系删件 → 同材质去重；
3. 按场景风格把整套零件分到第 2-4 格（极简 1 / 生活 3 / 花哨 4-6，整套合计，不跨格重复）。

件数口径与 ``service._scene_family_plan``、前端 ``PROP_STYLE_LIMITS`` 完全一致。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).with_name("scene_props.json")

# 档位 → （最少, 最多）整套零件件数；花哨上限 6 与前端一致。
STYLE_PROP_RANGES: dict[str, tuple[int, int]] = {
    "minimal": (1, 1),
    "lifestyle": (3, 3),
    "ornate": (4, 6),
}
_DEFAULT_STYLE = "ornate"


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


def _normalize_category(category_id: str) -> str:
    key = str(category_id or "").strip().lower()
    return key if key in _data()["scenes"] else "general"


def scene_text(category_id: str, variant: str = "main") -> str:
    """品类对应的台面/背景/景深描述（variant: main | alt）。"""

    item = _data()["scenes"].get(_normalize_category(category_id)) or {}
    return str(item.get(str(variant or "main")) or "")


def prop_limit(style: str) -> int:
    """该档位整套最多几件零件。"""

    key = str(style or "").strip().lower()
    _low, high = STYLE_PROP_RANGES.get(key, STYLE_PROP_RANGES[_DEFAULT_STYLE])
    return high


def split_prop_shares(style: str) -> list[int]:
    """把整套件数切成第 2/3/4 格的份额（1 件只落在第 2 格）。"""

    return _shares_for(prop_limit(style))


def _shares_for(total: int) -> list[int]:
    total = max(0, int(total))
    base, remainder = divmod(total, 3)
    return [base + (1 if index < remainder else 0) for index in range(3)]


def _haystack(title: str, material: str) -> str:
    return f"{str(title or '')} {str(material or '')}".lower()


def _matches(haystack: str, keywords: Any) -> bool:
    return any(str(word).lower() in haystack for word in (keywords or ()))


def detect_subject_families(title: str, material: str) -> set[str]:
    """主体命中的材质族；「仿木纹 / 仿皮」这类描述不参与判定。"""

    data = _data()
    haystack = _haystack(title, material)
    if _matches(haystack, data.get("faux_material_hints", ())):
        return set()
    return {
        str(family)
        for family, words in (data.get("subject_material_triggers") or {}).items()
        if _matches(haystack, words)
    }


def detect_subject_color(title: str, material: str) -> str:
    """主体命中的颜色族（用于避开与商品同色的道具）。"""

    data = _data()
    haystack = _haystack(title, material)
    if _matches(haystack, data.get("faux_material_hints", ())):
        return ""
    for family, words in (data.get("subject_color_triggers") or {}).items():
        if _matches(haystack, words):
            return str(family)
    return ""


def resolve_props(
    category_id: str,
    title: str = "",
    material: str = "",
    style: str = "",
) -> list[str]:
    """按冲突规则挑出这一整套零件（最多到档位上限）。"""

    data = _data()
    category = _normalize_category(category_id)
    pool = list(
        (data.get("pools") or {}).get(category)
        or (data.get("presets") or {}).get(category)
        or ()
    )
    limit = prop_limit(style)
    haystack = _haystack(title, material)
    subject_families = detect_subject_families(title, material)
    subject_color = detect_subject_color(title, material)
    material_family = data.get("material_family") or {}
    color_family = data.get("color_family") or {}
    replacements = data.get("replacements") or {}
    subject_keywords = data.get("subject_keywords") or {}
    picked: list[str] = []
    used_colors: set[str] = set()

    def is_free(name: str) -> bool:
        family = str(color_family.get(name) or "")
        return not family or family not in used_colors

    for prop in pool:
        if len(picked) >= limit:
            break
        name = str(prop)
        if name in picked:
            continue
        # 1) 本体撞件：商品自己就是这件东西（瑜伽垫商品不放「瑜伽垫」道具）。
        if _matches(haystack, subject_keywords.get(name, ())):
            continue
        # 2) 材质冲突：商品是木质就换掉木质道具。
        if str(material_family.get(name) or "") in subject_families:
            name = next(
                (
                    str(candidate)
                    for candidate in replacements.get(name, ())
                    if str(candidate) not in picked and is_free(str(candidate))
                ),
                "",
            )
            if not name:
                continue
        # 3) 同色系删件：道具和商品同色会糊在一起。
        color = str(color_family.get(name) or "")
        if color and color == subject_color:
            continue
        # 4) 同色系/同材质重复去重。
        if not is_free(name):
            continue
        picked.append(name)
        if color:
            used_colors.add(color)
    return picked


def panel_props(
    category_id: str,
    title: str = "",
    material: str = "",
    style: str = "",
) -> list[list[str]]:
    """返回 [第 2 格, 第 3 格, 第 4 格] 各自的零件（同一件不跨格重复）。"""

    props = resolve_props(category_id, title=title, material=material, style=style)
    panels: list[list[str]] = []
    cursor = 0
    for share in _shares_for(len(props)):
        panels.append(props[cursor : cursor + share])
        cursor += share
    return panels
