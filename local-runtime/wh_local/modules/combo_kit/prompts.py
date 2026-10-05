"""combo_kit Prompt 组装：两套基础模板 + 每图独立辅助 Prompt + 主体信息注入。

完全复用老 AI 处理的 Prompt 底层逻辑（风格词/画质词/负面词），
但记录独立入库；本模块不调用 OCR 校验。
"""
from __future__ import annotations

from typing import Any

from .contracts import DEFAULT_GENERATION_MODE, IMAGE_ROLES, SCENE_ROLES

# 只保留一套内置基础通用提示词模板（对齐老 AI 处理风格）。不再提供模板 B。
BASE_PROMPT_A = (
    "professional e-commerce product photography, studio lighting, "
    "sharp focus, clean neutral background, accurate color and material, "
    "no human, no text overlay, no watermark"
)

# 单品多视角选型（multiview）专用基础模板：参考图是同一个商品的多张视角，
# 不能出现「套装/bundle」措辞，也不能把不同视角描述成不同的商品。
# 多张视角参考图极易让模型把各视角拼进同一张图（2×2 拼贴 / 九宫格 / 一图多只），
# 因此必须显式要求「一张图只画一个视角」。
# 轮廓/比例锁定：多张视角参考图若没有统一的尺度锚点，模型会各画各的比例：
# 同一件商品在不同成品图里被拉长压扁、弧度不一致。白底图/细节图/场景图共用同一句，
# 保证「白底图与场景图造型对不上」这类跨图漂移同时被约束。
MULTIVIEW_SHAPE_LOCK = (
    "Reproduce the exact proportions and silhouette of this product: keep its true "
    "length-to-height ratio, the curvature of every surface, and the position and size of "
    "every button, wheel and seam exactly as in the references. Never stretch, flatten, "
    "lengthen or otherwise distort the shape, and keep the product framed at the same "
    "apparent size and camera distance in every image"
)

MULTIVIEW_BASE_PROMPT = (
    "professional e-commerce product photography of ONE single product, "
    "studio lighting, sharp focus, clean neutral background, "
    "accurate color and material, no text overlay, no watermark. "
    "Render ONE single photographic frame showing ONE single view of this product: "
    "never build a collage, grid, multi-panel or multi-angle composition, "
    "and never repeat the same product more than once in the frame. "
    + MULTIVIEW_SHAPE_LOCK
)

# 使用场景图专用基础模板：场景图必须有真实生活环境与自然手部，
# 套用 BASE_PROMPT_A 的 no human / clean neutral background 会把场景元素直接压掉。
SCENE_BASE_PROMPT = (
    "professional lifestyle e-commerce photography, real home environment, "
    "soft natural window light, realistic adult hands allowed, sharp focus, "
    "accurate color and material, no watermark"
)

# 每张图默认辅助方向（可编辑角色向用户展示，其余为内部固定方向）。
DEFAULT_ROLE_DIRECTIONS: dict[str, str] = {
    "main": "",
    "carousel_2": "lifestyle scene: the complete bundled set placed on a real home surface "
                  "in a modern American living room or game-night table, minimal natural props "
                  "(glass, snack, small plant, linen) that never cover the products, "
                  "unified soft window light, every member product clearly visible",
    "carousel_3": "second lifestyle scene: the complete bundled set used in a different real "
                  "home setting (sunlit dining table or cozy reading corner), relaxed natural "
                  "arrangement, realistic adult hands allowed, consistent soft light, "
                  "every member product clearly recognizable",
    "white_bg": "complete bundled set on a clean white background, full product visible, "
                "balanced layout, professional studio shot",
    "detail_shot": "",
    "detail_page": "",
}

