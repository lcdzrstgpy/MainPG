"""本地后端的入口防护：Host 只认回环 + CORS 收敛。

背景（2026-11-20 取自队友分支的教训，见 3f0e5763d0 的说明）：
本地服务监听 127.0.0.1，但浏览器里的**任意网页**都能向它发请求；如果
`allow_origins=["*"]`，跨源响应就会被读走。更阴的是 DNS rebinding：攻击者域名
解析到 127.0.0.1，Host 头是 evil.com，请求照样进得来。

本模块只做两道**与业务无关**的闸门，不动鉴权模型（本线的鉴权仍在 session.py
的 Bearer 会话校验里）：
1. ``LocalHostGuardMiddleware`` —— Host 头的域名部分必须是回环名；
2. ``cors_allow_origins()`` —— 只放行本机来源，其余用环境变量白名单追加。

环境变量：
- ``WH_LOCAL_ALLOWED_HOSTS``    额外允许的 Host（逗号分隔，填域名不带端口）
- ``WH_LOCAL_ALLOWED_ORIGINS``  额外允许的 Origin（逗号分隔，含协议）
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# 回环域名：浏览器访问本机服务的全部合法写法。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# TestClient 的默认 Host；浏览器解析不到这个名字，留着不影响安全。
_TEST_HOST = "testserver"


def _extra_hosts() -> set[str]:
    raw = os.environ.get("WH_LOCAL_ALLOWED_HOSTS", "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _extra_origins() -> list[str]:
    raw = os.environ.get("WH_LOCAL_ALLOWED_ORIGINS", "")
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def _host_of(host_header: str) -> str:
    """剥掉端口与 IPv6 方括号，返回小写域名部分。"""
    value = (host_header or "").strip().lower()
    if value.startswith("["):  # [::1]:8010
        end = value.find("]")
        return value[1:end] if end > 0 else value
    return value.rsplit(":", 1)[0] if ":" in value else value


def is_allowed_host(host_header: str) -> bool:
    host = _host_of(host_header)
    if not host:
        # 没有 Host 头（HTTP/1.0 旧客户端）：放行，浏览器永远会带 Host。
        return True
    return host in _LOOPBACK_HOSTS or host == _TEST_HOST or host in _extra_hosts()


def cors_allow_origins() -> list[str]:
    """本机开发/打包场景的允许来源。

    自带：常见 dev 端口 + 本机回环；打包版是同源访问，不产生 CORS。
    企业自建前端用 ``WH_LOCAL_ALLOWED_ORIGINS`` 追加。
    """
    origins = [
        f"http://127.0.0.1:{port}" for port in (5173, 8010)
    ] + [
        f"http://localhost:{port}" for port in (5173, 8010)
    ]
    for item in _extra_origins():
        origins.append(item)
    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for origin in origins:
        if origin not in seen:
            seen.add(origin)
            unique.append(origin)
    return unique


class LocalHostGuardMiddleware(BaseHTTPMiddleware):
    """Host 头只认回环（堵 DNS rebinding）。放在最外层，preflight 也过一遍。"""

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if not is_allowed_host(request.headers.get("host", "")):
            origin = request.headers.get("origin", "")
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "只接受本机访问：Host 不是回环地址，已拒绝。"
                        f"（Host={request.headers.get('host', '')}"
                        + (f", Origin={origin}" if origin else "")
                        + "）"
                    )
                },
            )
        return await call_next(request)


def same_origin_of(url: str) -> str:
    """取 URL 的 origin（scheme://host[:port]）；解析不了返回空串。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"
