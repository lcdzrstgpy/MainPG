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
# 描述必须直接陈述商品事实，禁止转述来源图/参考图内容（如 “The reference image shows ...”）。
_META_COMMENTARY = re.compile(
    r"(?:the\s+)?(?:reference|source)\s+image\s+(?:shows?|displays?|depicts?|illustrates?)"
    r"|\b(?:the\s+)?(?:image|picture|photo)\s+(?:shows?|displays?|depicts?|illustrates?)"
    r"|\b(?:as\s+shown|in\s+the\s+(?:image|picture|photo))\b"
    r"|展示图|参考图|图片中|图中",
    re.IGNORECASE,
)
# 单行描述里「新要点」的边界：前一句以 .!? 结尾，随后是 1-6 词的标题 + 分隔符。
# 模型偶尔把 5 条卖点写成一整段（'LABEL: body. LABEL: body. ...'），而不是换行分点。
_POINT_BOUNDARY = re.compile(
    r"(?<=[.!?])\s+(?=[A-Z][A-Za-z0-9'&]*(?: [A-Za-z0-9'&]+){0,5}\s*[-:：—])"
)


def _split_inline_points(raw: str) -> str:
    """把单行内句号分隔的多个「LABEL: body」要点拆成多行再校验。

    仅当整段不含换行且能识别出多个要点边界时才拆分；单要点段落、
    普通段落或已换行的输入保持原样，避免误拆分正文中的普通句子。
    """
    if "\n" in raw:
        return raw
    parts = _POINT_BOUNDARY.split(raw.strip())
    if len(parts) <= 1:
        return raw
    return "\n".join(part.strip() for part in parts)


class DescriptionContractError(ValueError):
    pass


def normalize_five_point_description(value: str) -> str:
    """归一化描述并做最简结构校验（用户确认：仅保留基本底线）。

    底线：非空、纯英文、无内部占位文案、长度 ≤1000 字符、要点不重复、
    必须恰好 5 条、禁止转述「参考图/展示图」等元语言；
    词数（40-180）与标题「heading: body」格式不再强制。
    """
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    # 单行多要点（'LABEL: body. LABEL: body. ...'）先拆行，再走五点校验，
    # 避免模型把 5 条卖点写成一整段时被「必须恰好 5 条」误判为失败。
    raw = _split_inline_points(raw)
    if _INTERNAL_FALLBACK.search(raw):
        raise DescriptionContractError("description contains an internal fallback message")
    if _META_COMMENTARY.search(raw):
        raise DescriptionContractError("description must describe the product directly, not the reference/source image")
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

    if len(normalized) != 5:
        raise DescriptionContractError("description must contain exactly five selling points")
    result = "\n".join(normalized)
    if len(result) > 1000:
        raise DescriptionContractError("description must not exceed 1000 characters")
    return result