# 单品多视角选型的默认辅助方向：全部围绕「同一个商品」，视角图集已提供内部/展开信息，
# 因此不写「整套/每个成员」，只强调该商品本身的展示。
MULTIVIEW_ROLE_DIRECTIONS: dict[str, str] = {
    "main": "",
    "carousel_2": "lifestyle scene: this same product placed and used in a real home environment, "
                  "only one single copy of the product in the frame, "
                  "minimal natural props that never cover the product, soft window light, "
                  "realistic adult hands allowed, product unchanged in design, color and material",
    "carousel_3": "second lifestyle scene: the same product in a different real home setting and "
                  "usage moment, only one single copy of the product in the frame, "
                  "relaxed natural arrangement, realistic adult hands allowed, "
                  "consistent soft light, product unchanged in design, color and material",
    # 原措辞含 "interior / package structure clearly readable"，那是容器/包装类商品的描述；
    # 对鼠标等实心商品属错位指令，会诱导模型凭空补出内部结构或多视角拼贴。
    "white_bg": "this same product on a clean white background, one single view only, "
                "the whole product visible with a clear complete outline, "
                "balanced layout, professional studio shot, "
                "no duplicate copies of the product",
    "detail_shot": "",
    "detail_page": "",
}


def base_prompt_for_role(role: str, base_prompt: str, mode: str = DEFAULT_GENERATION_MODE) -> str:
    """按角色 + 生成选型选择基础模板。

    场景图固定用 SCENE_BASE_PROMPT（放开真实环境与手部）；
    单品多视角选型不做「多件商品」描述，非场景角色固定用 MULTIVIEW_BASE_PROMPT，
    场景角色追加轮廓锁定，其余沿用用户/内置基础模板 A。
    """
    if role in SCENE_ROLES:
        # 场景图沿用场景模板（放开真实环境与手部），但单品多视角仍要锁定轮廓，
        # 否则参考图归一化只统一了尺度，场景图里的商品仍会和白底图造型对不上。
        if mode == "multiview":
            return f"{SCENE_BASE_PROMPT}. {MULTIVIEW_SHAPE_LOCK}"
        return SCENE_BASE_PROMPT
    if mode == "multiview":
        return MULTIVIEW_BASE_PROMPT
    return str(base_prompt or "").strip() or BASE_PROMPT_A

# 融合套装主图模板：把多张来源图合并成一套套装主预览。
# 措辞与其它成功生图保持一致（studio/clean background/主体可见），避免触发
# 老生图 provider 对「fuse/blend 多主体」类指令的失败返回（status=3）。
FUSION_MAIN_PROMPT = (
    "professional e-commerce product photography, studio lighting, sharp focus, "
    "clean neutral background, accurate color and material, no human, no text overlay, "
    "no watermark. Show the entire bundled set together as one hero image, every member "
    "product clearly visible and well composed, unified lighting and scale."
)

# 细节图模板（复用老 AI 处理模块细节面板语义）：完整商品 + 真实细节特写，禁止纯 macro 裁剪。
DETAIL_SHOT_TEMPLATE = (
    "Show the complete bundled set at 55%-70% of the frame and emphasize one real "
    "material grain, printing texture, edge finish, or hardware detail with a small inset "
    "close-up. A pure macro crop without the complete product is forbidden. "
    "Studio lighting, sharp focus, no human, no text, no watermark."
)

# 单品多视角的细节图模板：整库只有一件商品，沿用 bundle 版「整套 + 局部 inset」会画出
# 一排小格子（俯视/滚轮/侧键/指示灯各一小块），对单件商品属无效信息且画面杂乱。
MULTIVIEW_DETAIL_SHOT_TEMPLATE = (
    "Show this ONE complete product large and centered, filling most of the frame with a "
    "clear complete outline. Add at most ONE inset close-up of a real hardware detail of "
    "this same product (scroll wheel, side button, seam, indicator light or material grain), "
    "placed in a single corner: the inset must be a straight crop of the same view, never a "
    "different angle. Never build a grid, strip or row of multiple small thumbnails, and "
    "never show more than one copy of the product. Reproduce the exact proportions and "
    "silhouette of the product. Studio lighting, sharp focus, no human, no text, no watermark."
)

