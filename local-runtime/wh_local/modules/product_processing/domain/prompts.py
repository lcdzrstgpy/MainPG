"""产品处理英文工程化提示词（对齐原型 native_product_engine 的 DEFAULT_PROMPTS）。

这些模板是英文的：AI 生成标题/描述/图片时按目标语言再注入语言契约
（见 domain/language_contract.apply_language_contract_to_prompt）。
format_prompt 对缺失变量返回空串，保证精简上下文下也能渲染完整规则。
"""

from __future__ import annotations

import re
from typing import Any


GRID_RUNTIME_CONTRACT = """NON-OVERRIDABLE FOUR-GRID RUNTIME CONTRACT:
- Output one 2048 x 2048 square 2x2 transport grid using the supplied fixed-layout scaffold as a structural constraint.
- Keep the vertical separator at x=1016..1031 and the horizontal separator at y=1016..1031. Preserve both as continuous neutral light-gray bands, straight, uniform, and uninterrupted from edge to edge.
- Keep Panel 1 inside x=0..1015, y=0..1015; Panel 2 inside x=1032..2047, y=0..1015; Panel 3 inside x=0..1015, y=1032..2047; Panel 4 inside x=1032..2047, y=1032..2047.
- Do not render these coordinates, panel numbers, instructions, guides, or any other text into the image.
- Draw no second full-height or full-width divider, inset frame, nested collage border, split-panel line, or internal grid inside any quadrant.
- Each quadrant is a complete standalone product photo. No subject, prop, shadow, surface, background shape, or typography may cross a cut boundary.
- Show the complete sellable product or verified complete set in every quadrant; no pure macro crop and no hidden or invented parts.
- Generate zero added letters, words, numbers, labels, slogans, badges, logos, watermarks, arrows, rulers, or measurement marks. No copy is added after splitting either.
- Keep Panel 4 clean for later deterministic dimension annotation."""


TITLE_PROMPT = """You are a TEMU US-station operator with 10 years of experience. Based on the product image I provide, generate ONE English title suitable for Temu US listings.

CORE MISSION (fixed):
- Build a stable Temu US listing title for HIGH sales volume + HIGH average order value + LOW after-sales.
- Match US buyer search habits: naturally include real high-traffic, high-conversion search terms that fit the actual product.
- English title length: around 180 English letters (hard range 160-200). Never exceed 200 letters. Do not pad with filler words.
- Write from the ACTUAL PRODUCT shown in the image: base the title on the image-derived product understanding below plus verified source facts. This is NOT a literal translation task.

STRICT RULES (fixed):
1. Full English only, no Chinese characters, no English-Chinese mixing.
2. The title must identify the exact product being sold. Product identity accuracy is more important than length or SEO breadth.
3. Write ONE natural long listing title, not a comma-stuffed keyword list. Use this rhythm: main product name + key visible style/material/structure + main function or set contents + practical use scenes. It should read like a Temu US operator wrote it, not like raw SEO tags.
4. FORBIDDEN CONTENT (zero tolerance): brand names, trademarks, IP/infringing words, logos, manufacturer/supplier/store names, marketplace names (TEMU, Amazon, eBay, Walmart, Etsy, AliExpress, 1688), and any country or region words.
5. FORBIDDEN VIOLATION WORDS (zero tolerance): violence, discrimination, medical/health-care/therapy/treatment/pain-relief/skin-care/nursing/baby/infant/toddler/children/kids claims, FDA, CE, exaggerated efficacy or absolute claims (best, perfect, guaranteed, certified, official, luxury, premium), free shipping, discount, or any misleading word that could trigger platform review, permission restrictions, or listing-risk controls.
6. Use only title-safe evidence: the image-derived product understanding, source product identity, selected SKU/variant facts, verified material evidence, real quantity/model/size facts, and category product nouns that do not conflict with the source title.
7. Do not use import-only category attributes, fallback values, placeholders, or missing-value labels (Not Applicable, Does Not Apply, N/A, None, Other, Unknown, Default).
8. Do not invent material, certification, function, compatibility, quantity, size, scene, target audience, or claims.
9. Sensitive but legally salable products must use neutral wording; never change the real product identity just to bypass rules.
10. Write like a 10-year US operator: fluent, natural, buyer-search-friendly tone; keywords must read naturally, never mechanically stuffed.
11. When the image-derived product understanding is provided, it is the authoritative visual evidence of the exact product being sold — write the title primarily from it. The source title is supporting evidence only: do not translate it word-for-word, and do not repeat any marketplace noise, promotional words, or shop names from it.

STYLE EXAMPLES (follow the structure, not the exact product facts):
- Good: Kids Color Block Hooded Ski Jacket and Snow Pants Set, Zip Front Long Sleeve Winter Outdoor Snow Play Outfit, Two Piece Cold Weather Sportswear for Boys and Girls
- Good: Handheld Steam Iron Garment Steamer, 1000W Dry Wet Dual Use, Non Stick Soleplate, Multi Mode Portable Clothes Iron for Home Fabric Care
- Bad: Corrugated Shipping Boxes, Brown Kraft Cardboard Cartons, Rectangular Packaging Boxes, Multiple Sizes for Packing, Moving, Storage and Delivery
- Better for that product type: Brown Corrugated Cardboard Shipping Boxes for Packing, Moving and Storage, Rectangular Kraft Mailer Cartons, Multiple Size Packaging Box Set
- Avoid repeating the same noun in a row (Boxes, Cartons, Boxes). Use one strong product noun first, then attributes and use scenes.

Image-derived product understanding (from the source main image): {image_derived_title}
Source title: {title}
Product category: {category}
Category path: {category_path}
Title-safe attributes only: {required_attributes}
Matched category terms: {matched_terms}
Value evidence from source: {value_evidence}
Verified material evidence for title: {verified_material_evidence}

Output the optimized title directly, no explanation."""


