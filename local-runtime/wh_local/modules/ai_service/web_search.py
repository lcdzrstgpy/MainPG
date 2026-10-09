from __future__ import annotations

import re
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx


SEARCH_ENDPOINT = "https://www.bing.com/search?format=rss&q="
SEARCH_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


def search_public_web(query: str, limit: int = 5) -> list[dict[str, str]]:
    normalized = query.strip()
    if not normalized:
        return []
    try:
        response = httpx.get(
            f"{SEARCH_ENDPOINT}{quote_plus(normalized[:200])}",
            headers={"User-Agent": SEARCH_USER_AGENT, "Accept": "application/rss+xml, application/xml;q=0.9"},
            follow_redirects=True,
            timeout=15.0,
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
    except (httpx.HTTPError, ElementTree.ParseError) as exc:
        raise RuntimeError("public web search is temporarily unavailable") from exc
    results: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:max(1, min(limit, 8))]:
        title = _plain(item.findtext("title") or "")
        url = (item.findtext("link") or "").strip()
        summary = _plain(item.findtext("description") or "")
        if title and url.startswith("https://"):
            results.append({"title": title[:160], "url": url, "summary": summary[:500]})
    return results


def search_context(results: list[dict[str, str]]) -> str:
    if not results:
        return ""
    lines = ["以下是本轮联网检索到的公开资料。仅据此回答事实性内容，并在结论后附对应 URL："]
    for index, result in enumerate(results, start=1):
        lines.append(f"[{index}] {result['title']}\n摘要：{result['summary']}\n来源：{result['url']}")
    return "\n\n".join(lines)


def _plain(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value)).strip()
