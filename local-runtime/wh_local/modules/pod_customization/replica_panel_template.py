"""复刻派生三格（panel_2~4）提示词模板：只以主图为锚点，换机位重拍。

与全定制的派生轮同构，但一件商品只有一张锚点图（主图），且每次调用只出一张单帧图：

锚点绑定 → 硬质图案锁 → 连续/差异分离 → 几何形态锁 → 本格拍法（来自复刻构图模板）
→ 单帧输出 → 禁止项。

本模块刻意不注入图案元素清单、配色、场景或美术指导：图案与场景已经在主图里定稿，
派生只允许改机位；主图视角本身不可指定（模板的 panel_1 被后端忽略）。
"""

from __future__ import annotations

from .prompt_anchor_blocks import (
    ANCHOR_CONTINUITY,
    CAMERA_MUST_DIFFER,
    RIGID_ARTWORK_LOCK,
)
from .replica_specs import ReplicaPlan

# 复刻派生面板提示词版本：改动任一输出语句都必须递增（随款级计划间接影响可复现性）。
REPLICA_PANEL_PROMPT_VERSION = "pod-replica-panel-v1"

# 只有 2/3/4 是派生格；panel_1（主图）由豆包方案决定，模板不参与。
DERIVED_PANEL_INDEXES: tuple[int, ...] = (2, 3, 4)
DERIVED_PANEL_KEYS: tuple[str, ...] = ("panel_2", "panel_3", "panel_4")

# 模板缺槽位时的兜底拍法（与全定制派生轮的默认机位口径一致）。
DEFAULT_DERIVED_PANEL_DIRECTIONS: dict[str, str] = {
    "panel_2": (
        "A tight close-up of the printed artwork on the same product, revealing print texture and "
        "material detail."
    ),
    "panel_3": (
        "A different close structural/material detail of the same product, from a three-quarter angle, "
        "keeping the same light direction as the anchor."
    ),
    "panel_4": (
        "Neutral ecommerce angle: the complete product centred on a clean neutral background with no "
        "props, straight-on framing, full design sharply visible."
    ),
}

_SINGLE_ANCHOR_BINDING = (
    "SINGLE ANCHOR BINDING — the ONE attached reference image is the accepted main image of this "
    "product, and the ONLY source of truth for product identity, structure, material, base colour, "
    "printable surface, scene, set surface, light direction and the printed artwork. Never regenerate "
    "the product or the artwork from scratch, and never re-design, re-layout, re-centre, rescale or "
    "recolour the print."
)

_GEOMETRY_LOCK = (
    "GEOMETRY LOCK — keep the silhouette, seam lines, panel divisions, hardware positions and "
    "proportions of the anchor exactly; never redraw, reshape or re-proportion the product."
)

_SINGLE_FRAME_OUTPUT = (
    "SINGLE FRAME OUTPUT — return ONE clean full-frame square image of this panel only. Never return a "
    "2x2 contact sheet, a grid, a collage, a 1x4 or 4x1 strip, unequal panels or a divider: this batch "
    "renders each panel as an independent request."
)

_PANEL_PROHIBITIONS = (
    "Do not add any new text, letters, numbers, logos, labels, watermarks, signatures, captions, "
    "borders or stickers; text that is already part of the printed pattern may stay. Do not bring in "
    "the sample image background, room, furniture, model, props or a second pattern, and do not turn a "
    "printed motif into a 3D object."
)

_MATERIAL_PANEL_CLAUSE = (
    "MATERIAL PANEL — this panel may switch ONLY the background to a clean neutral ecommerce surface "
    "with no props; product, artwork, print placement, light direction and colour temperature stay "
    "identical to the anchor."
)


def build_replica_panel_prompt(plan: ReplicaPlan, panel_index: int, attempt: int = 1) -> str:
    """拼装派生面板（panel_2/3/4）的单帧提示词；同一计划同一格必然得到同一文本。"""
    panel_index = int(panel_index)
    if panel_index not in DERIVED_PANEL_INDEXES:
        raise ValueError("replica derived panels are panel_2, panel_3 and panel_4")
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")

    key = f"panel_{panel_index}"
    direction = str((plan.derived_panels or {}).get(key) or "").strip()
    if not direction:
        direction = DEFAULT_DERIVED_PANEL_DIRECTIONS[key]

    parts: list[str] = [
        "=== PART 1 · ANCHOR BINDING — this panel is a re-shoot of the accepted main image ===",
        _SINGLE_ANCHOR_BINDING,
        RIGID_ARTWORK_LOCK,
        ANCHOR_CONTINUITY,
        _GEOMETRY_LOCK,
        "",
        "=== PART 2 · THIS PANEL'S CAMERA (framing only) ===",
        f"SHOOTING DIRECTION for {key}:",
        direction,
        "Framing-only clause: the sentence above changes only the camera — angle, shot distance, crop, "
        "framing and depth of field. Product, artwork, print placement, scene, set surface, light "
        "direction, colour temperature and contrast must stay identical to the anchor.",
        CAMERA_MUST_DIFFER,
    ]
    if panel_index == 4:
        parts.append(_MATERIAL_PANEL_CLAUSE)
    parts.extend((
        "",
        "=== PART 3 · OUTPUT FORMAT AND PROHIBITIONS ===",
        _SINGLE_FRAME_OUTPUT,
        _PANEL_PROHIBITIONS,
        "",
        f"CAMERA REMINDER (last check) — shoot this panel as: {direction} The camera must be visibly "
        "different from the anchor camera; copying the anchor framing (same distance, same crop, same "
        "full-product view) is a FAILED result.",
    ))
    if attempt == 2:
        parts.extend((
            "",
            "RETRY ATTEMPT 2 OF 2:",
            "The first result was invalid: it did not follow this panel's shooting direction or it "
            "drifted from the anchor. Keep the anchor's product design and printed artwork exactly, "
            "change only the camera as instructed, and return ONE clean full-frame image.",
        ))
    return "\n".join(parts)


__all__ = [
    "DEFAULT_DERIVED_PANEL_DIRECTIONS",
    "DERIVED_PANEL_INDEXES",
    "DERIVED_PANEL_KEYS",
    "REPLICA_PANEL_PROMPT_VERSION",
    "build_replica_panel_prompt",
]