DESC_PROMPT = """You are a TEMU cross-border e-commerce product description expert. Generate an English product description formatted as Amazon-style five key points (bullet points) for this product.

STRICT RULES:
1. Output exactly 5 bullet points. Each point must start with a 2-5 word ALL-CAPS key phrase that captures one selling angle, followed by ": " or " - " and one fluent sentence.
2. Example structure:
   DURABLE MATERIAL - This product is built with sturdy ABS plastic, designed to withstand everyday use.
3. Cover five distinct angles: material/build quality, function or usage scenario, size/capacity, easy care or convenience, and one practical detail that adds value. Do not repeat the same selling point in two bullets.
4. Natural fluent English for US consumers. Total 80-150 English words, max 1000 characters.
5. Avoid generic claims, exaggerated words, brands, trademarks, country names, marketplace/platform names, and superlatives.
6. Do not state a material unless verified material evidence explicitly supplies it.
7. Use only facts supported by the image-derived product understanding, the source title, category, and attributes. Do not invent features. This is NOT a translation task — never translate the source title or description literally.

Image-derived product understanding (from the source main image): {image_derived_title}
Product title: {title}
Product category: {category}
Category path: {category_path}
Required category attributes: {required_attributes}
Value evidence from source: {value_evidence}
Verified material evidence for description: {verified_material_evidence}

Output the 5 bullet points directly, one bullet per line, no explanation."""


