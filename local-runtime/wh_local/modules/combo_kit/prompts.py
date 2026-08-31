"""combo_kit Prompt 组装：两套基础模板 + 每图独立辅助 Prompt + 主体信息注入。

完全复用老 AI 处理的 Prompt 底层逻辑（风格词/画质词/负面词），
但记录独立入库；本模块不调用 OCR 校验。
"""
from __future__ import annotations

from typing import Any

from .contracts import IMAGE_ROLES

# 只保留一套内置基础通用提示词模板（对齐老 AI 处理风格）。不再提供模板 B。
BASE_PROMPT_A = (
    "professional e-commerce product photography, studio lighting, "
    "sharp focus, clean neutral background, accurate color and material, "
    "no human, no text overlay, no watermark"
)

# 每张图默认辅助方向（仅 3 个可编辑角色向用户展示，其余为内部固定方向）。
DEFAULT_ROLE_DIRECTIONS: dict[str, str] = {
    "main": "",
    "carousel_2": "alternate angle emphasizing shape and key silhouette, set fully visible",
    "carousel_3": "detail-oriented angle highlighting materials and edges",
    "white_bg": "complete bundled set on a clean white background, full product visible, "
                "balanced layout, professional studio shot",
    "detail_shot": "",
    "detail_page": "",
}

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
    subject: dict[str, Any] | None,
    set_specs: list[str],
    set_name: str,
) -> str:
    """单图 Prompt = 基础模板 + 角色方向 + 主体/规格注入。"""
    parts = [str(base_prompt or "").strip()]
    role_direction = str(role_direction or "").strip() or DEFAULT_ROLE_DIRECTIONS.get(role, "")
    if role_direction:
        parts.append(role_direction)
    identity_lines = [f"Product set: {set_name or 'the product'}" if set_name else ""]
    if subject and subject.get("sellable_subject"):
        identity_lines.append(f"Subject: {subject['sellable_subject']}")
    visible = subject.get("visible_attributes") if isinstance(subject, dict) else None
    if isinstance(visible, list) and visible:
        identity_lines.append(f"Visible attributes: {', '.join(str(v) for v in visible[:8])}")
    if set_specs:
        identity_lines.append(f"Set specs: {', '.join(set_specs[:12])}")
    parts.extend(line for line in identity_lines if line)
    # 兜底质量约束：附加到每张成品图提示词末尾，保证清晰/无缺陷/无多余文字或中文。
    parts.extend(("", QUALITY_CONTRACT))
    return "\n".join(part for part in parts if part)


def build_text_prompt(
    *,
    set_name: str,
    category: str,
    specs: list[str],
    subject_summaries: list[str],
    primary_subject: str = "",
) -> str:
    """组合套装文本生成提示词：标题 + 详情描述 + 五点。

    标题与描述的规则直接复用老 AI 处理模块的 TITLE_PROMPT / DESC_PROMPT
    （TEMU US operator 风格、标题 180~200 字符、禁品牌/违禁词、五点结构），
    仅保留组合套装特有的「多件套、单 SKU、成员商品清单」上下文。
    primary_subject 为用户手动标记的「主要商品」，标题以其为主名词。
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
    return (
        f"{set_context}\n"
        f"--- TITLE RULES (borrowed from the TITLE_PROMPT, unchanged) ---\n"
        f"{title_rules}\n"
        f"--- DESCRIPTION RULES (borrowed from the DESC_PROMPT, unchanged) ---\n"
        f"{desc_rules}\n\n"
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


def default_image_prompts() -> dict[str, str]:
    return dict(DEFAULT_ROLE_DIRECTIONS)


def all_image_roles() -> list[dict[str, str]]:
    return list(IMAGE_ROLES)