# 全局质量约束：无论用户如何自定义融合主图/基础模板，只要走生图都必须追加本段，
# 确保输出满足「所有主题商品都在主图中显示、无缺陷、单图多主体只保留一个、
# 页面整洁无多余文字/中文、高清主体清晰」。该约束不可被覆盖，用于兜底生图质量。
QUALITY_CONTRACT = (
    "QUALITY CONTRACT (MANDATORY):\n"
    "- Show every member product of this set in the image; do not omit any single item.\n"
    "- Products must be flawless: no defects, distortion, broken geometry, or malformed parts.\n"
    "- If one source photo contains multiple subjects, keep only ONE (the primary subject) and hide the rest.\n"
    "- Keep the composition clean and tidy: no extra text, no Chinese characters, no captions, "
    "no slogans, no labels, no logos, no watermark.\n"
    "- Output must be high-resolution, sharp, with the main subject clearly visible and well-defined."
)


def default_base_for_index(index: int) -> str:
    # 兼容旧调用：只保留模板 A。
    return BASE_PROMPT_A


# 单品多视角选型的商品主图模板：参考图是同商品多视角，
# 目标是挑出最能代表该商品的 hero 视角，而不是把多视角拼成一张组合图。
MULTIVIEW_MAIN_PROMPT = (
    "professional e-commerce product photography of ONE single product, studio lighting, "
    "sharp focus, clean neutral background, accurate color and material, no text overlay, "
    "no watermark. The reference images are multiple views of the SAME single product: "
    "render ONE single photographic frame showing it from its most representative complete "
    "view, never combining, tiling or overlaying the reference views into one image. Keep "
    "design, colors, materials, proportions and printing identical to the reference views."
)

# 单品多视角的质量约束：禁止把视角差异当成不同商品，禁止拼贴/一图多只，禁止凭空增减配件。
MULTIVIEW_QUALITY_CONTRACT = (
    "QUALITY CONTRACT (MANDATORY):\n"
    "- The reference images show ONE product from different angles; never render them as "
    "multiple different products.\n"
    "- Output exactly ONE product in ONE single frame: no collage, no grid, no multi-panel "
    "or multi-angle layout, and no duplicated copies of the product side by side.\n"
    "- Keep shape, structure, color, material and printing identical to the reference views.\n"
    "- The reference views are structural information only: do not add extra accessories, "
    "parts or contents that are not visible in the references.\n"
    "- The product must be flawless: no defects, distortion, broken geometry, or malformed parts.\n"
    "- Keep the composition clean and tidy: no extra text, no Chinese characters, no captions, "
    "no slogans, no labels, no logos, no watermark.\n"
    "- Output must be high-resolution, sharp, with the product clearly visible and well-defined."
)


# ---- 视角标签（单品多视角选型） ----
# 参考图是同一商品的不同机位。若不在提示词里写明「第几张图是哪个视角」，模型只会把多张
# 图当成同一角度的重复参考去平均，平均出一个真机不存在的造型，并且所有成品图反复回到同
# 一个机位 —— 用户上传的俯视/底部图在构图层面等于完全没用上。
VIEW_TOP = "top view"
VIEW_BOTTOM = "bottom view"
VIEW_FRONT = "front view"
VIEW_BACK = "back view"
VIEW_LEFT = "left side view"
VIEW_RIGHT = "right side view"
VIEW_THREE_QUARTER = "three-quarter view"
VIEW_OTHER = "other view"
VIEW_LABELS: tuple[str, ...] = (
    VIEW_THREE_QUARTER,
    VIEW_FRONT,
    VIEW_BACK,
    VIEW_LEFT,
    VIEW_RIGHT,
    VIEW_TOP,
    VIEW_BOTTOM,
    VIEW_OTHER,
)
VIEW_LABEL_SET = frozenset(VIEW_LABELS)

# 每个生图角色优先用哪个视角的参考图（从左到右为优先级）：
# 白底图要最能交代整体轮廓的正视角，场景图/主图要最有立体感的 3/4 视角，
# 细节图要能看见滚轮、侧键这类硬件细节的侧视或 3/4 视角。
ROLE_VIEW_PREFERENCE: dict[str, tuple[str, ...]] = {
    "main": (VIEW_THREE_QUARTER, VIEW_LEFT, VIEW_RIGHT, VIEW_FRONT, VIEW_TOP, VIEW_BACK, VIEW_BOTTOM, VIEW_OTHER),
    "carousel_2": (VIEW_THREE_QUARTER, VIEW_LEFT, VIEW_RIGHT, VIEW_FRONT, VIEW_TOP, VIEW_BACK, VIEW_BOTTOM, VIEW_OTHER),
    "carousel_3": (VIEW_LEFT, VIEW_RIGHT, VIEW_THREE_QUARTER, VIEW_FRONT, VIEW_TOP, VIEW_BACK, VIEW_BOTTOM, VIEW_OTHER),
    "white_bg": (VIEW_TOP, VIEW_FRONT, VIEW_LEFT, VIEW_RIGHT, VIEW_THREE_QUARTER, VIEW_BACK, VIEW_BOTTOM, VIEW_OTHER),
    "detail_shot": (VIEW_LEFT, VIEW_RIGHT, VIEW_THREE_QUARTER, VIEW_TOP, VIEW_FRONT, VIEW_BACK, VIEW_BOTTOM, VIEW_OTHER),
}

