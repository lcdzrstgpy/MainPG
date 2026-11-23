"""精选处理的提示词层：文案生成（豆包文本）与照片层（Seedream 生图）提示词。

双层合成的分工再次强调：
- 文案层（这里）：豆包出英文标题/副文案 → premium_composer 确定性渲染，绝不让
  生图模型写字。
- 照片层（这里）：Seedream 只出照片，提示词**禁渲染文字**并按版式预留文案区。
"""

from __future__ import annotations

import json
import re
from typing import Any

from .premium_composer import MarketingCopy


# 生图照片层的公共合同：产品保真 + 禁文字 + 真实摄影质感。
PHOTO_CONTRACT = """This is a professional studio product photograph of the exact product in the reference images.

1. Copy the product faithfully from the reference: same silhouette, proportions, colours, materials, structure, markings and part count. Every jaw, blade, tooth, serration, hinge, rivet, screw, spring and joint must be reproduced with identical geometry — nothing merged, melted, smoothed away or invented.
2. The product is the only object in frame. The surface it stands on is the only other element: no wires, tools, packaging, loose parts, hands or people.
3. Zero text of any kind: no letters, numbers, labels, logos, watermarks. Typography is added later.
4. Look like a real studio photograph: soft key light with shaped highlights, natural contact shadow, true-to-life materials, slight depth falloff, photographic grain. No illustration, no 3D render look.
"""

# 各版式的照片层构图要求（文案区预留）。
PHOTO_LAYOUTS: dict[str, str] = {
    "headline_hero": (
        "COMPOSITION: dramatic square product shot on a dark charcoal studio background. "
        "Keep the upper 35% of the frame clean and uncluttered (plain dark backdrop) as copy space; "
        "the product fills the lower 60% at a dynamic three-quarter angle, standing or resting naturally on the surface. "
        "No action props beyond what the scene plan allows."
    ),
    "action_proof": (
        "COMPOSITION: square macro close-up of the product's working end (the head / tip / key detail), "
        "product closed and at rest, placed lower-right, sharp focus on the mechanism, "
        "no action, no loose objects, ONLY the product. "
        "Keep the upper-left 45% of the frame darker and uncluttered as copy space."
    ),
    "icon_grid": (
        "COMPOSITION: clean square product hero on a dark background, product centred and fully visible in the middle band "
        "(35%-60% of frame height). Keep the top 15% and the bottom 38% uncluttered (plain dark surface) — feature chips will be "
        "overlaid there later. No props near the bottom area."
    ),
    "material_strip": (
        "COMPOSITION: square product shot at a three-quarter angle on a dark workbench, product fills the upper two thirds "
        "with sharp focus on the product's key surface detail. Keep the upper-left 35% uncluttered for a headline and the bottom "
        "15% a plain dark surface for material pills. No extra props in the bottom band (names are rendered later as text pills)."
    ),
    "use_cases": (
        "COMPOSITION: square product hero in the centre of a dark studio frame, product fully visible and centred. "
        "Keep all four corners darker and empty (plain background) — use-case labels will be overlaid in the corners later. "
        "Keep the top 15% uncluttered for a headline."
    ),
}

_COPY_SYSTEM = (
    "You are a senior e-commerce listing copywriter for the US market. "
    "You write punchy, factual marketing copy for marketplace listing images. "
    "Never use absolute claims (best, No.1, #1, guaranteed, 100%, perfect). "
    "Never invent product facts that are not given to you."
)

_COPY_USER = """Write marketing copy for a listing image of this product.

Product title: {title}
Category: {category}
Image layout: {layout_hint}

Requirements:
- headline: ONE punchy English phrase of 4 to 8 words. It may use a line break to split into two balanced lines. No period at the end.
- subline: ONE supporting sentence of 8 to 16 words that states a concrete benefit or material fact. No period at the end.
- bullets: 6 SHORT feature labels, each 1 to 3 words, Title Case, factual, no punctuation (e.g. "Hardened Steel", "Comfort Grip", "Safety Lock", "Rust Proof"). Used on feature-chip layouts.
- All must be plain ASCII English. No emojis, no quotes, no hashtags.

Output ONLY a JSON object: {{"headline": "...", "subline": "...", "bullets": ["...", "..."]}}"""

_LAYOUT_HINTS = {
    "headline_hero": "big centered headline poster; subline sits under the headline",
    "action_proof": "action shot; headline top-left over the darker area",
}


def build_copy_messages(title: str, category: str, template: str) -> list[dict[str, Any]]:
    layout_hint = _LAYOUT_HINTS.get(template, "premium listing image")
    return [
        {"role": "system", "content": _COPY_SYSTEM},
        {
            "role": "user",
            "content": _COPY_USER.format(
                title=str(title or "the product").strip()[:200],
                category=str(category or "general").strip()[:120],
                layout_hint=layout_hint,
            ),
        },
    ]


