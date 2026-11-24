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

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


# ── 照片层提示词（第一版形态：短合同 + 版式构图）─────────────────────────
PHOTO_CONTRACT = """HARD RULES:
- The reference images are the ONLY source of truth for the product. Keep silhouette, proportions, colours, materials, structure, printed markings and every physical detail IDENTICAL. Only scene, background, camera angle and lighting may change.
- Render ZERO text of any kind: no letters, words, numbers, labels, logos, watermarks or UI elements. Text will be added later by a layout engine.
- Realistic commercial product photography, physically plausible light and shadows, sharp focus, no illustration or 3D-render look, no hands or people.
"""

# 各版式的照片层构图要求（文案区预留）。
PHOTO_LAYOUTS: dict[str, str] = {
    "headline_hero": (
        "COMPOSITION: dramatic square product shot on a dark charcoal studio background. "
        "Keep the upper 35% of the frame clean and uncluttered (plain dark backdrop) as copy space; "
        "the product fills the lower 60% at the reference-evidenced angle, standing or resting naturally on the surface. "
        "No action props beyond what the scene plan allows."
    ),
    "action_proof": (
        "COMPOSITION: square macro close-up of the product's working end (the head / tip / key detail), "
        "viewed from the same side as the reference, product closed and at rest, placed lower-right, sharp focus on the mechanism, "
        "no action, no loose objects, ONLY the product. "
        "Keep the upper-left 45% of the frame darker and uncluttered as copy space."
    ),
    "icon_grid": (
        "COMPOSITION: clean square product hero on a dark background, product centred and fully visible in the middle band "
        "(35%-60% of frame height). Keep the top 15% and the bottom 38% uncluttered (plain dark surface) — feature chips will be "
        "overlaid there later. No props near the bottom area."
    ),
    "material_strip": (
        "COMPOSITION: square product shot at the reference-evidenced angle on a dark workbench, product fills the upper two thirds "
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



def build_photo_prompt(template: str, copy: MarketingCopy) -> str:
    layout = PHOTO_LAYOUTS.get(template, PHOTO_LAYOUTS["headline_hero"])
    tone = copy.headline.replace(chr(10), " ")[:120].rstrip(" .;:")
    return f"{PHOTO_CONTRACT}\n{layout}\nOverall tone the scene should convey: {tone}."


def build_card_prompt(label: str) -> str:
    """细节卡小图：产品工作端微距（短合同，产品单独出镜）。"""
    return (
        f"{PHOTO_CONTRACT}\n"
        "COMPOSITION: extreme close-up of this product's working end demonstrating — "
        f"{label}. Square 1:1, shallow depth of field, dramatic studio light on a dark surface, "
        "the product alone in frame."
    )


# ── 编号标注：让视觉模型在产品图上标出关键部位坐标（0-1000 归一化）──────
_CALLOUT_SYSTEM = (
    "You locate key parts on a product photo for callout annotations. "
    "Coordinates are normalized 0-1000 with origin at the top-left of the image."
)

_CALLOUT_USER = """Look at this product image and locate 3 to 5 key parts worth annotating
(e.g. jaws, teeth, ratchet mechanism, pivot, grip, lock lever).

Return ONLY JSON: {"parts": [{"label": "<2-3 word English label>", "x": <0-1000>, "y": <0-1000>}]}

Rules: x/y must point at the part's centre in the IMAGE (not percentages of the product);
spread the points across the product; do not invent parts the product does not have."""


def build_callout_messages(hero_data_url: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": _CALLOUT_SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": hero_data_url}},
                {"type": "text", "text": _CALLOUT_USER},
            ],
        },
    ]


def parse_callout_response(text: str) -> list[dict]:
    raw = str(text or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    parts_raw = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(parts_raw, list):
        return []
    parts: list[dict] = []
    for item in parts_raw[:5]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:28]
        try:
            x = float(item.get("x"))
            y = float(item.get("y"))
        except (TypeError, ValueError):
            continue
        if not (0 <= x <= 1000 and 0 <= y <= 1000):
            continue
        parts.append({"label": label or "Detail", "x": x, "y": y})
    return parts
