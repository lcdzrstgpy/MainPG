"""Shared, sanitized Ark chat transport for internal Doubao clients."""

from __future__ import annotations

import json
from typing import Any

import requests

from .server_ai_proxy import gateway_base_url, remote_token, usage_id


API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
MODEL_ID = "doubao-seed-2-0-mini-260428"
REQUEST_TIMEOUT_SECONDS = 60.0
USER_AGENT = "MainPG-Doubao/1.0"

_HTTP_SESSION = requests.Session()
_HTTP_SESSION.trust_env = False


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
    """One-attempt chat client routed through the platform gateway."""

    def __init__(self) -> None:
        self.platform_token = remote_token()
        self.usage_id = usage_id("text")
        if not self.platform_token or not self.usage_id:
            raise DoubaoArkError(
                "server-managed text usage is not reserved",
                error_kind="configuration",
                retryable=False,
            )
        # Compatibility for existing diagnostics; never an upstream credential.
        self.api_key = "server-managed"

    def complete(self, messages: list[dict[str, Any]]) -> str:
        response: requests.Response | None = None
        try:
            response = _HTTP_SESSION.post(
                f"{gateway_base_url()}/api/customer/ai/chat",
                headers={
                    "Authorization": f"Bearer {self.platform_token}",
                    "Content-Type": "application/json",
                    "User-Agent": USER_AGENT,
                },
                json={
                    "model": "gpt-5.6-terra",
                    "messages": messages,
                    "usage_id": self.usage_id,
                },
                timeout=REQUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            body = bytes(response.content)
        except (requests.RequestException, TimeoutError, OSError) as exc:
            raise DoubaoArkError(
                "Doubao Ark provider is temporarily unreachable",
                error_kind="transient",
                retryable=True,
            ) from exc
        finally:
            if response is not None:
                response.close()

        status_code = int(response.status_code)
        if status_code >= 400 or 300 <= status_code < 400:
            if status_code in {401, 403} or 400 <= status_code < 429:
                error_kind, retryable = "configuration", False
            elif status_code == 429 or status_code >= 500:
                error_kind, retryable = "transient", True
            else:
                error_kind, retryable = "provider_http", False
            raise DoubaoArkError(
                f"Doubao Ark provider returned HTTP {status_code}",
                error_kind=error_kind,
                retryable=retryable,
                status_code=status_code,
            )

        try:
            payload = json.loads(body.decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise DoubaoArkError(
                "Doubao Ark provider returned an invalid response",
                error_kind="invalid_response",
                retryable=False,
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise DoubaoArkError(
                "Doubao Ark provider returned empty content",
                error_kind="invalid_response",
                retryable=False,
            )
        return content.strip()
