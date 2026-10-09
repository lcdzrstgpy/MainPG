"""Translate a Temu English title into 1688-friendly Chinese search keywords.

Uses Google Translate's no-key public endpoint (reachable from the user's
network; the OB translation API is not enabled on this account).  Results are
cached per title and the raw title is kept as a fallback whenever the
translation service is unreachable, so a failure never blocks sourcing.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from threading import Lock

_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
_TIMEOUT_SECONDS = 6
_MAX_TEXT = 400
_MAX_KEYWORDS = 30

_cache: dict[str, str] = {}
_cache_lock = Lock()


def translate_title_to_chinese(title: str) -> str:
    """Translate one product title to Chinese; return the raw title on failure."""
    text = " ".join((title or "").split())
    if not text:
        return ""
    if len(text) > _MAX_TEXT:
        text = text[:_MAX_TEXT]
    with _cache_lock:
        cached = _cache.get(text)
    if cached is not None:
        return cached
    try:
        translated = _request_translation(text)
    except Exception:
        # Never cache a failed attempt: the fallback (raw title) would poison
        # the cache for the rest of the process after a transient network blip.
        return text
    with _cache_lock:
        _cache[text] = translated
    return translated


def to_search_keywords(translated: str, *, limit: int = _MAX_KEYWORDS) -> str:
    """Pick the leading clause of a translated title as the search keyword.

    A translated Temu title usually starts with the core product noun (e.g.
    "竹制宠物降温垫、夏季床垫…" -> "竹制宠物降温垫"); when the title has no
    punctuation, fall back to a bounded prefix.
    """
    cleaned = re.split(r"[，,、。;；\s]+", (translated or "").strip(), maxsplit=1)[0].strip()
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    prefix = (translated or "").strip()
    return prefix[: limit + 8] if prefix else cleaned[:limit]


def _request_translation(text: str) -> str:
    query = urllib.parse.urlencode({"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text})
    request = urllib.request.Request(
        f"{_TRANSLATE_URL}?{query}",
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://translate.google.com/"},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    segments = payload[0] if isinstance(payload, (list, tuple)) else []
    parts = [
        segment[0]
        for segment in segments
        if isinstance(segment, (list, tuple)) and segment and isinstance(segment[0], str)
    ]
    translated = "".join(parts).strip()
    return translated or text
