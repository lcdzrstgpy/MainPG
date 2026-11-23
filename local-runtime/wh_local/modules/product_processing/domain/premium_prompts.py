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
- Render ZERO text of any kind: no letters, words, numbers, labels, logos, watermarks or UI elements. Text will be added later by a layout engine.
- Realistic commercial product photography, physically plausible light and shadows, sharp focus, no illustration or 3D-render look, no hands or people.
"""

# 各版式的照片层构图要求（文案区预留）。
PHOTO_LAYOUTS: dict[str, str] = {
    "headline_hero": (
        "COMPOSITION: dramatic square product shot on a dark charcoal studio background. "
        "Keep the upper 35% of the frame clean and uncluttered (dark backdrop only) as copy space; "
        "the product fills the lower 60% at a dynamic three-quarter angle, optionally cutting a thin steel wire."
    ),
    "action_proof": (
        "COMPOSITION: square action close-up — the product actively performing its strongest use case "
        "(e.g. cutting a steel wire / rope / cable) with crisp detail on the working edge. "
        "Keep the upper-left 45% of the frame darker and uncluttered as copy space; action happens centre-right and lower."
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
- Both must be plain ASCII English. No emojis, no quotes, no hashtags.

Output ONLY a JSON object: {{"headline": "...", "subline": "..."}}"""

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
    subline = str(data.get("subline") or "").strip()
    if not headline:
        return None
    # JSON 里允许用 \n 控制断行；清洗掉引号残留
    headline = headline.replace("\\n", "\n").strip('" ')
    subline = subline.replace("\\n", "\n").strip('" ')
    return MarketingCopy(headline=headline, subline=subline)


def fallback_copy(title: str) -> MarketingCopy:
    """豆包失败时的保底文案：标题截短做 headline，不编造卖点。"""
    headline = str(title or "Quality Product").strip() or "Quality Product"
    words = headline.split()
    headline = " ".join(words[:8])
    return MarketingCopy(headline=headline, subline="")


def build_photo_prompt(template: str, copy: MarketingCopy) -> str:
    """照片层提示词：合同 + 版式构图（含文案区预留）。不含任何要渲染的文字。"""
    layout = PHOTO_LAYOUTS.get(template, PHOTO_LAYOUTS["headline_hero"])
    # 把文案语义喂给照片层，让场景/道具匹配卖点（例如 cutting 型标题配剪切动作）
    semantic = f"Overall tone the scene should convey: {copy.headline.replace(chr(10), ' ')[:120]}."
    return f"{PHOTO_CONTRACT}\n{layout}\n{semantic}"
