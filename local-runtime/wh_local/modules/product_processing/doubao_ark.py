"""Shared, sanitized Ark chat transport for internal Doubao clients."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import requests

from .server_ai_proxy import gateway_base_url, granted_key, remote_token, usage_id
from ...config import is_ip_literal_host


API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL_ID = "doubao-seed-2-1-lite-260915"
# 服务端单次上游文本调用上限是 240s（多 SKU 翻译分批时经常远超 60s）。客户端若沿用
# 60s 会在服务端仍在处理时放弃，随后的重试立即命中网关
# 409「identical gateway request is already in progress」，重试预算被白白耗尽。
REQUEST_TIMEOUT_SECONDS = 180.0
# 网关对同一 usage+request 的在途请求一律回 409，且这类 409 不计尝试次数；服务端完成后
# 会直接回放缓存结果。因此「在途」要按间隔轮询等待，而不是当成失败立刻重试。
# 只匹配 to be 前缀无关的片段，兼容服务端文案微调；额度耗尽类的 409 不含该片段。
GATEWAY_IN_PROGRESS_DETAIL = "already in progress"
GATEWAY_IN_PROGRESS_POLL_SECONDS = 5.0
GATEWAY_IN_PROGRESS_WAIT_BUDGET_SECONDS = 300.0
# 多图视觉识别（主体分析）实测单次可超过 60s；放宽超时避免被误判为 transient 超时，
# 否则会出现「60s 超时 × 3 重试 ≈ 180s+」的伪失败。
VISION_TIMEOUT_SECONDS = 120.0
USER_AGENT = "MainPG-Doubao/1.0"

_HTTP_SESSION = requests.Session()
_HTTP_SESSION.trust_env = False
_SERVER_AI_REQUEST_GATE = threading.BoundedSemaphore(2)


# 未归类 4xx（provider_http）的重试预算。历史上这一档被当成「不可重试」直接抛，
# 但生产数据（2026-08 至 09）显示：504 次 provider_http 失败**全部只尝试了 1 次**
# 就整单判死，平均耗时仅 13.8s（远低于 120s 超时），而对外文案却是「请稍后重试」
# —— 内部判定与对外承诺自相矛盾。上游 4xx 里混着瞬时抖动（网关限流变体、在途
# 冲突的其它表达、上游短暂 refusal），给一次追加尝试即可救回；真属坏请求时也只
# 多花一次调用。上限固定为 2（首次 + 一次追加），不占用 MAX_ATTEMPTS 的三次预算。
_PROVIDER_HTTP_MAX_ATTEMPTS = 2


def _classify_http_status(status_code: int) -> tuple[str, bool]:
    """Classify an Ark HTTP status into a (error_kind, retryable) pair.

    401/403 are persistent credential/config problems. 408/409/425/429 and any
    5xx are transient and worth retrying. Remaining 4xx are opaque provider
    errors: not permanently hopeless, but budgeted to
    ``_PROVIDER_HTTP_MAX_ATTEMPTS`` instead of the full retry budget.
    """
    if status_code in {401, 403}:
        return "configuration", False
    if status_code in {408, 409, 425, 429} or status_code >= 500:
        return "transient", True
    return "provider_http", True


def _retry_budget_for(error_kind: str) -> int | None:
    """Per-error-kind cap on total attempts, or None to use the caller's default."""
    if error_kind == "provider_http":
        return _PROVIDER_HTTP_MAX_ATTEMPTS
    return None


def _extract_upstream_detail(body: bytes) -> str | None:
    """Pull a short, printable error detail (code/message) out of an Ark/网关 body.

    Returns None when the body is not the expected JSON error envelope. The result
    is intentionally truncated and stripped of control chars so it is safe to persist
    in task diagnostics without leaking credentials.
    """
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    parts: list[str] = []
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        if code:
            parts.append(f"code={str(code)[:80]}")
        if message:
            parts.append(str(message)[:200])
    else:
        # 平台网关用 FastAPI 的 {"detail": "..."} 表达 4xx/5xx。只认 error 包装会让
        # 409/429 的真实原因被整段丢弃，失败诊断退化成不可区分的「HTTP 409」。
        detail = payload.get("detail")
        if isinstance(detail, str):
            parts.append(detail[:200])
        elif isinstance(detail, dict):
            parts.append(json.dumps(detail, ensure_ascii=False)[:200])
    if not parts:
        return None
    detail = "；".join(part for part in parts if part)
    detail = "".join(ch for ch in detail if ch.isprintable()).strip()
    return detail or None