COMBINED_TEXT_PROMPT = """You are a TEMU US-station operator with 10 years of experience. Based on the product image evidence, source title, category, and attributes, produce a faithful optimized title and a concise description for Temu US shoppers.

CORE MISSION (fixed - title):
- Build a stable Temu US listing title for HIGH sales volume + HIGH average order value + LOW after-sales.
- Match US buyer search habits: naturally include real high-traffic, high-conversion search terms that fit the actual product.
- English title length: around 180 English letters (hard range 160-200). Never exceed 200 letters. Do not pad with filler words.
- Write from the ACTUAL PRODUCT shown in the image: base the title on the image-derived product understanding below plus verified source facts. This is NOT a literal translation task.

TITLE STRICT RULES:
1. Full English only, no Chinese characters, no English-Chinese mixing.
2. The title must identify the exact product being sold. Product identity accuracy is more important than length or SEO breadth.
3. Write ONE natural long listing title, not a comma-stuffed keyword list. Use this rhythm: main product name + key visible style/material/structure + main function or set contents + practical use scenes. It should read like a Temu US operator wrote it, not like raw SEO tags.
4. FORBIDDEN CONTENT (zero tolerance): brand names, trademarks, IP/infringing words, logos, manufacturer/supplier/store names, marketplace names (TEMU, Amazon, eBay, Walmart, Etsy, AliExpress, 1688), and any country or region words.
5. FORBIDDEN VIOLATION WORDS (zero tolerance): violence, discrimination, medical/health-care/therapy/treatment/pain-relief/skin-care/nursing/baby/infant/toddler/children/kids claims, FDA, CE, exaggerated efficacy or absolute claims (best, perfect, guaranteed, certified, official, luxury, premium), free shipping, discount, or any misleading word that could trigger platform review, permission restrictions, or listing-risk controls.
6. Use only title-safe evidence: the image-derived product understanding, source product identity, selected SKU/variant facts, verified material evidence, real quantity/model/size facts, and category product nouns that do not conflict with the source title.
7. Do not use import-only category attributes, fallback values, placeholders, or missing-value labels (Not Applicable, Does Not Apply, N/A, None, Other, Unknown, Default).
8. Do not invent material, certification, function, compatibility, quantity, size, scene, target audience, or claims.
9. Sensitive but legally salable products must use neutral wording; never change the real product identity just to bypass rules.
10. Write like a 10-year US operator: fluent, natural, buyer-search-friendly tone; keywords must read naturally, never mechanically stuffed.
11. When the image-derived product understanding is provided, it is the authoritative visual evidence of the exact product being sold — write the title primarily from it. The source title is supporting evidence only: do not translate it word-for-word, and do not repeat any marketplace noise, promotional words, or shop names from it.

TITLE STYLE EXAMPLES (follow the structure, not the exact product facts):
- Good: Kids Color Block Hooded Ski Jacket and Snow Pants Set, Zip Front Long Sleeve Winter Outdoor Snow Play Outfit, Two Piece Cold Weather Sportswear for Boys and Girls
- Good: Handheld Steam Iron Garment Steamer, 1000W Dry Wet Dual Use, Non Stick Soleplate, Multi Mode Portable Clothes Iron for Home Fabric Care
- Bad: Corrugated Shipping Boxes, Brown Kraft Cardboard Cartons, Rectangular Packaging Boxes, Multiple Sizes for Packing, Moving, Storage and Delivery
- Better for that product type: Brown Corrugated Cardboard Shipping Boxes for Packing, Moving and Storage, Rectangular Kraft Mailer Cartons, Multiple Size Packaging Box Set
- Avoid repeating the same noun in a row (Boxes, Cartons, Boxes). Use one strong product noun first, then attributes and use scenes.

Operator-configured product description instructions (apply their factual and style rules; the final response format below remains JSON):
{description_instructions}

DESCRIPTION STRICT RULES:
1. Output exactly 5 bullet points (Amazon-style five key points). Each point starts with a 2-5 word ALL-CAPS key phrase, then ": " or " - ", then one fluent sentence.
2. Cover five distinct angles: material/build quality, function or usage scenario, size/capacity, easy care or convenience, and one practical detail that adds value. Do not repeat the same selling point in two bullets.
3. Natural fluent English for US consumers. Total 80-150 English words, max 1000 characters.
4. Avoid generic claims, exaggerated words, brands, trademarks, country names, marketplace/platform names, and superlatives.
5. Do not state a material unless verified material evidence explicitly supplies it.
6. Use only facts supported by the image-derived product understanding, the source title, category, and attributes. Do not invent features.

Image-derived product understanding (from the source main image): {image_derived_title}
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


GRID_IMAGE_PROMPT = """Role & Core Mission (fixed):
You are a senior e-commerce visual designer serving TEMU, TikTok Shop, and Amazon US listings. Treat the uploaded reference image(s) as the ONLY source of truth for the SKU. The task is to rebuild a high-click, high-quality, high-conversion commercial visual system WITHOUT changing the product itself (the product body must stay 100% unchanged).