# 每个角色最多带几张参考图：只送与该角色构图匹配的视角。把 4 个机位全送过去，
# 模型会在多个机位之间「平均」出一个真机不存在的造型（本轮用户看到的怪形状）。
MAX_ROLE_REFERENCES = 2


def pick_role_view_indices(
    views: list[str] | None, role: str, *, limit: int = MAX_ROLE_REFERENCES
) -> list[int]:
    """按角色视角偏好挑出该角色要送的参考图下标（按偏好优先级，最多 limit 张）。

    返回空列表表示「不做视角筛选」：没有机位标签，或标签一个都没命中该角色的偏好。
    调用方此时应退回把全部参考图送给该角色（至少不比原行为更差）。
    """
    labels = list(views or [])
    preference = ROLE_VIEW_PREFERENCE.get(str(role) or "")
    if not labels or not preference:
        return []
    picked: list[int] = []
    for view in preference:
        for index, label in enumerate(labels):
            if label == view and index not in picked:
                picked.append(index)
                break
        if len(picked) >= limit:
            break
    return picked


def build_view_guidance(*, reference_views: list[str] | None = None, output_view: str = "") -> str:
    """组装「参考图各是什么机位 + 必须按哪个机位输出」的说明；无视角信息返回空串。

    multiview 的参考图是同一商品的多张机位：不写明机位，模型会把它们当成同一角度的
    重复参考去平均；不指定输出视角，每张成品图会各自乱挑一个角度。
    """
    lines: list[str] = []
    views = [str(view).strip() for view in (reference_views or []) if str(view).strip()]
    if views:
        listed = ", ".join(f"{index}) {view}" for index, view in enumerate(views, start=1))
        lines.append(
            "REFERENCE VIEWS (different camera angles of the SAME single product, in the "
            f"order supplied): {listed}. Never treat them as different products."
        )
    target = str(output_view or "").strip()
    if target:
        lines.append(
            f"REQUIRED OUTPUT VIEW: {target} — render the product from exactly this camera "
            "angle, the same angle as the reference image showing that view. Do not switch to "
            "another angle, do not mirror the product, and never merge two views into one frame."
        )
    return " ".join(lines)


def build_multiview_main_prompt(
    *,
    set_name: str,
    reference_views: list[str] | None = None,
    output_view: str = "",
    custom_prompt: str = "",
) -> str:
    """组装单品多视角的商品主图提示词：内置模板（或用户自定义）+ 商品名 + 视角清单。

    reference_views 为本次送出的参考图机位（顺序与参考图完全一致），output_view 为要求
    输出的机位；两者都缺失时退化为原「不加机位说明」的通用主图提示词。
    """
    template = str(custom_prompt or "").strip() or MULTIVIEW_MAIN_PROMPT
    lines = [template]
    lines.append(f"Product name: {set_name or 'the product'}")
    guidance = build_view_guidance(reference_views=reference_views, output_view=output_view)
    if guidance:
        lines.append(guidance)
    lines.extend(("", MULTIVIEW_QUALITY_CONTRACT))
    return "\n".join(lines)