def parse_copy_response(text: str) -> MarketingCopy | None:
    """容错解析豆包返回的 JSON 文案；拿不到就返回 None（调用方走保底文案）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    headline = str(data.get("headline") or "").strip()
    bullets_raw = data.get("bullets")
    bullets = []
    if isinstance(bullets_raw, list):
        bullets = [str(item or "").strip() for item in bullets_raw if str(item or "").strip()][:6]
    subline = str(data.get("subline") or "").strip()
    if not headline:
        return None
    # JSON 里允许用 \n 控制断行；清洗掉引号残留
    headline = headline.replace("\\n", "\n").strip('" ')
    subline = subline.replace("\\n", "\n").strip('" ')
    return MarketingCopy(headline=headline, subline=subline, bullets=bullets)


def fallback_copy(title: str) -> MarketingCopy:
    """豆包失败时的保底文案：标题截短做 headline，不编造卖点。"""
    headline = str(title or "Quality Product").strip() or "Quality Product"
    words = headline.split()
    headline = " ".join(words[:8])
    return MarketingCopy(headline=headline, subline="")


_SCENE_SYSTEM = (
    "You are a product-photography art director. You design a MINIMAL, physically plausible scene plan "
    "for a single e-commerce listing photo of a real product."
)

_SCENE_USER = """Design the photo scene plan for this product (surface only, no action, no props).

Product title: {title}
Category: {category}
Product understanding: {subject}

Return a JSON object:
- "surface": one short phrase for the surface it stands on (e.g. "dark walnut workbench", "brushed steel table", "matte stone slab").
- "action": always "" (the product is shown alone at rest — action shots break product fidelity).
- "props": always [] (the product is the only object in frame).

Rules: no text, no hands, no people, no props, nothing floating.
Output ONLY the JSON object: {{"surface": "...", "action": "", "props": []}}"""


def build_scene_messages(title: str, category: str, subject: str = "") -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _SCENE_SYSTEM},
        {
            "role": "user",
            "content": _SCENE_USER.format(
                title=str(title or "the product").strip()[:200],
                category=str(category or "general").strip()[:120],
                subject=str(subject or "").strip()[:400] or "not provided — infer conservatively from title and category",
            ),
        },
    ]


def parse_scene_plan(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    surface = str(data.get("surface") or "").strip()
    action = str(data.get("action") or "").strip()
    props_raw = data.get("props")
    props: list[str] = []
    if isinstance(props_raw, list):
        props = [str(item or "").strip() for item in props_raw if str(item or "").strip()][:2]
    if not surface:
        return None
    return {"surface": surface, "action": action, "props": props}


def build_photo_prompt(template: str, copy: MarketingCopy, scene: dict[str, Any] | None = None) -> str:
    """照片层提示词：合同 + 版式构图（含文案区预留）。不含任何要渲染的文字。"""
    layout = PHOTO_LAYOUTS.get(template, PHOTO_LAYOUTS["headline_hero"])
    semantic = f"Overall tone the scene should convey: {copy.headline.replace(chr(10), ' ')[:120]}."
    scene = scene or {}
    surface = str(scene.get("surface") or "a clean matte dark surface").strip()
    # 动作/道具永久禁用：2026-11-23 两轮实测，模型画动作必然穿模/画坏产品。
    action = ""
    props: list[str] = []
    scene_lines = [f"SCENE PLAN: product stands on {surface}."]
    scene_lines.append(
        f"ACTION: {action}" if action else "ACTION: none — the product is shown at rest, no action, no extra objects touching it."
    )
    if props:
        scene_lines.append("PROPS (resting on the surface, not touching the product): " + "; ".join(props) + ".")
    else:
        scene_lines.append("PROPS: none.")
    return f"{PHOTO_CONTRACT}\n{layout}\n" + "\n".join(scene_lines) + f"\n{semantic}"


# ── 出图质检（豆包 2.1 视觉）：比对参考图与生成图，决定是否重渲 ─────────────
_QA_SYSTEM = (
    "You are a strict e-commerce image QA inspector. You compare a reference product photo "
    "with an AI-generated listing image and judge whether the product is faithfully reproduced."
)

_QA_USER = """Image 1 is the REFERENCE product photo. Image 2 is the GENERATED listing image.

Judge the generated image on:
1. same_product: is the object in image 2 unmistakably the same product model as image 1?
2. fidelity: are shape, proportions, part count and mechanism preserved? Any deformed, fused, melted or missing parts?
3. extra_objects: any wires, tools, hands, packaging or loose objects that should not be there?
4. text: any visible letters, numbers, logos or watermarks rendered in the image?

Return ONLY JSON: {{"same_product": true|false, "fidelity_ok": true|false, "extra_objects": true|false, "has_text": true|false, "issues": "<short English description of the main defect, or empty>"}}"""


def build_qa_messages(reference_data_url: str, generated_data_url: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _QA_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": reference_data_url}},
                {"type": "image_url", "image_url": {"url": generated_data_url}},
                {"type": "text", "text": _QA_USER},
            ],
        },
    ]


def parse_qa_response(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if "same_product" not in data:
        return None
    ok = bool(data.get("same_product")) and bool(data.get("fidelity_ok", True)) and not bool(
        data.get("extra_objects")
    ) and not bool(data.get("has_text"))
    return {
        "ok": ok,
        "same_product": bool(data.get("same_product")),
        "fidelity_ok": bool(data.get("fidelity_ok", True)),
        "extra_objects": bool(data.get("extra_objects")),
        "has_text": bool(data.get("has_text")),
        "issues": str(data.get("issues") or "").strip()[:300],
    }
