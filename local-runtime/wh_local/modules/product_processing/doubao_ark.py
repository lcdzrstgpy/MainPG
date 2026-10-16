"""Shared, sanitized Ark chat transport for internal Doubao clients."""

from __future__ import annotations

import json
from typing import Any

import requests

from .server_ai_proxy import granted_key


API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL_ID = "doubao-seed-2-1-lite-260915"
# 服务端单次上游文本调用上限是 240s（多 SKU 翻译分批时经常远超 60s）。客户端若沿用
# 60s 会在服务端仍在处理时放弃，随后的重试立即命中网关
# 409「identical gateway request is already in progress」，重试预算被白白耗尽。
REQUEST_TIMEOUT_SECONDS = 180.0
# 多图视觉识别（主体分析）在图像下采样前实测单次逼近 120s；下采样后应明显回落，
# 但多图 + 上游排队仍可能超过 120s。放到 180s（与服务端单次上游调用上限 240s 留出余量），
# 避免「逼近超时线 → 被判 transient 超时 → 重试 ≈ 翻倍」的伪失败。
VISION_TIMEOUT_SECONDS = 180.0
USER_AGENT = "MainPG-Doubao/1.0"

_HTTP_SESSION = requests.Session()
_HTTP_SESSION.trust_env = False


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

    客户端固定直连：只使用批次冻结下发的短期 Ark 密钥直连火山方舟，不再回落到
    平台托管网关。缺少直连密钥说明当前不在直连任务上下文，立即报错，由上层中止
    任务并把具体原因上报服务器。
    """

    def __init__(self, usage_kind: str = "text") -> None:  # usage_kind 保留以兼容既有调用签名
        self.granted_key = granted_key("ark")
        if not self.granted_key:
            raise DoubaoArkError(
                "ark direct key is not granted for this batch",
                error_kind="configuration",
                retryable=False,
            )
        self.direct = True
        # Compatibility for existing diagnostics; never logs the real key.
        self.api_key = self.granted_key

    def complete(
        self, messages: list[dict[str, Any]], *, timeout: float = REQUEST_TIMEOUT_SECONDS
    ) -> str:
        return self._complete_direct(messages, timeout=timeout)

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
                    # 深度思考统一关闭：本模块都是识别/改写/翻译类请求，开深度思考只会让
                    # 单条商品处理多等数十秒（与 POD 模块保持同一策略）。
                    "thinking": {"type": "disabled"},
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
