"""Small, provider-neutral helpers for collecting similar 1688 products by URL."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit


_OFFER_ID = re.compile(r"(?:offer/)?(\d{5,})(?:\.html)?(?:/|$)")


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
        raise ValueError("1688 item detail did not include a title")
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
