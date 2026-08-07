"""产品处理英文工程化提示词（对齐原型 native_product_engine 的 DEFAULT_PROMPTS）。

这些模板是英文的：AI 生成标题/描述/图片时按目标语言再注入语言契约
（见 domain/language_contract.apply_language_contract_to_prompt）。
format_prompt 对缺失变量返回空串，保证精简上下文下也能渲染完整规则。
"""

from __future__ import annotations

import re
from typing import Any


TITLE_PROMPT = """You are a TEMU cross-border e-commerce product-title translator and summarizer. Rewrite the source title into a faithful, concise listing title for US and European shoppers.

STRICT RULES:
1. Full English only, no Chinese characters.
2. The title must identify the exact product being sold. Product identity accuracy is more important than length or SEO breadth.
3. Ideal length is 60-130 characters; allow up to 160 only when the source has enough real evidence. Hard maximum 180 characters.
4. Use only title-safe evidence: source product identity, selected SKU/variant facts, verified material evidence, real quantity/model/size facts, and category product nouns that do not conflict with the source title.
5. Do not use import-only category attributes, fallback values, placeholders, or missing-value labels in the title. Forbidden placeholder examples: Not Applicable, Does Not Apply, N/A, None, Other, Unknown, Default.
6. Match US/EU shopping habits: clear, specific, easy to scan, not mechanical keyword stuffing.
7. Do not invent material, certification, function, compatibility, quantity, size, scene, target audience, or claims.
8. Remove brand, store, manufacturer, supplier, platform, channel, wholesale, hot-sale, in-stock, dropshipping, labeling, and advertising words unless they are the product type itself.
9. Do not use exaggerated, absolute, comparative, country, region, brand, trademark, IP, or misleading words. Forbidden examples include: best, perfect, guaranteed, medical, certified, official, luxury, premium, FDA, CE, wholesale, free shipping, discount, TEMU, Amazon, eBay, Walmart, Etsy, AliExpress, 1688.
10. Avoid qualification-sensitive words, especially medical, health care, therapy, treatment, pain relief, skin care, skincare, nursing, baby, infant, toddler, children, and kids.

Source title: {title}
Product category: {category}
Category path: {category_path}
Title-safe attributes only: {required_attributes}
Matched category terms: {matched_terms}
Value evidence from source: {value_evidence}
Verified material evidence for title: {verified_material_evidence}

Output the optimized title directly, no explanation."""


DESC_PROMPT = """You are a TEMU cross-border e-commerce product description expert. Generate an English product description for this product.

STRICT RULES:
1. Highlight concrete selling points and usage scenarios.
2. Natural fluent English for US consumers.
3. 80-150 English words, max 500 characters.
4. Avoid generic claims, exaggerated words, brands, trademarks, country names, marketplace/platform names, and superlatives.
5. Do not state a material unless verified material evidence explicitly supplies it.
6. Use only facts supported by the source title, category, and attributes. Do not invent features.

Product title: {title}
Product category: {category}
Category path: {category_path}
Required category attributes: {required_attributes}
Value evidence from source: {value_evidence}
Verified material evidence for description: {verified_material_evidence}

Output the description directly, no explanation."""


