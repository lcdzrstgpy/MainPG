from __future__ import annotations

import hashlib
import random
import re
from typing import Mapping, Sequence

from .contracts import COMPOSITION_PANEL_KEYS, BusinessFields


PATTERN_PROMPT_VERSION = "v1"
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
    """按款式确定性分配元素（种子随机 + 批内不重复）。

    - 以 ``seed``（batch_id）洗牌元素数组；同一批次重放得到同一分配（重试可复现），
      不同批次种子不同 → 分配不同（跨批变化）。
    - 每款结构：1 主打 + 1 辅主 + ≤4 点缀（共 3~6 个）；款内不重复，
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
    for step in (1, 2, 3, 4):
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


# 四格固定角色/机位的默认描述（未配置用户构图时使用）。
_DEFAULT_LISTING_PANEL_LINES = (
    "Panel 1 — PRIMARY IMAGE (top-left): show the same complete product in one newly generated, natural, commercially useful lifestyle setting. Keep the full product and unchanged artwork visible; this is the marketplace primary image and title reference. Do not reuse the template background or add another product.",
    "Panel 1 must be a wide lifestyle scene with the whole product inside a real environment, shot at a normal eye-level product angle, with the full silhouette in frame. It must never be a close-up, macro, cropped, partial, extreme-angle, or three-quarter detail shot: those belong to Panel 2 and Panel 3 and must not be repeated in the top-left panel.",
    "Panel 2 — DETAIL IMAGE A (top-right): show a tight high-resolution close-up of the newly invented surface artwork on this same product. Make color, edges, print or material texture, and manufacturing detail easy to inspect; do not alter the artwork or its placement. This close-up belongs to the top-right panel only.",
    "Panel 3 — DETAIL IMAGE B (bottom-left): show a different close product detail or three-quarter product view. Choose a product-appropriate structural or material detail, while keeping the artwork visibly identical to Panel 1 and Panel 2. This detail view belongs to the bottom-left panel only.",
    "Panel 4 — MATERIAL IMAGE (bottom-right): show one complete product against a newly generated clean neutral ecommerce background. Keep the whole product clearly visible, make it fill most of the panel, and show the full design sharply. This is supporting material imagery, not the marketplace primary image.",
    "Final check on the fixed order by position: top-left = the complete product in a lifestyle primary scene, top-right = tight artwork close-up, bottom-left = a different close product detail, bottom-right = the complete product on a neutral background. Do not swap, shift, or duplicate panels.",
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
    "panel exactly as its own direction says. These directions OVERRIDE the default shot normally implied "
    "by the panel role name; never fall back to the generic role shot and never reuse another panel's "
    "framing. Read every direction literally: '45-degree downward' must be clearly shot from above, "
    "'vertically top-down' must look straight down from directly overhead, 'low eye-level' must sit below "
    "the product, 'front-facing eye-level' must be a straight on-axis view, and 'macro close-up' must fill "
    "the frame with the surface. Do not average the four directions into one compromise framing."
)
# 四格趋同是这批图最致命的失败模式，单独再钉一句。
_PANEL_DIFFERENTIATION_LINE = (
    "MANDATORY DIFFERENCE CHECK — before finishing, verify the four quadrants show four clearly different "
    "camera angles: different elevation (high / eye-level / low) and different distance (wide / medium / "
    "close-up / macro) as each panel requests. If two quadrants end up sharing the SAME camera angle, the "
    "image is WRONG — re-shoot one of them. Four identical viewpoints is a failed result."
)


# 竞品观感的另一个关键：图要"鲜艳通透有吸引力"。实测我们出的图偏暗、偏灰、偏素——
# 因为整条提示词里没有任何"提饱和/提亮"的要求，模型只会照暗色板忠实输出。
# 注意：不改变用户指定色板，只要求"在色板内拉满饱和度与亮度，别做灰做旧"。
_COLOR_IMPACT_BLOCK = (
    "COLOR IMPACT — the result must look vivid, rich and inviting, never dull, grey or washed out:\n"
    "- Render the palette at its most saturated and luminous. Deep/dark colors must read as RICH and "
    "JEWEL-LIKE (lacquer, enamel, velvet), never muddy, hazy, dusty or faded.\n"
    "- Keep the picture crisp and high-contrast: clean bright highlights, clear mid-tones and deep rich "
    "shadows. Avoid a flat, hazy, low-contrast, under-lit or aged-look image.\n"
    "- Warm the white balance toward sunlight; do not let a cold blue-grey cast flatten the picture.\n"
    "- Build the PRODUCT BODY itself on the strongest, most saturated signature color of the palette "
    "(the batch-wide theme's hero color) as its dominant base — the product must be the boldest color "
    "statement in the whole frame. A pale, white or neutral product shell with only faint motifs is a "
    "FAILURE. Lighter palette colors may only fill the pattern's negative space, the interior/lining, "
    "or the background.\n"
    "- TONAL SEPARATION — the product must separate clearly from its background through a strong "
    "difference in value and/or saturation. Never place a pale product on a pale background: if the "
    "product reads light, the background must be clearly darker or richer, and vice versa. A "
    "low-contrast, light-on-light, washed-out frame is a FAILURE.\n"
    "- Keep props and the scene colorful and high-saturation too; a muted beige/grey set is a FAILURE."
)

# POD 绝大多数是布艺（帆布/棉/绗缝/PU）包袋。实测我们出的图"像把图案喷在光滑塑料壳上"——
# 因为提示词从没要求过面料工艺与布面行为。这里补上真实的纺织品质感。
_FABRIC_REALISM_BLOCK = (
    "TEXTILE REALISM — this is a real sewn fabric product, never a flat spray-painted surface:\n"
    "- The supplied template photo shows this product's TRUE construction — reproduce that construction "
    "faithfully. If the template body is quilted/padded, the output MUST keep that same quilted relief; "
    "do not smooth it away.\n"
    "- Match the product's ACTUAL construction shown in the template: a padded/quilted body must show its "
    "quilt grid and the puffy padding raised between the stitch lines; a plain canvas body must show clean "
    "seams and topstitching. Never render a smooth, featureless, hard shell.\n"
    "- QUILTING (apply only when the template body is quilted or padded; skip it for plain canvas): "
    "reproduce the TEMPLATE's exact quilting type, cell size and stitch pitch — never invent a coarser, "
    "larger or more irregular quilting than the template shows. The quilt grid must be REGULAR and "
    "perfectly UNIFORM: every cell the same size, laid out in one consistent geometry (parallel diagonal "
    "diamond or square grid) that aligns with the product's panels, seams and edges.\n"
    "- Each quilted cell must be puffed to the SAME even, gentle height with a smooth rounded crown: a "
    "soft highlight on the crown and a soft shadow in the valley between cells. Cell size must stay "
    "small-to-medium and proportionate to the product — fine grain, never oversized pillows.\n"
    "- The stitch lines must stay clearly readable: a continuous, straight line of fine dashed "
    "topstitching runs along EVERY grid line, with a subtle indentation/groove and a faint thread shadow "
    "where the stitching pulls the fabric down.\n"
    "- QUILTING FAILURES — never produce: irregular lumpy blobs, marshmallow-like random bubbles, uneven "
    "or oversized padding, a grid pitch that changes across the product, wavy or missing stitch lines, or "
    "a surface that reads as a puffy duvet instead of a tailored quilted product.\n"
    "- Show real fabric: visible woven canvas/cotton weave with a soft matte sheen, subtle fibre fuzz along "
    "edges. Never glossy plastic, vinyl or airbrushed paint.\n"
    "- Show natural fabric behaviour: gentle folds, soft creases, slight sag where the fabric hangs, and "
    "tension lines where straps and zippers pull. Light must wrap these folds with soft shadows and highlights.\n"
    "- Show real sewing details: topstitching along every edge and strap, a fabric zipper with tape, and "
    "subtle seam shadowing.\n"
    "- The printed/embroidered motifs must sit ON the weave and FOLLOW the fabric's folds and curvature "
    "(they bend with the surface) — they must never look like flat stickers floating over a smooth shell.\n"
    "- Keep the artwork itself unchanged and sharply readable while adding this textile realism."
)

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
        "Every panel must still show the same exact product wearing the same unchanged newly invented artwork, with the four-panel divider clean and centered.",
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
        "Treat the supplied template only as a structural product reference for product geometry, construction, proportions, material, scale, and printable surface location — including its sewn construction relief: the quilting/padding grid, the puffiness between stitch lines, seams, topstitching and hardware.",
        "Do not copy, trace, preserve, or extend the template's existing artwork, decoration, colour scheme, background, room, furniture, lighting, shadows, camera framing, or scene.",
        "CARVE-OUT: the template's quilting/padding relief, seams and stitching are STRUCTURE, not decoration. They MUST be preserved exactly as the template shows them — do not flatten the quilted surface into a smooth shell. Only the artwork, the colour scheme and the scene are to be replaced.",
        "That restriction only forbids REUSING the template's own scene. It does NOT mean the background may be plain: a freshly styled, richly lit scene is required (see PART 4 ART DIRECTION).",
        "The template is not a background plate and must not appear as the base image. Invent a completely new PRINTED pattern and a fresh commercially suitable colour scheme — while keeping the fabric's real quilted/padded relief and stitching.",
        "Keep the same exact product across all four panels: identical structure, material, proportions, base color, newly invented artwork, artwork scale, and artwork placement.",
        "Do not invent another product, accessories attached to the product itself, text, captions, logos, labels, watermarks, collages, or borders. Keep the four-panel divider clean and centered. (Standalone scene props beside the product ARE required — see PART 4 ART DIRECTION.)",
        "Panel order is fixed and every panel must show the same exact product with the same unchanged newly invented artwork.",
        "When the product has an interior or lining (for example a laundry hamper, storage basket, or tote bag with an inner lining), keep the interior surface unprinted: a plain uniform solid color, black by default. Never extend the outer surface artwork onto the interior, and never add a second pattern inside.",
        _COLOR_IMPACT_BLOCK,
        _FABRIC_REALISM_BLOCK,
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


def _set_design_block(theme: str, palette: str, scene: str) -> str:
    subject = theme or "this style's own subject"
    return (
        "SET DESIGN — the lifestyle scene must look rich, warm and inviting; a plain studio wall or an "
        "empty outdoor field is a FAILURE (this is the main quality gap versus top-selling listings):\n"
        f"- Derive the scene's beauty from the PART 1 batch-wide theme together with this style's PART 3 "
        f"elements ({subject}) — the setting must clearly belong to this exact collection.\n"
        f"- Stage this ONE lived-in scene, adapted to that theme (starting point for this style: {scene}). "
        "Only props that genuinely belong to that scene; never generic filler.\n"
        f"- Dress it with 3-5 supporting props from the SAME subject family as the artwork ({subject}); "
        "never generic filler, and never a duplicate of the product itself.\n"
        f"- Prop colors must come from this style's own palette: {palette}. They must harmonize with the "
        "artwork, never clash with it.\n"
        "- Lighting: warm directional light (window light, golden afternoon light, or a warm lamp) with "
        "clear highlights and soft natural shadows. Never flat, grey, even light.\n"
        "- GLOW — in the lifestyle scene include at least ONE visible warm glowing light source (string "
        "fairy lights, a lit candle, a lantern, a warmly lit window or a glowing lamp) that appears in "
        "frame as a bright warm highlight with a soft warm halo or bokeh. The scene must contain a real "
        "point of glow, not only diffuse ambient daylight; an evenly lit scene with no visible light "
        "source is a FAILURE.\n"
        "- Camera: shallow depth of field, soft background bokeh, optionally one blurred foreground prop "
        "framing the product.\n"
        "- Materials: include tactile surfaces (wood, linen, lace, marble, woven mat, checkered cloth).\n"
        "- The product stays the hero: centered, fully visible, sharply in focus; props only support it.\n"
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
    ][:4]

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
            subject_lines.append(f"点缀：{', '.join(accents)}（小尺度，0.25，至多 4 个）")
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
        f"STYLE-{style_index:03d} | elements=primary:{primary or '-'} co:{co or '-'} "
        f"accents:{','.join(accents) or '-'} | composition={composition} | "
        f"palette={palette} | rendering={rendering} | density={density}"
        + (f" | accent_colors={accent_colors}" if accent_colors else "")
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
    # 场景美术：用本款元素与配色去布置一个具体生活场景，避免背景空洞灰冷（竞品观感差距的主因）。
    # 场景逐款轮换，避免同批次所有款共用同一个背景。
    theme_desc = ", ".join(token for token in (primary, co) if token)
    scene = _STYLE_SCENES[(offset * 5) % len(_STYLE_SCENES)]
    rules.append("")
    rules.append(_PART4_HEADER)
    rules.append(_set_design_block(theme_desc, palette, scene))
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
        elements = raw_elements if isinstance(raw_elements, Mapping) else {}
        primary = str(elements.get("primary") or "").strip()
        co = str(elements.get("co") or "").strip()
        accents = [
            token
            for token in (str(item).strip() for item in elements.get("accents") or [])
            if token and token not in (primary, co)
        ][:4]

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