def _retry_after_seconds(response: requests.Response | None) -> float | None:
    """Read a bounded Retry-After delay from a gateway response, when present."""
    if response is None:
        return None
    headers = getattr(response, "headers", None) or {}
    raw = str(headers.get("Retry-After", "") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, min(float(raw), 600.0))
    except ValueError:
        return None


def _is_gateway_conflict_terminal(detail: str | None) -> bool:
    """True when a 409 means the reserved usage has exhausted its gateway budget.

    Those rows never become retryable in place: replaying the same usage only burns
    more attempts, so the caller must stop instead of retrying.
    """
    return bool(detail and "limit reached for reserved usage" in detail)


def _decode_gateway_content(body: bytes) -> str:
    """Decode a 2xx gateway body into the assistant message content."""
    try:
        payload = json.loads(body.decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise DoubaoArkError(
            "server text-and-vision gateway returned an invalid response",
            error_kind="invalid_response",
            retryable=False,
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise DoubaoArkError(
            "server text-and-vision gateway returned empty content",
            error_kind="invalid_response",
            retryable=False,
        )
    return content.strip()


class DoubaoArkError(RuntimeError):
    """Sanitized Ark failure safe for persisted task diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        retryable: bool,
        status_code: int | None = None,
        attempt_count: int = 0,
        upstream_detail: str | None = None,
        retry_after: float | None = None,
        max_attempts: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_kind = str(error_kind)
        self.retryable = bool(retryable)
        self.status_code = status_code
        self.attempt_count = max(0, int(attempt_count))
        # 上游（网关或火山方舟）返回的脱敏错误详情（code/message），用于区分 400/413/404，
        # 避免只看到一个不透光的 provider_http。
        self.upstream_detail = upstream_detail
        # 上游给出的建议等待秒数（Retry-After）。上层退避优先采用它，避免 0.5s 抢跑
        # 让服务端仍在处理时被反复重试。
        self.retry_after = retry_after
        # 本次错误允许的总尝试次数上限（None = 用调用方的默认预算）。provider_http 这类
        # 「可重试但预算小」的错误靠它把追加尝试压到 1 次，不占用常规三次预算。
        self.max_attempts = max(1, int(max_attempts)) if max_attempts else None


class DoubaoArkClient:
    """One-attempt chat client.

    In direct mode the client uses the short-lived Ark key granted at batch
    freeze time and calls the upstream directly.  Otherwise it falls back to
    the legacy server-managed gateway so older clients keep working (gray
    rollout keeps both paths live).
    """

    def __init__(self, usage_kind: str = "text") -> None:
        self.granted_key = granted_key("ark")
        self.direct = bool(self.granted_key)
        self.platform_token = remote_token()
        self.usage_id = usage_id(usage_kind)
        # 兼容：旧服务端未声明 vision 独立计费时，视觉识别不单独预留，此时
        # 回落到主链路（text）的 usage_id，避免触发 "usage is not reserved" 失败。
        if (
            usage_kind == "vision"
            and not self.usage_id
            and not self.direct
            and usage_id("text")
        ):
            self.usage_id = usage_id("text")
        if not self.direct and (not self.platform_token or not self.usage_id):
            raise DoubaoArkError(
                "server-managed usage is not reserved",
                error_kind="configuration",
                retryable=False,
            )
        # Compatibility for existing diagnostics; never logs the real key.
        self.api_key = self.granted_key if self.direct else "server-managed"

    def complete(
        self, messages: list[dict[str, Any]], *, timeout: float = REQUEST_TIMEOUT_SECONDS
    ) -> str:
        if self.direct:
            return self._complete_direct(messages, timeout=timeout)
        return self._complete_gateway(messages, timeout=timeout)

    def _complete_gateway(
        self, messages: list[dict[str, Any]], *, timeout: float = REQUEST_TIMEOUT_SECONDS
    ) -> str:
        # 网关对「同一 usage+request 已在处理中」回 409，并**不**计入尝试额度；服务端完成后
        # 会直接回放缓存结果。这种 409 不代表失败，必须原地等待重发；若交给上层立刻重试，
        # 只会连续撞上同一个在途 409，三次预算被全部打空，最终对外报 HTTP 409
        # / text_service_unavailable，标题也因此保持采集原值（中文）且标 attention_required。
        wait_deadline = time.monotonic() + GATEWAY_IN_PROGRESS_WAIT_BUDGET_SECONDS
        while True:
            status_code, body, retry_after = self._post_gateway(messages, timeout=timeout)
            if 200 <= status_code < 300:
                break
            detail = _extract_upstream_detail(body)
            if (
                status_code == 409
                and detail
                and GATEWAY_IN_PROGRESS_DETAIL in detail
                and time.monotonic() + GATEWAY_IN_PROGRESS_POLL_SECONDS <= wait_deadline
            ):
                time.sleep(GATEWAY_IN_PROGRESS_POLL_SECONDS)
                continue
            error_kind, retryable = _classify_http_status(status_code)
            if _is_gateway_conflict_terminal(detail):
                # 预留额度或同请求尝试次数已耗尽：重放只会继续消耗额度，属终局失败。
                error_kind, retryable = "quota_exhausted", False
            raise DoubaoArkError(
                f"server text-and-vision gateway returned HTTP {status_code}",
                error_kind=error_kind,
                retryable=retryable,
                status_code=status_code,
                upstream_detail=detail,
                retry_after=retry_after,
                max_attempts=_retry_budget_for(error_kind),
            )
        return _decode_gateway_content(body)

    def _post_gateway(
        self, messages: list[dict[str, Any]], *, timeout: float
    ) -> tuple[int, bytes, float | None]:
        """Send one gateway request and return (status, body, retry_after).

        The two-slot gate is held only for the round trip, never while waiting for an
        in-flight duplicate to finish.
        """
        response: requests.Response | None = None
        try:
            with _SERVER_AI_REQUEST_GATE:
                # IP-direct gateway connections cannot match the public certificate
                # hostname; skip verification only for bare IP literals.
                response = _HTTP_SESSION.post(
                    f"{gateway_base_url()}/api/customer/ai/chat",
                    headers={
                        "Authorization": f"Bearer {self.platform_token}",
                        "Content-Type": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                    json={
                        # 旧网关契约：服务端用该 model 路由文本模型（灰度回退路径保持原样）。
                        "model": "gpt-5.6-terra",
                        "messages": messages,
                        "usage_id": self.usage_id,
                    },
                    timeout=timeout,
                    allow_redirects=False,
                    verify=not is_ip_literal_host(gateway_base_url()),
                )
                body = bytes(response.content)
                return int(response.status_code), body, _retry_after_seconds(response)
        except (requests.RequestException, TimeoutError, OSError) as exc:
            raise DoubaoArkError(
                "server text-and-vision gateway is temporarily unreachable",
                error_kind="transient",
                retryable=True,
            ) from exc
        finally:
            if response is not None:
                response.close()

    def _complete_direct(
        self, messages: list[dict[str, Any]], *, timeout: float = REQUEST_TIMEOUT_SECONDS
    ) -> str:
        response: requests.Response | None = None
        try:
            response = _HTTP_SESSION.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self.granted_key}",
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
                json={
                    "model": MODEL_ID,
                    "messages": messages,
                },
                timeout=timeout,
                allow_redirects=False,
            )
            body = bytes(response.content)
        except (requests.RequestException, TimeoutError, OSError) as exc:
            raise DoubaoArkError(
                "ark upstream is temporarily unreachable",
                error_kind="transient",
                retryable=True,
            ) from exc
        finally:
            if response is not None:
                response.close()

        status_code = int(response.status_code)
        if status_code >= 400 or 300 <= status_code < 400:
            error_kind, retryable = _classify_http_status(status_code)
            raise DoubaoArkError(
                f"ark upstream returned HTTP {status_code}",
                error_kind=error_kind,
                retryable=retryable,
                status_code=status_code,
                upstream_detail=_extract_upstream_detail(body),
                max_attempts=_retry_budget_for(error_kind),
            )

        try:
            payload = json.loads(body.decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise DoubaoArkError(
                "ark upstream returned an invalid response",
                error_kind="invalid_response",
                retryable=False,
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise DoubaoArkError(
                "ark upstream returned empty content",
                error_kind="invalid_response",
                retryable=False,
            )
        return content.strip()
