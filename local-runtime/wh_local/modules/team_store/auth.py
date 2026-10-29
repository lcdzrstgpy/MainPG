"""账号识别：把请求头里的 Bearer token 换成 account_id。

团队服务不自己存账号密码，统一向现有账号服务确认身份，
避免两套账号体系对不上；结果缓存 60 秒，减少一次请求一次往返。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

CACHE_TTL_SECONDS = 60.0


class AccountResolver:
    def __init__(
        self,
        *,
        auth_base_url: str = "",
        dev_account_id: str = "",
        timeout: float = 3.0,
    ) -> None:
        self._base_url = str(auth_base_url or "").rstrip("/")
        self._dev_account_id = str(dev_account_id or "").strip()
        self._timeout = timeout
        self._cache: dict[str, tuple[str, str, float]] = {}

    def resolve(self, authorization: str | None) -> str:
        """返回账号 ID；无法确认身份时抛 :class:`PermissionError`。"""
        return self.resolve_identity(authorization)[0]

    def resolve_identity(self, authorization: str | None) -> tuple[str, str]:
        """返回（账号 ID，登录账号）；登录账号取不到时为空串。"""
        if self._dev_account_id:
            return self._dev_account_id, ""
        token = _bearer_token(authorization)
        if not token:
            raise PermissionError("缺少登录令牌")
        cached = self._cache.get(token)
        if cached and cached[2] > time.monotonic():
            return cached[0], cached[1]
        if not self._base_url:
            raise PermissionError("团队服务未配置账号服务地址")
        payload = self._fetch_me(token)
        account_id = _pick_account_id(payload)
        if not account_id:
            raise PermissionError("账号服务未返回账号标识")
        username = _pick_username(payload)
        self._cache[token] = (account_id, username, time.monotonic() + CACHE_TTL_SECONDS)
        return account_id, username

    def _fetch_me(self, token: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._base_url}/api/customer/me",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise PermissionError("登录已过期，请重新登录") from error
            raise PermissionError(f"账号服务返回 {error.code}") from error
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise PermissionError("无法连接账号服务") from error


def _bearer_token(authorization: str | None) -> str:
    text = str(authorization or "").strip()
    if not text.lower().startswith("bearer "):
        return ""
    return text[7:].strip()


def _pick_account_id(payload: Any) -> str:
    """在返回体里找到账号 ID，兼容字段被包在 user/account 里的情况。"""
    queue: list[Any] = [payload]
    while queue:
        item = queue.pop(0)
        if isinstance(item, dict):
            for key in ("account_id", "customer_id", "user_id"):
                value = item.get(key)
                if value:
                    return str(value)
            for value in item.values():
                if isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(item, list):
            queue.extend(item)
    return ""


def _pick_username(payload: Any) -> str:
    """在返回体里找到登录账号（登录时用的那串），找不到返回空串。"""
    queue: list[Any] = [payload]
    while queue:
        item = queue.pop(0)
        if isinstance(item, dict):
            for key in ("username", "user_name", "login_name", "login"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for value in item.values():
                if isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(item, list):
            queue.extend(item)
    return ""
