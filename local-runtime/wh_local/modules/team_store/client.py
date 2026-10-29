"""本地工作台调用远端团队服务的客户端。

只做三件事：登记店铺拿店铺码、填写员工标识、查询自己的画像。
断网或服务不可用时由调用方降级为本地临时码，不影响用户继续采集。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote


class TeamApiError(RuntimeError):
    def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class TeamApiClient:
    def __init__(self, base_url: str, *, timeout: float = 6.0) -> None:
        self._base_url = str(base_url or "").rstrip("/")
        self._timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    def profile(self, token: str) -> dict[str, Any]:
        return self._request("GET", "/profile", token)

    def save_profile(
        self,
        token: str,
        *,
        staff_no: str,
        username: str = "",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/profile",
            token,
            {"staff_no": staff_no, "username": username},
        )

    def register_store(
        self,
        token: str,
        *,
        name: str,
        platform: str = "temu",
        mall_id: str = "",
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/stores",
            token,
            {"name": name, "platform": platform, "mall_id": mall_id},
        )

    def list_stores(self, token: str) -> dict[str, Any]:
        return self._request("GET", "/stores", token)

    def submit_rollup(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/rollup/submit", token, payload)

    def list_submissions(self, token: str) -> dict[str, Any]:
        return self._request("GET", "/submissions", token)

    # ---------- 账号关联（好友申请式） ----------

    def links(self, token: str) -> dict[str, Any]:
        return self._request("GET", "/links", token)

    def request_link(self, token: str, target_account_id: str) -> dict[str, Any]:
        return self._request(
            "POST", "/links", token, {"target_account_id": str(target_account_id or "")}
        )

    def accept_link(self, token: str, account_id: str) -> dict[str, Any]:
        return self._request("POST", f"/links/{quote(str(account_id), safe='')}/accept", token)

    def reject_link(self, token: str, account_id: str) -> dict[str, Any]:
        return self._request("POST", f"/links/{quote(str(account_id), safe='')}/reject", token)

    def remove_link(self, token: str, account_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/links/{quote(str(account_id), safe='')}", token)

    def _request(
        self,
        method: str,
        path: str,
        token: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._base_url:
            raise TeamApiError("团队服务未配置")
        if not token:
            raise TeamApiError("缺少登录令牌", status=401, code="unauthorized")
        body = None
        headers = {"Authorization": f"Bearer {token}"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            detail = _error_detail(error)
            raise TeamApiError(
                detail or f"团队服务返回 {error.code}",
                status=error.code,
                code=_error_code(detail),
            ) from error
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise TeamApiError(f"无法连接团队服务：{error}") from error


def _error_detail(error: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8") or "{}")
    except (ValueError, OSError):
        return ""
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail
    return ""


def _error_code(detail: str) -> str:
    """把「首次创建店铺需要先填写员工标识」这类文案收敛成稳定的错误码。"""
    if "员工标识" in detail and ("填写" in detail or "需要" in detail):
        return "staff_no_required"
    if "登录" in detail:
        return "unauthorized"
    return ""
