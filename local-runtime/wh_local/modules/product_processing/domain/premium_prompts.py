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
PHOTO_CONTRACT = """HARD RULES:
- The reference images are the ONLY source of truth for the product. Keep silhouette, proportions, colours, materials, structure, printed markings and every physical detail IDENTICAL. Only scene, background, camera angle and lighting may change.
- MECHANICAL FIDELITY (red line): reproduce every jaw, blade, tooth, serration, bevel, hinge, rivet, screw, spring, coil, pivot and joint with the same count, same position and same geometry as the reference. Serrations must keep their exact number and spacing. NEVER melt, blur, merge, simplify, smooth away or invent any part; a deformed, blobby or fused jaw is a failed image and must be re-rendered. Keep the tool in the same open/closed state as the reference.
- PHYSICAL PLAUSIBILITY (red line): no object may pass through, pierce, float around or intersect the product; every prop either rests on the surface or is genuinely gripped between the jaws. No floating fragments, no impossible angles, no extra loose parts. When in doubt, render FEWER props.
- Render ZERO text of any kind: no letters, words, numbers, labels, logos, watermarks or UI elements. Text will be added later by a layout engine.
- Realistic commercial product photography, physically plausible light and shadows, sharp focus, no illustration or 3D-render look, no hands or people.
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
        "COMPOSITION: square close-up on a dark workbench, product in the lower-right two thirds, "
        "performing the SINGLE action described in the scene plan (if the scene plan allows none, show the product "
        "at rest with one small related prop beside it). "
        "The action must be physically correct end-to-end: every prop is either resting on the surface or genuinely "
        "held/clamped by the product — never passing through it, never disconnected, never floating. "
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

_SCENE_USER = """Design the photo scene plan for this product.

Product title: {title}
Category: {category}
Product understanding: {subject}

Return a JSON object:
- "surface": one short phrase for the surface it stands on (e.g. "dark walnut workbench", "brushed steel table", "matte stone slab").
- "action": either "" (no action; product shown at rest) or ONE simple, physically obvious action that this exact product performs BY ITSELF, described in one sentence with the prop it uses (e.g. "a straight steel wire clamped inside the jaws, both cut ends falling below"). Only include an action if it is trivially correct to render; when unsure return "". Never invent accessories the product does not have.
- "props": 0 to 2 small related props that rest on the surface and do not touch the product (e.g. "coil of copper wire", "wood shavings"). No props that could be confused for product parts.

Rules: no text, no hands, no people, no packaging, nothing floating. Prefer FEWER props.
Output ONLY the JSON object: {{"surface": "...", "action": "...", "props": ["..."]}}"""


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
    action = str(scene.get("action") or "").strip()
    props = [str(item) for item in (scene.get("props") or []) if str(item or "").strip()][:2]
    scene_lines = [f"SCENE PLAN: product stands on {surface}."]
    scene_lines.append(
        f"ACTION: {action}" if action else "ACTION: none — the product is shown at rest, no action, no extra objects touching it."
    )
    if props:
        scene_lines.append("PROPS (resting on the surface, not touching the product): " + "; ".join(props) + ".")
    else:
        scene_lines.append("PROPS: none.")
    return f"{PHOTO_CONTRACT}\n{layout}\n" + "\n".join(scene_lines) + f"\n{semantic}"
