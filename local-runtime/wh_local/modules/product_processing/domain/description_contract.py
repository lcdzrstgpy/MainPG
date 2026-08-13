"""Best-effort normalizer for English Amazon-style selling points."""

from __future__ import annotations

import re


_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")
_MARKDOWN_HEADING = re.compile(r"^(?:\*\*|__)\s*(.+?)\s*(?:\*\*|__)")
_POINT = re.compile(
    r"^([A-Za-z][A-Za-z0-9'-]*(?:\s+[A-Za-z0-9][A-Za-z0-9'-]*){1,4})\s*(?::|-)\s+(.+)$"
)
_CHINESE = re.compile(r"[\u3400-\u9fff]")
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
    if not lines:
        raise DescriptionContractError("description must contain at least one usable selling point")

    normalized: list[str] = []
    for index, line in enumerate(lines[:5], start=1):
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
            heading = f"PRODUCT DETAIL {index}"
            body = " ".join(content.split())
        else:
            heading = " ".join(match.group(1).split()).upper()
            body = " ".join(match.group(2).split())
        if not body:
            continue
        normalized.append(f"- {heading}: {body}")

    if not normalized:
        raise DescriptionContractError("description must contain at least one usable selling point")
    result = "\n".join(normalized)
    return result
