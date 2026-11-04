"""服务商级 Temu 网关凭据的远端同步。

背景：YL client id / token 是整个系统共用的一对凭据，让每台机器手填不合理。
现在集中放在服务器（``/etc/wh-workbench/auth.env``），客户端登录后拉一次、
加密落到本人库；安装包里因此不需要带明文凭据。

设计要点：
- **只在本地没有凭据时**才去拉，拉到就落库，之后完全离线可用；
- 已经有时不盲拉：先看``指纹``变没变（按服务器给的那对凭据算摘要）。
  指纹变了 = 服务商换过凭据 → **自动覆盖本地**。这是「一份泄露能作废」的关键：
  服务器改完，所有装机用户最迟 5 分钟自动换成新值，不用发版、不用手填；
- 网关回报 client/token 失效时，把指纹清掉并强制重拉一次，用户重试即可恢复；
- 失败（未登录 / 服务器没配 / 网络不通）一律静默跳过，退回「随包下发文件」兜底；
- 失败后 60 秒内不再重试，避免每次进页面都白等一个 RTT。
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any, Callable

GATEWAY_PATH = "/api/customer/temu-gateway"
# 失败退避：进页面、切店铺都会调 status()，不能每次都去打远端
_RETRY_AFTER_SECONDS = 60.0
# 本地已有凭据时，最多 5 分钟跟服务器核对一次指纹（不是每次都问，避免拖慢发布）
_VERIFY_INTERVAL_SECONDS = 300.0
_TIMEOUT_SECONDS = 4.0

_lock = threading.Lock()
_last_failure: dict[str, float] = {}
_last_verified: dict[str, float] = {}


def provider_fingerprint(client: str, token: str) -> str:
    """一对凭据的指纹（不是凭据本身，不能反推）。

    服务器换过 YL client/token → 指纹一定变 → 客户端据此覆盖本地那份。
    只截 16 位十六进制，够区分、也不占地方。
    """
    raw = f"{str(client or '').strip()}\n{str(token or '').strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _should_skip(owner_id: str) -> bool:
    with _lock:
        failed_at = _last_failure.get(owner_id)
    return bool(failed_at and time.time() - failed_at < _RETRY_AFTER_SECONDS)


def _recently_verified(owner_id: str) -> bool:
    with _lock:
        ok_at = _last_verified.get(owner_id)
    return bool(ok_at and time.time() - ok_at < _VERIFY_INTERVAL_SECONDS)


def _note_failure(owner_id: str) -> None:
    with _lock:
        _last_failure[owner_id] = time.time()


def _note_success(owner_id: str) -> None:
    with _lock:
        _last_failure.pop(owner_id, None)
        _last_verified[owner_id] = time.time()


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
    force: bool = False,
) -> bool:
    """本地没有、或服务器上的凭据变了，就拉一次并加密落库。返回是否真的写入了。

    ``force=True`` 用于「网关说 client/token 失效」之后 —— 跳过所有节流，立刻重拉。

    任何一步不满足都直接返回 False —— 调用方不需要 try/except。
    """
    owner = str(owner_id or "").strip()
    if not owner or token_resolver is None:
        return False
    if not force and _should_skip(owner):
        return False
    try:
        from .service import TemuCredentialStore  # noqa: PLC0415 - 避免循环导入

        store = TemuCredentialStore(database_path, owner_id=owner)
        current = store.load()
        has_credentials = bool(current.client and current.token)
        # 本地有凭据时不必每次都问服务器：5 分钟核对一次指纹就够了。
        # （服务商换 token 后最迟 5 分钟自动生效；用户重试发布也会触发）
        if has_credentials and not force and _recently_verified(owner):
            return False
        token = str(token_resolver() or "").strip()
        if not token:
            return False
        fetched = fetch_gateway_credentials(base_url, token)
        if not fetched:
            _note_failure(owner)
            return False
        version = provider_fingerprint(fetched["yl_client"], fetched["yl_token"])
        unchanged = (
            has_credentials
            and fetched["yl_client"] == current.client
            and fetched["yl_token"] == current.token
        )
        if unchanged and store.provider_version() == version:
            # 值和指纹都没变：不写库，只记下「刚核对过」
            _note_success(owner)
            return False
        store.save_provider_credentials(
            client=fetched["yl_client"],
            token=fetched["yl_token"],
            actor_id=owner,
            version=version,
        )
    except Exception:  # noqa: BLE001 - 同步失败不能影响设置页/发布链路
        _note_failure(owner)
        return False
    _note_success(owner)
    return True


def mark_provider_credentials_stale(database_path: Path, owner_id: str) -> None:
    """网关说 client/token 失效时调用：只忘掉指纹，下次同步就会重新拉一遍。

    不清凭据本身：万一是网关抽风/临时限流，凭据还是好的，清掉反而让用户
    「本来能用突然不能用了」。清指纹的语义是「下次核对时以服务器为准」。
    """
    owner = str(owner_id or "").strip()
    if not owner:
        return
    try:
        from .service import TemuCredentialStore  # noqa: PLC0415

        TemuCredentialStore(database_path, owner_id=owner).clear_provider_version()
    except Exception:  # noqa: BLE001 - 标记失败不影响把原始错误抛给用户
        pass
    with _lock:
        _last_verified.pop(owner, None)


def reset_backoff() -> None:
    """测试用：清掉失败退避与核对节流。"""
    with _lock:
        _last_failure.clear()
        _last_verified.clear()


def _debug_state() -> dict[str, Any]:
    with _lock:
        return {"failure": dict(_last_failure), "verified": dict(_last_verified)}
