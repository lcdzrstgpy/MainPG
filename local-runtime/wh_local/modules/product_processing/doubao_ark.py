"""Shared, sanitized Ark chat transport for internal Doubao clients."""

from __future__ import annotations

import json
import threading
from typing import Any

import requests

from .server_ai_proxy import gateway_base_url, granted_key, remote_token, usage_id


API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL_ID = "doubao-seed-2-0-mini-260428"
# 文本调用通常很快，保持 60s 默认即可。
REQUEST_TIMEOUT_SECONDS = 60.0
# 多图视觉识别（主体分析）实测单次可超过 60s；放宽超时避免被误判为 transient 超时，
# 否则会出现「60s 超时 × 3 重试 ≈ 180s+」的伪失败。
VISION_TIMEOUT_SECONDS = 120.0
USER_AGENT = "MainPG-Doubao/1.0"

_HTTP_SESSION = requests.Session()
_HTTP_SESSION.trust_env = False
_SERVER_AI_REQUEST_GATE = threading.BoundedSemaphore(2)


def _classify_http_status(status_code: int) -> tuple[str, bool]:
    """Classify an Ark HTTP status into a (error_kind, retryable) pair.

    401/403 are persistent credential/config problems. 408/409/425/429 and any
    5xx are transient and worth retrying. The remaining 4xx (bad body, upstream
    refusal) are non-retryable provider errors.
    """
    if status_code in {401, 403}:
        return "configuration", False
    if status_code in {408, 409, 425, 429} or status_code >= 500:
        return "transient", True
    return "provider_http", False


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
    ) -> None:
        super().__init__(message)
        self.error_kind = str(error_kind)
        self.retryable = bool(retryable)
        self.status_code = status_code
        self.attempt_count = max(0, int(attempt_count))


class DoubaoArkClient:
    """One-attempt chat client.

    In direct mode the client uses the short-lived Ark key granted at batch
    freeze time and calls the upstream directly.  Otherwise it falls back to
    the legacy server-managed gateway so older clients keep working (gray
    rollout keeps both paths live).
    """

    def __init__(self) -> None:
        self.granted_key = granted_key("ark")
        self.direct = bool(self.granted_key)
        self.platform_token = remote_token()
        self.usage_id = usage_id("text")
        if not self.direct and (not self.platform_token or not self.usage_id):
            raise DoubaoArkError(
                "server-managed text usage is not reserved",
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
        response: requests.Response | None = None
        try:
            with _SERVER_AI_REQUEST_GATE:
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
                )
                body = bytes(response.content)
        except (requests.RequestException, TimeoutError, OSError) as exc:
            raise DoubaoArkError(
                "server text-and-vision gateway is temporarily unreachable",
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
                f"server text-and-vision gateway returned HTTP {status_code}",
                error_kind=error_kind,
                retryable=retryable,
                status_code=status_code,
            )

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
