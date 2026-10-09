"""爆款复刻四宫格提示词：锁定样图图案 + 目标产品，不做原创花色配方。

与原创全定制（build_direct_listing_prompt / build_style_listing_prompt）严格区分：
复刻的图案只来自第一张样图，不能注入风格元素、随机配色、渲染工艺或疏密配方，
元素关键词与配色偏好也不渲染。四格是同一目标产品、同一图案的四种电商展示角色。
"""

from __future__ import annotations

from .contracts import BusinessFields


def build_replica_listing_prompt(fields: BusinessFields, *, attempt: int) -> str:
    """构造一次「爆款复刻」四宫格商品图提示词。

    参考图顺序固定为 ``[pattern_source, target_product]``：第 1 图只提供图案，
    第 2 图只提供目标产品；生成画面只能出现目标产品。图案颜色、元素与排列关系
    尽量取自样图，仅按目标表面做透视/缩放/重复适配，不能随意变色、换元素或重新设计。
    """
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")

    parts = [
        "Create one square 2x2 ecommerce contact sheet with exactly four equal panels.",
        "You are given two reference images in this exact order.",
        "Reference image 1 provides ONLY the surface pattern to replicate: its motifs, colors, and their arrangement, spacing, and scale relationships.",
        "Reference image 2 provides ONLY the target product: its shape, structure, material, proportions, and available printable surface.",
        "The generated panels must contain ONLY the target product. Never draw the product or background that appears next to the pattern in reference image 1, and never bring in the sample image background, room, furniture, model, props, or surrounding scene from reference image 1.",
        "Faithfully replicate the pattern from reference image 1. Keep the same motifs, the same colors, and the same arrangement relationships. Do not change colors, do not replace or drop elements, and do not redesign the pattern into a different style.",
        "Adapt the pattern to the target product's printable surface only through reasonable perspective, scaling, and repetition that follow the surface geometry. Never lose the pattern's motifs, colors, or arrangement, and never add new motifs or a second pattern.",
        "All four panels show the same target product wearing the same replicated pattern in four ecommerce viewing roles. They are NOT four different products, NOT four different patterns, and NOT four recolored variants of the pattern.",
        "Text that is already part of the pattern itself may stay. Do not add any new text, labels, logos, watermarks, signatures, captions, collage borders, stickers, or the sample image background. Keep the four-panel divider clean and centered.",
        "When the target product has an interior or lining (for example a laundry hamper, storage basket, or tote bag with an inner lining), keep the interior surface unprinted: a plain uniform solid color, black by default. Never extend the outer pattern onto the interior, and never add a second pattern inside.",
        "Panel 1 — MATERIAL IMAGE (top-left): show one complete target product against a clean neutral ecommerce background. Keep the whole product clearly visible, make it fill most of the panel, and show the full replicated pattern sharply. This is supporting material imagery, not the marketplace primary image.",
        "Panel 2 — DETAIL IMAGE A (top-right): show a tight high-resolution close-up of the replicated pattern on this same target product. Make color, edges, print or material texture, and manufacturing detail easy to inspect; do not alter the pattern or its placement. This close-up belongs to the top-right panel only.",
        "Panel 3 — DETAIL IMAGE B (bottom-left): show a different close product detail or three-quarter product view. Choose a product-appropriate structural or material detail while keeping the pattern visibly identical to Panel 1 and Panel 2. This detail view belongs to the bottom-left panel only.",
        "Panel 4 — PRIMARY IMAGE (bottom-right): show the same complete target product in a natural, commercially useful lifestyle setting. Keep the full product and unchanged pattern visible; this is the marketplace primary image and title reference. Do not reuse the sample image background or add another product.",
        "Panel 4 must be a wide lifestyle scene with the whole product inside a real environment, shot at a normal eye-level product angle, with the full silhouette in frame. It must never be a close-up, macro, cropped, partial, extreme-angle, or three-quarter detail shot: those belong to Panel 2 and Panel 3 and must not be repeated in the bottom-right panel.",
        "Final check on the fixed order by position: top-left = complete product on a neutral background, top-right = tight pattern close-up, bottom-left = a different close product detail, bottom-right = the complete product in a lifestyle scene. Do not swap, shift, or duplicate panels.",
        f"Product name: {fields.product_name or 'POD product'}.",
    ]
    if fields.product_category:
        parts.append(f"Product category: {fields.product_category}.")
    if attempt == 2:
        parts.extend(
            (
                "",
                "RETRY ATTEMPT 2 OF 2:",
                "Keep the exact same pattern from reference image 1 and the exact same target product from reference image 2. Strengthen only the consistency between the replicated pattern and the target product, and correct any deviation from the pattern's motifs, colors, or arrangement.",
            )
        )
    return "\n".join(parts)