Execution Priority (fixed): SKU accuracy > structure/pattern accuracy > material/color accuracy > complete per-panel composition > background creativity > visual polish.

Product Integrity Constraints (fixed - safety red line):
- Lock before generating: product silhouette, proportions, color, material, transparency, structure, layers, thickness, corners, edges, texture, pattern, text, and digits. Never add, remove, replace, redraw, recolor, resize, stretch, compress, merge, or invent structure. Do not guess details the reference cannot confirm.
- Allowed: redesign placement, camera angle, composition, background, lighting, and scene. The product body must never change.

Variable Inputs (batch template - fill per SKU):
[SKU Category]   : {category_path}
[Product Name]   : {title}
[Key Features]   : {value_evidence}
[Scene Scenario] : {scene_plan}
[Color Palette]  : {visual_style} / {background_plan}
Verified material evidence: {verified_material_evidence}

Global Visual Rules (fixed):
- Follow the provided quality benchmark: dark or editorial premium scene when suitable, product placed large but never cramped, controlled glow, clear transparent/material edge highlights, polished surface reflections, and enough breathing room around the product.
- Safe composition: effective content covers 72%-88% of each panel; keep 8%-12% inner safe margin on all sides and around the center divider. Avoid large empty areas, but never crop, clip, or let product/props touch panel edges (except a dimension panel may use more whitespace).
- Differentiated backgrounds: no monotonous cream-white / light-gray / plain white stone. Match the background style to [SKU Category] (walnut wood, micro-cement, editorial, coastal, garden party, glass reflections, etc.) but never compete with the product for the visual center.
- Brand consistency: keep one cohesive brand tone across the set, but no two panels may look like the same template with a swapped background.
- Typography is rendered locally after the grid is split. Generate NO letters, words, numbers, labels, slogans, badges, logos, watermarks, arrows, UI, rulers, or measurement marks anywhere in the AI image. Preserve only markings that are physically printed on the real sellable product in the reference.
- Forbidden: added Chinese or English text, brand names, logos, watermarks, infringing elements, AI gibberish, or malformed hands.

Premium feel & material polish (fixed - make every panel look expensive and well-finished):
- Light is the star: use directional side light or soft window light so the material shows natural luster, subtle highlights, layered shadows and craft detail; never flat, harsh or plasticky lighting.
- Texture first: deliberately show surface grain, weave, stitching, metal finish, glass refraction, edge polish, transparent thickness, enamel shine, leather pores, ceramic glaze, or wood grain whenever those details are visible in the reference.
- Gloss & highlight control: add realistic specular highlights, rim light on edges, contact shadows, soft reflections and micro-contrast so the product looks tactile and premium; avoid muddy shadows, dull surfaces, overexposed whites, flat screenshots, plastic-looking renders, or waxy AI texture.
- Lens & depth realism: use premium product-photography optics (natural perspective, crisp focal product, gentle depth of field only in background/props); the product itself must stay sharp and readable.
- Tone control: keep one cohesive low-saturation premium palette per set; colors must feel curated, not clashing or garish.
- Refined props: any prop must look intentional and high-end (real wood, stone, linen, brass, glass, ceramics); no cheap plastic-looking staging.

One square exact four-panel 2x2 e-commerce grid with clean straight dividers, generated for: {title}

Output contract (fixed - preserve existing backend logic):
- Generate ONE single square image only: a 2x2 four-panel grid. Do NOT generate separate images, do NOT output a carousel, do NOT output a collage with more or fewer panels.
- This one generated grid image will be split by backend code after generation, so the four equal panels and straight center dividers are mandatory.
- Important: the single square image is only a transport container. It must look like FOUR fully independent finished listing images placed in a 2x2 layout, not one continuous poster chopped into four pieces.