COMBINED_TEXT_PROMPT = """You are a TEMU cross-border e-commerce product title and description editor. Analyze the source title, category, and attributes, then produce a faithful optimized title and a concise description.

TITLE STRICT RULES:
1. Full English only, no Chinese characters.
2. The title must identify the exact product being sold. Product identity accuracy is more important than length or SEO breadth.
3. Ideal length is 60-130 characters; allow up to 160 only when the source has enough real evidence. Hard maximum 180 characters.
4. Use only title-safe evidence: source product identity, selected SKU/variant facts, verified material evidence, real quantity/model/size facts, and category product nouns that do not conflict with the source title.
5. Do not use import-only category attributes, fallback values, placeholders, or missing-value labels in the title. Forbidden placeholder examples: Not Applicable, Does Not Apply, N/A, None, Other, Unknown, Default.
6. Match US/EU shopping habits: clear, specific, easy to scan, not mechanical keyword stuffing.
7. Do not invent material, certification, function, compatibility, quantity, size, scene, target audience, or claims.
8. Remove brand, store, manufacturer, supplier, platform, channel, wholesale, hot-sale, in-stock, dropshipping, labeling, and advertising words unless they are the product type itself.
9. Do not use exaggerated, absolute, comparative, country, region, brand, trademark, IP, or misleading words. Forbidden examples include: best, perfect, guaranteed, medical, certified, official, luxury, premium, FDA, CE, wholesale, free shipping, discount, TEMU, Amazon, eBay, Walmart, Etsy, AliExpress, 1688.
10. Avoid qualification-sensitive words, especially medical, health care, therapy, treatment, pain relief, skin care, skincare, nursing, baby, infant, toddler, children, and kids.
11. Sensitive but legally salable products must use neutral wording; never change the real product identity just to bypass rules.

DESCRIPTION STRICT RULES:
1. Highlight concrete selling points and usage scenarios.
2. Natural fluent English for US consumers.
3. 80-150 English words, max 500 characters.
4. Avoid generic claims, exaggerated words, brands, trademarks, country names, marketplace/platform names, and superlatives.
5. Do not state a material unless verified material evidence explicitly supplies it.
6. Use only facts supported by the source title, category, and attributes. Do not invent features.

Source title: {title}
Product category: {category}
Category path: {category_path}
Title-safe attributes only: {required_attributes}
Matched category terms: {matched_terms}
Value evidence from source: {value_evidence}
Verified material evidence for text: {verified_material_evidence}

VARIANT OPTION TRANSLATION RULES:
- If {variant_options} is empty, return an empty "variant_translations" array.
- Otherwise translate every captured 1688 variant option text into concise {target_language_name} ({language_code}) shopper-readable values for the Dianxiaomi import template.
- Preserve real meaning such as color, pattern, size, package, capacity, model code, bundle quantity, or material.
- Preserve meaningful model/style codes, digits and units (cm, pcs, ml, etc.) exactly.
- Remove Chinese bracket symbols 【 】 and other decorative symbols; use clean readable text.
- Do not use placeholder labels like Pattern A, Style B, Default, Option, Unknown, Other, or Variant.
- Each mapping must keep raw_value copied exactly; do not merge or drop any option.

Captured variant options:
{variant_options}

Return ONLY a JSON object with exactly three keys, no explanation:
{{"optimized_title": "...", "description": "...", "variant_translations": [{{"raw_value": "exact raw value", "export_value": "translated value"}}]}}"""


GRID_IMAGE_PROMPT = """Use the reference image as the non-negotiable source of truth. The product itself must stay the same: same product type, shape, color, material, pattern, quantity, proportions, structure, and visible details. Do not redesign, recolor, simplify, upgrade, replace, or add features to the product.

Create one square exact four-panel 2x2 e-commerce grid for: {title}
Category path: {category_path}
Value evidence from source: {value_evidence}
Verified material for visible copy: {verified_material_evidence}

Visual formula:
Product: {product_visual_identity}
Style: {visual_style}
Lighting: {lighting_plan}
Material: {material_plan}
Background: {background_plan}
Composition: {composition_plan}

Category-specific scene plan:
{scene_plan}

Video-ready four-panel material plan:
{video_shot_plan}

Each panel must work both as a standalone marketplace carousel image and as a clean short-video frame.
Keep an exact four-panel 2x2 grid with clean straight dividers. Do not change the four-grid structure, divider layout, or split logic.
Official authenticity rules: the sellable product must be complete, sharp, prominent, and unobstructed in every panel. Do not show only a packaging bag unless the product being sold is packaging bags. Do not crop away key attributes, hide important parts behind props/text/hands, make the product tiny, blur it, use an unrelated background, or perform deceptive Photoshop-style edits.
Strict rules: Differentiate only through style, lighting, background, scene, and composition. Each quadrant must be a different composition/angle/scene; do not repeat or lightly recolor the same panel. Do not change the product itself. Do not invent material, dimensions, functions, accessories, certifications, brand, or claims. No added text overlays, labels, arrows, UI, logo, watermark, price, discount badge, certification badge, medical claim, exaggerated claim, or promotional text. If the product itself contains decorative characters, symbols, or patterns, keep them only as product design. Text rule: any visible text in the generated image must be English only. If the source product or packaging shows Chinese characters or other non-English text, replace it with the equivalent English text or remove it entirely; never reproduce Chinese characters or other non-English text in the generated panels. Realistic, bright, sharp, clean, marketplace-ready for US/EU shoppers."""


