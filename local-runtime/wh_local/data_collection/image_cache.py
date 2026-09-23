"""Bounded, workspace-scoped cache for already-safe public product images."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from .public_image_fetch import FetchedPublicImage, fetch_public_image

# 图片缓存的数据结构与接口定义在本模块（缓存自己的领域），而不是 service。
# 原先它们住在 service.py，导致本模块为了几个类型反向 import 编排层（循环依赖的种子）。
# service.py 仍然 re-export 这些名字，外部既有引用路径不受影响。


@dataclass(frozen=True)
class CachedDailySelectionImage:
    """Bytes returned by an injected safe image cache/fetch adapter."""

    content: bytes
    media_type: str
    final_url: str
    resolved_address: str | None = None


class DailySelectionImageAccessDenied(PermissionError):
    """Raised for unrecorded or unsafe image targets."""


class DailySelectionImageNotFound(LookupError):
    """Raised when a requested URL is not part of an owned run snapshot."""


class DailySelectionImageCache(Protocol):
    """Host adapter that validates every network target before connecting.

    Implementations must invoke ``validate_target`` for the initial resolved
    address and again for every redirect target before opening that connection.
    """

    def get_or_fetch(
        self,
        *,
        workspace_id: str,
        url: str,
        validate_target: Callable[[str, str | None], None],
    ) -> CachedDailySelectionImage: ...


class PublicDailySelectionImageCache(DailySelectionImageCache):
    """Fetch product images through the same SSRF-safe path used by OneBound.

    Bytes stay in process memory only, are keyed by workspace, and are evicted
    under a small LRU bound.  The downloader validates every redirect and pins
    its connection to a checked public IP before this cache ever sees bytes.
    """

    def __init__(
        self,
        *,
        fetcher: Callable[[str], FetchedPublicImage] = fetch_public_image,
        max_entries: int = 20,
        max_total_bytes: int = 20 * 1024 * 1024,
    ) -> None:
        if max_entries < 1 or max_total_bytes < 1:
            raise ValueError("image cache limits must be positive")
        self._fetcher = fetcher
        self._max_entries = max_entries
        self._max_total_bytes = max_total_bytes
        self._entries: OrderedDict[tuple[str, str], CachedDailySelectionImage] = OrderedDict()
        self._total_bytes = 0
        self._lock = RLock()

    def get_or_fetch(
        self,
        *,
        workspace_id: str,
        url: str,
        validate_target: Callable[[str, str | None], None],
    ) -> CachedDailySelectionImage:
        key = (workspace_id, url)
        validate_target(url, None)
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached

        fetched = self._fetcher(url)
        validate_target(fetched.final_url, None)
        cached = CachedDailySelectionImage(
            content=fetched.content,
            media_type=fetched.media_type,
            final_url=fetched.final_url,
        )
        self._store(key, cached)
        return cached

    def _store(
        self, key: tuple[str, str], image: CachedDailySelectionImage
    ) -> None:
        size = len(image.content)
        if size > self._max_total_bytes:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._total_bytes -= len(previous.content)
            self._entries[key] = image
            self._total_bytes += size
            while self._entries and (
                len(self._entries) > self._max_entries
                or self._total_bytes > self._max_total_bytes
            ):
                _, evicted = self._entries.popitem(last=False)
                self._total_bytes -= len(evicted.content)
