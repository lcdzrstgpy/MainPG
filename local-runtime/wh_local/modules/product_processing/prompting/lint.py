"""生成前矛盾检查。"""

from __future__ import annotations

import re

from .brief import ProductBrief

RULES = (
    ("small-scale", r"72%-85%|78%-85%", lambda b: b.size_class in {"tiny", "small"}),
    ("soft-upright", r"standing upright|stands upright", lambda b: b.rigidity == "soft"),
    ("headwear-person", r"\bmodel\b|person wearing", lambda b: b.usage == "headwear"),
    ("face", r"human face|visible face|full face", lambda b: True),
    ("season-mismatch", r"pumpkin|christmas tree|tombstone", lambda b: not b.season),
)


def lint_prompt(prompt: str, brief: ProductBrief, style_key: str = "A") -> list[str]:
    """返回 error 列表；空列表表示通过。"""

    errors: list[str] = []
    text = str(prompt or "")
    for name, pattern, applies in RULES:
        if applies(brief) and re.search(pattern, text, re.IGNORECASE):
            errors.append(name)
    if style_key != "A" and re.search(r"pure white seamless", text, re.IGNORECASE):
        errors.append("white-hero-in-scene-style")
    if brief.scale_anchor and not re.search(r"coin|ring box|scale", text, re.IGNORECASE):
        errors.append("missing-scale-anchor")
    if len(text) > 8000:
        errors.append("too-long")
    return errors
