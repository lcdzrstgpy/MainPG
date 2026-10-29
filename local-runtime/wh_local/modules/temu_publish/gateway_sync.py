"""服务商级 Temu 网关凭据的远端同步。

背景：YL client id / token 是整个系统共用的一对凭据，让每台机器手填不合理。
现在集中放在服务器（``/etc/wh-workbench/auth.env``），客户端登录后拉一次、
加密落到本人库；安装包里因此不需要带明文凭据。

设计要点：
- **只在本地没有凭据时**才去拉，拉到就落库，之后完全离线可用；
- 失败（未登录 / 服务器没配 / 网络不通）一律静默跳过，退回「随包下发文件」兜底；
- 失败后 60 秒内不再重试，避免每次进页面都白等一个 RTT。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

GATEWAY_PATH = "/api/customer/temu-gateway"
# 失败退避：进页面、切店铺都会调 status()，不能每次都去打远端
_RETRY_AFTER_SECONDS = 60.0
_TIMEOUT_SECONDS = 4.0

_lock = threading.Lock()
_last_failure: dict[str, float] = {}


def _should_skip(owner_id: str) -> bool:
    with _lock:
        failed_at = _last_failure.get(owner_id)
    return bool(failed_at and time.time() - failed_at < _RETRY_AFTER_SECONDS)


def _note_failure(owner_id: str) -> None:
    with _lock:
        _last_failure[owner_id] = time.time()


def _note_success(owner_id: str) -> None:
    with _lock:
        _last_failure.pop(owner_id, None)


def fetch_gateway_credentials(
    base_url: str,
    token: str,
    *,
    timeout: float = _TIMEOUT_SECONDS,
) -> dict[str, str] | None:
    """向客户中心要服务商凭据。拿不到就返回 None，不抛异常。"""
    if not base_url or not token:
        return None
    try:
        import requests  # noqa: PLC0415 - 只有真要拉的时候才需要

        session = requests.Session()
        # 用户机器上可能配了 HTTP_PROXY：访问自家服务器不能走代理
        session.trust_env = False
        response = session.get(
            base_url.rstrip("/") + GATEWAY_PATH,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
    except Exception:  # noqa: BLE001 - 网络/解析失败都按「这次没拿到」处理
        return None
    if not isinstance(payload, dict):
        return None
    client = str(payload.get("yl_client") or "").strip()
    secret = str(payload.get("yl_token") or "").strip()
    if not client or not secret:
        return None
    return {"yl_client": client, "yl_token": secret}


def sync_provider_credentials(
    database_path: Path,
    owner_id: str,
    *,
    base_url: str,
    token_resolver: Callable[[], str] | None,
) -> bool:
    """本地没有服务商凭据时，从服务器拉一次并加密落库。返回是否真的写入了。

    任何一步不满足都直接返回 False —— 调用方不需要 try/except。
    """
    owner = str(owner_id or "").strip()
    if not owner or token_resolver is None or _should_skip(owner):
        return False
    try:
        from .service import TemuCredentialStore  # noqa: PLC0415 - 避免循环导入

        store = TemuCredentialStore(database_path, owner_id=owner)
        current = store.load()
        if current.client and current.token:
            return False  # 已经有（可能是自己填的），绝不覆盖
        token = str(token_resolver() or "").strip()
        if not token:
            return False
        fetched = fetch_gateway_credentials(base_url, token)
        if not fetched:
            _note_failure(owner)
            return False
        store.save_provider_credentials(
            client=fetched["yl_client"],
            token=fetched["yl_token"],
            actor_id=owner,
        )
    except Exception:  # noqa: BLE001 - 同步失败不能影响设置页/发布链路
        _note_failure(owner)
        return False
    _note_success(owner)
    return True


def reset_backoff() -> None:
    """测试用：清掉失败退避。"""
    with _lock:
        _last_failure.clear()


def _debug_state() -> dict[str, Any]:
    with _lock:
        return dict(_last_failure)
