"""Best-effort normalizer for English Amazon-style selling points."""

from __future__ import annotations

import re


_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_MARKDOWN_HEADING = re.compile(r"^(?:\*\*|__)\s*(.+?)\s*(?:\*\*|__)")
# 标题大小写不限、1-6 个单词，分隔符支持 ": " " - " "： " "—"（适当放松：
# 模型未按全大写/恰好 2-5 词输出时，不再整段判失败，避免阻塞店小秘导出）。
_POINT = re.compile(r"^([A-Za-z][A-Za-z0-9'&]*(?: [A-Za-z0-9'&]+){0,5})\s*[-:：—]\s+(.+)$")
_CHINESE = re.compile(r"[\u3400-\u9fff]")
_INTERNAL_FALLBACK = re.compile(r"source information preserved|operator review", re.IGNORECASE)


class DescriptionContractError(ValueError):
    pass


def normalize_five_point_description(value: str) -> str:
    """归一化描述并做最简结构校验（用户确认：仅保留基本底线）。

    底线只保留：非空、纯英文、无内部占位文案、长度 ≤1000 字符、要点不重复；
    行数（不再要求 3-5）、词数（不再要求 40-180）、标题「heading: body」格式
    均不再强制，避免模型输出小差异就整单失败。
    """
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if _INTERNAL_FALLBACK.search(raw):
        raise DescriptionContractError("description contains an internal fallback message")
    if _CHINESE.search(raw):
        raise DescriptionContractError("description must be English only")
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if not lines:
        raise DescriptionContractError("description must contain at least one usable selling point")

    normalized: list[str] = []
    bodies: set[str] = set()
    for line in lines:
        content = _BULLET_PREFIX.sub("", line, count=1).strip()
        # Models commonly emit Markdown headings, title case, or a full-width colon.
        # These are presentation-only differences, so canonicalize them locally before
        # applying the substantive five-point, language, distinctness, and length gates.
        content = content.replace("：", ":").replace("–", "-").replace("—", "-")
        content = _MARKDOWN_HEADING.sub(lambda match: match.group(1).strip(), content, count=1)
        content = re.sub(r"\s*:\s*", ": ", content, count=1)
        content = re.sub(r"\s+-\s*", " - ", content, count=1)
        match = _POINT.fullmatch(content)
        if match is None:
            # 无标题分隔符的纯要点行：整行作为正文（适当放松，避免整段失败）。
            heading, body = "", content
        else:
            heading = " ".join(match.group(1).split())
            body = " ".join(match.group(2).split())
        body_key = re.sub(r"[^a-z0-9]+", " ", body.lower()).strip()
        if body_key in bodies:
            raise DescriptionContractError("five selling points must be distinct")
        bodies.add(body_key)
        normalized.append(f"- {heading}: {body}" if heading else f"- {body}")

    if not normalized:
        raise DescriptionContractError("description must contain at least one usable selling point")
    result = "\n".join(normalized)
    if len(result) > 1000:
        raise DescriptionContractError("description must not exceed 1000 characters")
    return result
