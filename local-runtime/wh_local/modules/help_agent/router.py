"""操作答疑：远端答疑服务的同源代理。

问答已上移到服务端（知识库检索 + 豆包生成，契约见
``docs/superpowers/specs/答疑接口-客户端对接说明.md``）。客户端不再持有 FAQ 数据、
不接触任何模型 Key、也不做本地匹配——本模块只把渲染层的 ``/api/help-agent/*``
转发到 ``workbench.haocoming.top/qa/*``，并把 SSE 原样流回。

为什么由本进程代理而不是前端直连：

* 渲染层与远端不同源，直连要等服务端补 CORS 预检；
* 服务地址只出现在一处，不散落在前端代码里；
* ``Authorization`` 只有一条通路（沿用现有的 ``wh_auth_*`` token）。
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import Any, Final

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger("wh_local.modules.help_agent.router")

DEFAULT_QA_BASE_URL: Final[str] = "https://workbench.haocoming.top/qa"
ENV_QA_BASE_URL: Final[str] = "WH_QA_BASE_URL"

_CONNECT_TIMEOUT_SECONDS: Final[float] = 10.0
# 服务端要先检索知识库再调模型，首个 delta 之前可能等 1~3 秒；两段 delta 之间给足余量。
_READ_TIMEOUT_SECONDS: Final[float] = 120.0

# 只放回渲染层真正要用的头：内容类型、限流等待时间、以及排查用的 request id。
_PASS_THROUGH_RESPONSE_HEADERS: Final[frozenset[str]] = frozenset(
    {"content-type", "cache-control", "retry-after", "x-request-id"}
)


def qa_base_url() -> str:
    """远端答疑服务根地址（``WH_QA_BASE_URL`` 可覆盖，便于联调指向测试环境）。"""
    return (os.environ.get(ENV_QA_BASE_URL) or DEFAULT_QA_BASE_URL).strip().rstrip("/")


def create_router(sessions: Any = None) -> APIRouter:
    """``sessions`` 必须是 ``main.py`` 里那个共享的 LocalSessionService。

    远端令牌只存在该进程的内存会话里（见 ``main.py`` 的注释），新建一个
    ``SQLiteCustomerSessionStore`` 是拿不到的，所以这里只能接共享实例。
    """
    router = APIRouter(prefix="/api/help-agent", tags=["help-agent"])

    def remote_token_of(request: Request) -> str:
        """把前端的本地会话 token 换成远端 ``/qa`` 认的远端令牌。

        前端 ``Authorization`` 里带的是本地会话 token（``wh_demo_token``），远端答疑
        服务不接受它 —— 直接转发必然 401「登录已失效」。远端令牌由登录时本地 runtime
        保存在会话记录里，这里按本地 token 查出来再转发，前端不需要知道两套 token。
        """
        authorization = request.headers.get("authorization", "").strip()
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        if not token:
            raise HTTPException(status_code=401, detail="请先登录后再使用私人助手")

        store = getattr(sessions, "store", None)
        get_session = getattr(store, "get_session", None)
        session = get_session(token) if callable(get_session) else None
        remote_token = str(getattr(session, "remote_token", "") or "")
        if not remote_token:
            raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
        return remote_token

    async def _forward(request: Request, path: str, *, require_login: bool) -> StreamingResponse:
        headers: dict[str, str] = {}
        if require_login:
            headers["authorization"] = f"Bearer {remote_token_of(request)}"
        if request.headers.get("accept"):
            headers["accept"] = request.headers["accept"]
        # 不让上游压缩：响应头只回传白名单字段（不含 content-encoding），
        # 若上游 gzip 过，原始字节直接透出去就是乱码。
        headers["accept-encoding"] = "identity"
        body = await request.body()
        if body:
            headers["content-type"] = request.headers.get("content-type", "application/json")

        url = f"{qa_base_url()}/{path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT_SECONDS,
                read=_READ_TIMEOUT_SECONDS,
                write=_CONNECT_TIMEOUT_SECONDS,
                pool=_CONNECT_TIMEOUT_SECONDS,
            ),
            follow_redirects=False,
        )
        try:
            upstream = await client.send(
                client.build_request(request.method, url, headers=headers, content=body),
                stream=True,
            )
        except httpx.TimeoutException as exc:
            await client.aclose()
            logger.warning("qa upstream timeout: %s %s (%s)", request.method, path, exc)
            raise HTTPException(status_code=504, detail="答疑服务响应超时，请稍后再试") from exc
        except httpx.HTTPError as exc:
            await client.aclose()
            logger.warning("qa upstream unreachable: %s %s (%s)", request.method, path, exc)
            raise HTTPException(status_code=502, detail="无法连接答疑服务，请检查网络后重试") from exc

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        # 状态码与响应体原样透传：401/413/429 的文案和 Retry-After 都由服务端决定，
        # 代理层不重新解释，避免两侧对限流/鉴权的说法漂移。
        return StreamingResponse(
            stream(),
            status_code=upstream.status_code,
            headers={
                name: value
                for name, value in upstream.headers.items()
                if name.lower() in _PASS_THROUGH_RESPONSE_HEADERS
            },
        )

    @router.get("/health")
    async def health(request: Request) -> StreamingResponse:
        """远端健康检查（无需登录，用于联调与故障排查）。"""
        return await _forward(request, "health", require_login=False)

    @router.get("/suggestions")
    async def suggestions(request: Request) -> StreamingResponse:
        """面板空态展示的引导问题。"""
        return await _forward(request, "suggestions", require_login=True)

    @router.post("/chat")
    async def chat(request: Request) -> StreamingResponse:
        """提问（SSE 流式响应，事件为 meta / delta / citation / done）。"""
        return await _forward(request, "chat", require_login=True)

    @router.post("/feedback")
    async def feedback(request: Request) -> StreamingResponse:
        """对某条回答点「有用 / 没用」，按 request_id 反馈。"""
        return await _forward(request, "feedback", require_login=True)

    return router
