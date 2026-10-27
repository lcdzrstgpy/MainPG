"""列式生成的四轮提示词。

与 legacy 的 ``prompts.build_style_listing_prompt`` 不同，这里不再是「一款四格」，
而是「一轮四款」：

- 第 1 轮（主图）：A1 整图硬约束 + B1 场景构图 + C1 元素/色系轮转 + D1 美术指导；
- 第 2/3/4 轮（派生）：只有 A2 整图硬约束/主体锚定 + B2 场景构图。

派生轮刻意不渲染 C（元素轮转）与 D（美术指导）：图案已被锚点锁死，再写轮转引擎会
诱导模型重新设计；场景与质感在第 1 轮定稿，再写美术指导等于允许它换场景。

复用 ``prompts`` 的公共常量与轮转引擎，避免把 ``prompts.py`` 继续撑大；这里读取
的同包私有常量在 ``prompts.py`` 顶部已有版本注释，改动时两边一起看。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .column_generation import (
    DERIVED_ROLES,
    PRIMARY_ROLE,
    ROLE_LABELS,
    ROLE_PANEL_KEY,
)
from .contracts import ArtDirection, BusinessFields, IP_EXCLUDED_ELEMENTS, SceneOption
# 锚定段落与复刻派生面板共用一份（见 prompt_anchor_blocks），这里沿用原私有名。
from .prompt_anchor_blocks import (
    ANCHOR_BINDING as _ANCHOR_BINDING,
    ANCHOR_CONTINUITY as _ANCHOR_CONTINUITY,
    CAMERA_MUST_DIFFER as _CAMERA_MUST_DIFFER,
    OUTPUT_FORMAT_LOCK as _OUTPUT_FORMAT_LOCK,
    RIGID_ARTWORK_LOCK as _RIGID_ARTWORK_LOCK,
)
from .prompts import (
    _COLOR_IMPACT_BLOCK,
    _FALLBACK_SCENE,
    _FALLBACK_SURFACE,
    _FLAT_LISTING_PANEL_LINES,
    _FLAT_PRINT_LOCK,
    _MATERIAL_REALISM_BLOCK,
    _NO_QUADRANT_LABELS,
    _PRODUCT_FORM_LOCK,
    _brief_color_preferences,
    _coerce_style_elements,
    _fabric_craft_lines,
    _flat_set_design_block,
    _structure_anchor_blocks,
    assign_style_colors,
    element_area_shares,
    pick_scene_option,
    render_element_weight_contract,
)

# 提示词版本随 prompts.PATTERN_PROMPT_VERSION 一起看：v18 起新增列式生成的四轮口径；
# v19：派生轮拆分「必须连续 / 必须变化」，新增景别契约与禁止复刻锚点机位。
# v20：删除布局/密度/渲染池——每款只保留元素层级与 60-30-10 配色，排布交给模型自由发挥。
# v21：scene_mood 不再注入轮次提示词；场景基调统一由美术指导（art_direction）承担。
# v22：恢复满印密度契约（主锚≥8 次、narrative≥6 次、rhythm 填空隙、覆盖率≥90%）。
# v23：权重升级为「面积份额 + 相对尺寸」——典型 1/2/4 配比时约 53/31/16%。
# v24：Rhythm 不再从元素池分配，由生图模型按主题自选 2-3 个小纹样/纹理。
# v25：去掉「主元素至少重复 3 次」，改为「图案可以重复，但主元素必须是本款最大图案」。
COLUMN_PROMPT_VERSION = "v25"

_QUADRANT_POSITIONS = ("TOP-LEFT", "TOP-RIGHT", "BOTTOM-LEFT", "BOTTOM-RIGHT")

_BUILTIN_COLUMN_RULES = (
    "硬性规则：\n"
    "1. 每个二级批次（4 款）正常生成 4 轮，每轮一张 2×2 成组图片；失败最多重试一次。\n"
    "2. 每轮四格固定对应组内 4 个款：左上=第 1 款、右上=第 2 款、左下=第 3 款、右下=第 4 款。\n"
    "3. 同一款的四轮产物必须保持产品、结构、底色、图案内容、图案尺寸与位置完全一致；"
    "同一轮四格必须是四个不同款，禁止串色串图。\n"
    "4. 不得添加未授权品牌、商标、版权角色、文字或水印。\n"
    "5. 内饰/内衬保持无花色统一纯色，图案只印外表面。"
)

_QUADRANT_ISOLATION_LOCK = (
    "QUADRANT ISOLATION LOCK — the four panels are four DIFFERENT product designs (four styles of "
    "one collection) on the SAME physical product. Each quadrant's motifs, palette, scale rhythm and "
    "rendering are exclusive to that quadrant. Never copy, blend, bleed or swap any motif or colour "
    "across quadrants or across the divider. If two quadrants read as the same design, the result is "
    "FAILED."
)

_SAME_CAMERA_LOCK = (
    "SAME CAMERA, DIFFERENT ARTWORK — all four quadrants share one camera setup, one lens, one framing "
    "and one lighting direction (this round's direction). The four quadrants must differ by ARTWORK, "
    "never by camera angle. Four different elevations, crops or scene styles in one sheet = FAILED."
)

_ART_DIRECTION_LOCK = (
    "ART DIRECTION QUALITY (always on top of the composition direction above):\n"
    "- ONE clearly directional key light with a readable direction, defined highlights, a soft contact "
    "shadow under the product and natural falloff; flat, shadowless, evenly lit studio light is a "
    "FAILURE.\n"
    "- At least one visible warm light source or a clearly readable warm/cool contrast in frame.\n"
    "- Shallow depth of field with a soft background, plus a blurred foreground element when the "
    "composition allows it.\n"
    "- The product stays the hero: fully visible, sharply in focus, never occluded by props.\n"
    "- PRINTED MOTIFS ARE 2D ARTWORK: never turn a printed motif into a 3D prop or object."
)


@dataclass(frozen=True)
class ColumnStyleRecipe:
    """组内某一款的确定性配方（元素层级 / 配色角色）。"""

    style_index: int
    hero: str
    narrative: tuple[str, ...]
    rhythm: tuple[str, ...]
    palette: str
    color_harmony: str

    @property
    def quadrant(self) -> str:
        return _QUADRANT_POSITIONS[(self.style_index - 1) % 4]

    def signature(self) -> str:
        return (
            f"STYLE-{self.style_index:03d} | elements=hero:{self.hero or '-'} "
            f"narrative:{','.join(self.narrative) or '-'} rhythm:{','.join(self.rhythm) or '-'}"
            + (f" | palette={self.palette}" if self.palette else "")
            + (f" | color_harmony={self.color_harmony}" if self.color_harmony else "")
        )


def build_column_style_recipes(
    fields: BusinessFields,
    styles: Sequence[Mapping[str, object]],
    *,
    seed: str,
) -> tuple[ColumnStyleRecipe, ...]:
    """按款序生成 4 个配方；``styles`` 每项形如 ``{"style_index": n, "elements": {...}}``。"""
    color_preferences = _brief_color_preferences(fields.model_dump())
    recipes: list[ColumnStyleRecipe] = []
    for entry in styles:
        raw_style_index = entry.get("style_index")
        style_index = int(raw_style_index) if isinstance(raw_style_index, (int, str)) else 0
        if style_index < 1:
            raise ValueError("column style recipe requires a positive style_index")
        raw_elements = entry.get("elements")
        hero, narrative, rhythm = _coerce_style_elements(
            raw_elements if isinstance(raw_elements, Mapping) else None
        )
        # 配色只来自用户/智能填写给出的色板；没给就交给模型按主题自行选择。
        palette = (
            f"the brief's specified colors ({', '.join(color_preferences)})"
            if color_preferences
            else ""
        )
        colors = assign_style_colors(color_preferences, style_index, seed)
        color_harmony = ""
        if colors["base"]:
            color_harmony = (
                f"base≈{colors['base']}(60%) / structural≈{colors['structural']}(30%) / "
                f"highlight≈{colors['highlight']}(10%)"
            )
        recipes.append(
            ColumnStyleRecipe(
                style_index=style_index,
                hero=hero,
                narrative=tuple(narrative),
                rhythm=tuple(rhythm),
                palette=palette,
                color_harmony=color_harmony,
            )
        )
    return tuple(recipes)


def build_column_base_prompt(
    fields: BusinessFields,
    subject_anchor: str,
    *,
    structure_facts: Mapping[str, object] | None = None,
    reference_view_roles: Sequence[str] | None = None,
) -> str:
    """L1 冻结的共享底座：产品事实 + 结构锁 + 全局硬约束（不含任何一款配方）。

    该文本写进 ``pod_customization_batches.prompt_snapshot`` 作为批次级审计快照；
    真正提交给 provider 的每轮提示词由 :func:`build_column_round_prompt` 拼装，
    并冻结在 ``group_sheets.prompt_snapshot``。
    """
    parts = [
        "=== PART 1 · CORE CONTRACT (highest priority) — 2x2 four-panel format, batch-wide style, "
        "global hard rules ===",
        "Create one square 2x2 ecommerce contact sheet with exactly four equal panels.",
        "The four panels are FOUR DIFFERENT STYLES of one collection printed on the SAME physical product.",
        f"Product name: {fields.product_name or 'POD product'}.",
    ]
    for label, value in (
        ("Product category", fields.product_category),
        ("Target market", fields.target_market),
        ("Target audience", fields.target_audience),
        ("Core selling points", ", ".join(fields.core_selling_points)),
        ("Batch-wide theme & style", fields.design_theme),
        ("Color preferences", ", ".join(fields.color_preferences)),
        ("Excluded elements", ", ".join(IP_EXCLUDED_ELEMENTS)),
    ):
        if value:
            parts.append(f"{label}: {value}.")
    parts.extend(("", _BUILTIN_COLUMN_RULES))
    anchor = str(subject_anchor or "").strip()
    if anchor:
        parts.extend((
            "主体硬约束（用户填写，必须逐条满足；主体结构、材质、尺寸出现任何偏离即视为失败）：",
            anchor,
            "以上主体硬约束与上述硬性规则同级，优先级高于模板的视觉比例。",
        ))
    parts.extend((
        "",
        "Treat the supplied template only as a STRUCTURAL reference for this product: geometry, "
        "proportions, construction, material, printable surface location, and its true surface relief "
        "(quilting/padding grid if present, seams, topstitching, hardware).",
        "GEOMETRY LOCK: the product's silhouette, seam lines, panel divisions, quilting grid (if "
        "present), hardware positions and strap/handle drop must match the template exactly. Do not "
        "stretch, squash, melt, bend, reshape or re-proportion any part, and do not add, merge or "
        "delete structural parts.",
        *_structure_anchor_blocks(structure_facts, reference_view_roles),
        "Do not copy the template's existing artwork, decoration, colour scheme, background, lighting "
        "or scene.",
        "CARVE-OUT: the template's quilting/padding relief, seams and stitching are STRUCTURE, not "
        "decoration — preserve them exactly as shown; do not flatten the quilted surface into a smooth "
        "shell. Only the artwork, the colour scheme and the scene are to be replaced.",
        "Keep the same exact product in every panel and every round: identical structure, material, "
        "proportions, base color and print surfaces.",
        "ALL-OVER PRINT LOCK: each style's artwork is a full-surface all-over print — it repeats "
        "seamlessly edge to edge across every printable surface (front, sides, base, handles and "
        "bindings), with no blank ground areas and never a single isolated motif floating on a plain "
        "shell.",
        _QUADRANT_ISOLATION_LOCK,
        _NO_QUADRANT_LABELS,
        "Do not invent another product, extra accessories attached to the product, text, captions, "
        "logos, labels, watermarks, collages or borders; keep the four-panel divider clean and "
        "centered. (Standalone scene props beside the product ARE required — see the art direction.)",
        "If the product has an interior or lining, keep the interior surface unprinted (one plain "
        "solid color, black by default) and never extend the outer artwork inside.",
        _PRODUCT_FORM_LOCK,
        _FLAT_PRINT_LOCK,
        _COLOR_IMPACT_BLOCK,
        _MATERIAL_REALISM_BLOCK,
        *_fabric_craft_lines(fields.fabric_craft),
    ))
    return "\n".join(parts)


def _style_block(recipe: ColumnStyleRecipe) -> str:
    lines = [f"QUADRANT {recipe.quadrant} — style {recipe.style_index} (this quadrant only):"]
    shares = element_area_shares(recipe.hero, recipe.narrative, recipe.rhythm)
    if recipe.hero:
        lines.append(
            f"- 本款印花主元素：{recipe.hero}（Tier 1 主锚，印在产品表面的平面图案，不是独立实物；尺度 1.0）"
        )
        if recipe.narrative:
            lines.append(
                f"- 印花叙事层 Narrative：{', '.join(recipe.narrative)}（Tier 2，尺度 0.55，与主元素交替出现）"
            )
        if recipe.rhythm:
            lines.append(
                f"- 印花点缀层 Rhythm：{', '.join(recipe.rhythm)}（Tier 3，尺度 0.28，连续铺满主元素之间的空隙）"
            )
        else:
            lines.append(
                "- 印花点缀层 Rhythm（自选）：请从同主题中自选 2-3 个小纹样/纹理"
                "（如雪花、星星、铃铛、格纹）作为点缀填充，合计约占可印表面 16%，不得抢主元素尺寸。"
            )
    else:
        lines.append(
            "- 本款未分配印花元素：必须在 Batch-wide theme & style 主题内自创一个新图案元素作为本款印花主元素。"
        )
    if recipe.palette:
        lines.append(f"- palette = {recipe.palette}。")
    lines.append(f"- {render_element_weight_contract(shares)}")
    lines.extend((
        "- 满印密度（本款硬要求）：可印表面覆盖率 ≥ 90%，不允许出现比一个主元素还大的连续空白底色；"
        "三层必须按上述面积份额同时铺满整个可印表面，只在局部放几个图案视为失败。",
        "- 排布方式（重复/错位/拼贴/条纹等）可自由选择，但不得降低上一条的密度；"
        "不得因此改变相机、景别或四格角色。",
        "- 本款配方只作用于本象限：其他象限的元素、配色与尺度在本象限禁止出现。",
        f"Recipe signature: {recipe.signature()} | "
        f"weight={shares['hero']}/{shares['narrative']}/{shares['rhythm']}",
    ))
    if recipe.color_harmony:
        lines.append(
            f"- 60-30-10 配色：{recipe.color_harmony}；主基调覆盖大面积底色，结构副色用于提手/包边，"
            "点缀色只用于金线/金属件/小坠饰。"
        )
    return "\n".join(lines)


def _composition_slot(
    composition: Mapping[str, Mapping[str, str]] | None,
    role: str,
) -> str:
    key = ROLE_PANEL_KEY[role]
    entry = (composition or {}).get(key) or {}
    if isinstance(entry, Mapping):
        return str(entry.get("en") or "").strip()
    return ""


def _default_round_direction(role: str, *, flat_product: bool) -> str:
    if flat_product:
        return _FLAT_LISTING_PANEL_LINES[0] if role == PRIMARY_ROLE else _FLAT_LISTING_PANEL_LINES[3]
    if role == PRIMARY_ROLE:
        return (
            "Natural eye-level 3/4 hero angle: the complete product with its full silhouette in frame, "
            "centred with generous negative space, shot wide rather than cropped."
        )
    if role == "hero":
        return (
            "Neutral ecommerce angle: the complete product centred on a clean neutral background with "
            "no props, straight-on framing, full design sharply visible."
        )
    if role == "detail_b":
        return (
            "A different close structural/material detail of the same product, from a three-quarter "
            "angle, keeping the same light direction as the anchor."
        )
    return (
        "A tight close-up of the printed artwork on the same product, revealing print texture and "
        "material detail."
    )


def _b1_block(
    *,
    composition: Mapping[str, Mapping[str, str]] | None,
    flat_product: bool,
) -> list[str]:
    direction = _composition_slot(composition, PRIMARY_ROLE) or _default_round_direction(
        PRIMARY_ROLE, flat_product=flat_product
    )
    lines = [
        "=== PART 2 · SCENE & COMPOSITION — this round's single shared camera ===",
        "ROUND-1 SHOOTING DIRECTION (applies to all four quadrants of this sheet; read it literally, "
        "do not average it into a compromise framing):",
        direction,
        _SAME_CAMERA_LOCK,
    ]
    if flat_product:
        lines.extend((
            "FLAT PRODUCT COMPOSITION OVERRIDE: this is a flat sheet product. Ignore any eye-level, "
            "three-quarter or lifestyle camera direction; all four quadrants are TOP-DOWN flat lays, "
            "with no overhang, drape, table skirt, hanging corner or thickness.",
            _FLAT_LISTING_PANEL_LINES[0],
        ))
    return lines


def _column_set_design_block(
    *,
    palette: str,
    scene: str,
    props: Sequence[str] = (),
    lighting: str = "",
) -> str:
    """第 1 轮的 D1（无批级美术指导时的回退）：一轮四格共用一套布景与光位。"""
    prop_line = (
        f"- SUPPORTING PROPS (real objects only): {', '.join(props)}."
        if props
        else ""
    )
    light_line = f"- LIGHTING (art direction): {lighting}." if lighting else ""
    return "\n".join(
        line
        for line in (
            "SET DESIGN — the lifestyle scene must look rich, warm and inviting; a plain studio wall or an "
            "empty outdoor field is a FAILURE:",
            "- Derive the scene's mood from the batch-wide theme. The printed motifs are ARTWORK on the "
            "product surface, NOT physical props — never turn them into 3D objects.",
            f"- Stage this ONE lived-in scene, shared by all four quadrants of this round: {scene}.",
            prop_line,
            light_line,
            "- FILL THE FRAME — dress the set with 5-8 supporting props from this theme's real usage world, "
            "in clear near/mid/far layers, each with its own material and colour; at least two different "
            "materials; never generic filler, never a duplicate of the product, never a 3D copy of a "
            f"printed motif. Colours from the batch palette ({palette}) so they harmonize with all four "
            "designs.",
            "- LIGHTING — one clear warm directional key light that puts defined bright highlights on the "
            "product, a soft contact shadow beneath it and natural falloff into the background; optionally "
            "add a warm rim/backlight. Add at least one visible warm light source in frame (warmly lit "
            "window, lamp, candle or fairy lights). Flat, grey, evenly lit studio light is a FAILURE.",
            "- Shallow depth of field, soft background bokeh, one optional blurred foreground prop; tactile "
            "materials (wood, linen, lace, marble, woven mat, checkered cloth). The product stays the hero.",
            "- All four quadrants share ONE set and ONE lighting setup. Never build four different sets in "
            "one sheet, and never drop the detail quadrants to a plain grey studio backdrop.",
        )
        if line
    )


def _art_direction_block(
    art_direction: ArtDirection,
    *,
    palette: str,
    scene_option: SceneOption | None = None,
) -> str:
    lines = ["ART DIRECTION — batch-wide set design, shared by every quadrant of this round:"]
    scene = scene_option.scene if scene_option is not None else art_direction.scene
    props = tuple(scene_option.props) if scene_option is not None else tuple(art_direction.props)
    for label, value in (
        ("SCENE", scene),
        ("LIGHTING", art_direction.lighting),
        ("COLOR TEMPERATURE", art_direction.color_temperature),
        ("MOOD", art_direction.mood),
        ("PALETTE", ", ".join(art_direction.palette) or palette),
        ("BACKGROUND", art_direction.background),
        ("COMPOSITION", art_direction.composition_hint),
    ):
        if value:
            lines.append(f"- {label}: {value}")
    if props:
        lines.append("- PROPS (near / mid / far layers, at least two materials, never occlude the product):")
        lines.extend(f"  · {prop}" for prop in props)
    if art_direction.avoid:
        lines.append(f"- AVOID: {', '.join(art_direction.avoid)}")
    lines.append(
        "- All four quadrants share this ONE set, this ONE lighting setup and this ONE colour "
        "temperature. Never build four different sets in one sheet."
    )
    return "\n".join(lines)


def _d1_block(
    *,
    art_direction: ArtDirection | None,
    palette: str,
    scene: str,
    flat_product: bool,
    surface: str,
    scene_option: SceneOption | None = None,
) -> str:
    if art_direction is not None and not art_direction.is_empty():
        body = _art_direction_block(
            art_direction, palette=palette, scene_option=scene_option
        )
        if flat_product:
            body += (
                "\nFLAT-LAY OVERRIDE: the product lies flat on a flat photography surface, shot from "
                "directly above. No room, table edge, chairs, furniture or hanging cloth; keep the "
                f"full square silhouette visible. Surface: {surface}."
            )
        return "\n".join((body, _ART_DIRECTION_LOCK))
    props = tuple(scene_option.props) if scene_option is not None else ()
    if flat_product:
        return "\n".join((
            _flat_set_design_block(palette, surface, props=props),
            _ART_DIRECTION_LOCK,
        ))
    return "\n".join((
        _column_set_design_block(palette=palette, scene=scene, props=props),
        _ART_DIRECTION_LOCK,
    ))


# 派生轮的机位以冻结的构图模板为唯一来源：这里不写死任何景别，
# 只负责把「必须连续」与「必须变化」分开，并禁止复刻锚点机位。
# （_CAMERA_MUST_DIFFER 文本来自 prompt_anchor_blocks，与复刻派生面板共用。）


def _round_direction(
    role: str,
    *,
    composition: Mapping[str, Mapping[str, str]] | None,
    flat_product: bool,
) -> str:
    return _composition_slot(composition, role) or _default_round_direction(
        role, flat_product=flat_product
    )


def _b2_block(
    *,
    role: str,
    direction: str,
    flat_product: bool,
) -> list[str]:
    lines = [
        f"=== PART 2 · ROUND {ROLE_LABELS.get(role, role)} SCENE & COMPOSITION — camera only ===",
        "ROUND SHOOTING DIRECTION (all four quadrants of this sheet share it):",
        direction,
        "Framing-only clause: the sentence above changes only the camera — angle, shot distance, crop, "
        "framing and depth of field (a macro round is expected to have shallow focus). Scene, set "
        "surface, light direction, colour temperature and contrast must stay identical to the four "
        "reference images.",
        _CAMERA_MUST_DIFFER,
    ]
    if role == "hero":
        lines.append(
            "MATERIAL PANEL: unless the direction above specifies another background, use a clean "
            "neutral ecommerce background with no props; the full design must stay sharply visible."
        )
    if flat_product:
        lines.extend((
            "FLAT PRODUCT COMPOSITION OVERRIDE: this is a flat sheet product. All four quadrants are "
            "TOP-DOWN flat lays; never a corner, rim, side profile or three-quarter view.",
            "The product stays flat on the tabletop with no overhang, drape, thickness or 3D structure.",
        ))
    return lines


def build_column_round_prompt(
    base_prompt: str,
    *,
    role: str,
    recipes: Sequence[ColumnStyleRecipe],
    composition: Mapping[str, Mapping[str, str]] | None = None,
    art_direction: ArtDirection | None = None,
    group_index: int = 1,
    attempt: int = 1,
    flat_product: bool = False,
) -> str:
    """拼装某一轮实际提交的完整提示词。

    ``recipes`` 必须是该组的 4 个配方（顺序 = 象限顺序）；``composition`` 为
    ``panel_1..4 → {zh, en}`` 的冻结快照。
    """
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")
    if len(recipes) != 4:
        raise ValueError("column round prompt requires exactly four style recipes")
    if role not in (PRIMARY_ROLE, *DERIVED_ROLES):
        raise ValueError(f"unknown column role: {role}")

    if role == PRIMARY_ROLE:
        parts: list[str] = [base_prompt.rstrip(), ""]
        parts.extend(
            _b1_block(
                composition=composition,
                flat_product=flat_product,
            )
        )
        parts.extend(("", "=== PART 3 · ELEMENT ROTATION — one exclusive recipe per quadrant ==="))
        for recipe in recipes:
            parts.append(_style_block(recipe))
            parts.append("")
        anchor_recipe = recipes[0]
        # 场景来自豆包返回的 2-3 个候选，按组轮换；没有时用中性兜底（不是池）。
        scene_option = pick_scene_option(art_direction, group_index)
        surface = (
            scene_option.surface
            if scene_option is not None and scene_option.surface
            else _FALLBACK_SURFACE
        )
        scene = scene_option.scene if scene_option is not None else _FALLBACK_SCENE
        parts.extend((
            "=== PART 4 · ART DIRECTION — one shared set for all four quadrants ===",
            _d1_block(
                art_direction=art_direction,
                palette=anchor_recipe.palette or "the artwork's own palette",
                scene=scene,
                flat_product=flat_product,
                surface=surface,
                scene_option=scene_option,
            ),
        ))
        parts.append(
            "FINAL CHECK — four equal panels, one clean divider, four DIFFERENT designs from one "
            "collection, one shared camera and one shared set, no text or labels."
        )
    else:
        direction = _round_direction(
            role, composition=composition, flat_product=flat_product
        )
        lines: list[str] = [
            f"=== ROUND {ROLE_LABELS.get(role, role)} · A2 — hard output format + subject anchoring ===",
            "This call produces the SAME four styles of the same contact sheet, seen from a clearly "
            "DIFFERENT camera than the reference images. The four attached images are the accepted "
            "round-1 result and the ONLY source of truth for product structure and printed artwork.",
            _OUTPUT_FORMAT_LOCK,
            _ANCHOR_BINDING,
            _RIGID_ARTWORK_LOCK,
            _ANCHOR_CONTINUITY,
            _QUADRANT_ISOLATION_LOCK,
            _NO_QUADRANT_LABELS,
            "GEOMETRY LOCK: keep the silhouette, seam lines, panel divisions, hardware positions and "
            "proportions of the reference exactly; never redraw, reshape or re-proportion the product.",
            _PRODUCT_FORM_LOCK,
            _FLAT_PRINT_LOCK,
            "Do not add text, captions, logos, labels, watermarks, collages or borders, and never copy "
            "one quadrant's artwork into another quadrant.",
            "",
        ]
        lines.extend(_b2_block(role=role, direction=direction, flat_product=flat_product))
        # 方向句在前文容易被锚点锁稀释，末尾再钉一次（模型对结尾指令同样敏感）。
        lines.extend((
            "",
            f"CAMERA REMINDER (last check) — shoot this round as: {direction} "
            "The camera must be visibly different from the anchor's camera; copying the anchor's "
            "framing (same distance, same full-product view) is a FAILED result.",
        ))
        parts = lines

    if attempt == 2:
        parts.extend(("", "RETRY ATTEMPT 2 OF 2:"))
        if role == PRIMARY_ROLE:
            parts.append(
                "The first result was invalid: it was not a usable four-panel sheet or a panel was "
                "blank. Return a complete 2x2 sheet with four valid panels, keeping every quadrant's "
                "assigned recipe exactly as specified."
            )
        else:
            parts.append(
                "The first result was invalid: it was not a usable four-panel sheet or a panel was "
                "blank. Return four valid panels, each still anchored to its own reference image; do "
                "not redesign the artwork."
            )
    return "\n".join(parts)


__all__ = [
    "COLUMN_PROMPT_VERSION",
    "ColumnStyleRecipe",
    "build_column_base_prompt",
    "build_column_round_prompt",
    "build_column_style_recipes",
]
