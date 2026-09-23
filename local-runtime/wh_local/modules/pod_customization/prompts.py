from __future__ import annotations

import hashlib
import random
import re
from typing import Mapping, Sequence

from .contracts import BusinessFields


PATTERN_PROMPT_VERSION = "v1"
LISTING_IMAGE_ROLES = ("hero", "detail_a", "detail_b", "lifestyle")

# 元素关键词独立切分器：仅顿号/逗号/分号/换行，不含空格。
# 注意不要复用公共 splitBusinessField（前端），这里与后端自洽即可；
# 空格不做分隔，避免拆散 "Seamless Pattern" 这类英文复词。
_ELEMENT_SPLIT_PATTERN = "[、，,;；\n]+"

_STYLE_COMPOSITIONS = (
    "one off-center focal composition",
    "balanced all-over repeat",
    "large cropped edge-to-edge motif",
    "orderly modular grid",
    "diagonal movement with open negative space",
    "radial composition without a sunburst",
    "vertical cascading arrangement",
    "horizontal banded arrangement",
    "sparse floating placement",
    "layered foreground-and-background motif",
)
_STYLE_PALETTES = (
    "two-color high contrast",
    "muted earth tones with one dark accent",
    "cool analogous colors",
    "warm analogous colors",
    "deep jewel tones",
    "soft mineral pastels",
    "black plus one saturated accent",
    "desaturated heritage palette",
    "bright complementary colors",
    "monochrome tonal variation",
    "natural greens and clay neutrals",
    "navy, cream, and restrained warm accents",
)
_STYLE_RENDERINGS = (
    "clean vector-like flat shapes",
    "rough screen-print edges",
    "fine ink linework",
    "block-print texture",
    "cut-paper texture",
    "dry-brush marks",
    "stitched applique appearance",
    "subtle grain with crisp silhouettes",
    "watercolor-like edges without gradients",
    "bold marker-like strokes",
)
_STYLE_DENSITIES = (
    "very sparse with generous negative space",
    "sparse",
    "medium density",
    "dense but readable",
    "one oversized motif with minimal supporting marks",
    "small-scale repeat with clear rhythm",
    "mixed scale with one dominant and several supporting forms",
)


def split_element_keywords(value: str | Sequence[str]) -> list[str]:
    """机械切分元素关键词为数组：去空、去重、保序，不含空格分隔。"""
    if isinstance(value, str):
        value = [value]
    parts: list[str] = []
    for item in value:
        parts.extend(re.split(_ELEMENT_SPLIT_PATTERN, str(item)))
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        token = part.strip()
        if token and token not in seen:
            seen.add(token)
            cleaned.append(token)
    return cleaned


