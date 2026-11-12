"""商品英文标题翻译成中文。

Temu 卖家后台的美区商品标题是英文，界面本身没有中文标题，只能自己翻。
这里用「免费公共接口 + 本地 sqlite 缓存」实现：
- 先查缓存，命中就不发请求；
- 未命中按顺序尝试几个免密钥接口，任何一个成功就用；
- 全部失败就返回空字符串，界面继续显示英文原标题，不影响采集。
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_LENGTH = 400
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def needs_translation(text: str) -> bool:
    value = str(text or "").strip()
    if len(value) < 2 or len(value) > MAX_LENGTH:
        return False
    # 已经是中文（或几乎没有拉丁字母）就不用翻。
    return not CJK_RE.search(value)


def _http_json(url: str, timeout: float = 4.0) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _youdao(text: str) -> str:
    """有道词典免密钥接口，返回 JSON。"""
    url = (
        "https://dict.youdao.com/translate?&doctype=json&type=AUTO&i="
        + urllib.parse.quote(text)
    )
    data = _http_json(url)
    rows = (data or {}).get("translateResult") or []
    parts = []
    for row in rows:
        for cell in row or []:
            value = str((cell or {}).get("tgt") or "").strip()
            if value:
                parts.append(value)
    return " ".join(parts).strip()


def _mymemory(text: str) -> str:
    url = (
        "https://api.mymemory.translated.net/get?q="
        + urllib.parse.quote(text)
        + "&langpair=en|zh-CN"
    )
    data = _http_json(url)
    value = str(((data or {}).get("responseData") or {}).get("translatedText") or "").strip()
    return "" if "MYMEMORY WARNING" in value.upper() else value


PROVIDERS = (_youdao, _mymemory)


def translate_one(text: str) -> str:
    for provider in PROVIDERS:
        try:
            value = provider(text)
        except Exception:  # noqa: BLE001 - 翻译是增强项，失败不影响主流程
            continue
        if value:
            return value
    return ""


def translate_many(store: Any, texts: list[str], limit: int = 40) -> dict[str, str]:
    """返回 {英文原文: 中文}，只包含成功翻译的条目。"""
    wanted: list[str] = []
    for raw in texts:
        value = str(raw or "").strip()
        if value and value not in wanted:
            wanted.append(value)
    wanted = wanted[: max(1, min(80, int(limit)))]

    cached = store.get_translations(wanted)
    result = {src: dst for src, dst in cached.items() if dst}
    fresh: dict[str, str] = {}
    for value in wanted:
        if value in result or not needs_translation(value):
            continue
        translated = translate_one(value)
        if translated and translated.strip() != value.strip():
            fresh[value] = translated.strip()
    if fresh:
        store.save_translations(fresh)
        result.update(fresh)
    return result
