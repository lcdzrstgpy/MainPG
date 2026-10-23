"""核价采集 ↔ 商品主档的匹配规则（纯函数，便于单测）。

匹配优先级（2026-10-22 与产品确认）：
1. 平台货号 ``productSkcExtCode``（= 我们生成的 PP-xxxxxx）精确命中；
2. 上架标题规范化后完全相等；
3. 标题相似度 ≥ 0.90 → 待确认（列出候选，绝不自动入库）；
4. 其余 → 未匹配，进待确认列表。
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

# 相似度阈值：只用于「待确认」候选，不用于自动匹配。
TITLE_SIMILARITY_THRESHOLD = 0.90

# 去标点/空白时保留中文与字母数字；其余字符（空格、逗号、括号、连字符）一律折叠。
_NON_WORD_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def code_key(value: Any) -> str:
    """货号比较键：全半角归一 + 去空白 + 大写。"""
    return unicodedata.normalize("NFKC", str(value or "")).strip().upper()


def title_key(value: Any) -> str:
    """标题指纹：全半角归一 + 大小写折叠 + 去标点与空白。"""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return _NON_WORD_RE.sub("", text)


def title_similarity(left: Any, right: Any) -> float:
    """两个标题的相似度（0~1），已做过归一化。"""
    a, b = title_key(left), title_key(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def title_candidates(
    title: Any,
    options: list[tuple[str, str]],
    *,
    threshold: float = TITLE_SIMILARITY_THRESHOLD,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """在 ``(skc, title)`` 候选集里找相似标题，返回按相似度降序的前 N 个。"""
    scored: list[dict[str, Any]] = []
    for skc, candidate_title in options:
        score = title_similarity(title, candidate_title)
        if score < threshold:
            continue
        scored.append(
            {
                "skc": skc,
                "title": candidate_title,
                "similarity": round(score, 4),
            }
        )
    scored.sort(key=lambda item: item["similarity"], reverse=True)
    return scored[:limit]