def assign_style_elements(
    keywords: str | Sequence[str],
    style_index: int,
    seed: str,
) -> dict[str, str | list[str]]:
    """按款式确定性分配元素（种子随机 + 批内不重复）。

    - 以 ``seed``（batch_id）洗牌元素数组；同一批次重放得到同一分配（重试可复现），
      不同批次种子不同 → 分配不同（跨批变化）。
    - 每款结构：1 主打 + 1 辅主 + ≤2 点缀；款内不重复，
      相邻款式主打必不同（主打按款式顺序在洗牌序列上轮换）。
    """
    if style_index < 1:
        raise ValueError("style_index must be positive")
    pool = split_element_keywords(keywords)
    if not pool:
        return {"primary": "", "co": "", "accents": []}
    digest = hashlib.sha256(str(seed).encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    shuffled = list(pool)
    rng.shuffle(shuffled)
    count = len(shuffled)
    offset = style_index - 1
    primary = shuffled[offset % count]
    if count == 1:
        return {"primary": primary, "co": "", "accents": []}
    co = shuffled[(offset + (count + 1) // 2) % count]
    accents: list[str] = []
    for step in (1, 2):
        candidate = shuffled[(offset + step) % count]
        if candidate != primary and candidate != co and candidate not in accents:
            accents.append(candidate)
    return {"primary": primary, "co": co, "accents": accents}


def _brief_color_preferences(business_fields: Mapping[str, object] | None) -> list[str]:
    if not business_fields:
        return []
    raw = business_fields.get("color_preferences") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def build_direct_listing_prompt(fields: BusinessFields, creative_prompt: str) -> str:
    """Prompt for one product-locked four-panel listing contact sheet.

    Batch-wide facts are rendered verbatim for every style. Element keywords are
    deliberately NOT rendered here: they are assigned per style and injected by
    ``build_style_listing_prompt``, so no style ever receives the full list.
    """
    parts = [
        "Create one square 2x2 ecommerce contact sheet with exactly four equal panels.",
        "Treat the supplied template only as a structural product reference for product geometry, construction, proportions, material, scale, and printable surface location.",
        "Do not copy, trace, preserve, or extend the template's existing artwork, decoration, product color, background, room, furniture, surface, lighting, shadows, camera framing, or scene.",
        "The template is not a background plate and must not appear as the base image. Invent a completely new surface design and a fresh commercially suitable product color and presentation.",
        "Keep the same exact product across all four panels: identical structure, material, proportions, base color, newly invented artwork, artwork scale, and artwork placement.",
        "Do not invent another product, extra accessories, text, captions, logos, labels, watermarks, collages, or borders. Keep the four-panel divider clean and centered.",
        "Panel order is fixed and every panel must show the same exact product with the same unchanged newly invented artwork.",
        "When the product has an interior or lining (for example a laundry hamper, storage basket, or tote bag with an inner lining), keep the interior surface unprinted: a plain uniform solid color, black by default. Never extend the outer surface artwork onto the interior, and never add a second pattern inside.",
        "Panel 1 — MATERIAL IMAGE (top-left): show one complete product against a newly generated clean neutral ecommerce background. Keep the whole product clearly visible, make it fill most of the panel, and show the full design sharply. This is supporting material imagery, not the marketplace primary image.",
        "Panel 2 — DETAIL IMAGE A (top-right): show a tight high-resolution close-up of the newly invented surface artwork on this same product. Make color, edges, print or material texture, and manufacturing detail easy to inspect; do not alter the artwork or its placement. This close-up belongs to the top-right panel only.",
        "Panel 3 — DETAIL IMAGE B (bottom-left): show a different close product detail or three-quarter product view. Choose a product-appropriate structural or material detail, while keeping the artwork visibly identical to Panel 1 and Panel 2. This detail view belongs to the bottom-left panel only.",
        "Panel 4 — PRIMARY IMAGE (bottom-right): show the same complete product in one newly generated, natural, commercially useful lifestyle setting. Keep the full product and unchanged artwork visible; this is the marketplace primary image and title reference. Do not reuse the template background or add another product.",
        "Panel 4 must be a wide lifestyle scene with the whole product inside a real environment, shot at a normal eye-level product angle, with the full silhouette in frame. It must never be a close-up, macro, cropped, partial, extreme-angle, or three-quarter detail shot: those belong to Panel 2 and Panel 3 and must not be repeated in the bottom-right panel.",
        "Final check on the fixed order by position: top-left = complete product on a neutral background, top-right = tight artwork close-up, bottom-left = a different close product detail, bottom-right = the complete product in a lifestyle scene. Do not swap, shift, or duplicate panels.",
        f"Product name: {fields.product_name or 'POD product'}.",
    ]
    for label, value in (
        ("Product category", fields.product_category),
        ("Target market", fields.target_market),
        ("Target audience", fields.target_audience),
        ("Core selling points", ", ".join(fields.core_selling_points)),
        ("Batch-wide theme & style", fields.design_theme),
        ("Color preferences", ", ".join(fields.color_preferences)),
        ("Excluded elements", ", ".join(fields.excluded_elements)),
        ("Creative direction", creative_prompt.strip()),
    ):
        if value:
            parts.append(f"{label}: {value}.")
    return "\n".join(parts)


def build_style_listing_prompt(
    base_prompt: str,
    *,
    style_index: int,
    attempt: int,
    business_fields: Mapping[str, object] | None = None,
    creative_prompt: str = "",
    style_elements: Mapping[str, object] | None = None,
) -> str:
    """Append one deterministic, batch-diverse creative recipe to a listing prompt.

    The recipe varies composition, palette, accent colors, rendering, and density.
    The style's SUBJECT comes from the assigned element keywords (primary / co /
    accents); when no assignment exists (legacy data), the model is told to invent
    one new subject within the batch-wide theme. Element keywords are never
    rendered wholesale.
    """
    if style_index < 1:
        raise ValueError("style_index must be positive")
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")
    offset = style_index - 1
    elements = style_elements if isinstance(style_elements, Mapping) else {}
    primary = str(elements.get("primary") or "").strip()
    co = str(elements.get("co") or "").strip()
    accents = [
        token
        for token in (str(item).strip() for item in elements.get("accents") or [])
        if token and token not in (primary, co)
    ][:2]

    composition = _STYLE_COMPOSITIONS[(offset * 3) % len(_STYLE_COMPOSITIONS)]
    color_preferences = _brief_color_preferences(business_fields)
    # When the user named exact colors, defer to them instead of the recipe pool,
    # so a fixed palette cannot fight the brief.
    palette = (
        f"the brief's specified colors ({', '.join(color_preferences)})"
        if color_preferences
        else _STYLE_PALETTES[(offset * 7) % len(_STYLE_PALETTES)]
    )
    accent_colors = ""
    if len(color_preferences) >= 2:
        first = color_preferences[(offset * 7) % len(color_preferences)]
        second = color_preferences[(offset * 7 + 3) % len(color_preferences)]
        if second == first:
            second = color_preferences[(offset * 7 + 1) % len(color_preferences)]
        accent_colors = f"{first}, {second}"
    rendering = _STYLE_RENDERINGS[(offset * 7) % len(_STYLE_RENDERINGS)]
    density = _STYLE_DENSITIES[(offset * 3) % len(_STYLE_DENSITIES)]

    if primary:
        subject_lines = [
            f"本款素材主语：{primary}（视觉主角，尺度 1.0）"
        ]
        if co:
            subject_lines.append(f"辅主：{co}（第二大尺度，0.6）")
        if accents:
            subject_lines.append(f"点缀：{', '.join(accents)}（小尺度，0.25，至多 2 个）")
        subject_lines.append(
            "清单中未分配给本款的元素在本款中禁止出现。"
        )
    else:
        subject_lines = [
            "本款未分配素材：必须在 Batch-wide theme & style 的主题内自创一个新元素作为本款素材主语。",
        ]
    subject_block = "\n".join(subject_lines)

    signature = (
        f"STYLE-{style_index:03d} | elements=primary:{primary or '-'} co:{co or '-'} "
        f"accents:{','.join(accents) or '-'} | composition={composition} | "
        f"palette={palette} | rendering={rendering} | density={density}"
        + (f" | accent_colors={accent_colors}" if accent_colors else "")
    )

    rules = [
        base_prompt.rstrip(),
        "",
        "STYLE-SPECIFIC DIVERSITY CONTRACT:",
        f"Style creative signature: {signature}",
        subject_block,
        "主题整批统一风格必须保持，优先级高于本款配方建议；配方只提供可在该主题风格允许范围内实现的变化方式（尺度节奏、色块分割、带状/网格/散点律动等）。",
        f"Create this as style {style_index}. Its surface artwork must be visibly different from every other style in this batch.",
        "素材可以在批次内复用，但每款必须拥有不同的视觉主角与花样：主打元素身份、尺度关系、排布逻辑、色彩重心、渲染工艺至少两项不同。禁止输出“把所有元素等权复制粘贴”的通用大杂烩图案。",
        f"整幅图案（含所有元素与底色）必须采用本款渲染工艺（{rendering}）表现，不得与其他款式共用同一视觉语言。",
    ]
    if accent_colors:
        rules.append(f"本款色彩重心：强调色 {accent_colors}，其余指定颜色退为辅助色。")
    rules.append(
        "All four panels in this one contact sheet must nevertheless use exactly this one "
        "new design; do not create four design variants inside the sheet."
    )
    if attempt == 2:
        rules.extend((
            "",
            "RETRY ATTEMPT 2 OF 2:",
            "The first result was invalid, failed, or too similar to another style; reinvent the surface artwork from scratch while keeping this style's assigned elements and recipe, and the same product structure.",
            "Do not reuse the first attempt's focal shape, motif arrangement, or color blocking.",
        ))
    rules.append(
        "Panel positions stay fixed: the bottom-right panel is the PRIMARY lifestyle scene with the "
        "whole product in a real setting, never a close-up, macro, cropped, or three-quarter detail "
        "shot; those belong to the top-right and bottom-left panels only."
    )
    return "\n".join(rules)


def build_semi_pattern_base(fields: BusinessFields, creative_prompt: str) -> str:
    """半定制纯图案底稿：只描述「图案本身」，不含产品/主体/场景/文字。

    半定制是纯图案花色制作，与具体产品无关，因此只渲染图案相关业务字段
    （主题风格 / 配色 / 禁用元素）；元素关键词不在此渲染，交由
    ``build_semi_pattern_prompt`` 按组注入，保证同组四格互不相同。
    """
    parts = [
        "Create one square 2x2 contact sheet with exactly four equal panels.",
        "Every panel is a flat surface pattern design only: no product, no mockup, no object, no person, no room, no furniture, no shadow, no background scene.",
        "Do not draw any product silhouette, packaging, model, hand, table, or 3D object. The design is purely a two-dimensional repeating ornament on a clean background.",
        "Do not include any text, letters, numbers, logos, labels, watermarks, stamps, signatures, captions, or borders in any panel.",
        "Use a plain white or single solid-color background. Keep the motif edges clean and the composition self-contained so it can be printed directly onto a product.",
        "The four panels must be four visibly different pattern designs, not four copies of one pattern.",
    ]
    for label, value in (
        ("Batch-wide theme & style", fields.design_theme),
        ("Color preferences", ", ".join(fields.color_preferences)),
        ("Excluded elements", ", ".join(fields.excluded_elements)),
        ("Creative direction", creative_prompt.strip()),
    ):
        if value:
            parts.append(f"{label}: {value}.")
    return "\n".join(parts)


def build_semi_pattern_prompt(
    base_prompt: str,
    *,
    group_index: int,
    attempt: int,
    business_fields: Mapping[str, object] | None = None,
    panel_elements: Sequence[Mapping[str, object] | None] | None = None,
) -> str:
    """为一个「组」（一次速创调用 = 一张 2×2 四宫格 = 4 款）拼装提示词。

    关键：四宫格里的四格是**四个独立款式**，所以提示词必须把四格分别描述清楚
    （各自的主打元素 / 构图 / 配色 / 渲染 / 疏密），而不是只描述「整张要不一样」——
    后者会让模型产出一张统一图案的四个局部。

    ``panel_elements`` 为四格各自的元素分配（左上 / 右上 / 左下 / 右下），
    缺省时该格自行在批次主题内自创元素。
    """
    if group_index < 1:
        raise ValueError("group_index must be positive")
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")
    color_preferences = _brief_color_preferences(business_fields)
    slots = ("TOP-LEFT", "TOP-RIGHT", "BOTTOM-LEFT", "BOTTOM-RIGHT")
    supplied = list(panel_elements or [])

    panel_blocks: list[str] = []
    for panel in range(1, 5):
        # 每格独立取配方：同组四格的 offset 连续，必然落在不同的构图/配色/渲染组合上。
        offset = (group_index - 1) * 4 + (panel - 1)
        raw_elements = supplied[panel - 1] if panel - 1 < len(supplied) else None
        elements = raw_elements if isinstance(raw_elements, Mapping) else {}
        primary = str(elements.get("primary") or "").strip()
        co = str(elements.get("co") or "").strip()
        accents = [
            token
            for token in (str(item).strip() for item in elements.get("accents") or [])
            if token and token not in (primary, co)
        ][:2]

        composition = _STYLE_COMPOSITIONS[(offset * 3) % len(_STYLE_COMPOSITIONS)]
        palette = (
            f"only the brief's specified colors ({', '.join(color_preferences)})"
            if color_preferences
            else _STYLE_PALETTES[(offset * 7) % len(_STYLE_PALETTES)]
        )
        rendering = _STYLE_RENDERINGS[(offset * 7) % len(_STYLE_RENDERINGS)]
        density = _STYLE_DENSITIES[(offset * 3) % len(_STYLE_DENSITIES)]
        accent_colors = ""
        if len(color_preferences) >= 2:
            first = color_preferences[(offset * 7) % len(color_preferences)]
            second = color_preferences[(offset * 7 + 3) % len(color_preferences)]
            if second == first:
                second = color_preferences[(offset * 7 + 1) % len(color_preferences)]
            accent_colors = f"{first}, {second}"

        lines = [f"Panel {panel} — {slots[panel - 1]}: a separate pattern design."]
        if primary:
            motif = f"motif = {primary} (dominant)"
            if co:
                motif += f", support = {co}"
            if accents:
                motif += f", accents = {', '.join(accents)}"
            lines.append(motif + ". Use ONLY this panel's motif; every motif not listed for this panel is forbidden here.")
        else:
            lines.append("motif = invent one new motif for this panel inside the batch-wide theme.")
        lines.append(f"composition = {composition}.")
        lines.append(f"palette = {palette}." + (f" Color focus: {accent_colors}." if accent_colors else ""))
        lines.append(f"rendering = {rendering}.")
        lines.append(f"density = {density}.")
        panel_blocks.append("\n".join(lines))

    rules = [
        base_prompt.rstrip(),
        "",
        "FOUR INDEPENDENT PATTERNS — the four panels are four different pattern designs:",
        "Each panel must be a self-contained, independently designed pattern with its own motif, "
        "its own layout, its own colors and its own rendering technique.",
        "The four panels must read as four different designs from the same collection — "
        "NOT four crops of one pattern, NOT four colour variants of one pattern, "
        "and NOT the same motif re-arranged four times.",
        "Do not carry a motif from one panel into another panel, and do not fill every panel with the same element list.",
        "",
        *panel_blocks,
        "",
        "Keep the batch-wide theme recognizable across all four panels, but the pattern itself must differ panel by panel.",
    ]
    if attempt == 2:
        rules.extend((
            "",
            "RETRY ATTEMPT 2 OF 2:",
            "The first result was invalid, failed, or its four panels looked like one single pattern; "
            "redesign all four panels from scratch following the per-panel descriptions above.",
            "Do not reuse the first attempt's focal shapes, motif arrangement, or color blocking.",
        ))
    return "\n".join(rules)