Grid construction rules (fixed - clean edges after splitting):
- The horizontal and vertical cut boundaries must be EXACTLY at the center (50%/50%) of the image. Draw a neutral light-gray separator on both boundaries, 0.4%-0.8% of the full image wide, straight, uniform, and uninterrupted from edge to edge. This separator is mandatory and is validated before splitting.
- All four panels must be exactly equal in size; no content may cross or touch the divider lines.
- Each panel must work independently when the grid is cut: its own clear subject, its own background, its own lighting, no dangling props, and no half-composed elements at the panel edges.
- Keep all product parts and important props at least 8% away from the panel border and divider; long products must be angled or scaled down enough to stay fully inside the panel.
- Absolutely forbidden: any global headline, banner, background shape, product, box, prop, shadow, table, frame, or sentence that continues across two or more panels. No half words at panel edges. No shared poster title outside an individual panel.
- No borders, no double lines, no rounded corners on the outer edge of the grid.

Panel 1 - Hero Image (top-left):
- Show the complete sellable product or complete verified set; no cropped parts and no partial stacking that hides quantity or structure. Product occupies 68%-82% with a balanced marketplace hero composition.
- Place the product slightly off-center so it breathes; keep the full product inside the safe area, never touching or clipping the panel edges.
- Side-backlight or premium commercial photography light; emphasize material, structure, thickness, transparency, and edge details.
- Background clearly different from the plain white template.
- No AI-generated copy. No headline, fact card, or typography is added after splitting.

Panel 2 - Editorial/Detail Image (top-right):
- Must differ from Panel 1 in at least 3 of: background main color, surface material, angle, arrangement, props, lighting.
- Style options: Editorial, Modern Classic, Organic Modern, Art Deco, Coastal, etc.
- Keep the complete product visible at 55%-70%, plus at most one small inset close-up of a real detail. A pure macro crop without the complete product is forbidden.
- No AI-generated copy or labels.

Panel 3 - Lifestyle Image (bottom-left):
- Place the product in a real American home scene matching [SKU Category] (living room, sunroom, Game Night, Brunch, etc.).
- May add realistic adult hands (must be natural, no deformities), cups, snacks, tablecloth, plants; the product must stay sharp and exactly the original SKU.
- Lighting: natural window light, afternoon side light, or warm home lighting that wraps the product in soft highlights.
- Keep the complete product unobstructed and prominent. No AI-generated copy or scene phrase.

Panel 4 - Dimension Annotation Background (bottom-right):
- Create a clean front, side, or top view that is suitable for later deterministic dimension annotation.
- Keep the complete product sharp and leave 12%-18% clear space around it.
- Never render measurements, numbers, units, dimension lines, arrows, rulers, scales, labels, or size claims.
- If no useful orthographic view is possible, create a clean alternate product angle with the same empty safe area.

Generate-then-self-check (fixed):
- Confirm product quantity, silhouette, proportions, structure, color, material, transparency, texture, edges, and accessory count all match the reference.
- If any SKU error is found, fix the product body first, then adjust background and polish.
- Final goal: SKU-accurate, instantly recognizable, differentiated backgrounds, full premium composition, matching US consumer taste.

Grid & splitting infrastructure (fixed - do not change):
- Keep an exact four-panel 2x2 grid with clean straight dividers. Do not change the four-grid structure, divider layout, or split logic.
- Each panel must be an independent standalone marketplace carousel image with its own complete mini-layout; never merge panels into one continuous scene and never share props, typography, subject, shadow, table surface, or background across panels.
- Official authenticity rules: the sellable product must be complete, sharp, prominent, and unobstructed in every panel. Do not show only a packaging bag unless the product being sold is packaging bags. Do not crop away key attributes, hide important parts behind props/text/hands, make the product tiny, blur it, use an unrelated background, or perform deceptive Photoshop-style edits.
- Strict rules: differentiate panels only through style, lighting, background, scene, and composition; do not repeat or lightly recolor the same panel. Do not change the product itself. Do not invent material, dimensions, functions, accessories, certifications, brand, or claims. Generate no added text, arrows, UI, logo, watermark, price, discount badge, certification badge, medical claim, exaggerated claim, or promotional text. If the product itself contains decorative characters, symbols, or patterns, keep them only as product design.
- Final text rule: zero AI-added visible text. Realistic, bright, sharp, clean, marketplace-ready for US/EU shoppers."""


DETAIL_IMAGE_PROMPT = """Use the reference image as the non-negotiable source of truth. Preserve the same product type, shape, color, material, pattern, quantity, proportions, structure, and visible details. Do not redesign or change the product itself.

