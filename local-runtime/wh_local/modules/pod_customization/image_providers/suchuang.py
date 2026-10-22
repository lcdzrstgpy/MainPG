"""速创（无印）生图平台。

协议是**异步**的：`POST /api/async/image_gpt[_2.5]` 拿 ``task_id``，再轮询
``/api/async/detail`` 直到拿到图片地址。参考图必须由上游能直接访问，因此调用方
需要把本地图先发布成公网地址（host.publish_reference 负责）。

本文件是速创链路的唯一实现；豆包（Ark）在 ``doubao.py``，两者互不影响。
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from wh_local.modules.product_processing.domain.policy import is_safe_external_url
from wh_local.modules.product_processing.infrastructure.media import MediaProcessingError

from ....runtime_logs import business_logger

from ..errors import PodProviderResultReceivedError, safe_error_message
from .base import (
    GridImageProvider,
    GridImageRequest,
    GridImageResult,
    GridProviderHost,
    GridReference,
)
# 单次速创生图的分段超时：超过该时长仍未拿到图片地址就丢弃这次调用，交给已规划的重试
# 立刻重投。依据本地真实时延（近千次调用均值 ~74s、p95 ~115s、上游健康日上限 136s），
# 240s 约为 3 倍 p95。原先 600s 的上限会让一个长尾任务独占 1/8 的生图槽位长达 10 分钟，
# 把整批有效并发从 8 拖到 4~5，整批耗时直接翻倍。
SUCHUANG_RESULT_TIMEOUT_SECONDS: float = 240.0
SUCHUANG_POLL_INTERVAL_SECONDS: float = 3.0
SUCHUANG_BASE_URL = "https://api.wuyinkeji.com"
SUCHUANG_DETAIL_URL = f"{SUCHUANG_BASE_URL}/api/async/detail"
SUCHUANG_IMAGE_SUBMIT_PATHS = {
    "image_gpt": "/api/async/image_gpt",
    "image_gpt_2.5": "/api/async/image_gpt_2.5",
}
# 速创内部的未知模型兜底：仍走 2.5，不受 POD 全局默认（豆包）影响。
SUCHUANG_FALLBACK_MODEL = "image_gpt_2.5"
SUCHUANG_IMAGE_ASPECT_RATIO_2_5 = {
    "1:1": "1024x1024",
    "16:9": "1280x720",
    "9:16": "720x1280",
    "4:3": "1152x864",
    "3:4": "864x1152",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
    "5:4": "1120x896",
    "4:5": "896x1120",
    "21:9": "1456x624",
    "9:21": "624x1456",
    "1:3": "688x2048",
    "3:1": "2048x688",
    "2:1": "1536x768",
    "1:2": "768x1536",
}


class SuChuangImageProvider:
    """速创异步生图：提交 → 轮询 → 由 host 下载结果。"""

    name = "suchuang"
    grant_provider = "wuyin"

    def __init__(
        self,
        *,
        result_timeout_seconds: float = SUCHUANG_RESULT_TIMEOUT_SECONDS,
        poll_interval_seconds: float = SUCHUANG_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._result_timeout_seconds = max(0.0, float(result_timeout_seconds))
        self._poll_interval_seconds = max(0.0, float(poll_interval_seconds))

    def prepare(
        self,
        host: GridProviderHost,
        references: tuple[GridReference, ...],
        *,
        namespace: str,
    ) -> list[str]:
        """速创只吃公网地址；已发布好的锚点 URL 直接复用，其余本地图才发布到 COS。"""
        return [
            reference.url
            or host.publish_reference(
                reference.content, reference.content_type, namespace=namespace
            )
            for reference in references
        ]

    def generate(
        self,
        host: GridProviderHost,
        request: GridImageRequest,
        prepared: Any,
        *,
        model: str,
        api_key: str,
        on_start: Callable[[], None] | None = None,
    ) -> GridImageResult:
        reference_urls = [str(url) for url in (prepared or [])]
        task_id = self.submit(
            host, request, reference_urls, model=model, api_key=api_key, on_start=on_start
        )
        result_url = self.poll(host, task_id, resolve_key=lambda: api_key)
        try:
            content, content_type = host.download_result(result_url)
        except Exception as exc:
            raise PodProviderResultReceivedError(
                "wuyin",
                f"速创已返回结果，但本地下载或解析失败：{safe_error_message(exc)}",
            ) from exc
        return GridImageResult(content=content, content_type=content_type)

    def submit(
        self,
        host: GridProviderHost,
        request: GridImageRequest,
        reference_urls: list[str],
        *,
        model: str,
        api_key: str,
        on_start: Callable[[], None] | None = None,
    ) -> str:
        host.acquire_request_token()
        with host.connection_slot(timeout_seconds=30.0):
            host.ensure_open()
            if on_start is not None:
                on_start()
            host.ensure_open()
            size_value = suchuang_size(request.size)
            submit_path = SUCHUANG_IMAGE_SUBMIT_PATHS.get(
                model, SUCHUANG_IMAGE_SUBMIT_PATHS[SUCHUANG_FALLBACK_MODEL]
            )
            # 诊断留痕：明确记录本次解析到的模型与真实提交端点，避免"以为切了 2.5 其实没切"。
            business_logger("pod_processing").info(
                "POD 生图提交 | trial_id=%s | model=%s | endpoint=%s | size=%s | 参考图=%d",
                request.trial_id,
                model,
                f"{SUCHUANG_BASE_URL}{submit_path}",
                size_value,
                len(reference_urls),
            )
            if model == "image_gpt_2.5":
                body: dict[str, Any] = {
                    "prompt": request.prompt,
                    "aspectRatio": SUCHUANG_IMAGE_ASPECT_RATIO_2_5.get(size_value, "1024x1024"),
                }
                if reference_urls:
                    # 2.5 端点的 urls 要求逗号拼接字符串，传数组上游会返回 500。
                    body["urls"] = ",".join(reference_urls)
            else:
                body = {"prompt": request.prompt, "size": size_value}
                if reference_urls:
                    body["urls"] = list(reference_urls)
            response = host.session.post(
                f"{SUCHUANG_BASE_URL}{submit_path}",
                params={"key": api_key},
                headers={"Authorization": api_key, "Content-Type": "application/json"},
                json=body,
                timeout=30.0,
                allow_redirects=False,
            )
            try:
                if not bool(response.ok):
                    raise MediaProcessingError(
                        f"速创提交返回 HTTP {int(response.status_code)}",
                        status_code=int(response.status_code),
                        attempt_count=1,
                        status_class=suchuang_status_class(int(response.status_code)),
                    )
                payload = response.json()
            finally:
                response.close()
        if not isinstance(payload, dict) or int(payload.get("code") or 0) != 200:
            raise MediaProcessingError(
                f"速创提交失败：{suchuang_message(payload)}",
                attempt_count=1,
                status_class="non_retryable_4xx"
                if suchuang_code_is_auth_or_request_error(payload)
                else "transient",
            )
        data = payload.get("data") or {}
        task_id = str(data.get("id") or data.get("task_id") or "").strip() if isinstance(data, dict) else ""
        if not task_id:
            raise MediaProcessingError("速创返回中没有生图任务 ID", attempt_count=1, status_class="transient")
        return task_id

    def poll(
        self, host: GridProviderHost, task_id: str, *, resolve_key: Callable[[], str]
    ) -> str:
        """轮询任务直到拿到图片地址。

        ``resolve_key`` 每轮都重新取一次：短期密钥在轮询途中过期时必须立刻停下，
        而不是拿着已失效的 key 继续空转。
        """
        deadline = time.monotonic() + self._result_timeout_seconds
        started_at = time.monotonic()
        last_message = ""
        last_payload: object = None
        last_status = ""
        polls = 0

        def trace(raw_limit: int) -> str:
            """失败留痕：上游只回「调用成功」这类套话，缺任务 ID 与原始响应就无从回查。"""

            return suchuang_poll_trace(
                task_id,
                polls,
                time.monotonic() - started_at,
                last_payload,
                last_status,
                raw_limit=raw_limit,
            )

        def fail(reason: str, status_class: str = "transient") -> MediaProcessingError:
            """日志留全量留痕，落库的错误信息里嵌精简版（safe_error_message 还会再截到 500 字符）。"""

            business_logger("pod_processing").warning(
                "POD 速创生图失败：%s | %s", reason, trace(2000)
            )
            return MediaProcessingError(
                f"{reason} | {trace(200)}", attempt_count=1, status_class=status_class
            )

        while time.monotonic() < deadline:
            polls += 1
            if self._poll_interval_seconds:
                host.interruptible_wait(self._poll_interval_seconds)
            host.acquire_request_token()
            with host.connection_slot(timeout_seconds=30.0):
                api_key = resolve_key()
                response = host.session.get(
                    SUCHUANG_DETAIL_URL,
                    params={"key": api_key, "id": task_id},
                    headers={"Authorization": api_key},
                    timeout=30.0,
                    allow_redirects=False,
                )
                try:
                    if not bool(response.ok):
                        status = int(response.status_code)
                        if 400 <= status < 500:
                            raise MediaProcessingError(
                                f"速创轮询返回 HTTP {status}",
                                status_code=status,
                                attempt_count=1,
                                status_class="non_retryable_4xx",
                            )
                        last_message = f"detail HTTP {status}"
                        continue
                    payload = response.json()
                    last_payload = payload
                finally:
                    response.close()
            if not isinstance(payload, dict):
                last_message = "detail response is incompatible"
                continue
            code = int(payload.get("code") or 0)
            if code and code != 200:
                last_message = suchuang_message(payload)
                if code in {400, 401, 403, 404}:
                    raise MediaProcessingError(
                        f"速创图片任务失败：{last_message}",
                        attempt_count=1,
                        status_class="non_retryable_4xx",
                    )
                continue
            data = payload.get("data") or {}
            result_url = _first_suchuang_image_url(data) or _first_suchuang_image_url(payload)
            if result_url:
                return result_url
            status_value = str(data.get("status") or payload.get("status") or "").strip().lower() if isinstance(data, dict) else ""
            last_status = status_value
            # 顶层 msg 只说「这次查询成功」，任务本身的状态在 data.message / data.msg 里：
            # 上游对生成失败会回 status=3 且 message="generate image failed"，只按状态码判断
            # 会把它当成「处理中」白等到分段超时，真实原因（生成失败）被吞掉。
            message = suchuang_message(payload)
            task_message = _suchuang_task_message(data)
            if _suchuang_task_failed(task_message):
                raise fail(f"速创图片任务失败：{task_message}")
            # 上游 status 为异步任务状态码（纯数字或文本）：≥0 为排队/准备/等待/处理中/发布，
            # 1<0 为失败，1/成功文本才表示完成。3/4/5 数字在任务消息未报失败时实为“处理中”，
            # 不能按文本失败集误判成失败。
            try:
                numeric_status = int(float(status_value)) if status_value else None
            except (TypeError, ValueError):
                numeric_status = None
            if numeric_status is not None:
                if numeric_status < 0:
                    raise fail(f"速创图片任务失败：{task_message or message}")
                last_message = task_message or message or f"status={status_value}"
                continue
            if status_value in {"success", "succeeded", "finish", "finished", "completed", "done"}:
                raise fail(f"速创已完成但没有图片地址：{task_message or message}")
            if status_value in {"fail", "failed", "error", "cancelled", "canceled"}:
                raise fail(f"速创图片任务失败：{task_message or message}")
            last_message = task_message or message or f"status={status_value or 'processing'}"
        raise fail(f"速创图片任务分段超时（{self._result_timeout_seconds:.0f}s）：{last_message}")


def suchuang_size(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"1024x1024", "2048x2048", "4096x4096"}:
        return "1:1"
    if raw in {"auto", "1:1", "3:2", "2:3", "16:9", "9:16", "4:3", "3:4", "21:9", "9:21", "1:3", "3:1", "2:1", "1:2"}:
        return raw
    return "1:1"


def suchuang_status_class(status_code: int) -> str:
    return "non_retryable_4xx" if 400 <= status_code < 500 else "transient"


def suchuang_code_is_auth_or_request_error(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    return int(payload.get("code") or 0) in {400, 401, 403, 404}


def _first_suchuang_image_url(value: Any) -> str:
    if isinstance(value, str):
        candidate = value.strip()
        if is_safe_external_url(candidate):
            return candidate
        return ""
    if isinstance(value, dict):
        for key in ("url", "image_url", "image", "src", "href"):
            found = _first_suchuang_image_url(value.get(key))
            if found:
                return found
        for key in ("result", "results", "images", "urls", "output", "outputs"):
            found = _first_suchuang_image_url(value.get(key))
            if found:
                return found
    if isinstance(value, (list, tuple)):
        for item in value:
            found = _first_suchuang_image_url(item)
            if found:
                return found
    return ""


def suchuang_message(payload: object) -> str:
    if not isinstance(payload, dict):
        return "响应格式不兼容"
    for key in ("msg", "message", "error", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return safe_error_message(value, fallback="未提供错误说明")[:180]
    return "未提供错误说明"


# 上游「任务级」消息里的失败语义：生成失败时 data.message 形如 "generate image failed"。
# 审核拒绝也走同一条路径（"此内容可能违反了我们的内容政策 请更换提示词或图片内容后重试"），
# 不收录这些词就会退化成「还在处理中」，白轮询到 240s 超时并报错成「分段超时」。
_SUCHUANG_TASK_FAILURE_TOKENS: tuple[str, ...] = (
    "failed",
    "failure",
    "fail",
    "失败",
    "错误",
    "异常",
    "违反",
    "违规",
    "内容政策",
    "敏感",
    "请更换提示词",
    "policy",
    "violat",
    "sensitive",
    "blocked",
    "rejected",
)


def _suchuang_task_message(data: object) -> str:
    """读任务级消息（data.message / data.msg）；顶层 msg 只说明这次查询成功与否。

    上游对失败的生成会回 ``status=3`` 且 ``data.message="generate image failed"``：
    状态码本身区分不了「还在处理」与「生成失败」，唯一的判据是这条消息。
    """

    if not isinstance(data, dict):
        return ""
    for key in ("message", "msg", "error", "reason"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return safe_error_message(value, fallback="")[:180]
    return ""


def _suchuang_task_failed(task_message: str) -> bool:
    lowered = str(task_message or "").strip().casefold()
    if not lowered:
        return False
    return any(token in lowered for token in _SUCHUANG_TASK_FAILURE_TOKENS)


# 留痕时屏蔽的敏感键：上游详情响应理论上不含密钥，但这些内容会落库/落盘，先兜一层。
_TRACE_REDACT_KEYS = frozenset({"key", "api_key", "apikey", "token", "access_token", "authorization"})


def _trace_payload(value: Any, depth: int = 0) -> Any:
    """把上游响应裁剪成可安全留痕的形状：限深、限宽，并屏蔽密钥类字段。

    深度要留够（code → data → images → item → url 已经 4 层）：截太浅会把最关键的
    图片地址本身换成省略号，反而看不出"上游到底给没给图、挂在哪个键下"。
    """

    if depth >= 8:
        return "…"
    if isinstance(value, dict):
        return {
            str(name): (
                "***" if str(name).lower() in _TRACE_REDACT_KEYS else _trace_payload(item, depth + 1)
            )
            for name, item in list(value.items())[:20]
        }
    if isinstance(value, (list, tuple)):
        return [_trace_payload(item, depth + 1) for item in list(value)[:8]]
    return value


def suchuang_poll_trace(
    task_id: str,
    polls: int,
    elapsed: float,
    payload: object,
    status: str,
    *,
    raw_limit: int = 200,
) -> str:
    """把上游任务 ID 与最后一次轮询原文压成一行，用于生图失败留痕。

    上游失败时只回「调用成功」这类套话，而任务 ID 与原始状态此前既不落库也不打日志，
    一旦分段超时，事后既无法回查上游任务、也判断不出是上游卡住还是我们没认出已完成的响应。
    """

    try:
        raw = json.dumps(_trace_payload(payload), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = repr(payload)
    return (
        f"task={task_id or '<none>'} polls={polls} elapsed={elapsed:.0f}s "
        f"status={status or '<empty>'} raw={raw[:raw_limit]}"
    )


__all__ = [
    "GridImageProvider",
    "SUCHUANG_RESULT_TIMEOUT_SECONDS",
    "SuChuangImageProvider",
    "suchuang_message",
    "suchuang_poll_trace",
    "suchuang_size",
]
