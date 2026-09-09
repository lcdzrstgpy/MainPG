"""轻量版启动器日志上传（独立于主程序，不引入 wh_local）。

launcher 是独立 onefile（windowed），无法直接复用 wh_local.customer.remote_client。
本模块仅使用标准库 urllib，实现：
  - login()：用户账户/密码 → 平台认证服务换取 remote_token
  - upload_log()：携带 token 将本地 runtime.log 上报到服务器对应位置

认证与上报端点与 wh_local/customer/remote_client.py 保持一致：
  - 认证 base：https://workbench.haocoming.top/auth-api
  - 登录：POST /api/customer/login
  - 上报：POST /api/customer/log-upload（本模块新增）
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

AUTH_BASE = "https://workbench.haocoming.top/auth-api"
_LOG_FILE_NAME = "runtime.log"
_MAX_LOG_PAYLOAD_BYTES = 16 * 1024 * 1024  # 16MB 上限，避免超大日志


class LogUploadError(ValueError):
    """登录/上传失败。"""


def runtime_log_path() -> Path:
    """本地运行日志路径：冻结时 %APPDATA%\\MainPG\\runtime.log，源码运行时 cwd/runtime.log。"""
    if getattr(sys, "frozen", False):
        appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return appdata / "MainPG" / _LOG_FILE_NAME
    return Path.cwd() / _LOG_FILE_NAME


def login(username: str, password: str, timeout: float = 10.0) -> tuple[str, dict[str, Any]]:
    """用账户/密码登录，返回 (remote_token, account)。"""
    if not username.strip() or not password:
        raise LogUploadError("请输入用户名与密码")
    body = json.dumps({"username": username.strip(), "password": password}).encode("utf-8")
    req = Request(
        AUTH_BASE + "/api/customer/login",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "MainPG-Launcher"},
    )
    data = _post_json(req, timeout)
    if not data.get("ok"):
        detail = data.get("detail") or data.get("message") or "登录失败"
        raise LogUploadError(_friendly_auth_error(str(detail)))
    token = data.get("token")
    if not token:
        raise LogUploadError("登录成功但未返回凭证，请重试")
    return str(token), data.get("account") or {}


def upload_log(token: str, log_path: Path, timeout: float = 60.0) -> dict[str, Any]:
    """上传日志文件到服务器，返回服务器记录的信息。"""
    if not log_path or not log_path.exists():
        raise LogUploadError(f"找不到日志文件：{log_path}")
    raw = log_path.read_bytes()
    if len(raw) > _MAX_LOG_PAYLOAD_BYTES:
        raw = raw[-_MAX_LOG_PAYLOAD_BYTES:]  # 只取末尾（最新）部分
    content_b64 = base64.b64encode(raw).decode("ascii")
    payload = {
        "log_name": log_path.name,
        "log_size": len(raw),
        "content_b64": content_b64,
        "app_version": _app_version(),
        "platform": sys.platform,
    }
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        AUTH_BASE + "/api/customer/log-upload",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "MainPG-Launcher",
        },
    )
    data = _post_json(req, timeout)
    if not data.get("ok"):
        detail = data.get("detail") or data.get("message") or "上传失败"
        raise LogUploadError(str(detail))
    return data


def _post_json(req: Request, timeout: float) -> dict[str, Any]:
    attempt = 0
    last_error: Exception | None = None
    while attempt < 3:
        try:
            with urlopen(req, timeout=timeout) as resp:  # nosec B310: fixed allowlisted host
                body = resp.read()
            decoded = json.loads(body)
            if isinstance(decoded, dict):
                return decoded
            return {"ok": False, "detail": "服务器返回异常数据"}
        except HTTPError as exc:
            last_error = exc
            detail = "HTTP 错误"
            try:
                decoded = json.loads(exc.read())
                if isinstance(decoded, dict):
                    detail = str(decoded.get("detail") or decoded.get("message") or detail)
            except Exception:  # noqa: BLE001
                pass
            if exc.code == 401:
                raise LogUploadError("登录凭证已失效，请重新登录") from exc
            raise LogUploadError(detail) from exc
        except URLError as exc:
            last_error = exc
            attempt += 1
            if attempt >= 3:
                break
            time.sleep(0.5 * attempt)
    raise LogUploadError(f"无法连接服务器（网络异常）：{last_error}") from last_error


def _friendly_auth_error(detail: str) -> str:
    low = detail.lower()
    if "password" in low or "invalid" in low or "not found" in low:
        return "用户名或密码错误"
    if "not active" in low or "disabled" in low:
        return "账号未激活或已被禁用"
    return detail


def _app_version() -> str:
    try:
        from . import update  # type: ignore

        return update.current_version()
    except Exception:  # noqa: BLE001
        return "unknown"