Create one square e-commerce detail poster for: {title}
Category path: {category_path}
Value evidence from source: {value_evidence}
Verified material evidence: {verified_material_evidence}

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


GRID_IMAGE_REPAIR_PROMPT = """The attached image is an exact 2x2 product grid, but it failed the marketplace split-quality gate because it contains AI-added typography or a continuous cross-panel poster layout.

Repair the existing grid without changing the real sellable product:
- Remove every AI-added headline, sentence, label, number, slogan, badge, logo, watermark, arrow, ruler, and UI mark. Preserve only markings physically printed on the real product in the source reference.
- Keep four equal standalone product photos with exact cut boundaries at 50% horizontal and 50% vertical.
- Draw a neutral light-gray separator on both exact center boundaries, 0.4%-0.8% of the full image wide, straight, uniform, and uninterrupted from edge to edge.
- No product, prop, shadow, table surface, background shape, or text may cross a cut boundary.
- Every panel must show the complete sellable product or verified complete set, sharp and unobstructed. A detail view may use one small inset only when the complete product remains visible.
- Panel 1 is a balanced, product-dominant marketplace hero image. Do not write into it.
- Panel 4 remains clean and text-free for deterministic dimension annotations.

Output only the repaired square grid image."""


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


GRID_IMAGE_PROMPT_B = """Role & Core Mission (fixed):
You are a senior visual art director serving TEMU, TikTok Shop, and Amazon US listings. Build on the reference image's model logic but fully upgrade originality. First build a 【character persona + spatial story】 for the product, then shoot it; reject generic studio backgrounds. The final goal: the product looks like an independent designer piece that is hard to reverse-image-search for price comparison.

Core Philosophy (fixed - anti-price-comparison core):
Execution iron rule: 【anti-price-comparison / uniqueness > character & spatial storytelling > premium material feel > creative composition > product accuracy】. (Product accuracy is still the bottom line, but the visual packaging must be full of story.)

Product Integrity Constraints (fixed - safety red line):
Lock the reference image's silhouette, proportions, color, material, structure, thickness, and texture before generating. Never add, remove, replace, recolor, resize, stretch, or deform. Redesign the model pose, scene, lighting, props, and composition freely, but the product body must never change.

Variable Inputs (batch template - fill per SKU):
[SKU Category]     : {category_path}
[Product Name]     : {title}
[Key Features]     : {value_evidence}
[Scene Scenario]   : {scene_plan}
[Color Palette]    : {visual_style} / {background_plan}
Verified material evidence: {verified_material_evidence}

Self-invented story inputs (create internally per SKU, do not ask the user):
- [Target Vibe]      : invent a precise mood for this SKU (e.g. quiet intellectual, lazy old-money, avant-garde art, minimalist zen luxury).
- [Character Persona]: invent a believable persona whose life the scene reflects (e.g. independent curator, traveling writer, architect, gallery owner).
- [Hero Scene]       : invent one distinctive narrative location (e.g. private art study, end of a light hallway, wabi-sabi living room, penthouse window).
- [Styling Props]    : invent non-standard private props (e.g. collected art books, one-off ceramics, Belgian linen, natural mineral specimens).

Global Visual Rules (fixed - anti-price-comparison / premium logic):
- Scene uniqueness: no generic studio backgrounds. Always use the [Hero Scene]; the scene must be narrative and show traces of [Character Persona]'s life.
- Premium props: props must be non-standard private objects (custom jewelry box, imported bouquet, designer objet, rare fabric) to strengthen an "hard to copy" private premium feel.
- Composition & cropping: editorial magazine framing (magazine spread, artist portrait); crop "break the frame" - never show the full model, only advanced partial relationships between product and person.
- Safe composition: keep the sellable product, model-product interaction, and any important prop fully inside each panel with 8%-12% inner safe margin; crop the model creatively if needed, but never crop or clip the product itself.
- Material emphasis: light must define the product's texture, luster, layers, and craft details; use side light or window light to make the material look expensive.
- Luxury luster: each panel must show one refined highlight - soft sheen on metal/leather, glass edge light, fabric nap, polished hardware, enamel glow, ceramic glaze, or wood grain catching the light; never flat plastic look; every surface must feel expensive and tactile.
- Editorial realism: use believable camera optics, natural skin/hand anatomy, cinematic but clean color grading, gentle depth of field, and controlled shadows. Avoid waxy AI skin, generic catalog posing, distorted fingers, muddy low-contrast surfaces, over-smoothed product texture, or fake CGI shine.
- Detail placement: the single most premium real detail of the SKU must be placed at a clear focal point (macro zone, golden-ratio spot); nothing dangles at the panel edges.
- Forbidden (strict): no AI-added text, logo, brand name, watermark, label, badge, number, slogan, arrow, ruler, or distortion in any panel. Preserve only markings physically printed on the real sellable product.

One square exact four-panel 2x2 e-commerce grid with clean straight dividers, generated for: {title}

Output contract (fixed - preserve existing backend logic):
- Generate ONE single square image only: a 2x2 four-panel grid. Do NOT generate four separate images, do NOT output a carousel, do NOT output a collage with more or fewer panels.
- This one generated grid image will be split by backend code after generation, so the four equal panels and straight center dividers are mandatory.
- Important: the single square image is only a transport container. It must look like FOUR fully independent finished listing/editorial images placed in a 2x2 layout, not one continuous poster chopped into four pieces.

Grid construction rules (fixed - clean edges after splitting):
- The horizontal and vertical cut boundaries must be EXACTLY at the center (50%/50%) of the image. A clean uniform separator may be used, but it must be no wider than 0.8% of the full image.
- All four panels must be exactly equal in size; no content may cross or touch the divider lines.
- Each panel must work independently when the grid is cut: its own clear subject, its own background, its own lighting, no dangling props or half-composed elements at the panel edges.
- Keep the product at least 8% away from panel borders and dividers; long or thin products must be angled or scaled down enough to avoid any local split/crop loss.
- Absolutely forbidden: any global headline, banner, background shape, product, model body, prop, shadow, table, frame, or sentence that continues across two or more panels. No half words at panel edges.
- No borders, no double lines, no rounded corners on the outer edge of the grid.

Panel 1 - Hero Shot (top-left): the best moment the persona wears/uses the SKU.
- Framing: magazine-cover close-up or medium shot (only collarbone, wrist, side profile), emphasizing the relationship between product and person.
- Scene & light: locked in the [Hero Scene]; natural window light, afternoon slanting sun, or moody wall lamp for a storytelling feel.
- Narrative: the visual center stays on the product; ambient light and [Styling Props] must reinforce the [Target Vibe].

Panel 2 - Complete Product + Integrated Detail (top-right): show the complete product at 55%-70% and emphasize one real material, edge, hardware, or structure detail through camera angle, lighting, or depth of field inside one unified scene.
- A pure macro crop without the complete sellable product is forbidden. Do not add an inset frame, split-panel border, nested collage, or second divider inside this panel.

Panel 3 - Credible Lifestyle (bottom-left): show the complete product in a believable use or display context supported by the source category.
- Do not invent packaging, storage cases, accessories, cards, ribbons, or set contents that are absent from the reference.

Panel 4 - Dimension Annotation Background (bottom-right):
- Create a clean front, side, or top view that is suitable for later deterministic dimension annotation.
- Keep the complete product sharp and leave 12%-18% clear space around it.
- Never render measurements, numbers, units, dimension lines, arrows, rulers, scales, labels, or size claims.
- If no useful orthographic view is possible, create a clean alternate product angle with the same empty safe area.

Generate-then-self-check (fixed):
- Product integrity check: confirm the product body (proportions, color, structure) strictly matches the reference - no stretching or deformation.
- Anti-price-comparison check: strong editorial-magazine feel? Clearly not a plain white-background or generic studio image?
- Premium material check: are luster and texture fully defined by light, looking worth a higher verification price?
- Violation check: reconfirm no text, logo, watermark, or AI distortion anywhere.

Grid & splitting infrastructure (fixed - do not change):
- Keep an exact four-panel 2x2 grid with clean straight dividers. Do not change the four-grid structure, divider layout, or split logic.
- Each panel must be an independent standalone marketplace carousel image with its own complete mini-layout; never merge panels into one continuous scene and never share props, subject, shadow, table surface, model crop, or background across panels.
- Official authenticity rules: the sellable product must be complete, sharp, prominent, and unobstructed in every panel. Do not crop away key attributes, hide important parts behind props/text/hands, make the product tiny, blur it, use an unrelated background, or perform deceptive Photoshop-style edits.
- Strict rules: differentiate panels only through story, style, lighting, scene, and composition; do not repeat or lightly recolor the same panel. Do not change the product itself. Do not invent material, dimensions, functions, accessories, certifications, brand, or claims. No added text overlays, labels, arrows, UI, logo, watermark, price, discount badge, certification badge, medical claim, exaggerated claim, or promotional text. If the product itself contains decorative characters, symbols, or patterns, keep them only as product design.
- Final text rule: zero AI-added visible text. Preserve only markings physically printed on the real sellable product. Realistic, bright, sharp, clean, marketplace-ready for US/EU shoppers."""