DETAIL_IMAGE_PROMPT = """Use the reference image as the non-negotiable source of truth. Preserve the same product type, shape, color, material, pattern, quantity, proportions, structure, and visible details. Do not redesign or change the product itself.

Create one square e-commerce detail poster for: {title}
Category path: {category_path}
Value evidence from source: {value_evidence}
Verified material for visible copy: {verified_material_evidence}

Visual formula:
Product: {product_visual_identity}
Style: {visual_style}
Lighting: {lighting_plan}
Material: {material_plan}
Background: {background_plan}
Composition: {composition_plan}

Category-specific detail plan:
{detail_plan}

Poster layout: one unified e-commerce poster composition, not a carousel image. Use one large dominant main product hero scene as the visual center, one circular or magnifier-style close-up inset for the strongest real detail, and exactly 3 light short labels placed cleanly around the poster. Do not use multiple rectangular information cards.
Do not create a four-panel grid, 2x2 grid, quadrant layout, split-panel layout, collage grid, row/column card layout, or image intended for carousel splitting.
Callout text rules: exactly 3 factual labels, 1-4 words each, no sentence captions, slogans, unsupported claims, or invented dimensions.
Official authenticity rules: product complete, sharp, dominant, unobstructed; no packaging-only image unless selling packaging bags; no tiny product, unrelated background, blocked key attribute, or deceptive edits.
Strict copy rules: no logo, watermark, price, certification badge, medical claim, or promotional copy anywhere in visible text. Text rule: any visible text in the generated image must be English only. If the source product or packaging shows Chinese characters or other non-English text, replace it with the equivalent English text or remove it entirely; never reproduce Chinese characters or other non-English text in the generated poster. English only for added labels. Realistic, bright, clean US/EU marketplace style."""


# 图片中文重绘提示词（OCR 质量门检出中文后，把上一轮生成图回传给模型定向修复）
IMAGE_REPAIR_CHINESE_PROMPT = """The attached image is a product photo generated for an e-commerce listing, but it still contains Chinese characters. Fix only the text inside the image:

- Replace every Chinese character or word with its equivalent English text, or remove it entirely when no sensible English equivalent exists.
- Keep the product, its color, material, structure, quantity, composition, layout, style, background, and any existing English labels or decorative design exactly the same.
- Do not add new elements, change the composition, or redesign the product.
- The corrected image must contain zero Chinese characters.

Output only the corrected image."""


SIZE_PROMPT = """Estimate realistic shipping package dimensions and weight for this TEMU product from structured text evidence.

Product title: {title}
Product category: {category}
Category path: {category_path}
Required category attributes: {required_attributes}
Source data: {source_data}

Rules:
1. Output shipping package size, not a clothing size chart and not a visual size image.
2. Use only source text, confirmed product category, SKU/specification records, and quantity evidence. Never infer scale, size, or weight from an image background or visual scene.
3. If source data contains explicit dimensions or weight, preserve that evidence and estimate only the missing fields with modest packaging allowance.
4. If this is a set or kit, use the largest item plus sensible arrangement space and the stated quantity; do not add every piece length end-to-end.
5. First decide the physical package profile from evidence: flat soft item, foldable soft bag, folded large bag, rigid container, compact tool, or small accessory. Use that profile to keep the estimate physically plausible.
6. Keep the estimate close to real shipping packaging. Do not intentionally oversize or invent marketing claims.
7. Use centimeters and grams.
8. Return valid JSON only, no markdown, no explanation.

JSON schema:
{{"length_cm": 20, "width_cm": 15, "height_cm": 6, "weight_g": 180, "confidence": "medium", "package_profile": "compact_tool", "reason": "short reason"}}"""


