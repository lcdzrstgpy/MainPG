from __future__ import annotations

import hashlib
import random
import re
from typing import Mapping, Sequence

from .contracts import COMPOSITION_PANEL_KEYS, BusinessFields


# 整个「大提示词」的版本号：**只要改动任一处的提示词内容就必须递增**。
# 覆盖范围 = 一次生图的全部内置提示词：四段式结构（PART 1 核心契约 / PART 2 四格机位 /
# PART 3 元素轮换 / PART 4 美术指导）及其中的硬条款（色彩冲击、面料与绗缝、场景发光等）。
# 该值随批次写进 `pod_customization_batches.prompt_version`，是事后区分「改前 / 改后」产出的
# 依据；只改提示词不改版本号，新旧批次就会记成同一版本，结果无法归因。
# v1：早期的单段式提示词。
# v2：四部分共组的整批统一提示词。
# v3：新增批级全局字段（工艺 fabric_craft / 使用场景 scene_mood）、
#     PART 3 元素三级 Tier 权重（hero/narrative/rhythm）、配色 60-30-10 契约，PART 4 光照降级（只留 GLOW）。
# v4：创意编辑内容提升为 PART 1 开头的 BATCH-SPECIFIC HARD RULES；
#     新增 PRODUCT FORM & SIZE LOCK；面料块改为按真实材质分支（不再无条件假设绗缝布料）。
PATTERN_PROMPT_VERSION = "v4"
# 位置↔角色固定：左上=主图(lifestyle)、右上=细节A(detail_a)、左下=细节B(detail_b)、右下=素材(hero)。
# 标识符沿用历史值（lifestyle=主图、hero=素材），避免历史批次错标；仅位置分配按本元组顺序正确落位。
LISTING_IMAGE_ROLES = ("lifestyle", "detail_a", "detail_b", "hero")

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
    """按款式确定性分配元素（种子随机 + 批内不重复），并映射为三级视觉权重（Tier）。

    - 以 ``seed``（batch_id）洗牌元素数组；同一批次重放得到同一分配（重试可复现），
      不同批次种子不同 → 分配不同（跨批变化）。
    - Tier 映射（位置序 = 权重序）：
      - ``hero``：Tier 1 Hero 主锚，按款序在洗牌序列上轮换取 1 个（相邻款必不同），尺度 1.0；
      - ``narrative``：Tier 2，紧随 hero 的后续 2 个（第二尺度 0.6）；
      - ``rhythm``：Tier 3，其余归为「1 组点缀纹样」（小尺度 0.25，≤4 个）。
    - 款内不重复。
    """
    if style_index < 1:
        raise ValueError("style_index must be positive")
    pool = split_element_keywords(keywords)
    if not pool:
        return {"hero": "", "narrative": [], "rhythm": []}
    digest = hashlib.sha256(str(seed).encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    shuffled = list(pool)
    rng.shuffle(shuffled)
    count = len(shuffled)
    offset = style_index - 1
    hero = shuffled[offset % count]
    narrative: list[str] = []
    for step in (1, 2):
        candidate = shuffled[(offset + step) % count]
        if candidate != hero and candidate not in narrative:
            narrative.append(candidate)
    rhythm: list[str] = []
    for step in range(1, count):
        candidate = shuffled[(offset + step) % count]
        if candidate != hero and candidate not in narrative and candidate not in rhythm:
            rhythm.append(candidate)
        if len(rhythm) == 4:
            break
    return {"hero": hero, "narrative": narrative, "rhythm": rhythm}


def _coerce_style_elements(
    elements: Mapping[str, object] | None,
) -> tuple[str, list[str], list[str]]:
    """归一化元素分配为 ``(hero, narrative, rhythm)``，兼容旧批次形状。

    新形状 = ``{hero, narrative, rhythm}``；旧批次（v2 及更早）落库的是
    ``{primary, co, accents}``，这里降级映射（primary→hero、co→narrative、accents→rhythm），
    保证历史批次仍可渲染、不炸。
    """
    if not isinstance(elements, Mapping):
        return "", [], []
    if "hero" in elements or "narrative" in elements or "rhythm" in elements:
        hero = str(elements.get("hero") or "").strip()
        narrative = [str(item).strip() for item in (elements.get("narrative") or [])]
        rhythm = [str(item).strip() for item in (elements.get("rhythm") or [])]
    else:
        hero = str(elements.get("primary") or "").strip()
        co = str(elements.get("co") or "").strip()
        narrative = [co] if co else []
        rhythm = [str(item).strip() for item in (elements.get("accents") or [])]
    clean_narrative: list[str] = []
    for token in narrative:
        if token and token != hero and token not in clean_narrative:
            clean_narrative.append(token)
    clean_rhythm: list[str] = []
    for token in rhythm:
        if token and token != hero and token not in clean_narrative and token not in clean_rhythm:
            clean_rhythm.append(token)
    return hero, clean_narrative[:2], clean_rhythm[:4]


def _normalized_color_pool(color_preferences: str | Sequence[str]) -> list[str]:
    if isinstance(color_preferences, str):
        color_preferences = [color_preferences]
    pool: list[str] = []
    seen: set[str] = set()
    for item in color_preferences or []:
        token = str(item).strip()
        if token and token not in seen:
            seen.add(token)
            pool.append(token)
    return pool


def assign_style_colors(
    color_preferences: str | Sequence[str],
    style_index: int,
    seed: str,
) -> dict[str, str]:
    """按款式确定性分配 60-30-10 三角色配色（与元素 Tier 引擎同构）。

    - 以 ``seed``（batch_id）洗牌配色池；同一批次重放得到同一分配，不同批次不同。
    - 每款三角色互不相同；相邻款主基调不同（主基调按款序在洗牌序列上轮换）。
    - 配色池不足 3 色时返回全空 → 调用方回退既有色板行为（不凭空造色）。
    """
    if style_index < 1:
        raise ValueError("style_index must be positive")
    pool = _normalized_color_pool(color_preferences)
    if len(pool) < 3:
        return {"base": "", "structural": "", "highlight": ""}
    digest = hashlib.sha256(str(seed).encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    shuffled = list(pool)
    rng.shuffle(shuffled)
    count = len(shuffled)
    offset = style_index - 1
    step = max(1, count // 3)
    base = shuffled[offset % count]
    structural = shuffled[(offset + step) % count]
    if structural == base:
        structural = shuffled[(offset + step + 1) % count]
    taken = {base, structural}
    probe = offset + 2 * step
    highlight = shuffled[probe % count]
    while highlight in taken:
        probe += 1
        highlight = shuffled[probe % count]
    return {"base": base, "structural": structural, "highlight": highlight}


def _brief_color_preferences(business_fields: Mapping[str, object] | None) -> list[str]:
    if not business_fields:
        return []
    raw = business_fields.get("color_preferences") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


# 四格固定角色/机位的默认描述（未配置用户构图时使用）。
_DEFAULT_LISTING_PANEL_LINES = (
    "Panel 1 — PRIMARY IMAGE (top-left): the complete product in one newly generated, commercially useful lifestyle setting, shot wide at a normal eye-level product angle with the full silhouette in frame. It must never be a close-up, macro, cropped, partial or three-quarter detail shot — those belong to Panels 2-3 and must not be repeated here. This is the marketplace primary image and the title reference.",
    "Panel 2 — DETAIL IMAGE A (top-right): a tight high-resolution close-up of the newly invented artwork on this same product, making colour, edges, print/material texture and manufacturing detail easy to inspect. Do not alter the artwork or its placement.",
    "Panel 3 — DETAIL IMAGE B (bottom-left): a different close product detail or three-quarter view — a product-appropriate structural or material detail — with the artwork visibly identical to Panels 1-2.",
    "Panel 4 — MATERIAL IMAGE (bottom-right): the complete product on a newly generated clean neutral ecommerce background, filling most of the panel with the full design sharply visible. Supporting material imagery, not the marketplace primary image.",
    "Final check on the fixed order by position: top-left = complete product in a lifestyle scene, top-right = tight artwork close-up, bottom-left = a different close product detail, bottom-right = complete product on a neutral background. Do not swap, shift or duplicate panels.",
)
# 系统「默认模板」的四格描述（等价于未配置构图时的默认机位）；内置项，不需要用户新建。
DEFAULT_COMPOSITION_ID = "__default__"
DEFAULT_COMPOSITION_NAME = "默认模板"
DEFAULT_COMPOSITION_RAW_INPUT = "系统默认机位（与未配置构图时一致）"
DEFAULT_COMPOSITION_PANELS: Mapping[str, Mapping[str, str]] = {
    "panel_1": {
        "zh": "平视自然机位的生活场景主图：完整产品置于真实使用场景中，可搭配少量低调衬托，光线柔和自然。",
        "en": "Natural eye-level lifestyle hero shot: the complete product placed in a real, usable setting with a few subtle supporting props under soft natural light.",
    },
    "panel_2": {
        "zh": "产品正面图案的高清微距特写，清楚呈现图案纹理与基底材质。",
        "en": "Tight high-resolution macro close-up of the product's front surface artwork, clearly showing the pattern texture and base material.",
    },
    "panel_3": {
        "zh": "另一处结构或材质细节的近景（如四分之三视角、边角或提手结构）。",
        "en": "Close-up of a different structural or material detail (for example a three-quarter view, an edge, or the handle construction).",
    },
    "panel_4": {
        "zh": "完整产品居中置于干净的中性纯白背景，规整正面拍摄，不要衬托。",
        "en": "The complete product centered on a clean neutral pure-white background, neat straight-on framing with no supporting props.",
    },
}
# 位置/角色由我们固定；用户构图只决定每一格「怎么拍」，不得改变四格角色。
_FIXED_PANEL_ROLE_LINE = (
    "Panel positions and roles stay fixed: top-left = PRIMARY image, top-right = detail A, "
    "bottom-left = detail B, bottom-right = material image. Do not swap, shift, or duplicate panels."
)
_LISTING_PANEL_SLOTS = (
    ("panel_1", "top-left", "primary image"),
    ("panel_2", "top-right", "detail image A"),
    ("panel_3", "bottom-left", "detail image B"),
    ("panel_4", "bottom-right", "material image"),
)
# 有用户构图时，四格描述改成「用户指令最高优先级」的口径：避免用户指令被角色名默认机位淹没。
# 实测：只写「按用户指令」模型仍会把四格画成同一机位，因此这里额外下「四格机位必须互不相同 +
# 必须字面照做」的硬条款（用户反馈：四张图视角一模一样 = 失败）。
_USER_DIRECTION_HEADER = (
    "USER-SPECIFIED SHOOTING DIRECTIONS — HIGHEST PRIORITY, HARD REQUIREMENTS (not hints): shoot each "
    "panel exactly as its own direction says. These OVERRIDE the default shot implied by the panel role "
    "name, and never reuse another panel's framing. Read each direction literally (e.g. 'vertically "
    "top-down' = shot straight down from overhead; 'macro close-up' = the surface fills the frame). "
    "Never average the four directions into one compromise framing."
)
# 四格趋同是这批图最致命的失败模式，单独再钉一句。
_PANEL_DIFFERENTIATION_LINE = (
    "MANDATORY DIFFERENCE CHECK — the four quadrants must show clearly different elevations "
    "(high / eye-level / low) and distances (wide / close-up / macro), as each panel requests. If two "
    "quadrants share the same camera angle, re-shoot one of them: Four identical viewpoints is a "
    "failed result."
)


# 竞品观感的另一个关键：图要"鲜艳通透有吸引力"。实测我们出的图偏暗、偏灰、偏素——
# 因为整条提示词里没有任何"提饱和/提亮"的要求，模型只会照暗色板忠实输出。
# 注意：不改变用户指定色板，只要求"在色板内拉满饱和度与亮度，别做灰做旧"。
_COLOR_IMPACT_BLOCK = (
    "COLOR IMPACT — the result must look vivid, rich and inviting, never dull, grey or washed out:\n"
    "- Max out saturation and luminosity inside the palette: bright clean highlights, clear mid-tones, "
    "deep rich shadows; dark colors must read jewel-like (lacquer, enamel, velvet), never muddy or faded. "
    "Warm the white balance toward sunlight — no cold blue-grey cast.\n"
    "- Build the PRODUCT BODY itself on the palette's strongest, most saturated signature colour (the "
    "batch theme's hero colour) as its dominant base: the product must be the boldest colour statement "
    "in frame. A pale, white or neutral product shell with only faint motifs is a FAILURE; lighter "
    "palette colours may only fill pattern negative space, the interior/lining, or the background.\n"
    "- TONAL SEPARATION — the product must separate clearly from its background through a strong "
    "difference in value and/or saturation. Never place a pale product on a pale background, and keep "
    "props and the scene colourful too: a muted beige/grey set or a washed-out frame is a FAILURE.\n"
    "- 60-30-10 COLOR HARMONY (fixed ratio contract): about 60% DOMINANT BASE (the product's large-area "
    "body colour), about 30% STRUCTURAL ACCENT (handles, bindings, large patch blocks), about 10% POP "
    "HIGHLIGHT (gold thread, metal hardware, small charms). The three proportions must be clearly "
    "readable; an even, untiered spread is a FAILURE."
)

# POD 绝大多数是布艺（帆布/棉/绗缝/PU）包袋，但也存在天然橡胶桌布、PVC 垫、皮革、
# 亚克力、陶瓷等硬质/光滑材质。原实现无条件断言「this is a real sewn fabric product」，
# 会把橡胶桌布硬掰成厚实绗缝方块（实测用户反馈：80×80 正方形桌布被生成成正方体）。
# 这里改为按模板真实材质分支：只有确实是缝制布料时才要求绗缝/缝线/织物纹理。
_MATERIAL_REALISM_BLOCK = (
    "MATERIAL REALISM — reproduce the template's TRUE material and construction, never a flat "
    "spray-painted surface or a generic invented shell:\n"
    "- Do NOT assume this is a sewn fabric product. Read the real material from the template and the "
    "batch facts, and match its true finish, thickness, edge profile and physical behaviour.\n"
    "- TEXTILE REALISM (sewn fabric products only — canvas, cotton, quilted fabric, PU fabric): "
    "if its body is quilted/padded, keep the same quilt relief: regular uniform cells at the template's "
    "own cell size and stitch pitch, fine dashed topstitching along every grid line, each cell gently "
    "puffed with a soft crown highlight and a soft valley shadow. Never smooth it into a featureless "
    "hard shell, and never invent coarser, larger or irregular quilting, random bubbles or a "
    "puffy-duvet look. Show real woven fibre with a soft matte sheen, subtle edge fuzz, natural folds "
    "and tension lines, plus topstitching and a fabric zipper with tape. Printed/embroidered motifs "
    "must sit ON the weave and bend with the fabric's folds and curvature.\n"
    "- Smooth or rigid materials (natural rubber, silicone, PVC, leather, paper, wood, ceramic, glass, "
    "metal): keep the true surface — a matte rubber sheet stays smooth, flat and matte, with the "
    "template's own edge profile and thickness. Never add fabric weave, quilting, stitching, padding, "
    "zipper tape, soft cloth folds or a puffy body that the template does not show.\n"
    "- On every material, printed motifs must follow the real surface: they bend with fabric folds, or "
    "sit flush on a smooth rubber/rigid face — never look like flat stickers floating over a generic "
    "shell, and stay sharply readable."
)


def _fabric_craft_lines(craft: str) -> list[str]:
    """批级工艺行：用户告知了工艺就渲染一行；留空则不渲染（回退静态面料块）。

    措辞强调「优先使用」而非「必须使用」：CARVE-OUT（模板真实工艺）始终最高优先，
    绝不凭空添加模板没有的工艺。
    """
    token = str(craft or "").strip()
    if not token:
        return []
    return [
        f"FABRIC CRAFT (this batch): {token}. Prefer these craft terms, but the template's TRUE "
        "construction always wins — never add a craft the template does not show (the CARVE-OUT above "
        "has the highest priority)."
    ]

# 生成提示词按四段式组织（产品侧指定的结构），段头显式标出，便于模型与人工都看清职责：
#   PART 1 最高内置契约：2×2 四宫格格式 + 整批统一风格 + 全局硬规则
#   PART 2 四格角色与机位：每格叫什么、怎么拍、视角怎么换（用户构图在此覆盖默认机位）
#   PART 3 元素轮换：逐款元素分配，保证款间差异
#   PART 4 美术指导：真实面料工艺 + 场景/背景美感（美感来源 = PART 1 的主题 + PART 3 的本款元素）
_PART1_HEADER = (
    "=== PART 1 · CORE CONTRACT (highest priority) — 2x2 four-panel format, batch-wide style, "
    "global hard rules ==="
)
_PART2_HEADER = (
    "=== PART 2 · PANEL ROLES, FRAMING & CAMERA — the four fixed slots and how each is shot ==="
)
_PART3_HEADER = (
    "=== PART 3 · ELEMENT ROTATION (this style only) — per-style elements that guarantee cross-style variety ==="
)
_PART4_HEADER = (
    "=== PART 4 · ART DIRECTION — real textile craft + scene beauty. The scene's beauty MUST be derived "
    "from the PART 1 batch-wide theme together with THIS style's PART 3 elements ==="
)
# 运营在「高级：本批次创意编辑」里写的内容整段提升为批级硬条款：放在 PART 1 最前，
# 而不是混进几十行通用条款末尾的 "Creative direction"（模型会把后者当建议而忽略）。
_BATCH_HARD_RULES_HEADER = (
    "=== BATCH-SPECIFIC HARD RULES (operator instructions — HIGHEST PRIORITY, hard requirements, not hints) ==="
)
# 形态/尺寸锁定：模板只提供结构参考，但运营明确写出的平面/尺寸事实必须优先。
# 触发场景：80cm×80cm 全覆盖、无下挂的正方形天然橡胶桌布被生成成了正方体方块。
_PRODUCT_FORM_LOCK = (
    "PRODUCT FORM & SIZE LOCK — the product form and dimensions stated in the batch facts and batch "
    "hard rules are geometric facts and outrank the template's apparent proportions: if the product is "
    "flat (tablecloth, mat, towel, blanket, sticker, paper), it must read as a flat sheet — shown flat "
    "or with natural soft drape — never as a box, cube, slab, stack, cushion or other 3D block. Keep "
    "stated sizes and coverage literal (for example a square 80x80 tablecloth must stay square, cover "
    "the tabletop and have no overhang). Never thicken, pad, inflate or extrude a flat product to give "
    "it volume."
)


def _listing_panel_lines(composition: Mapping[str, str] | None) -> list[str]:
    """返回四格描述：有用户构图时用其画面指令替换默认机位，硬约束由调用方保留。"""
    panels = {
        key: str((composition or {}).get(key) or "").strip()
        for key in COMPOSITION_PANEL_KEYS
    }
    if not any(panels.values()):
        return list(_DEFAULT_LISTING_PANEL_LINES)
    if any(not panels[key] for key in COMPOSITION_PANEL_KEYS):
        # 四格不完整（理论上被契约挡住）：回退默认，避免生成半套指令。
        return list(_DEFAULT_LISTING_PANEL_LINES)
    return [
        _USER_DIRECTION_HEADER,
        *(
            f"Panel {index} ({position} — role: {role}; user direction): {panels[key]}"
            for index, (key, position, role) in enumerate(_LISTING_PANEL_SLOTS, start=1)
        ),
        _PANEL_DIFFERENTIATION_LINE,
        _FIXED_PANEL_ROLE_LINE,
    ]


def build_direct_listing_prompt(
    fields: BusinessFields,
    creative_prompt: str,
    *,
    composition: Mapping[str, str] | None = None,
) -> str:
    """Prompt for one product-locked four-panel listing contact sheet.

    Batch-wide facts are rendered verbatim for every style. Element keywords are
    deliberately NOT rendered here: they are assigned per style and injected by
    ``build_style_listing_prompt``, so no style ever receives the full list.

    ``composition`` 为用户最新创作的构图（panel_1..4 画面指令）。提供时用它替换写死的
    四格机位/构图描述，但保留全部硬约束（2×2、同产品同图案、禁文字/水印、内饰不印等）。
    """
    parts = [
        _PART1_HEADER,
        "Create one square 2x2 ecommerce contact sheet with exactly four equal panels.",
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
    ):
        if value:
            parts.append(f"{label}: {value}.")
    # 运营的创意编辑（含其手写「硬性规则」）整段作为批级硬条款，紧跟产品事实之后、
    # 通用条款之前，避免被后面的长条款稀释成 "Creative direction" 建议。
    operator_rules = creative_prompt.strip()
    if operator_rules:
        parts.extend(("", _BATCH_HARD_RULES_HEADER, operator_rules, ""))
    parts.extend((
        # 精简原则：一条约束只说一遍。原先同一件事（别复用模板 / 保留绗缝 / 同款同图）各写了 3-4 遍，
        # 长提示词会把关键契约（色彩冲击、工艺）稀释掉；这里逐条去重，语义一条不减。
        "Treat the supplied template only as a STRUCTURAL reference for this product: geometry, proportions, construction, material, printable surface location, and its true surface relief (quilting/padding grid if present, seams, topstitching, hardware).",
        # 几何锁定：模板白底图是唯一的结构基准，显式禁止拉伸/压扁/熔形等畸变，避免四格之间结构漂移。
        "GEOMETRY LOCK: the product's silhouette, seam lines, panel divisions, quilting grid (if present), hardware positions and strap/handle drop must match the template exactly. Do not stretch, squash, melt, bend, reshape or re-proportion any part, and do not add, merge or delete structural parts.",
        "Do not copy the template's existing artwork, decoration, colour scheme, background, lighting or scene.",
        "CARVE-OUT: the template's quilting/padding relief, seams and stitching are STRUCTURE, not decoration — preserve them exactly as shown; do not flatten the quilted surface into a smooth shell. Only the artwork, the colour scheme and the scene are to be replaced.",
        "That restriction only forbids REUSING the template's own scene. It does NOT mean the background may be plain: a freshly styled, richly lit scene is required (see PART 4 ART DIRECTION).",
        "Never use the template as the base image: invent a completely new PRINTED pattern and a fresh commercially suitable colour scheme over its real material construction.",
        _PRODUCT_FORM_LOCK,
        "Keep the same exact product across all four panels: identical structure, material, proportions, base color, artwork, artwork scale and artwork placement.",
        "Do not invent another product, extra accessories attached to the product, text, captions, logos, labels, watermarks, collages or borders; keep the four-panel divider clean and centered. (Standalone scene props beside the product ARE required — see PART 4 ART DIRECTION.)",
        "If the product has an interior or lining, keep the interior surface unprinted (one plain solid color, black by default) and never extend the outer artwork inside.",
        _COLOR_IMPACT_BLOCK,
        _MATERIAL_REALISM_BLOCK,
        *_fabric_craft_lines(fields.fabric_craft),
    ))
    # PART 2 只放四格角色与机位/构图，且用户构图在这里覆盖默认机位。
    parts.append(_PART2_HEADER)
    parts.extend(_listing_panel_lines(composition))
    return "\n".join(parts)


# 从热卖 POD listing 反推出来的「场景美术（set design）」公式：背景若只是空白棚拍或空旷外景，
# 观感就"死气沉沉"。这里给正向约束——具体生活场景 + 同题材道具 + 同色系 + 暖向光 + 浅景深。
# 只作用于生活化主图（左上）与细节图背景；素材图（右下）必须保持纯净中性背景，不要道具。
# 场景池：**逐款轮换**。原来给的是固定列表（模型每次都挑同一条）→ 同批次各款背景雷同。
_STYLE_SCENES = (
    "a sunlit windowsill with soft morning light",
    "a warm wooden kitchen counter",
    "a cozy festive tabletop corner",
    "a styled living-room side table under warm lamplight",
    "a rustic garden table in golden afternoon light",
    "a linen-draped console table beside a bright window",
    "a pale marble countertop with soft daylight",
)


def _set_design_block(theme: str, palette: str, scene: str, *, scene_mood: str = "") -> str:
    """场景美术（set design）块。

    - ``scene_mood`` 非空时作为**整批场景基调 / 场景族**，逐款 ``scene`` 降级为「该基调内的具体起点场景」；
      留空则维持现状（逐款轮换 ``_STYLE_SCENES``）。
    - 光影归属构图模版（见 composition_runtime），本块**不再写主导性光线**，只保留 GLOW 作为场景氛围。
    """
    subject = theme or "this style's own subject"
    mood = str(scene_mood or "").strip()
    mood_lines = (
        [
            f"- BATCH SCENE MOOD (this batch): {mood}. Every style in this batch shares this same mood / "
            "scene family; the concrete setting below is this style's own starting point within that mood."
        ]
        if mood
        else []
    )
    return (
        "SET DESIGN — the lifestyle scene must look rich, warm and inviting; a plain studio wall or an "
        "empty outdoor field is a FAILURE (this is the main quality gap versus top-selling listings):\n"
        f"- Derive the scene's beauty from the PART 1 batch-wide theme together with this style's PART 3 "
        f"elements ({subject}) — the setting must clearly belong to this exact collection.\n"
        f"- Stage this ONE lived-in scene, adapted to that theme (this style's own starting point: {scene}).\n"
        + ("\n".join(mood_lines) + "\n" if mood_lines else "")
        + f"- Dress it with 3-5 supporting props from the same subject family as the artwork ({subject}) — "
        f"never generic filler, never a duplicate of the product itself — in colours from this style's "
        f"palette ({palette}) so they harmonize with the artwork.\n"
        "- GLOW — the scene must contain at least ONE visible warm light source in frame (fairy lights, "
        "candle, lantern, warmly lit window or glowing lamp) reading as a bright warm highlight with a "
        "soft halo or bokeh; an evenly lit scene with no visible light source is a FAILURE.\n"
        "- Camera and surfaces: shallow depth of field, soft background bokeh, one optional blurred "
        "foreground prop; tactile materials (wood, linen, lace, marble, woven mat, checkered cloth). The "
        "product stays the hero — centered, fully visible, sharply in focus; props only support it.\n"
        "- The scene must change from style to style inside this batch — never reuse one background set "
        "across different styles.\n"
        "- Applies to the lifestyle panel (top-left) and may softly continue into the detail panels' "
        "backgrounds. The material panel (bottom-right) must stay a clean neutral ecommerce background "
        "with no props."
    )


def build_style_listing_prompt(
    base_prompt: str,
    *,
    style_index: int,
    attempt: int,
    business_fields: Mapping[str, object] | None = None,
    creative_prompt: str = "",
    style_elements: Mapping[str, object] | None = None,
    seed: str = "",
) -> str:
    """Append one deterministic, batch-diverse creative recipe to a listing prompt.

    The recipe varies composition, palette, color tiers, rendering, and density.
    The style's SUBJECT comes from the assigned element tiers (hero / narrative /
    rhythm); when no assignment exists (legacy data), the model is told to invent
    one new subject within the batch-wide theme. Element keywords are never
    rendered wholesale.

    ``seed``（batch_id）用于配色 60-30-10 的确定性洗牌；留空时退化为仅按款序取值。
    """
    if style_index < 1:
        raise ValueError("style_index must be positive")
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")
    offset = style_index - 1
    hero, narrative, rhythm = _coerce_style_elements(style_elements)

    composition = _STYLE_COMPOSITIONS[(offset * 3) % len(_STYLE_COMPOSITIONS)]
    color_preferences = _brief_color_preferences(business_fields)
    # When the user named exact colors, defer to them instead of the recipe pool,
    # so a fixed palette cannot fight the brief.
    palette = (
        f"the brief's specified colors ({', '.join(color_preferences)})"
        if color_preferences
        else _STYLE_PALETTES[(offset * 7) % len(_STYLE_PALETTES)]
    )
    colors = assign_style_colors(color_preferences, style_index, seed)
    color_harmony = ""
    if colors["base"]:
        color_harmony = (
            f"base≈{colors['base']}(60%) / structural≈{colors['structural']}(30%) / "
            f"highlight≈{colors['highlight']}(10%)"
        )
    rendering = _STYLE_RENDERINGS[(offset * 7) % len(_STYLE_RENDERINGS)]
    density = _STYLE_DENSITIES[(offset * 3) % len(_STYLE_DENSITIES)]

    if hero:
        subject_lines = [
            f"本款素材主语：{hero}（Tier 1 Hero 主锚，视觉主角，尺度 1.0）"
        ]
        if narrative:
            subject_lines.append(
                f"叙事层 Narrative：{', '.join(narrative)}（Tier 2，第二尺度 0.6，围绕 Hero 成组）"
            )
        if rhythm:
            subject_lines.append(
                f"节奏层 Rhythm：{', '.join(rhythm)}（Tier 3，小尺度 0.25，归为一组点缀纹样）"
            )
        subject_lines.append(
            "元素搭配要有主次与联系：把本款元素组织成 2~3 处『成组的小景』（一处主元素 + 若干围绕它的陪衬），"
            "不要均匀散点平铺；同一组内的元素在题材或形态上必须互相呼应，避免把不相干的元素硬凑在一起。"
        )
        subject_lines.append(
            "清单中未分配给本款的元素在本款中禁止出现。"
        )
    else:
        subject_lines = [
            "本款未分配素材：必须在 Batch-wide theme & style 的主题内自创一个新元素作为本款素材主语。",
        ]
    subject_block = "\n".join(subject_lines)

    signature = (
        f"STYLE-{style_index:03d} | elements=hero:{hero or '-'} narrative:{','.join(narrative) or '-'} "
        f"rhythm:{','.join(rhythm) or '-'} | composition={composition} | "
        f"palette={palette} | rendering={rendering} | density={density}"
        + (f" | color_harmony={color_harmony}" if color_harmony else "")
    )

    rules = [
        base_prompt.rstrip(),
        "",
        _PART3_HEADER,
        "STYLE-SPECIFIC DIVERSITY CONTRACT:",
        f"Style creative signature: {signature}",
        subject_block,
        "主题整批统一风格必须保持，优先级高于本款配方建议；配方只提供可在该主题风格允许范围内实现的变化方式（尺度节奏、色块分割、带状/网格/散点律动等）。",
        f"Create this as style {style_index}. Its surface artwork must be visibly different from every other style in this batch.",
        "素材可以在批次内复用，但每款必须拥有不同的视觉主角与花样：主打元素身份、尺度关系、排布逻辑、色彩重心、渲染工艺至少两项不同。禁止输出“把所有元素等权复制粘贴”的通用大杂烩图案。",
        f"整幅图案（含所有元素与底色）必须采用本款渲染工艺（{rendering}）表现，不得与其他款式共用同一视觉语言。",
    ]
    if color_harmony:
        rules.append(
            f"本款配色 60-30-10（高定调色契约）：{color_harmony}。"
            "主基调色覆盖产品大面积底色，结构副色用于提手/包边/大面积拼接块，"
            "点缀色只用于金线刺绣/金属件/小坠饰；三档比例必须读得出来，避免均等无主次的配色。"
        )
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
    # 场景美术：用本款元素与配色去布置一个具体生活场景，避免背景空洞灰冷（竞品观感差距的主因）。
    # 场景逐款轮换，避免同批次所有款共用同一个背景。
    theme_desc = ", ".join(token for token in (hero, *narrative) if token)
    scene = _STYLE_SCENES[(offset * 5) % len(_STYLE_SCENES)]
    scene_mood = str((business_fields or {}).get("scene_mood") or "").strip()
    rules.append("")
    rules.append(_PART4_HEADER)
    rules.append(_set_design_block(theme_desc, palette, scene, scene_mood=scene_mood))
    rules.append(
        "Panel positions and roles stay fixed: top-left = PRIMARY image, top-right = detail A, "
        "bottom-left = detail B, bottom-right = material image. Do not swap, shift, or duplicate panels."
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
        # 半定制沿用「主打 / 辅主 / 点缀」措辞：这里把三级 Tier 归一化回收（hero→主打、
        # narrative→辅主、rhythm→点缀），既兼容新形状也兼容历史批次的旧形状。
        primary, narrative, accents = _coerce_style_elements(
            raw_elements if isinstance(raw_elements, Mapping) else None
        )
        co = narrative[0] if narrative else ""

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
