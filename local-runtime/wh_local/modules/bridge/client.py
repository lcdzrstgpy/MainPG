"""成本桥接转发客户端（本地 runtime → 采集服务器）。

共享密钥只留在本地进程里，浏览器发起的请求不携带它。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode


class BridgeApiError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class BridgeApiClient:
    def __init__(
        self, base_url: str, key: str, client_id: str, *, timeout: float = 30.0
    ) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._key = str(key or "").strip()
        self._client_id = str(client_id or "").strip() or "local"
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._base_url and self._key)

    @property
    def client_id(self) -> str:
        return self._client_id

    def bindings(self) -> dict[str, Any]:
        return self._request("GET", "/bindings", params={"client_id": self._client_id})

    def bind(self, phone: str, password: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/bindings",
            payload={
                "client_id": self._client_id,
                "phone": phone,
                "password": password,
            },
        )

    def unbind(self, account_id: str) -> dict[str, Any]:
        return self._request(
            "DELETE",
            "/bindings",
            params={"client_id": self._client_id, "account_id": account_id},
        )

    def items(self, **filters: Any) -> dict[str, Any]:
        params = {
            key: value for key, value in filters.items() if value not in (None, "")
        }
        params["client_id"] = self._client_id
        return self._request("GET", "/items", params=params)

    def costs(self) -> dict[str, Any]:
        return self._request("GET", "/costs", params={"client_id": self._client_id})

    def save_costs(
        self, account_id: str, items: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/costs",
            payload={
                "client_id": self._client_id,
                "account_id": account_id,
                "items": items,
            },
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise BridgeApiError("成本桥接未配置（缺少服务器地址或密钥）")
        url = f"{self._base_url}{path}"
        if params:
            query = urlencode(
                {key: value for key, value in params.items() if value not in (None, "")}
            )
            if query:
                url = f"{url}?{query}"
        body = None
        headers = {"X-Bridge-Key": self._key, "Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            raise BridgeApiError(
                _error_detail(error) or f"桥接服务返回 {error.code}",
                status=error.code,
            ) from error
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise BridgeApiError(f"无法连接桥接服务：{error}") from error


def _error_detail(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8") or "{}")
    except (ValueError, OSError):
        return ""
    detail = payload.get("detail")
    return detail if isinstance(detail, str) else ""