VARIANT_VALUE_TRANSLATION_PROMPT = """You are an e-commerce SKU option interpreter for the Dianxiaomi import template. Translate captured 1688 variant option text into concise shopper-readable values in {target_language_name} ({language_code}).

STRICT RULES:
1. Return one valid JSON object only, no markdown or explanation.
2. For every input option, return exactly one mapping: raw_value copied exactly, export_value, confidence.
3. export_value must be concise {language_code} ({language_code}), 1-8 words, preserving real meaning such as color, pattern, size, package, capacity, model code, bundle quantity, or material.
4. Preserve meaningful model/style codes, digits and units (cm, pcs, ml, etc.) exactly.
5. Remove Chinese bracket symbols 【 】 and other decorative symbols; use clean readable text.
6. Do not return placeholder labels like Pattern A, Style B, Default, Option, Unknown, Other, or Variant.
7. Do not invent product features that are not in the option text. If the option is only a code, return the code itself.
8. Do not change SKU count or merge options. Each raw_value maps to exactly one export_value.

Product title: {title}
Captured variant options:
{variant_options}

JSON schema:
{{"mappings":[{{"raw_value":"exact input raw value","export_value":"translated shopper-readable value","confidence":"high|medium|low"}}]}}"""


DEFAULT_PROMPTS: dict[str, str] = {
    "title": TITLE_PROMPT,
    "desc": DESC_PROMPT,
    "size": SIZE_PROMPT,
    "grid_image": GRID_IMAGE_PROMPT,
    "detail_image": DETAIL_IMAGE_PROMPT,
    "image_repair_chinese": IMAGE_REPAIR_CHINESE_PROMPT,
    "combined_text": COMBINED_TEXT_PROMPT,
    "variant_values": VARIANT_VALUE_TRANSLATION_PROMPT,
}


class _PromptValues(dict):
    def __missing__(self, key: str) -> str:
        return ""


def format_prompt(template: str, **values: Any) -> str:
    safe_values = {key: _prompt_text(value) for key, value in values.items()}
    return str(template or "").format_map(_PromptValues(safe_values))


def _prompt_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def default_image_context(title: str, category: str, *, material_evidence: str = "") -> dict[str, str]:
    """为图片提示词补齐可复用的视觉公式默认值（无深度规划时的稳妥值）。"""
    category = str(category or "").strip() or "general merchandise"
    return {
        "title": str(title or "").strip() or "the product",
        "category_path": category,
        "value_evidence": _value_evidence_default(title),
        "verified_material_evidence": (
            material_evidence
            or "None. Do not state any material, fabric composition, wood, metal, rattan, plastic, silicone, or similar material term."
        ),
        "product_visual_identity": str(title or "").strip() or "the product in the reference image",
        "visual_style": "clean, modern, realistic e-commerce marketplace style",
        "lighting_plan": "bright, even, soft studio lighting with natural color accuracy",
        "material_plan": "preserve the exact real material and surface texture of the product",
        "background_plan": "clean, uncluttered background that does not distract from the product",
        "composition_plan": "product centered, complete, sharp, and prominent; keep at least one panel showing the full product",
        "scene_plan": "simple lifestyle scene suitable for this product category, using safe neutral props only",
        "video_shot_plan": "four clearly distinct angles (front, side, detail, lifestyle) that each work as a video frame",
        "detail_plan": "one hero scene plus one close-up of the most visible real detail of the product",
    }


def _value_evidence_default(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(title or "").strip())[:400]
    return (
        f"Verified source title facts only: {cleaned}."
        if cleaned
        else "No additional source facts beyond the reference image."
    )
