"""Small, provider-neutral helpers for collecting similar products by URL."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


_OFFER_ID = re.compile(r"(?:offer/)?(\d{5,})(?:\.html)?(?:/|$)")
_TAOBAO_ITEM_ID = re.compile(r"(?:item/|id=)?(\d{5,})(?:\.htm|/|$)", re.IGNORECASE)

# 手机端「分享 → 复制链接」得到的是跳转短链（如 https://e.tb.cn/…、https://m.tb.cn/…），
# 本身不含商品 id，必须跟随跳转拿到详情页地址后，才能提取 offer / item id。
_SHORT_LINK_HOSTS = frozenset({"e.tb.cn", "m.tb.cn", "tb.cn", "s.tb.cn"})
_SHORT_LINK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

# 淘宝「码上淘」短链（e.tb.cn / m.tb.cn）多数情况下不返回 3xx，而是返回 200 的
# 跳转落地页，真正的商品地址写在页面脚本里的 `var url = '...'`，并用 `var status`
# 标记短码是否有效。必须解析页面内容才能拿到目标地址。
_SHORT_LINK_JS_URL = re.compile(r"""var\s+url\s*=\s*['"]([^'"]*)['"]""")
_SHORT_LINK_JS_STATUS = re.compile(r"""var\s+status\s*=\s*['"]([^'"]*)['"]""")
_SHORT_LINK_META_REFRESH = re.compile(
    r"""<meta[^>]+http-equiv\s*=\s*['"]?refresh['"]?[^>]*content\s*=\s*['"][^'"]*?url=([^'">]+)""",
    re.IGNORECASE,
)
_SHORT_LINK_ITEM_URL = re.compile(
    r"""https?://[^"'\s<>\\]*(?:item\.taobao\.com|detail\.tmall\.com)[^"'\s<>\\]*""",
    re.IGNORECASE,
)


class _RedirectCaptured(Exception):
    """内部信号：捕获短链的跳转目标，不真正访问目标页。"""

    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.url = url


class _CaptureRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise _RedirectCaptured(newurl)


def _parse_short_link_page(body: str) -> tuple[str, bool]:
    """从淘宝短链落地页 HTML 中解析出目标地址。

    返回 ``(target_url, expired)``：``expired`` 为 True 表示短码已失效
    （页面 ``status`` 不为 true），此时 ``target_url`` 无意义。
    """
    if not body:
        return "", False
    status = _SHORT_LINK_JS_STATUS.search(body)
    if status is not None and status.group(1).strip().casefold() not in {"true", "1"}:
        return "", True
    for pattern in (_SHORT_LINK_JS_URL, _SHORT_LINK_META_REFRESH):
        match = pattern.search(body)
        if match is not None:
            candidate = html.unescape(match.group(1)).strip()
            if candidate:
                return candidate, False
    match = _SHORT_LINK_ITEM_URL.search(body)
    if match is not None:
        return html.unescape(match.group(0)).strip(), False
    return "", False


def resolve_short_link(value: object, *, max_hops: int = 3, timeout: float = 5.0) -> str:
    """把已知跳转短链展开为最终地址，用于提取商品 id。

    只对白名单短链域名生效。优先跟随 3xx 的 Location；淘宝「码上淘」短链
    （e.tb.cn 等）常返回 200 落地页，则在页面脚本里解析目标地址。解析失败时
    原样返回，交由后续校验报错；若判定短码已失效，直接抛出 ValueError 以便
    前端给出「链接已失效」的明确提示。
    """
    if not isinstance(value, str) or not value.strip():
        return ""
    raw = value.strip()
    if raw.startswith("//"):
        raw = f"https:{raw}"
    opener = build_opener(_CaptureRedirect)
    for _ in range(max_hops):
        host = (urlsplit(raw).hostname or "").casefold()
        if host not in _SHORT_LINK_HOSTS:
            break
        try:
            response = opener.open(Request(raw, headers={"User-Agent": _SHORT_LINK_UA}), timeout=timeout)
        except _RedirectCaptured as captured:
            target = urljoin(raw, captured.url)
        except Exception:  # noqa: BLE001 短链解析失败不应阻断采集，交由后续校验处理
            break
        else:
            # 200 落地页（无 3xx）：从页面内容里解析 JS 跳转目标。
            try:
                body = response.read(65536).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                body = ""
            target, expired = _parse_short_link_page(body)
            if expired:
                raise ValueError("source_url short link has expired or is invalid")
            if not target:
                break
        if not target or target == raw:
            break
        raw = target
    return raw


