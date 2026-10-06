"""HTTP surface for the loopback-only ClipForge sidecar.

Status codes carry the lifecycle semantics; the JSON snapshot carries the truth.
``GET /status`` always answers 200 — callers must inspect ``state`` instead of
treating HTTP success as "ready".

===========  ==================================================
``POST /start``
200          已 ready，未创建新进程
202          已接受启动请求（starting/stopping/stopped）
409          构建产物缺失或无效
500          非预期内部错误（仅稳定错误码，不含堆栈/路径）
===========  ==================================================
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response

from .service import ClipForgeError, ClipForgeService, ClipForgeStatus

logger = logging.getLogger("wh_local.modules.clipforge")

# 稳定错误码：原始异常只进日志，浏览器只看到这里定义的结构化错误。
_INTERNAL_FAILURE = ClipForgeError(
    code="CLIPFORGE_START_FAILED",
    message="AI 视频服务启动请求失败，请稍后重试",
    retryable=True,
)


def create_router(service: ClipForgeService) -> APIRouter:
    router = APIRouter(prefix="/api/clipforge", tags=["clipforge"])

    @router.get("/status")
    def status() -> dict[str, object]:
        return _payload(service.status())

    @router.post("/start")
    def start(response: Response) -> dict[str, object]:
        try:
            current = service.start()
        except Exception:
            logger.exception("clipforge start failed unexpectedly")
            response.status_code = 500
            payload = _payload(service.status())
            payload["error"] = _error_payload(_INTERNAL_FAILURE)
            return payload

        if current.state == "unavailable":
            response.status_code = 409
        elif current.state == "ready":
            response.status_code = 200
        else:
            response.status_code = 202
        return _payload(current)

    return router


def _payload(status: ClipForgeStatus) -> dict[str, object]:
    return {
        "state": status.state,
        "buildState": status.build_state,
        "available": status.available,
        "url": status.url,
        "instanceId": status.instance_id,
        "message": status.message,
        "error": None if status.error is None else _error_payload(status.error),
    }


def _error_payload(error: ClipForgeError) -> dict[str, object]:
    return {
        "code": error.code,
        "message": error.message,
        "retryable": error.retryable,
        "exitCode": error.exit_code,
        "diagnosticId": error.diagnostic_id,
    }
