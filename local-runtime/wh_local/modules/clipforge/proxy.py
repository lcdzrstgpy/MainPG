"""Same-origin proxy for the loopback-only ClipForge sidecar.

The renderer never talks to the sidecar directly: its port changes on every
start, and media URLs must stay stable. Everything goes through
``/api/clipforge/proxy/<rest>`` where ``<rest>`` starts with ``api/``.

Contract (docs/superpowers/specs/2026-10-20-ai-video-native-rewrite.md, T0.1):

* sidecar not ready -> 503 ``{"detail":{"code":"CLIPFORGE_NOT_READY",...}}``;
* upstream status/body pass through unchanged, 4xx/5xx included;
* ``Range`` is forwarded in, ``Content-Type/Content-Length/Content-Range/
  Accept-Ranges`` come back untouched;
* request and response bodies are streamed, never buffered as a whole;
* only the current loopback instance is reachable — no arbitrary URL forwarding.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Final
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from .service import ClipForgeService

logger = logging.getLogger("wh_local.modules.clipforge.proxy")

PROXY_METHODS: Final[tuple[str, ...]] = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")

_CONNECT_TIMEOUT_SECONDS: Final[float] = 5.0
_READ_TIMEOUT_SECONDS: Final[float] = 120.0
# 少数接口会同步阻塞几分钟（付费视频/脚本/配音生成、九宫格与整片渲染）。
# 前端应优先走 202 + 轮询，这里只是给无法异步化的接口留出余量。
_LONG_READ_TIMEOUT_SECONDS: Final[float] = 600.0
_LONG_READ_PREFIXES: Final[tuple[str, ...]] = ("api/ai/", "api/llm/", "api/topic/", "api/tts", "api/project/")

_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost", "::1"})
_DEFAULT_LANGUAGE: Final[str] = "zh-CN"

_HOP_BY_HOP_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# host/authorization 不外传；content-length 交给 httpx 按实际 body 重新计算。
_DROPPED_REQUEST_HEADERS: Final[frozenset[str]] = _HOP_BY_HOP_HEADERS | {
    "host",
    "authorization",
    "content-length",
}
# date/server 由本进程回填，避免与上游重复。
_DROPPED_RESPONSE_HEADERS: Final[frozenset[str]] = _HOP_BY_HOP_HEADERS | {"date", "server"}

_DEFAULTS_KEYS: Final[tuple[str, ...]] = ("providers", "llm", "defaultImageModel", "defaultVideoModel")


async def forward_proxy(request: Request, service: ClipForgeService, path: str) -> StreamingResponse:
    """Forward one request to the current sidecar instance and stream the answer back."""
    rest = _validated_path(path)
    base_url = _ready_base_url(service)
    url = f"{base_url}/{rest}"
    if request.url.query:
        url = f"{url}?{request.url.query}"

    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() not in _DROPPED_REQUEST_HEADERS
    }
    headers.setdefault("accept-language", _DEFAULT_LANGUAGE)

    body = await request.body()
    timeout = httpx.Timeout(
        connect=_CONNECT_TIMEOUT_SECONDS,
        read=_read_timeout_for(rest),
        write=_READ_TIMEOUT_SECONDS,
        pool=_CONNECT_TIMEOUT_SECONDS,
    )
    # trust_env=False：用户设了 HTTP_PROXY 时，loopback 请求也会被送给代理并失败
    # （与就绪探测同一个坑）。代理只允许直连当前 127.0.0.1 实例。
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False)
    try:
        upstream = await client.send(
            client.build_request(request.method, url, headers=headers, content=body),
            stream=True,
        )
    except httpx.TimeoutException as exc:
        await client.aclose()
        logger.warning("clipforge proxy timeout: %s %s (%s)", request.method, rest, exc)
        raise HTTPException(
            status_code=504,
            detail={"code": "CLIPFORGE_UPSTREAM_TIMEOUT", "message": "AI 视频服务响应超时，请稍后重试"},
        ) from exc
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("clipforge proxy unreachable: %s %s (%s)", request.method, rest, exc)
        raise HTTPException(
            status_code=502,
            detail={"code": "CLIPFORGE_UPSTREAM_FAILED", "message": "无法连接 AI 视频服务，请稍后重试"},
        ) from exc

    async def stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(
        stream(),
        status_code=upstream.status_code,
        headers={
            name: value
            for name, value in upstream.headers.items()
            if name.lower() not in _DROPPED_RESPONSE_HEADERS
        },
    )


def read_configured_defaults(service: ClipForgeService) -> dict[str, object]:
    """Read the operator-provided defaults (``clipforge.local.json``) for first-run injection.

    Missing/unreadable file is not an error: the settings page simply starts empty.
    Only the four keys the native frontend consumes are exposed.
    """
    try:
        path = service._configured_settings_path()
    except Exception:  # noqa: BLE001 - 配置解析失败不能拖垮设置页
        logger.exception("clipforge configured settings path resolution failed")
        return {}
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("clipforge configured settings unreadable: %s", path)
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in _DEFAULTS_KEYS if key in payload}


def _validated_path(path: str) -> str:
    """Reject anything that is not a sidecar ``api/`` path (defence in depth)."""
    rest = path.lstrip("/")
    if not rest.startswith("api/"):
        raise _invalid_path("代理路径必须以 api/ 开头")
    if ".." in rest or "\\" in rest:
        raise _invalid_path("代理路径不合法")
    return rest


def _invalid_path(message: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"code": "CLIPFORGE_PROXY_PATH_INVALID", "message": message},
    )


def _ready_base_url(service: ClipForgeService) -> str:
    """Return the sidecar base URL, but only when it is a live loopback instance."""
    status = service.status()
    if status.state != "ready" or not status.url:
        raise _not_ready()
    parts = urlsplit(status.url)
    if parts.scheme != "http" or (parts.hostname or "") not in _LOOPBACK_HOSTS:
        logger.error("clipforge reported an unusable sidecar url")
        raise _not_ready()
    return status.url.rstrip("/")


def _not_ready() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": "CLIPFORGE_NOT_READY", "message": "AI 视频服务未就绪"},
    )


def _read_timeout_for(rest: str) -> float:
    return _LONG_READ_TIMEOUT_SECONDS if rest.startswith(_LONG_READ_PREFIXES) else _READ_TIMEOUT_SECONDS
