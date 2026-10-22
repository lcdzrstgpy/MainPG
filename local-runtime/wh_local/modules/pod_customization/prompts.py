from __future__ import annotations

import hashlib
import random
import re
from typing import Mapping, Sequence

from .contracts import COMPOSITION_PANEL_KEYS, IP_EXCLUDED_ELEMENTS, BusinessFields
from .structure_analysis import (
    render_part_count_lock,
    render_reference_views_block,
    render_structure_facts_block,
    render_transparent_material_lock,
)


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
# v5：Excluded elements 固定为侵权/身份类清单；豆包链路去掉运营块重复字段行。
# v6：取消自动生成的 v1 模板；硬性规则内置，用户输入改为「主体人工锚定」接在规则下方。
# v7：主体锚定提升为硬约束；PART 3 元素明确为印刷纹样，禁止做成 3D 实物/道具。
# v8：新增平铺产品硬锁（无垂坠/无厚边/无鼓包）与平面印花锁（硬币/蜡烛等不得凸起）。
# v9：平铺产品强制四格俯拍，忽略 eye-level / lifestyle 构图，避免把平垫子铺成垂坠桌布。
# v10：把 composition 改为 pattern_layout，明确它只是印花排布，不是相机/3D 场景构图。
# v11：平铺产品的 PART 4 改为纯平面摄影背景，移除 lifestyle/房间/家具/垂坠场景。
# v12：显式禁止四格象限字母/数字（A/B/C/D、1/2/3/4），避免拆格后角上残留英文字母。
# v13：PART 4 重建美术指导——补回主导性光线（暖向主光 + 投影 + 轮廓光）、场景道具密度、
#      细节格沿用同一场景与光向，解决成图「又素又平、没光影」。
# v14：图案密度真正生效——density 从「只写在 signature 里」升级为硬指令，密度池去掉 sparse/oversized 档。
# v15：明确 POD 是满印（all-over print）——PART 1 加 ALL-OVER PRINT LOCK，PART 3 要求无缝铺满
#      所有可印表面、覆盖率 100%，排布池与密度池全部改为满印接花描述（不再局部覆盖）。
# v16：新增图案布局锁（ARTWORK LAYOUT LOCK）——主图确定图案在商品表面的位置与相对尺度，
#      其余三格只换机位/景别，禁止重排；面板描述同步改为「同一布局的裁切/换视角」。
# v17：新增困难件结构锚定——结构事实表（部件数/层数/尺寸/材质）以字面事实注入，
#      配套 PART COUNT & SIZE LOCK、TRANSPARENT MATERIAL LOCK 与 REFERENCE VIEWS CONTRACT；
#      无结构视图/事实表的普通批次不渲染这三块，提示词与 v16 等价。
# v18：新增列式生成（两级批次）的四轮口径——第 1 轮 A1+B1+C1+D1，第 2/3/4 轮只有 A2+B2；
#      构图模板语义从「一张图的四格」改为「四次调用的四轮」。legacy 批次的提示词文本不变，
#      版本号只用于区分「改前/改后」的产出归因。
# v19：派生轮区分「必须连续」与「必须变化」——景深/机位距离/裁切必须按本轮方向变化，
#      新增 detail_a / detail_b 景别契约与「禁止复刻锚点机位」硬条款。
PATTERN_PROMPT_VERSION = "v19"
# 位置↔角色固定：左上=主图(lifestyle)、右上=细节A(detail_a)、左下=细节B(detail_b)、右下=素材(hero)。
# 标识符沿用历史值（lifestyle=主图、hero=素材），避免历史批次错标；仅位置分配按本元组顺序正确落位。
LISTING_IMAGE_ROLES = ("lifestyle", "detail_a", "detail_b", "hero")

# 元素关键词独立切分器：仅顿号/逗号/分号/换行，不含空格。
# 注意不要复用公共 splitBusinessField（前端），这里与后端自洽即可；
# 空格不做分隔，避免拆散 "Seamless Pattern" 这类英文复词。
_ELEMENT_SPLIT_PATTERN = "[、，,;；\n]+"

