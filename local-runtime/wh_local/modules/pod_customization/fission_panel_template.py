"""裂变提示词模板。

两类面板：

- panel_1 主图由 :func:`build_fission_main_prompt` 拼装：AI 方案只提供风格、元素、配色、
  构图蓝图与场景，本模块负责把「A 只作参考库、不得复用其版式与场景道具」写成硬约束，
  否则图生图会把 A 的整幅成品图 1:1 搬到目标产品上；
- panel_2~4 派生格由 :func:`build_fission_panel_prompt` 拼装：只以主图为锚点换机位。

每格输出单帧，禁止 2×2、拼贴或条带。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .prompt_anchor_blocks import (
    ANCHOR_CONTINUITY,
    CAMERA_MUST_DIFFER,
    RIGID_ARTWORK_LOCK,
)
from .prompts import DEFAULT_COMPOSITION_PANELS

# 派生面板提示词版本：改动任一输出语句都必须递增（随款级计划间接影响可复现性）。
FISSION_PANEL_PROMPT_VERSION = "pod-fission-panel-v1"

# 主图提示词版本：同上，主图模板改动必须递增。
FISSION_MAIN_PROMPT_VERSION = "pod-fission-main-v1"

# 只有 2/3/4 是派生格；panel_1 是主图，由 AI 方案决定。
DERIVED_PANEL_INDEXES: tuple[int, ...] = (2, 3, 4)
DERIVED_PANEL_KEYS: tuple[str, ...] = ("panel_2", "panel_3", "panel_4")

# 模板缺槽位时的兜底拍法（沿用系统默认四格口径）。
DEFAULT_DERIVED_PANEL_DIRECTIONS: dict[str, str] = {
    key: str(DEFAULT_COMPOSITION_PANELS[key]["en"])
    for key in DERIVED_PANEL_KEYS
}

_REFERENCE_ROLES = (
    "REFERENCE ROLES — image 1 is A, the viral sample artwork; image 2 is B, the target product.\n"
    "KEEP A'S DNA — A's print medium and craft, A's core motif family and A's warm palette family must "
    "carry over; this design is a variation inside A's family, not a new art direction.\n"
    "A IS A REFERENCE FOR LOOK, NOT A LAYOUT — never reuse A's composition, motif arrangement, spacing, "
    "props, furniture or backdrop; never switch to watercolour, line art, flat vector or collage; and "
    "never copy A's product, people, labels or watermarks."
)

_PRINT_RULES = (
    "PRINT RULES — cover every printable region of B edge to edge; continue the artwork onto handles, "
    "straps, bindings, pockets and flaps instead of leaving them in the base colour; leave only a thin "
    "non-printed margin at the seams and never print over zippers, hardware or lining; keep B's "
    "silhouette, proportions, component count and material exactly; add no text, letters, numbers, "
    "logos, borders or stickers, and never turn a printed motif into a 3D object."
)

_SINGLE_FRAME = (
    "SINGLE FRAME OUTPUT — return ONE clean full-frame square product photo, never a 2x2 contact sheet, "
    "a grid, a collage, a 1x4 or 4x1 strip, unequal panels or a divider."
)

_SINGLE_ANCHOR_BINDING = (
    "SINGLE ANCHOR BINDING — the ONE attached reference image is the accepted main image of this "
    "fission design, and the ONLY source of truth for product identity, structure, material, base "
    "colour, printable surface, scene, set surface, light direction and the printed artwork. Never "
    "regenerate the product or the artwork from scratch, and never re-design, re-layout, re-centre, "
    "rescale or recolour the print."
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


def build_fission_main_prompt(
    *,
    style_family: str,
    motifs: Sequence[str],
    palette: Mapping[str, str],
    composition_blueprint: str,
    scene: str,
    detail_prompt: str,
    attempt: int = 1,
) -> str:
    """拼装 panel_1 主图的单帧提示词。

    A（爆款样图）在这里只被声明为配色与工艺的参考库，构图和场景一律取本款冻结的
    ``composition_blueprint`` / ``scene``；``detail_prompt`` 只作印花细节补充，
    与前面冲突时以蓝图和场景为准。这样图生图不会再把 A 的整幅成品图原样搬过来。
    """
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")

    family = str(style_family or "").strip() or "this fission design"
    motif_text = ", ".join(str(item).strip() for item in motifs if str(item).strip())
    blueprint = str(composition_blueprint or "").strip()
    staging = str(scene or "").strip().rstrip(".")
    detail = str(detail_prompt or "").strip()

    parts: list[str] = [
        "=== PART 1 · REFERENCE ROLES ===",
        _REFERENCE_ROLES,
        "",
        "=== PART 2 · THIS DESIGN ===",
        f"STYLE FAMILY — {family}",
    ]
    if motif_text:
        parts.append(f"MOTIFS — {motif_text}")
    parts.append(
        "PALETTE — primary: {primary}; secondary: {secondary}; accent: {accent}".format(
            primary=str(palette.get("primary") or "").strip() or "unspecified",
            secondary=str(palette.get("secondary") or "").strip() or "unspecified",
            accent=str(palette.get("accent") or "").strip() or "unspecified",
        )
    )
    if blueprint:
        parts.append(f"COMPOSITION BLUEPRINT — {blueprint}")
    if staging:
        parts.append(
            f"SCENE — {staging}. This staging belongs to this design; it must not be A's original room, "
            "props, furniture or backdrop."
        )
    parts.extend(("", "=== PART 3 · PRINT RULES ===", _PRINT_RULES))
    if detail:
        parts.extend((
            "",
            "=== PART 4 · PRINT DETAIL ===",
            "The text below adds print detail only. Where it conflicts with PART 2's composition "
            "blueprint or scene, PART 2 wins.",
            detail,
        ))
    parts.extend(("", "=== PART 5 · OUTPUT ===", _SINGLE_FRAME))
    if attempt == 2:
        parts.extend((
            "",
            "RETRY ATTEMPT 2 OF 2:",
            "The first result was invalid: it either copied A's layout or staging, or it broke the "
            "all-over print coverage. Keep this design's own composition blueprint and scene, do not "
            "reproduce A's arrangement or props, and return ONE clean full-frame image.",
        ))
    return "\n".join(parts)


def build_fission_panel_prompt(
    *,
    style_family: str,
    direction: str,
    panel_index: int,
    attempt: int = 1,
) -> str:
    """拼装派生面板（panel_2/3/4）的单帧提示词；同一计划同一格必然得到同一文本。"""
    panel_index = int(panel_index)
    if panel_index not in DERIVED_PANEL_INDEXES:
        raise ValueError("fission derived panels are panel_2, panel_3 and panel_4")
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")

    key = f"panel_{panel_index}"
    shooting = str(direction or "").strip() or DEFAULT_DERIVED_PANEL_DIRECTIONS[key]
    family = str(style_family or "").strip() or "this fission design"
    parts: list[str] = [
        f"=== PART 1 · ANCHOR BINDING — this panel is a re-shoot of the accepted main image ({family}) ===",
        _SINGLE_ANCHOR_BINDING,
        RIGID_ARTWORK_LOCK,
        ANCHOR_CONTINUITY,
        _GEOMETRY_LOCK,
        "",
        "=== PART 2 · THIS PANEL'S CAMERA (framing only) ===",
        f"SHOOTING DIRECTION for {key}:",
        shooting,
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
        f"CAMERA REMINDER (last check) — shoot this panel as: {shooting} The camera must be visibly "
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
    "FISSION_MAIN_PROMPT_VERSION",
    "FISSION_PANEL_PROMPT_VERSION",
    "build_fission_main_prompt",
    "build_fission_panel_prompt",
]
