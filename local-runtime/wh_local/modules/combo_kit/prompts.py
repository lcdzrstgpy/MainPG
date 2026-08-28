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


def default_base_for_index(index: int) -> str:
    # 兼容旧调用：只保留模板 A。
    return BASE_PROMPT_A


def build_fusion_main_prompt(
    *, set_name: str, subject_summaries: list[str], custom_prompt: str = ""
) -> str:
    """组装融合套装主图提示词：内置融合模板（或用户自定义）+ 套装名 + 各成员主体。

    custom_prompt 为用户在主体解析阶段填写的融合提示词，非空时替换内置模板方向。
    """
    template = str(custom_prompt or "").strip() or FUSION_MAIN_PROMPT
    lines = [template]
    lines.append(f"Product set name: {set_name or 'the bundle'}")
    subjects = [str(value).strip() for value in subject_summaries if str(value).strip()]
    if subjects:
        lines.append("Member subjects to fuse together:")
        lines.extend(f"- {value}" for value in subjects)
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
    return "\n".join(part for part in parts if part)


def build_text_prompt(
    *,
    set_name: str,
    category: str,
    specs: list[str],
    subject_summaries: list[str],
) -> str:
    """组合套装文本生成提示词：标题 + 详情描述 + 五点。"""
    subject_block = "\n".join(
        f"- {item}" for item in subject_summaries if item
    ) or "- （未解析主体）"
    spec_block = "\n".join(f"- {item}" for item in specs if item) or "- （无规格）"
    return (
        f"You are a professional marketplace copywriter. Write listing copy for a "
        f"product BUNDLE/SET sold as a single SKU.\n"
        f"Set name: {set_name or 'the set'}\n"
        f"Category: {category or 'general'}\n"
        f"Set contains the following member products:\n{subject_block}\n"
        f"Member specs:\n{spec_block}\n\n"
        "Produce:\n"
        "- title: an optimized, keyword-rich English listing title (<= 200 chars)\n"
        "- description: a well-structured detail-page description\n"
        "- bullets: exactly 5 benefit-driven bullet points\n"
        "Treat the whole BUNDLE as ONE sellable unit; do not create separate SKUs."
    )


def default_image_prompts() -> dict[str, str]:
    return dict(DEFAULT_ROLE_DIRECTIONS)


def all_image_roles() -> list[dict[str, str]]:
    return list(IMAGE_ROLES)