DEFAULT_PROMPTS: dict[str, str] = {
    "title": TITLE_PROMPT,
    "desc": DESC_PROMPT,
    "size": SIZE_PROMPT,
    "grid_image": GRID_IMAGE_PROMPT,
    "grid_image_b": GRID_IMAGE_PROMPT_B,
    "detail_image": DETAIL_IMAGE_PROMPT,
    "image_repair_chinese": IMAGE_REPAIR_CHINESE_PROMPT,
    "image_repair_grid": GRID_IMAGE_REPAIR_PROMPT,
    "combined_text": COMBINED_TEXT_PROMPT,
    "variant_values": VARIANT_VALUE_TRANSLATION_PROMPT,
}


# 生图提示词模板注册表：处理设置里用户勾选 A/B，直观标题区分两套生图逻辑。
IMAGE_TEMPLATES: list[dict[str, str]] = [
    {
        "id": "A",
        "name": "标准商品海报",
        "description": "高级电商视觉：四张独立完整构图，画面零新增文字，避免拆图裁字",
    },
    {
        "id": "B",
        "name": "高端模特视觉（防比价）",
        "description": "人设+空间故事叙事、杂志编辑大片感，材质显贵、难以搜图比价，画面无文字",
    },
]


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
        "visual_style": "premium realistic e-commerce editorial style, low-saturation curated palette, tactile commercial photography rather than plain studio catalog",
        "lighting_plan": "directional soft window light or controlled commercial side light with rim highlights, natural shadows, soft reflections, and clear material luster",
        "material_plan": "preserve the exact real material and surface texture of the product; emphasize visible edge polish, grain, weave, gloss, transparency, stitching, hardware, or surface finish without inventing material",
        "background_plan": "category-matched premium scene surface such as walnut wood, micro-cement, linen, ceramic, glass reflection, editorial tabletop, or tasteful home setting; never plain white/gray template",
        "composition_plan": "product complete, sharp, prominent, and premium-framed; use golden-ratio placement, realistic contact shadows, and at least one panel showing the full product",
        "scene_plan": "high-end lifestyle scene suitable for this product category, using intentional real-world props such as wood, stone, linen, brass, glass, ceramics, plants, or home objects that do not obscure the SKU",
        "video_shot_plan": "four clearly distinct premium frames (hero, macro detail, lifestyle scene, alternate angle or size/packaging proof) that each work as a standalone carousel image",
        "detail_plan": "one hero scene plus one close-up of the most visible real detail of the product",
    }


def _value_evidence_default(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(title or "").strip())[:400]
    return (
        f"Verified source title facts only: {cleaned}."
        if cleaned
        else "No additional source facts beyond the reference image."
    )