def canonical_1688_offer_url(value: object) -> tuple[str, str]:
    """Return a canonical public 1688 URL and offer id without fetching it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_url is required")
    raw = value.strip()
    if raw.startswith("//"):
        raw = f"https:{raw}"
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or not (host == "1688.com" or host.endswith(".1688.com")):
        raise ValueError("source_url must be a public 1688 product URL")
    match = _OFFER_ID.search(parsed.path)
    if match is None:
        for key, item in parse_qsl(parsed.query):
            if key in {"offerId", "offer_id", "num_iid"} and item.isdigit():
                match = re.match(r"(\d+)", item)
                break
    if match is None:
        raise ValueError("source_url does not contain a 1688 offer id")
    offer_id = match.group(1)
    return urlunsplit(("https", host, f"/offer/{offer_id}.html", "", "")), offer_id


def canonical_taobao_item_url(value: object) -> tuple[str, str]:
    """Return a canonical public Taobao item URL and item id without fetching it."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("source_url is required")
    raw = value.strip()
    if raw.startswith("//"):
        raw = f"https:{raw}"
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").casefold()
    is_taobao_host = (
        host == "taobao.com"
        or host.endswith(".taobao.com")
        or host == "tmall.com"
        or host.endswith(".tmall.com")
    )
    if parsed.scheme not in {"http", "https"} or not is_taobao_host:
        raise ValueError("source_url must be a public Taobao product URL")
    item_id: str | None = None
    for key, item in parse_qsl(parsed.query):
        if key in {"id", "item_id", "itemId", "num_iid"} and item.isdigit():
            item_id = item
            break
    if item_id is None:
        match = _TAOBAO_ITEM_ID.search(parsed.path)
        if match:
            item_id = match.group(1)
    if item_id is None:
        raise ValueError("source_url does not contain a Taobao item id")
    return f"https://item.taobao.com/item.htm?id={item_id}", item_id


def canonical_platform_url(platform: str, value: object) -> tuple[str, str]:
    """Resolve a public product URL to its canonical URL and id for a platform."""
    if platform == "taobao":
        return canonical_taobao_item_url(value)
    return canonical_1688_offer_url(value)


def detail_seed(payload: Mapping[str, Any]) -> tuple[str, str | None]:
    """Extract the title and a main image from documented OneBound detail shapes."""
    data = payload.get("data")
    source = data if isinstance(data, Mapping) else payload
    # OneBound item_get commonly wraps the product as {"item": {...}}.
    if not _text(source, "title", "name", "item_title"):
        item = source.get("item") if isinstance(source, Mapping) else None
        if not isinstance(item, Mapping):
            item = payload.get("item")
        if isinstance(item, Mapping):
            source = item
    title = _text(source, "title", "name", "item_title")
    if not title:
        raise ValueError("item detail did not include a title")
    image = _text(source, "main_image_url", "main_image", "pic_url", "image_url", "image")
    if not image:
        images = source.get("item_imgs") or source.get("images") or source.get("image_urls")
        if isinstance(images, (tuple, list)) and images:
            first = images[0]
            image = _text(first, "url", "image_url", "pic_url", "image") if isinstance(first, Mapping) else first
    if isinstance(image, str):
        image = image.strip() or None
    if image and not image.startswith(("http://", "https://")):
        image = None
    return title, image


def _text(source: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = source.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