# 注意：这是「印花在平面上的排布方式」，不是相机/场景构图。
# 之前的 "one off-center focal composition" 被模型理解成 3D 场景机位，
# 导致平垫子被生成成垂坠桌布，所以全部改写为图案排布措辞。
# POD 是满印（all-over print）：排布只决定「满印接花怎么排」，不决定「只印哪一块」。
_STYLE_PATTERN_LAYOUTS = (
    "straight-grid all-over repeat",
    "half-drop offset all-over repeat",
    "brick all-over repeat",
    "edge-to-edge all-over repeat",
    "layered multi-scale all-over repeat",
    "radial all-over repeat centered on the front panel",
    "vertical banded all-over repeat",
    "horizontal banded all-over repeat",
    "scattered all-over repeat with full coverage",
    "overlapping collage all-over repeat",
)
# 平铺产品的「场景」只能是平面摄影背景，不能是房间/桌子/家具。
_FLAT_LAY_SURFACES = (
    "warm natural linen flat-lay backdrop",
    "pale oak tabletop surface, shot from directly above",
    "soft ivory paper sweep",
    "muted canvas flat-lay backdrop",
    "brushed light wood board",
    "pale marble slab",
    "warm greige fabric backdrop",
    "clean matte plaster surface",
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
    "full-coverage all-over print with no empty ground",
    "dense all-over repeat with layered multi-scale motifs",
    "dense all-over repeat with large-scale hero and small filler",
    "tight small-scale all-over repeat",
    "mixed-scale all-over repeat filling every gap",
    "banded all-over repeat with every band filled",
    "dense collage all-over repeat with overlapping motifs",
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
    "Panel 2 — DETAIL IMAGE A (top-right): a tight high-resolution close-up of the newly invented artwork on this same product, making colour, edges, print/material texture and manufacturing detail easy to inspect. It is a literal zoom into Panel 1's layout: the same motif stays at the same spot on the product and at the same relative scale. Do not alter, move or re-arrange the artwork.",
    "Panel 3 — DETAIL IMAGE B (bottom-left): a different close product detail or three-quarter view — a product-appropriate structural or material detail — with the artwork visibly identical to Panels 1-2. If the angle changes, the printed layout rotates rigidly with the product; the hero motif keeps its position on the same face and never drifts to a side edge, the top or a corner.",
    "Panel 4 — MATERIAL IMAGE (bottom-right): the complete product on a newly generated clean neutral ecommerce background, filling most of the panel with the full design sharply visible. Use the same front-facing layout as Panel 1 — same motif in the same place at the same scale. Supporting material imagery, not the marketplace primary image.",
    "Final check on the fixed order by position: top-left = complete product in a lifestyle scene, top-right = tight artwork close-up, bottom-left = a different close product detail, bottom-right = complete product on a neutral background. Do not swap, shift or duplicate panels, and do not re-lay-out the artwork in any panel.",
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
# 全定制（列式生成）的系统默认模板：四轮机位必须能被肉眼区分。
# 与旧「四格」默认模板分开存放——复刻/裂变仍用 DEFAULT_COMPOSITION_PANELS，
# 不能把「四轮」文案注入到它们的四格生成里。
DEFAULT_COLUMN_COMPOSITION_RAW_INPUT = (
    "系统默认：四轮差异化机位（主图 45° 三分点 / 细节 A 90° 俯拍占九成 / "
    "细节 B 极端微距出画 / 素材居中六成）"
)
DEFAULT_COLUMN_COMPOSITION_PANELS: Mapping[str, Mapping[str, str]] = {
    "panel_1": {
        "zh": "略低机位、斜侧 45° 的中全景，主体完整入画、约占画面七成，落在画面三分点位置，"
        "另一侧留出大面积留白，采用低角度掠射侧光搭配漫射环境补光打出立体感与柔和投影，"
        "背景大幅虚化、仅保留柔和中性的模糊层次。",
        "en": "Slightly low camera position, 45-degree oblique angle medium-full shot, the complete "
        "subject occupies about 70% of the frame, placed at the rule-of-thirds intersection, with "
        "large blank space on the other side; low-angle raking side light combined with diffused "
        "ambient fill creates dimensionality and soft shadows, the background is heavily blurred, "
        "retaining only soft neutral hazy layers.",
    },
    "panel_2": {
        "zh": "正上方 90° 垂直俯拍，机位抬升至主体正上方，主体完整入画、四边几乎贴住画面边缘、"
        "占满约九成画面，画面内不出现任何边缘与衬托物，采用与主图同源的均匀柔和漫射顶光，"
        "背景处理保持同主图的虚化质感。",
        "en": "Direct 90° top-down overhead shot, the camera is raised directly above the subject, "
        "the complete subject occupies about 90% of the frame with its edges nearly touching the "
        "frame borders, no edges or supporting objects appear in the frame; use even, soft diffused "
        "top light consistent with the main image, the background maintains the same blurred "
        "texture as the main image.",
    },
    "panel_3": {
        "zh": "机位贴近主体表面做极端微距拍摄，画面仅保留约十分之一面积的局部纹理，"
        "主体完整轮廓与背景全部出画，浅景深效果使焦点精准落在纹理结构上，"
        "采用与主图同源的侧向柔光，无多余衬托元素入镜。",
        "en": "Extreme macro shot with the camera positioned close to the subject surface, the frame "
        "only retains a local textured area occupying about one-tenth of the total image area, the "
        "full outline of the subject and the entire background are cropped out of frame; shallow "
        "depth of field places sharp focus on the textured structure, use side soft light consistent "
        "with the main image, no extra supporting elements enter the frame.",
    },
    "panel_4": {
        "zh": "正上方垂直俯拍，主体居中放置、约占画面六成，四边等距留出均匀空白，"
        "采用均匀无阴影的平光照明，背景为纯净中性的干净处理，无任何多余环境元素。",
        "en": "Direct top-down overhead shot, the subject is centered in the frame, occupying about "
        "60% of the image area, with even equal margins of blank space on all four sides; use "
        "shadowless even flat lighting, the background is a clean, pure neutral surface with no "
        "extra environmental elements.",
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
    "Never average the four directions into one compromise framing. A shooting direction changes the "
    "CAMERA ONLY — the printed artwork layout stays exactly as Panel 1 defines it (see ARTWORK LAYOUT "
    "LOCK)."
)
# 四格趋同是这批图最致命的失败模式，单独再钉一句。
_PANEL_DIFFERENTIATION_LINE = (
    "MANDATORY DIFFERENCE CHECK — the four quadrants must show clearly different elevations "
    "(high / eye-level / low) and distances (wide / close-up / macro), as each panel requests. If two "
    "quadrants share the same camera angle, re-shoot one of them: Four identical viewpoints is a "
    "failed result. Framing alone may differ: the artwork layout (motif position and relative scale "
    "on the product) must stay identical in all four."
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
# 硬性规则改为后端内置，不再由前端 v1 模板生成；用户只补充「主体人工锚定」。
_BUILTIN_HARD_RULES = (
    "硬性规则：\n"
    "1. 每款正常只生成一次 2×2 成组图片；生成、拆分或去重失败时最多重试一次。\n"
    "2. 四格顺序固定为主图、细节图 A、细节图 B、素材图。\n"
    "3. 同一款四张图必须保持产品、结构、底色、图案内容、图案尺寸与位置完全一致。\n"
    "4. 不得添加未授权品牌、商标、版权角色、文字或水印。\n"
    "5. 带内饰/内衬的产品（如收纳筐、脏衣篓、束口袋内里）：内饰表面保持无花色的统一纯色"
    "（默认黑色），图案只印在产品外表面，严禁把外表面的花色、底纹延伸到内饰上。"
)
# 用户手写的主体锚定：作为硬约束直接接在硬性规则下方，不单独开段。
# 不这样写，模型会把「80cm×80cm 全覆盖、无下挂」当成建议，继续把桌布生成成厚方块。
_SUBJECT_ANCHOR_HEADER = (
    "主体硬约束（用户填写，必须逐条满足；主体结构、材质、尺寸出现任何偏离即视为失败）："
)
_SUBJECT_ANCHOR_PRIORITY = (
    "以上主体硬约束与上述硬性规则同级，优先级高于模板的视觉比例。"
)
# 形态/尺寸锁定：模板只提供结构参考，但运营明确写出的平面/尺寸事实必须优先。
# 触发场景：80cm×80cm 全覆盖、无下挂的正方形天然橡胶桌布被生成成了正方体方块。
_PRODUCT_FORM_LOCK = (
    "PRODUCT FORM & SIZE LOCK — the product form and dimensions stated in the batch facts and batch "
    "hard rules are geometric facts and outrank the template's apparent proportions: if the product is "
    "flat (tablecloth, mat, towel, blanket, sticker, paper), it must read as a FLAT SHEET lying on the "
    "tabletop — never as a box, cube, slab, stack, cushion, tray, molded shell or other 3D block. "
    "Keep stated sizes and coverage literal (for example a square 80x80 tablecloth must stay square, "
    "cover the tabletop and have no overhang). Corners and edges must stay paper-thin and flat: no "
    "rounded bulge, no raised rim, no thick bevel, no table skirt, no drape and no hanging corner. "
    "Never thicken, pad, inflate or extrude a flat product to give it volume. Every panel must preserve "
    "the exact flat square silhouette of the template."
)
# 平铺类产品（桌垫/桌布/餐垫）的细节格不能拍厚边/鼓包：只拍平面印花与包边。
_FLAT_PANEL_DETAIL_LOCK = (
    "FLAT SHEET PANEL LOCK: if the product is a flat mat / tablecloth / placemat, Panel 3 (detail B) "
    "must be a top-down macro of the FLAT printed surface and edge stitching, not a corner, rim, "
    "side profile or three-quarter view. All four panels must keep the product flat on the tabletop "
    "with no overhang, drape, thickness or 3D structure."
)
# 硬币/蜡烛/星星等只能作为平面印花，不能被模型做成凸起实物。
_FLAT_PRINT_LOCK = (
    "FLAT PRINT LOCK: every motif is a 2D printed graphic on the product surface. Coins, candles, "
    "stars, ornaments or any other motif must have no thickness, no emboss, no raised relief and no "
    "cast object shadow; never place them as separate 3D objects on top of the product."
)
# 模型偶尔会在四格角上写 A/B 或 1/2 标记；拆格后这些字母会落进成品图。
# 必须显式禁止任何象限编号/字母。
_NO_QUADRANT_LABELS = (
    "NO QUADRANT LABELS: never write A, B, C, D, 1, 2, 3, 4 or any letter/number in the corners "
    "or near the four-panel divider. The four panels must be separated only by the clean divider; "
    "no text, captions, labels, stamps, signatures or watermarks anywhere in the image."
)
# 四格之间「图案在商品表面上的位置」必须是同一张布局图，只允许换机位/景别。
# 实测失败模式：主图里主元素（例如一棵树）在商品正中，细节图换个视角后跑到侧边/上方——
# 图还是那张图，但结构被重新排布，四张图拼不回同一件商品。
_ARTWORK_LAYOUT_LOCK = (
    "ARTWORK LAYOUT LOCK — Panel 1 (top-left) defines the printed layout ONCE, and the other three "
    "panels must reproduce that exact same layout:\n"
    "- The hero motif's position on the product surface (for example a tree centred on the front "
    "face), its size relative to the product face, its orientation, and its spacing from seams, "
    "edges and hardware form ONE fixed map. Every panel must show that same map: the same motif in "
    "the same place at the same scale, with the same repeat spacing.\n"
    "- Panels 2-4 may change camera angle, distance and crop, but must NEVER re-arrange, move, "
    "rotate, re-centre or resize the artwork for a nicer composition. A motif that is centred in "
    "Panel 1 and appears near a side edge, at the top, in a corner or at a different size in "
    "another panel is a FAILED result.\n"
    "- If a panel shows the product from another angle, the whole printed surface rotates with the "
    "product as one rigid sheet: a motif keeps the same spot on the same face. A new camera angle "
    "is never permission to lay the pattern out again.\n"
    "- A detail panel is a literal zoom into the SAME layout (it may reveal another area of it), "
    "not a new version of the artwork. Panels may show different areas, but the overall layout and "
    "the hero motif's anchored position and relative scale must stay identical across all four."
)


_FLAT_PRODUCT_KEYWORDS = (
    "桌布", "桌垫", "餐垫", "地垫", "瑜伽垫", "鼠标垫", "杯垫", "毛毯", "毯子",
    "毛巾", "贴纸", "tablecloth", "placemat", "mat", "blanket", "towel", "sticker",
)
_FLAT_PRODUCT_SIGNALS = ("平铺", "无下挂", "无垂坠", "全覆盖桌面")
_FLAT_LISTING_PANEL_LINES = (
    "Panel 1 — PRIMARY IMAGE (top-left): straight top-down flat lay of the complete product lying "
    "flat on a styled tabletop; all four edges visible inside the frame, no table edge, no chairs, "
    "no drape, no hanging corner, no thickness.",
    "Panel 2 — DETAIL IMAGE A (top-right): top-down macro of the same artwork layout shown in "
    "Panel 1 — the same motif at the same spot on the sheet and at the same relative scale.",
    "Panel 3 — DETAIL IMAGE B (bottom-left): top-down macro of a different area of the same printed "
    "layout plus edge stitching; never a corner, rim, side profile or three-quarter view, and never "
    "a re-arranged or re-scaled version of the artwork.",
    "Panel 4 — MATERIAL IMAGE (bottom-right): straight top-down of the complete flat product on a "
    "clean neutral ecommerce background, using the same layout as Panel 1 (same motif position and "
    "scale).",
)
_FLAT_COMPOSITION_OVERRIDE = (
    "FLAT PRODUCT COMPOSITION OVERRIDE: this is a flat sheet product. Ignore any eye-level, "
    "three-quarter or lifestyle camera direction; all four panels are TOP-DOWN flat lays. The product "
    "must stay flat on the tabletop with no overhang, drape, table skirt, hanging corner or thickness."
)


def _is_flat_product(fields: BusinessFields, subject_anchor: str) -> bool:
    text = " ".join(
        (fields.product_name, fields.product_category, subject_anchor)
    ).casefold()
    return any(token.casefold() in text for token in _FLAT_PRODUCT_KEYWORDS) or any(
        signal in text for signal in _FLAT_PRODUCT_SIGNALS
    )


# 公开别名：列式生成（column_prompts / worker）需要复用同一套平铺判定。
is_flat_product = _is_flat_product


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


def _structure_anchor_blocks(
    structure_facts: Mapping[str, object] | None,
    reference_view_roles: Sequence[str] | None,
) -> tuple[str, ...]:
    """困难件结构锚定块：无事实表/无多视图时返回空元组（提示词与旧版等价）。"""
    blocks = [
        render_structure_facts_block(structure_facts),
        render_part_count_lock(structure_facts),
        render_transparent_material_lock(structure_facts),
        render_reference_views_block(reference_view_roles or ()),
    ]
    return tuple(block for block in blocks if block)


def build_direct_listing_prompt(
    fields: BusinessFields,
    subject_anchor: str,
    *,
    composition: Mapping[str, str] | None = None,
    structure_facts: Mapping[str, object] | None = None,
    reference_view_roles: Sequence[str] | None = None,
) -> str:
    """Prompt for one product-locked four-panel listing contact sheet.

    Batch-wide facts are rendered verbatim for every style. Element keywords are
    deliberately NOT rendered here: they are assigned per style and injected by
    ``build_style_listing_prompt``, so no style ever receives the full list.

    硬性规则由后端内置；``subject_anchor`` 是用户在「高级·主体人工锚定」里手写的
    主体描述，接在硬性规则下方。没有主体描述时只输出内置规则，不再拼接旧 v1 模板。

    ``composition`` 为用户最新创作的构图（panel_1..4 画面指令）。提供时用它替换写死的
    四格机位/构图描述，但保留全部硬约束（2×2、同产品同图案、禁文字/水印、内饰不印等）。

    ``structure_facts`` 为上传多视图后识图得到的结构事实表；``reference_view_roles``
    为本次实际下发给生图模型的参考图角色（主图在前）。两者只在困难件批次存在，
    普通批次不渲染任何新增块。
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
        # 只固定输出侵权/身份类；危险违禁内容交给模型自身审核。
        ("Excluded elements", ", ".join(IP_EXCLUDED_ELEMENTS)),
    ):
        if value:
            parts.append(f"{label}: {value}.")
    # 硬性规则始终内置；用户手写的主体锚定直接接在规则下方，作为同级硬约束。
    parts.extend(("", _BATCH_HARD_RULES_HEADER, _BUILTIN_HARD_RULES))
    anchor = subject_anchor.strip()
    if anchor:
        parts.extend((_SUBJECT_ANCHOR_HEADER, anchor, _SUBJECT_ANCHOR_PRIORITY))
    parts.append("")
    parts.extend((
        # 精简原则：一条约束只说一遍。原先同一件事（别复用模板 / 保留绗缝 / 同款同图）各写了 3-4 遍，
        # 长提示词会把关键契约（色彩冲击、工艺）稀释掉；这里逐条去重，语义一条不减。
        "Treat the supplied template only as a STRUCTURAL reference for this product: geometry, proportions, construction, material, printable surface location, and its true surface relief (quilting/padding grid if present, seams, topstitching, hardware).",
        # 几何锁定：模板白底图是唯一的结构基准，显式禁止拉伸/压扁/熔形等畸变，避免四格之间结构漂移。
        "GEOMETRY LOCK: the product's silhouette, seam lines, panel divisions, quilting grid (if present), hardware positions and strap/handle drop must match the template exactly. Do not stretch, squash, melt, bend, reshape or re-proportion any part, and do not add, merge or delete structural parts.",
        # 困难件结构锚定（v17）：只在有结构事实表 / 多视图时出现，普通批次不渲染。
        *_structure_anchor_blocks(structure_facts, reference_view_roles),
        "Do not copy the template's existing artwork, decoration, colour scheme, background, lighting or scene.",
        "CARVE-OUT: the template's quilting/padding relief, seams and stitching are STRUCTURE, not decoration — preserve them exactly as shown; do not flatten the quilted surface into a smooth shell. Only the artwork, the colour scheme and the scene are to be replaced.",
        "That restriction only forbids REUSING the template's own scene. It does NOT mean the background may be plain: a freshly styled, richly lit scene is required (see PART 4 ART DIRECTION).",
        "Use the template as the exact structural base: keep its silhouette, thickness, edge profile, proportions and flatness unchanged. Replace ONLY the printed artwork, the colour scheme and the scene; never redraw or reshape the product itself.",
        _PRODUCT_FORM_LOCK,
        _FLAT_PRINT_LOCK,
        _NO_QUADRANT_LABELS,
        "Keep the same exact product across all four panels: identical structure, material, proportions, base color, artwork, artwork scale and artwork placement.",
        _ARTWORK_LAYOUT_LOCK,
        "ALL-OVER PRINT LOCK: the artwork is a full-surface all-over print — it repeats seamlessly edge to edge across every printable surface (front, sides, base, handles and bindings), with no blank ground areas and never a single isolated motif floating on a plain shell.",
        "Do not invent another product, extra accessories attached to the product, text, captions, logos, labels, watermarks, collages or borders; keep the four-panel divider clean and centered. (Standalone scene props beside the product ARE required — see PART 4 ART DIRECTION.)",
        "If the product has an interior or lining, keep the interior surface unprinted (one plain solid color, black by default) and never extend the outer artwork inside.",
        _COLOR_IMPACT_BLOCK,
        _MATERIAL_REALISM_BLOCK,
        *_fabric_craft_lines(fields.fabric_craft),
    ))
    # PART 2 只放四格角色与机位/构图，且用户构图在这里覆盖默认机位。
    # 平铺产品强制四格俯拍：否则「eye-level lifestyle scene」会把平垫子铺成垂坠桌布。
    parts.append(_PART2_HEADER)
    if _is_flat_product(fields, subject_anchor):
        parts.extend(_FLAT_LISTING_PANEL_LINES)
        parts.append(_FLAT_COMPOSITION_OVERRIDE)
    else:
        parts.extend(_listing_panel_lines(composition))
    parts.append(_FLAT_PANEL_DETAIL_LOCK)
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


def _flat_set_design_block(palette: str, surface: str, *, scene_mood: str = "") -> str:
    """平铺产品的场景块：只有平面摄影背景，不允许房间/桌子/家具/垂坠。"""
    mood = str(scene_mood or "").strip()
    mood_line = f" BATCH SCENE MOOD: {mood}." if mood else ""
    return (
        "FLAT-LAY SET DESIGN — the product lies flat on a flat photography surface, shot from "
        "directly above. No room, no table edge, no chairs, no furniture, no hanging cloth, no "
        "3D scene. The full square silhouette must stay visible. 1-3 flat props may sit beside "
        f"the product, never on it, in colours from this style's palette ({palette})."
        f"{mood_line} Surface: {surface}.\n"
        "- LIGHTING — one clearly directional soft daylight from the upper side, with gentle cast "
        "shadows beside the props and visible surface shading; flat, shadowless, evenly lit light is a "
        "FAILURE.\n"
        "- Panels 2-3 keep the SAME surface and the SAME light direction as Panel 1, only tighter crops."
    )


def _set_design_block(theme: str, palette: str, scene: str, *, scene_mood: str = "") -> str:
    """场景美术（set design）块。

    - ``scene_mood`` 非空时作为**整批场景基调 / 场景族**，逐款 ``scene`` 降级为「该基调内的具体起点场景」；
      留空则维持现状（逐款轮换 ``_STYLE_SCENES``）。
    - 光影由本块主导：构图模板链路并未进入生图提示词，删掉这里的光线会让成图变平、变素。
    """
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
        "- Derive the scene's mood from the PART 1 batch-wide theme. The PART 3 elements are PRINTED "
        "motifs on the product surface, NOT physical props — do not turn them into 3D objects.\n"
        f"- Stage this ONE lived-in scene, adapted to that theme (this style's own starting point: {scene}).\n"
        + ("\n".join(mood_lines) + "\n" if mood_lines else "")
        + "- FILL THE FRAME — dress the set with 3-5 supporting props taken from this theme's real usage "
        "world (for a cosmetics bag: makeup brushes, bottles, a small mirror, folded linen), grouped "
        "around the product in clear near/mid/far layers. Never generic filler, never a duplicate of the "
        f"product, and never a 3D copy of a printed motif. Colours from this style's palette ({palette}) "
        "so they harmonize with the artwork.\n"
        "- LIGHTING — one clear warm directional key light (low warm window light, golden afternoon "
        "light, or a warm lamp) that puts defined bright highlights on the product, a soft contact "
        "shadow beneath it and natural falloff into the background; optionally add a warm rim/backlight "
        "to separate the product from the set. Flat, grey, evenly lit studio light with no visible "
        "light direction is a FAILURE.\n"
        "- GLOW — the scene must contain at least ONE visible warm light source in frame (fairy lights, "
        "candle, lantern, warmly lit window or glowing lamp) reading as a bright warm highlight with a "
        "soft halo or bokeh; an evenly lit scene with no visible light source is a FAILURE.\n"
        "- Camera and surfaces: shallow depth of field, soft background bokeh, one optional blurred "
        "foreground prop; tactile materials (wood, linen, lace, marble, woven mat, checkered cloth). The "
        "product stays the hero — centered, fully visible, sharply in focus; props only support it.\n"
        "- The scene must change from style to style inside this batch — never reuse one background set "
        "across different styles.\n"
        "- Panels 1-3 are ONE shoot: the detail panels keep the SAME set surface, the SAME light "
        "direction and the SAME shallow depth of field, only with tighter crops — they must never drop "
        "to a plain grey studio backdrop. The material panel (bottom-right) must stay a clean neutral "
        "ecommerce background with no props."
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

    The recipe varies the flat pattern layout, palette, color tiers, rendering, and density.
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

    pattern_layout = _STYLE_PATTERN_LAYOUTS[(offset * 3) % len(_STYLE_PATTERN_LAYOUTS)]
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
            f"本款印花主元素：{hero}（Tier 1 主锚，印在产品表面的平面图案，不是独立实物；尺度 1.0）"
        ]
        if narrative:
            subject_lines.append(
                f"印花叙事层 Narrative：{', '.join(narrative)}（Tier 2，第二尺度 0.6，满印中与主元素交替重复，"
                "每种至少重复 4 次）"
            )
        if rhythm:
            subject_lines.append(
                f"印花点缀层 Rhythm：{', '.join(rhythm)}（Tier 3，小尺度 0.25，作为满印填充纹样，"
                "连续铺满主元素之间的所有空隙）"
            )
        subject_lines.append(
            "以上所有元素都是印在产品表面的平面图案/纹样：禁止生成独立实物、摆件、餐具、食品、家具或场景道具，"
            "也不得把元素做成 3D 立体装饰。"
        )
        subject_lines.append(
            "满印硬要求：这是 all-over print，不是局部印花。图案必须无缝、连续地铺满产品所有可印表面"
            "（正面、侧面、底部、提手/包边等），边到边、四角到四角，任何位置都不留大片纯色空白；"
            "主元素、次级纹样、填充纹样按层级交织重复，不能只在局部放 1~2 个图案；"
            "同一组内的图案元素在题材或形态上必须互相呼应，避免把不相干的元素硬凑在一起。"
        )
        subject_lines.append(
            "清单中未分配给本款的元素在本款中禁止出现。"
        )
    else:
        subject_lines = [
            "本款未分配印花元素：必须在 Batch-wide theme & style 的主题内自创一个新图案元素作为本款印花主元素，"
            "并印在产品表面，不得做成独立实物。",
        ]
    subject_block = "\n".join(subject_lines)

    signature = (
        f"STYLE-{style_index:03d} | elements=hero:{hero or '-'} narrative:{','.join(narrative) or '-'} "
        f"rhythm:{','.join(rhythm) or '-'} | pattern_layout={pattern_layout} | "
        f"palette={palette} | rendering={rendering} | density={density}"
        + (f" | color_harmony={color_harmony}" if color_harmony else "")
    )

    rules = [
        base_prompt.rstrip(),
        "",
        _PART3_HEADER,
        "STYLE-SPECIFIC DIVERSITY CONTRACT:",
        f"Style creative signature: {signature}",
        "pattern_layout 只描述印花图案在平面上的排布方式，不是相机角度、景别或 3D 场景构图；机位固定由 PART 2 决定。",
        subject_block,
        "四格图案布局锁定：主图（左上）确定的图案在商品表面的位置、相对尺度与接花间距，"
        "在另外三格必须完全一致；其余三格只允许换机位/景别或裁切，禁止把主元素挪到侧边、顶部、"
        "角落或改变它相对商品的尺寸。换视角时整张图案随商品一起刚性旋转，不得重新排版。",
        f"图案密度（本款硬要求，density={density}）：整幅是满印，可印表面覆盖率 100%，"
        "不允许出现空白底色区；主元素、次级纹样、填充纹样必须同时、重复地出现在整个表面。"
        "只在局部放图案或出现大片留白，视为失败。",
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
            "若主基调色是白色/珠光白/浅色，它只能用于图案留白或场景背景，产品主体改用色板中最饱和的颜色，"
            "避免白包白底、画面发灰。"
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
    # 平铺产品的 PART 4 也必须走平面摄影背景，否则会把俯拍指令拉回 3D 桌面场景。
    flat_product = "FLAT PRODUCT COMPOSITION OVERRIDE" in base_prompt
    surface = _FLAT_LAY_SURFACES[(offset * 5) % len(_FLAT_LAY_SURFACES)]
    rules.append("")
    rules.append(_PART4_HEADER)
    rules.append(
        _flat_set_design_block(palette, surface, scene_mood=scene_mood)
        if flat_product
        else _set_design_block(theme_desc, palette, scene, scene_mood=scene_mood)
    )
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
        ("Excluded elements", ", ".join(IP_EXCLUDED_ELEMENTS)),
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

        pattern_layout = _STYLE_PATTERN_LAYOUTS[(offset * 3) % len(_STYLE_PATTERN_LAYOUTS)]
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
        lines.append(f"pattern_layout = {pattern_layout}.")
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
