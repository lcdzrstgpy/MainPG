"""POD 精小品（precision）通道：分级、画布配额、切格预算门与专属提示词。

与通用列式模板的差别（设计稿 docs/superpowers/specs/2026-11-05-pod-precision-small-item-design.md）：

- 删除场景、道具、浅景深、前景虚化、暖光斑等会伤害小件结构的指令；
- 锁定正交/正视、长焦压缩、深景深、均匀柔光；
- 要求单件占格 70%-85%，不压线、不跨区、不被裁切；
- ``precision_text`` 一律禁止生成文字，文字由本地后期叠加。

本模块只放纯逻辑与常量：不调用 provider、不写库、不读文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

PRECISION_PROMPT_VERSION = "precision-v1"

PRECISION_STANDARD = "standard"
PRECISION_DETAIL = "precision"
PRECISION_TEXT = "precision_text"
PRECISION_CLASSES: tuple[str, ...] = (
    PRECISION_STANDARD,
    PRECISION_DETAIL,
    PRECISION_TEXT,
)

# 画布候选必须落在豆包单次出图区间 [921_600, 4_624_220] 且宽高比 [1/16, 16]。
SQUARE_CANVAS = "2048x2048"
WIDE_CANVAS = "2880x1440"  # 4,147,200px；单格 1440×720
TALL_CANVAS = "1440x2880"  # 单格 720×1440

# 精小品拆格后的最小像素预算：2048² 母图单格约 1.05MP，1254² 母图只有 0.39MP。
# 低于该值说明模型没给足分辨率，直接判本次母图不合格（走既有的一次重试）。
PRECISION_MIN_CELL_PIXELS = 900_000

# 零件配额阈值：单格内独立零件/重复单元过多时，模型的语义预算先于像素预算耗尽。
PRECISION_WARN_DETAIL_UNITS = 24
PRECISION_MAX_DETAIL_UNITS = 60
# 单个零件至少要有这么多像素，形状才不容易粘连/缺角（约 50×50）。
PRECISION_MIN_PIXELS_PER_UNIT = 2_500
PRECISION_WARN_PIXELS_PER_UNIT = 6_000

# 宽物 / 竖物判定只用于选画布，不参与是否走精小品通道。
_WIDE_CATEGORY_TOKENS = (
    "键盘",
    "文字稿",
    "海报",
    "横幅",
    "长条",
    "书签",
    "卡牌",
    "明信片",
    "尺子",
)
_TALL_CATEGORY_TOKENS = ("挂轴", "立牌", "门帘", "竖版", "卷轴")

# 含文字/字母/数字的品类：一律禁止 AI 生成文字。
_PRECISION_TEXT_TOKENS = (
    "麻将",
    "键盘",
    "键帽",
    "文字",
    "字母",
    "数字",
    "字帖",
    "卡牌",
    "贴纸",
    "明信片",
    "扑克",
    "标语",
    "slogan",
)

# 小尺寸或结构精密，但图案可以交给模型重绘。
_PRECISION_DETAIL_TOKENS = (
    "拼图",
    "积木",
    "模型",
    "手办",
    "徽章",
    "冰箱贴",
    "钥匙扣",
    "耳钉",
    "项链",
    "戒指",
    "手表",
    "微缩",
)

_PRECISION_OVERRIDE = (
    "PRECISION OVERRIDE (highest priority) — this product is small or detail-critical. Every "
    "scene, prop, lifestyle styling, shallow-depth-of-field, foreground-blur, warm-spot-light and "
    "background-decoration instruction in the inherited text is VOID. Only the precision locks in "
    "this prompt are binding; if two rules conflict, the precision lock wins."
)

_PRECISION_OUTPUT_CONTRACT_BASE = (
    "Create one 2x2 sheet with exactly four equal panels and one clean divider.\n"
    "Treat each panel as an independent product photo: exactly one complete product per panel, "
    "centred and enlarged to 70%-85% of the panel, with even empty margins of at least 10% of the "
    "panel edge on all four sides.\n"
    "Give all four panels EQUAL visual weight: never concentrate detail, contrast or attention at the "
    "centre of the sheet or at the divider crossing.\n"
    "The product must never touch, cross, overlap or be cropped by the divider or the panel edge, and "
    "no motif, shadow or colour may bleed into the divider band or a neighbouring panel.\n"
)

_PRECISION_DIFFERENT_DESIGNS_TAIL = (
    "The four panels are four different designs of one collection on the same physical product; "
    "never copy, blend or swap motifs, colours or camera framing across panels."
)

_PRECISION_SAME_DESIGN_TAIL = (
    "The four panels are four different VIEWS of the SAME product and the SAME printed design; never "
    "redesign, recolour, re-layout or rescale the artwork between panels."
)

_PRECISION_CAMERA_FRONT = (
    "CAMERA LOCK — precision product photography:\n"
    "- Orthographic front view; never a three-quarter, corner or wide-angle perspective.\n"
    "- 100mm-equivalent telephoto compression: near-parallel projection, minimal foreshortening.\n"
    "- Deep depth of field: the entire product is in focus from front to back.\n"
    "- Even, soft, neutral studio light from the front; no warm spot light, no lens flare, no "
    "bokeh, no foreground blur, no hard cast shadow.\n"
    "- No perspective distortion: parallel edges stay parallel, right angles stay 90 degrees, "
    "circles stay circular, repeated parts keep equal size and equal spacing.\n"
    "- The product fills 70%-85% of its panel; a small object floating in a large frame is a "
    "FAILED result."
)

_PRECISION_CAMERA_FLAT = (
    "CAMERA LOCK — precision flat-lay photography:\n"
    "- Straight top-down flat lay, 90 degrees to the surface; never a corner, rim or side profile.\n"
    "- 100mm-equivalent telephoto compression: near-parallel projection, minimal foreshortening.\n"
    "- Deep depth of field: the entire product is in focus edge to edge.\n"
    "- Even, soft, neutral studio light from directly above; no warm spot light, no lens flare, no "
    "bokeh, no foreground blur, no hard cast shadow.\n"
    "- No perspective distortion: parallel edges stay parallel, right angles stay 90 degrees, "
    "circles stay circular, repeated parts keep equal size and equal spacing.\n"
    "- The product fills 70%-85% of its panel; a small object floating in a large frame is a "
    "FAILED result."
)

_PRECISION_LENS_LOCK_CORE = (
    "PRECISION LENS LOCK — keep the anchor's scene and light exactly, change only the camera:\n"
    "- Orthographic or 100mm-equivalent telephoto compression; never wide-angle or corner "
    "perspective.\n"
    "- Deep depth of field: everything visible in the frame is in focus; no macro shallow focus, no "
    "bokeh, no foreground blur.\n"
    "- No perspective distortion: parallel edges stay parallel, right angles stay 90 degrees, "
    "circles stay circular, repeated parts keep equal size and equal spacing."
)

_PRECISION_PRODUCT_FILL_LOCK = (
    "- The complete product fills 70%-85% of its panel; a small object floating in a large frame is "
    "a FAILED result."
)

_PRECISION_STRUCTURE_LOCK = (
    "STRUCTURE AND SURFACE LOCK:\n"
    "- Keep the exact part count, part proportions and connection points; never merge, fuse, "
    "duplicate, omit or invent a part.\n"
    "- Regular grids stay regular: rows and columns evenly spaced, identical parts identical in "
    "size and shape.\n"
    "- Printed motifs stay flat 2D artwork; never turn a printed motif into a 3D object.\n"
    "- Material, base colour and surface finish stay identical to the reference."
)

_PRECISION_BACKGROUND_LOCK = (
    "BACKGROUND LOCK:\n"
    "- One uniform solid background colour for the whole sheet: no gradient, no texture, no room, "
    "no table, no props, no shadow, no vignette.\n"
    "- The divider band and the 10% margin around each panel stay completely empty and neutral: no "
    "object, motif, shadow, reflection or colour spill.\n"
    "- The background is identical in all four panels; only the divider separates them, and no colour "
    "from one panel is absorbed by or transferred into another panel."
)

_PRECISION_TEXT_POLICY = (
    "TEXT POLICY — text exists only where the reference already has it:\n"
    "- The inherited no-text rules apply ONLY to added captions, labels, logos, watermarks and "
    "standalone lettering; text that belongs to the product itself is allowed and must be preserved.\n"
    "- Text, letters, numbers or legends that belong to the product's own design (keycap legends, tile "
    "faces, printed lettering) must be reproduced exactly as in the reference: same characters, same "
    "style, same position, same size.\n"
    "- Do not rewrite, translate, restyle, add, remove or invent any text, label, logo, watermark or "
    "pseudo-text.\n"
    "- If a glyph cannot be rendered faithfully, keep its position clean rather than inventing a "
    "wrong character."
)

_PRECISION_FINAL_CHECK = (
    "FINAL CHECK — four equal panels; one complete product per panel at 70%-85% fill; no crossing, "
    "no cropping; orthographic, deep-focus, no perspective distortion; uniform background; exact "
    "part count and even grid spacing; no invented text."
)

_PRECISION_RETRY = (
    "RETRY ATTEMPT 2 OF 2: the first result was invalid — a panel was unbalanced, the product "
    "crossed the divider, parts were merged or distorted, or the scene/lighting instructions were "
    "followed instead of these precision locks. Return a complete 2x2 sheet that satisfies every "
    "precision lock above."
)


def normalize_precision_class(value: Any) -> str:
    """把外部输入归一成三档之一；未知/空值按 standard 处理，绝不隐式升级。"""
    token = str(value or "").strip().lower()
    if token in {PRECISION_DETAIL, "fine_detail", "fine"}:
        return PRECISION_DETAIL
    if token in {PRECISION_TEXT, "text", "precision-lettering"}:
        return PRECISION_TEXT
    return PRECISION_STANDARD


def classify_precision(product_category: str, *, artwork_text: str = "") -> str:
    """按品类/图案文字做确定性兜底分类；显式字段永远优先于本函数。"""
    category = str(product_category or "").strip().lower()
    artwork = str(artwork_text or "").strip().lower()
    haystack = f"{category} {artwork}"
    if any(token in haystack for token in _PRECISION_TEXT_TOKENS):
        return PRECISION_TEXT
    if any(token in category for token in _PRECISION_DETAIL_TOKENS):
        return PRECISION_DETAIL
    return PRECISION_STANDARD


def resolve_precision_class(batch: Mapping[str, Any]) -> str:
    """从批次解析精小品档位：显式字段 > generation_meta > 品类关键词兜底。"""
    explicit = batch.get("precision_class")
    if explicit:
        return normalize_precision_class(explicit)
    meta = batch.get("generation_meta")
    if isinstance(meta, Mapping) and meta.get("precision_class"):
        return normalize_precision_class(meta.get("precision_class"))
    fields = batch.get("business_fields")
    category = ""
    if isinstance(fields, Mapping):
        category = str(fields.get("product_category") or "")
    if not category:
        context = batch.get("product_context")
        if isinstance(context, Mapping):
            nested = context.get("business_fields")
            if isinstance(nested, Mapping):
                category = str(nested.get("product_category") or "")
    return classify_precision(category)


def camera_lock_block(*, flat_product: bool = False) -> str:
    return _PRECISION_CAMERA_FLAT if flat_product else _PRECISION_CAMERA_FRONT


def lens_lock_block(*, require_product_fill: bool = True) -> str:
    """派生轮用：只锁镜头/透视/景深，不改锚点已定稿的场景与光线。

    ``require_product_fill=False`` 用于细节微距轮：那一格本来就该裁进产品，
    再要求"完整产品占 70%-85%"会和机位指令打架。
    """
    if require_product_fill:
        return f"{_PRECISION_LENS_LOCK_CORE}\n{_PRECISION_PRODUCT_FILL_LOCK}"
    return _PRECISION_LENS_LOCK_CORE


def canvas_size_for_precision(
    precision_class: str,
    *,
    product_category: str = "",
    shape: str = "",
) -> str:
    """精小品画布配额；standard 返回空串表示沿用既有方形默认值。

    ``shape`` 来自主体识别的 ``dominant_shape``，优先于品类关键词：
    键盘是宽物，但一个名叫「键盘清洁刷」的商品不该被当成宽物。
    """
    precision = normalize_precision_class(precision_class)
    if precision == PRECISION_STANDARD:
        return ""
    token = str(shape or "").strip().lower()
    if token == "wide":
        return WIDE_CANVAS
    if token == "tall":
        return TALL_CANVAS
    if token == "square":
        return SQUARE_CANVAS
    category = str(product_category or "").strip().lower()
    if any(token in category for token in _WIDE_CATEGORY_TOKENS):
        return WIDE_CANVAS
    if any(token in category for token in _TALL_CATEGORY_TOKENS):
        return TALL_CANVAS
    return SQUARE_CANVAS


def precision_class_for_subject(
    profile: Mapping[str, Any] | None,
    *,
    product_category: str = "",
    title: str = "",
) -> str:
    """主体识别的 ``precision_profile`` → 精小品档位。

    只要识别结果里带了布尔信号，就以识别为准（模型明确说"普通品"就按普通品走）；
    旧回执/降级回执没有这些字段时，才用品类关键词兜底。
    """
    payload = profile if isinstance(profile, Mapping) else {}
    has_signal = any(
        isinstance(payload.get(key), bool)
        for key in ("is_small_or_intricate", "has_legible_text", "is_flat")
    )
    if has_signal:
        if bool(payload.get("has_legible_text")):
            return PRECISION_TEXT
        if bool(payload.get("is_small_or_intricate")):
            return PRECISION_DETAIL
        return PRECISION_STANDARD
    return classify_precision(product_category, artwork_text=title)


def build_precision_product_facts(
    *,
    title: str,
    category: str,
    vision_identity: Mapping[str, Any] | None,
) -> list[str]:
    """AI 处理链路的精小品事实块：只保留身份/保真锁，不注入场景与道具。"""
    identity = vision_identity if isinstance(vision_identity, Mapping) else {}
    subject = str(identity.get("sellable_subject") or title or "the product").strip()
    attributes = [
        str(item).strip()
        for item in (identity.get("visible_attributes") or ())
        if str(item).strip()
    ][:8]
    facts = [
        "PRODUCT FACTS — the attached reference images are the ONLY source of truth.",
        f"Product: {subject}" + (f" ({category})." if str(category).strip() else "."),
        "PRODUCT FIDELITY LOCK — preserve silhouette, proportions, colour, material, transparency, "
        "structure, edges, part count and the printed artwork exactly; never add, remove, replace, "
        "redraw, recolour, resize or invent any part, accessory or decoration.",
    ]
    if attributes:
        facts.append("Visible attributes: " + ", ".join(attributes) + ".")
    return facts


def build_precision_panel_blocks(
    *,
    over_budget: bool = False,
    flat_product: bool = False,
) -> list[str]:
    """AI 处理链路的四格角色：P4 必须保留正投影，供下游尺寸标注使用。

    P2/P3 用来承接花纹/文字/零件细节；``over_budget`` 时把这两格推到更近的微距，
    而不是改动 P4 的用途（改动会让尺寸标注失去干净的底图）。
    """
    detail_scale = "90%-100%" if over_budget else "80%-100%"
    view_line = (
        "straight top-down 90-degree view"
        if flat_product
        else "true orthographic front or side view"
    )
    return [
        "PANEL 1 · TOP-LEFT — complete product, straight-on orthographic hero, 70%-85% fill, "
        "uniform background, no prop.",
        f"PANEL 2 · TOP-RIGHT — macro detail of the most detail-critical area (pattern, lettering or "
        f"part grid), filling {detail_scale} of the panel, deep focus.",
        f"PANEL 3 · BOTTOM-LEFT — second macro detail (material, edge, seam or part connection), "
        f"filling {detail_scale} of the panel, deep focus.",
        f"PANEL 4 · BOTTOM-RIGHT — {view_line} of the complete product with 12%-18% clear space; "
        "keep it clean and free of annotations.",
    ]


def min_cell_pixels_for(precision_class: str) -> int:
    """precision / precision_text 才启用单格像素门；standard 保持现状。"""
    precision = normalize_precision_class(precision_class)
    return 0 if precision == PRECISION_STANDARD else PRECISION_MIN_CELL_PIXELS


def inspect_grid_cell_budget(content: bytes) -> tuple[int, int]:
    """只读文件头返回 (单格宽, 单格高)；解码失败按 0 处理交给上层报错。"""
    try:
        import io

        from PIL import Image  # type: ignore

        with Image.open(io.BytesIO(content)) as probe:
            width, height = probe.size
    except Exception:
        return 0, 0
    return max(1, width // 2), max(1, height // 2)


def assert_grid_cell_budget(content: bytes, *, min_cell_pixels: int) -> tuple[int, int]:
    """精小品母图的单格像素门；不足时抛 ValueError，走既有的一次重试。"""
    if min_cell_pixels <= 0:
        return inspect_grid_cell_budget(content)
    cell_width, cell_height = inspect_grid_cell_budget(content)
    if cell_width <= 1 or cell_height <= 1:
        raise ValueError("precision grid image is not decodable")
    if cell_width * cell_height < min_cell_pixels:
        raise ValueError(
            "precision grid cell budget too low: "
            f"source cells are {cell_width}x{cell_height} "
            f"({cell_width * cell_height}px), need at least {min_cell_pixels}px per cell"
        )
    return cell_width, cell_height


@dataclass(frozen=True)
class PrecisionBudget:
    """提交前的精小品零件配额评估（纯规则，不调用模型）。"""

    level: str
    score: int
    detail_units: int
    pixels_per_unit: int
    reasons: tuple[str, ...]
    recommendation: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "score": self.score,
            "detail_units": self.detail_units,
            "pixels_per_unit": self.pixels_per_unit,
            "reasons": list(self.reasons),
            "recommendation": self.recommendation,
        }


def _canvas_cell_size(canvas_size: str) -> tuple[int, int]:
    raw = str(canvas_size or "").strip().lower()
    if "x" in raw:
        width_text, height_text = raw.split("x", 1)
        try:
            width, height = int(width_text), int(height_text)
        except ValueError:
            width = height = 0
        if width > 0 and height > 0:
            return max(1, width // 2), max(1, height // 2)
    return 1024, 1024


def assess_precision_budget(
    precision_class: str,
    *,
    part_count: int = 0,
    tiers: int = 0,
    units_per_tier: int = 0,
    canvas_size: str = "",
    has_text: bool = False,
    products_per_call: int = 1,
) -> PrecisionBudget:
    """按零件/重复单元数量与单格像素，给出 ok / warn / over_budget。

    ``detail_units`` 取"独立零件数"与"层数 × 每层单元数"的较大值：
    键盘这类产品 ``part_count`` 可能只有 1，但键帽总数来自 ``tiers × units_per_tier``。
    """
    precision = normalize_precision_class(precision_class)
    if precision == PRECISION_STANDARD:
        return PrecisionBudget("ok", 0, 0, 0, (), "standard")

    cell_width, cell_height = _canvas_cell_size(canvas_size)
    cell_pixels = cell_width * cell_height
    detail_units = max(
        int(part_count or 0),
        int(tiers or 0) * int(units_per_tier or 0),
        1,
    )
    pixels_per_unit = cell_pixels // detail_units

    score = 0
    reasons: list[str] = []
    if detail_units >= PRECISION_MAX_DETAIL_UNITS:
        score = max(score, 90)
        reasons.append(f"{detail_units} detail units exceed the per-panel limit")
    elif detail_units >= PRECISION_WARN_DETAIL_UNITS:
        score = max(score, 55)
        reasons.append(f"{detail_units} detail units are close to the per-panel limit")
    if pixels_per_unit < PRECISION_MIN_PIXELS_PER_UNIT:
        score = max(score, 90)
        reasons.append(f"only {pixels_per_unit}px per unit")
    elif pixels_per_unit < PRECISION_WARN_PIXELS_PER_UNIT:
        score = max(score, 55)
        reasons.append(f"{pixels_per_unit}px per unit is marginal")
    if has_text:
        score = max(score, 45)
        reasons.append("product text is rendered by the model and may distort")

    if score >= 80:
        level = "over_budget"
    elif score >= 40:
        level = "warn"
    else:
        level = "ok"

    if level == "over_budget":
        recommendation = (
            "same_pattern_recolor" if int(products_per_call) > 1 else "one_hero_three_details"
        )
    elif has_text:
        recommendation = "local_text_overlay"
    else:
        recommendation = "precision_template_only"

    return PrecisionBudget(
        level=level,
        score=score,
        detail_units=detail_units,
        pixels_per_unit=pixels_per_unit,
        reasons=tuple(reasons),
        recommendation=recommendation,
    )


def build_precision_single_prompt(
    *,
    facts: Sequence[str],
    panel_block: str,
    precision_class: str,
    flat_product: bool = False,
    detail_units: int = 0,
) -> str:
    """单格重绘用的精小品提示词：保留同样的机位/结构/文字锁，不回到场景模板。"""
    precision = normalize_precision_class(precision_class)
    if precision == PRECISION_STANDARD:
        raise ValueError("precision single prompt requires a precision class")
    parts: list[str] = [
        f"[PRECISION SINGLE {PRECISION_PROMPT_VERSION} | class={precision}]",
        _PRECISION_OVERRIDE,
        "OUTPUT: exactly ONE clean full-frame image of this panel only; never a grid, strip or collage.",
        "",
        "=== PRECISION PRODUCT FACTS ===",
        *[str(block).rstrip() for block in facts if str(block).strip()],
        "",
        "=== PRECISION CAMERA LOCK ===",
        camera_lock_block(flat_product=flat_product),
        "",
        "=== PRECISION STRUCTURE AND BACKGROUND ===",
        _PRECISION_STRUCTURE_LOCK,
        _PRECISION_BACKGROUND_LOCK,
        "",
        "=== THIS PANEL ===",
        str(panel_block).strip(),
    ]
    if detail_units > 0:
        parts.extend((
            "",
            f"DETAIL BUDGET — about {int(detail_units)} distinct repeated units; keep every unit "
            "separate with a clean gap, never merge, drop or duplicate a unit.",
        ))
    if precision == PRECISION_TEXT:
        parts.extend(("", "=== PRECISION TEXT POLICY ===", _PRECISION_TEXT_POLICY))
    return "\n".join(parts)


def build_precision_sheet_prompt(
    *,
    facts: Sequence[str],
    panel_blocks: Sequence[str],
    precision_class: str,
    attempt: int = 1,
    flat_product: bool = False,
    detail_units: int = 0,
    same_design: bool = False,
) -> str:
    """拼装精小品四格提示词；``facts`` 为调用方已渲染的产品事实与结构锁。

    ``detail_units > 0`` 时追加"DETAIL BUDGET LOCK"：把每格的重复单元数量写成
    显式约束，缓解键盘键帽、拼图块这类多单元产品被模型合并/漏画的问题。
    ``same_design=True`` 用于 AI 处理链路：四格是同一产品的四个视角，不是四款。
    """
    precision = normalize_precision_class(precision_class)
    if precision == PRECISION_STANDARD:
        raise ValueError("precision sheet prompt requires a precision class")
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")

    parts: list[str] = [
        f"[PRECISION SHEET {PRECISION_PROMPT_VERSION} | class={precision}]",
        _PRECISION_OVERRIDE,
        "",
        "=== PRECISION BLOCK A · OUTPUT CONTRACT — four independent panels ===",
        _PRECISION_OUTPUT_CONTRACT_BASE
        + (_PRECISION_SAME_DESIGN_TAIL if same_design else _PRECISION_DIFFERENT_DESIGNS_TAIL),
        "",
        "=== PRECISION BLOCK B · PRODUCT FACTS AND LOCKS (inherited, scene rules void) ===",
        *[str(block).rstrip() for block in facts if str(block).strip()],
        "",
        "=== PRECISION BLOCK C · CAMERA LOCK ===",
        camera_lock_block(flat_product=flat_product),
        "",
        "=== PRECISION BLOCK D · STRUCTURE AND BACKGROUND ===",
        _PRECISION_STRUCTURE_LOCK,
        _PRECISION_BACKGROUND_LOCK,
    ]
    if detail_units > 0:
        parts.extend((
            "",
            "=== PRECISION BLOCK D2 · DETAIL BUDGET LOCK ===",
            f"This product shows about {int(detail_units)} distinct repeated units in one panel. "
            "Keep every unit separate with a clean gap, at the same size and spacing: never merge, "
            "fuse, drop, duplicate or resize a unit to save space. If the count cannot be rendered "
            "faithfully, keep the grid regular and readable rather than inventing extra units.",
        ))
    parts.extend((
        "",
        "=== PRECISION BLOCK E · PER-PANEL CONTENT ===",
        *[str(block).rstrip() for block in panel_blocks if str(block).strip()],
    ))
    if precision == PRECISION_TEXT:
        parts.extend(("", "=== PRECISION BLOCK F · TEXT POLICY ===", _PRECISION_TEXT_POLICY))
    parts.extend(("", "=== PRECISION BLOCK G · FINAL CHECK ===", _PRECISION_FINAL_CHECK))
    if attempt == 2:
        parts.extend(("", _PRECISION_RETRY))
    return "\n".join(parts)


__all__ = [
    "PRECISION_CLASSES",
    "PRECISION_DETAIL",
    "PRECISION_MIN_CELL_PIXELS",
    "PRECISION_PROMPT_VERSION",
    "PRECISION_STANDARD",
    "PRECISION_TEXT",
    "SQUARE_CANVAS",
    "TALL_CANVAS",
    "WIDE_CANVAS",
    "PrecisionBudget",
    "assert_grid_cell_budget",
    "assess_precision_budget",
    "build_precision_panel_blocks",
    "build_precision_product_facts",
    "build_precision_sheet_prompt",
    "build_precision_single_prompt",
    "camera_lock_block",
    "canvas_size_for_precision",
    "classify_precision",
    "inspect_grid_cell_budget",
    "lens_lock_block",
    "min_cell_pixels_for",
    "normalize_precision_class",
    "precision_class_for_subject",
    "resolve_precision_class",
]