def build_fusion_main_prompt(
    *, set_name: str, subject_summaries: list[str], primary_subject: str = "", custom_prompt: str = ""
) -> str:
    """组装融合套装主图提示词：内置融合模板（或用户自定义）+ 套装名 + 各成员主体。

    primary_subject 为用户手动标记的「主要商品」，作为画面主角（放大/居中/最清晰）。
    custom_prompt 为用户在主体解析阶段填写的融合提示词，非空时替换内置模板方向。
    """
    template = str(custom_prompt or "").strip() or FUSION_MAIN_PROMPT
    lines = [template]
    lines.append(f"Product set name: {set_name or 'the bundle'}")
    subjects = [str(value).strip() for value in subject_summaries if str(value).strip()]
    primary = str(primary_subject or "").strip()
    if primary:
        # 主要商品是套装主角：画面里最大、最清晰、居中，其它成员作为搭配衬托。
        lines.append(
            f"PRIMARY / HERO member (make this the visual focus, largest and sharpest, "
            f"centered): {primary}"
        )
        subjects = [primary, *[s for s in subjects if s != primary]]
    if subjects:
        lines.append("Member subjects to fuse together:")
        lines.extend(f"- {value}" for value in subjects)
    # 兜底质量约束：无论是否自定义融合提示词，都强制追加，保证多主体全显示/无缺陷/无文字。
    lines.extend(("", QUALITY_CONTRACT))
    return "\n".join(lines)


def build_image_prompt(
    *,
    role: str,
    base_prompt: str,
    role_direction: str,
    subjects: list[dict[str, Any]] | None,
    set_specs: list[str],
    set_name: str,
    mode: str = DEFAULT_GENERATION_MODE,
    reference_views: list[str] | None = None,
    output_view: str = "",
) -> str:
    """单图 Prompt = 基础模板 + 角色方向 + 视角指引 + 主体/规格注入。

    subjects 为本次出图要呈现的全部成员主体。套装（bundle）必须传全，
    否则「只点名一个成员」与角色方向/质量约束的「必须画全整套」互相冲突，
    生图 provider 会凭空补出一个不存在的成员商品（例如造一只没上传过的笔袋）。
    reference_views / output_view 仅单品多视角（mode=multiview）使用：参考图是同商品
    的不同机位，必须把机位与要求输出的机位写清楚，否则模型会自己乱挑角度。
    """
    parts = [str(base_prompt or "").strip()]
    if not role_direction:
        # 兜底方向也必须与生成选型一致：multiview 回落到 bundle 的「整套/每个成员」措辞，
        # 会把同一商品的多张视角又变成拼贴或一图多只。
        role_direction = str(default_image_prompts_for_mode(mode).get(role) or "")
    role_direction = str(role_direction or "").strip()
    if role_direction:
        parts.append(role_direction)
    guidance = build_view_guidance(reference_views=reference_views, output_view=output_view)
    if guidance:
        parts.append(guidance)
    identity_lines = [f"Product set: {set_name or 'the product'}" if set_name else ""]
    members = [
        subject for subject in (subjects or [])
        if isinstance(subject, dict) and str(subject.get("sellable_subject") or "").strip()
    ]
    if len(members) == 1:
        identity_lines.append(f"Subject: {members[0]['sellable_subject']}")
        visible = members[0].get("visible_attributes")
        if isinstance(visible, list) and visible:
            identity_lines.append(f"Visible attributes: {', '.join(str(v) for v in visible[:8])}")
    elif members:
        identity_lines.append(
            "Member products of this set (each one must match its own reference photo "
            "exactly; keep exactly this list and never add, replace or invent any other item):"
        )
        for member in members:
            name = str(member.get("sellable_subject") or "").strip()
            visible = member.get("visible_attributes")
            if isinstance(visible, list) and visible:
                name = f"{name} — {', '.join(str(v) for v in visible[:4])}"
            identity_lines.append(f"- {name}")
    if set_specs:
        identity_lines.append(f"Set specs: {', '.join(set_specs[:12])}")
    parts.extend(line for line in identity_lines if line)
    # 兜底质量约束按生成选型切换：bundle 版第一条是「必须画全每个成员、一个都不能少」，
    # 对「同一件商品的多张视角」来说等于命令模型把每个视角都画成一个商品，
    # 是白底图/细节图出现拼贴与一图多只的直接来源。
    parts.extend(("", MULTIVIEW_QUALITY_CONTRACT if mode == "multiview" else QUALITY_CONTRACT))
    return "\n".join(part for part in parts if part)


