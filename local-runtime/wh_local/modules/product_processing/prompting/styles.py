"""五张风格卡：只定义构图语言，不碰物理、尺寸和场景。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StyleCard:
    key: str
    label: str
    hero_mode: str      # white / scene
    panel1: str         # 第①格的构图语言
    panel23: str        # 第②③格
    panel4: str = "true orthographic front, side or top view on a plain calm surface, 12%-18% clear space"


STYLE_CARDS: dict[str, StyleCard] = {
    "A": StyleCard(
        "A", "白底合规", "white",
        "pure white seamless hero, straight-on 0-degree, product only, no props",
        "one real-scene alternate and one product detail",
    ),
    "B": StyleCard(
        "B", "生活叙事", "scene",
        "real lifestyle scene hero, product naturally in use",
        "one in-use moment and one detail",
    ),
    "C": StyleCard(
        "C", "平铺套装", "scene",
        "true 90-degree top-down flat lay on a real surface, equal spacing",
        "one deconstructed layout and one material detail",
    ),
    "D": StyleCard(
        "D", "环境远景", "scene",
        "medium-wide establishing shot, product 45%-60%",
        "wide environment shots, product 20%-38%",
    ),
    "E": StyleCard(
        "E", "场景主图", "scene",
        "real-scene hero (never a white sweep or a flat lay), product prominent and best-lit",
        "one second scene and one detail",
    ),
}


def get_style(key: str) -> StyleCard:
    return STYLE_CARDS.get(str(key or "A").strip().upper(), STYLE_CARDS["A"])
