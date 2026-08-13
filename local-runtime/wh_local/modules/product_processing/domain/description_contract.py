"""Deterministic output contract for English Amazon-style five key points."""

from __future__ import annotations

import re


_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_MARKDOWN_HEADING = re.compile(r"^(?:\*\*|__)\s*(.+?)\s*(?:\*\*|__)")
_POINT = re.compile(
    r"^([A-Za-z][A-Za-z0-9'-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'-]*){1,4})\s*(?::|-)\s+(.+)$"
)
_CHINESE = re.compile(r"[\u3400-\u9fff]")
_WORD = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9'-]*\b")
_INTERNAL_FALLBACK = re.compile(r"source information preserved|operator review", re.IGNORECASE)


class DescriptionContractError(ValueError):
    pass


def normalize_five_point_description(value: str) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if _INTERNAL_FALLBACK.search(raw):
        raise DescriptionContractError("description contains an internal fallback message")
    if _CHINESE.search(raw):
        raise DescriptionContractError("description must be English only")
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if len(lines) != 5:
        raise DescriptionContractError("description must contain exactly five bullet points")

    normalized: list[str] = []
    headings: set[str] = set()
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
            raise DescriptionContractError("each point must start with a 2-5 word ALL-CAPS heading")
        heading = " ".join(match.group(1).split()).upper()
        body = " ".join(match.group(2).split())
        body_key = re.sub(r"[^a-z0-9]+", " ", body.lower()).strip()
        if heading in headings or body_key in bodies:
            raise DescriptionContractError("five selling points must be distinct")
        headings.add(heading)
        bodies.add(body_key)
        normalized.append(f"- {heading}: {body}")

    result = "\n".join(normalized)
    word_count = len(_WORD.findall(result))
    if not 80 <= word_count <= 150:
        raise DescriptionContractError("description must contain 80-150 English words")
    if len(result) > 1000:
        raise DescriptionContractError("description must not exceed 1000 characters")
    return result