def build_text_prompt(
    *,
    set_name: str,
    category: str,
    specs: list[str],
    subject_summaries: list[str],
    primary_subject: str = "",
    mode: str = DEFAULT_GENERATION_MODE,
) -> str:
    """组合套装文本生成提示词：标题 + 详情描述 + 五点。

    标题与描述的规则直接复用老 AI 处理模块的 TITLE_PROMPT / DESC_PROMPT
    （TEMU US operator 风格、标题 180~200 字符、禁品牌/违禁词、五点结构），
    仅保留组合套装特有的「多件套、单 SKU、成员商品清单」上下文。
    primary_subject 为用户手动标记的「主要商品」，标题以其为主名词。
    mode == "multiview" 时为「单品多视角」：只有一件商品，不得写成套装/N 件套。
    """
    from ..product_processing.domain.prompts import DESC_PROMPT, TITLE_PROMPT
    from ..product_processing.domain.prompts import format_prompt

    subject_block = "\n".join(
        f"- {item}" for item in subject_summaries if item
    ) or "- （未解析主体）"
    spec_block = "\n".join(f"- {item}" for item in specs if item) or "- （无规格）"
    category_text = category or "general"
    set_name_text = set_name or "the set"
    primary_text = str(primary_subject or "").strip()
    primary_line = (
        f"PRIMARY / MAIN product (the hero item this set is built around): {primary_text}\n"
        if primary_text
        else ""
    )
    if mode == "multiview":
        set_context = (
            f"This is ONE single product sold as ONE SKU on Temu US. The uploaded images are "
            f"multiple views of that SAME product (including interior / container-inside and "
            f"package flat-lay views); they are NOT separate included items.\n"
            f"Product views provided:\n{subject_block}\n"
            f"Product specs:\n{spec_block}\n"
            f"Product name: {set_name_text}\n"
            f"Category: {category_text}\n"
        )
    else:
        set_context = (
            f"This is a BUNDLE/SET sold as ONE single SKU on Temu US. It contains the "
            f"following member products (treat the whole bundle as one sellable unit, do not "
            f"create separate SKUs):\n{subject_block}\n"
            f"{primary_line}"
            f"Member specs:\n{spec_block}\n"
            f"Product set name: {set_name_text}\n"
            f"Category: {category_text}\n"
        )
    # 标题规则：直接复用 AI 处理模块的 TITLE_PROMPT 规则文本。把其中从图片/来源字段
    # 插值的占位符用组合上下文替换（主题用成员商品清单、标题用套装名、类目用套装类目），
    # 其余无来源的字段渲染为空串，避免花括号占位符原样进入 prompt 干扰模型。
    title_rules = format_prompt(
        TITLE_PROMPT,
        title=set_name_text,
        image_derived_title=subject_block,
        category=category_text,
        category_path=category_text,
        required_attributes="",
        matched_terms="",
        value_evidence=spec_block,
        verified_material_evidence="",
    )
    # 描述规则：直接复用 AI 处理模块的 DESC_PROMPT（五点结构），同样填充套装上下文。
    desc_rules = format_prompt(
        DESC_PROMPT,
        title=set_name_text,
        image_derived_title=subject_block,
        category=category_text,
        category_path=category_text,
        required_attributes="",
        value_evidence=spec_block,
        verified_material_evidence="",
    )
    if mode == "multiview":
        mandate = (
            "=== SINGLE PRODUCT TITLE MANDATE (overrides any bundle instinct) ===\n"
            "This listing is ONE product, NOT a set. The uploaded images are multiple views of "
            "the same item (front / side / interior / package flat-lay) and must never be "
            "described as separate included items or a piece count.\n"
            "1. Open the title with that product as the leading product noun.\n"
            "2. You MAY add one qualifier the views really show (e.g. \"with Transparent Inner "
            "Tray\", \"Foldable\", \"2-Compartment\"), but never add \"{N}-Piece\", \"Set of {N}\", "
            "\"{N}-Pack\" or \"Multipack\" framing.\n"
            "3. Never output a vague noun with no product identity.\n"
            "4. Still obey every TITLE_PROMPT rule: <=200 letters, English only, no brand/"
            "marketplace/violation words, natural US-operator tone, no comma-stuffed keyword list.\n\n"
            "Produce exactly these fields:\n"
            "- title: ONE optimized English listing title following the TITLE RULES and the "
            "SINGLE PRODUCT TITLE MANDATE above (<= 200 chars)\n"
            "- description: a concise, plain-text English description, NO HTML, NO image tags, "
            "and STRICTLY no longer than 320 characters (the Dianxiaomi/Temu description module "
            "caps each text block at 500 characters, and this body shares the layout with 5 "
            "bullets plus an image, so keep it short and leave headroom).\n"
            "- bullets: exactly 5 benefit-driven English bullet points, each <= 120 characters, "
            "following the DESCRIPTION RULES' five-key-point structure\n"
            "Treat the product as ONE sellable unit; do not create separate SKUs."
        )
    else:
        mandate = (
            "=== BUNDLE/SET TITLE MANDATE (overrides any single-item instinct) ===\n"
            "This listing is ONE multi-item BUNDLE/SET sold as a single SKU.\n"
            "1. Open the title with the PRIMARY / MAIN product as the leading product noun (the hero "
            "item this set is built around). If a PRIMARY product is provided, it MUST be the leading "
            "noun — name it first, e.g. \"Press-Type Ballpoint Pen & Pencil Case Stationery Set\".\n"
            "2. Immediately frame it as a SET with a SET noun plus the real total member count, using "
            "\"{N}-Piece\", \"{N} PCS\", \"Set of {N}\", \"{N}-Pack\", \"Multipack of {N}\". e.g. "
            "\"Press-Type Ballpoint Pen & Pencil Case Stationery Set - 2-Piece All-in-One Writing Kit\", "
            "\"16pc Dinnerware Set\", \"7-Piece Magnifying Glasses Set\".\n"
            "3. Other individual members (pencil case, placemat, storage box, etc.) appear only as the "
            "included CONTENTS further in the title, never as the leading noun unless they are the "
            "PRIMARY product.\n"
            "4. Never output a bare single-item noun with no set framing on its own.\n"
            "5. Still obey every TITLE_PROMPT rule: <=200 letters, English only, no brand/marketplace/"
            "violation words, natural US-operator tone, no comma-stuffed keyword list.\n\n"
            "Produce exactly these fields:\n"
            "- title: ONE optimized English listing title following the TITLE RULES and the BUNDLE/SET "
            "TITLE MANDATE above (<= 200 chars)\n"
            "- description: a concise, plain-text English description, NO HTML, NO image tags, "
            "and STRICTLY no longer than 320 characters (the Dianxiaomi/Temu description module "
            "caps each text block at 500 characters, and this body shares the layout with 5 "
            "bullets plus an image, so keep it short and leave headroom).\n"
            "- bullets: exactly 5 benefit-driven English bullet points, each <= 120 characters, "
            "following the DESCRIPTION RULES' five-key-point structure\n"
            "Treat the whole BUNDLE as ONE sellable unit; do not create separate SKUs."
        )
    return (
        f"{set_context}\n"
        f"--- TITLE RULES (borrowed from the TITLE_PROMPT, unchanged) ---\n"
        f"{title_rules}\n"
        f"--- DESCRIPTION RULES (borrowed from the DESC_PROMPT, unchanged) ---\n"
        f"{desc_rules}\n\n"
        f"{mandate}"
    )


def default_image_prompts() -> dict[str, str]:
    return dict(DEFAULT_ROLE_DIRECTIONS)


def default_image_prompts_for_mode(mode: str) -> dict[str, str]:
    """按生成选型返回 4 个可编辑角色的默认辅助词。"""
    if mode == "multiview":
        return dict(MULTIVIEW_ROLE_DIRECTIONS)
    return dict(DEFAULT_ROLE_DIRECTIONS)


def default_prompts_by_mode() -> dict[str, dict[str, str]]:
    return {"bundle": default_image_prompts_for_mode("bundle"), "multiview": default_image_prompts_for_mode("multiview")}


def all_image_roles() -> list[dict[str, str]]:
    return list(IMAGE_ROLES)
