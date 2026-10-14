"""远端会话令牌的本地加密存储（Windows DPAPI，用户级凭证）。

背景
----
``remote_token`` 原本只存在进程内存（``db_store._remote_tokens``），本地后端一重启
就全部丢失：前端仍持有有效的本地会话，但任何需要远端令牌的接口都会返回 401
``remote customer session is missing``，前端据此判定"会话失效"并退回登录页 ——
用户表现为"登录态过一会儿就掉"。而本地后端在这套桌面运行时里被回收/重启是常态
（看门狗、更新、异常退出），所以这个问题会反复出现。

本模块把远端令牌用 Windows DPAPI（``CryptProtectData``，用户级凭证）加密后写入
``customer_sessions.remote_token`` 列，使后端重启后仍能恢复会话：

* 密文绑定**当前 Windows 用户**，换机器 / 换用户 / 重装系统都解不开（这也是选择
  DPAPI 而不是自管密钥的原因：没有需要另存的密钥);
* 落库形式统一为 ``dpapi:v1:<base64>``；不带该前缀的历史值一律视为无效，
  不当作明文使用；
* DPAPI 不可用（非 Windows、调用失败）时**整体降级**为原有行为（只存内存、
  写空串），绝不因加密失败而影响登录。
"""

from __future__ import annotations

import base64
import logging
import sys
from typing import Any, Callable

_LOGGER = logging.getLogger("wh_local.customer.token_vault")

# 落库格式前缀（带版本号，便于以后换算法）
VAULT_PREFIX = "dpapi:v1:"


def _primitives() -> tuple[Callable[[bytes], bytes] | None, Callable[[bytes], bytes] | None]:
    """惰性取 DPAPI 原语。

    直接复用 ``login_credentials`` 里已封装好的 ctypes 调用（同一项目内共用一份
    crypt32 声明与调用签名），避免重复维护两套 ABI 定义。
    """
    try:
        from ..login_credentials import _dpapi_protect, _dpapi_unprotect  # type: ignore
    except Exception:  # pragma: no cover - 导入期异常一律视为不可用
        return None, None
    return _dpapi_protect, _dpapi_unprotect


def vault_available() -> bool:
    """DPAPI 是否可用于落盘（非 Windows 一律不可用）。"""
    if sys.platform != "win32":
        return False
    protect_fn, _ = _primitives()
    return callable(protect_fn)


def protect_remote_token(remote_token: str) -> str:
    """加密远端令牌以便落库；返回 "" 表示"不落盘"（调用方写空串即可）。"""
    token = str(remote_token or "")
    if not token:
        return ""
    protect_fn, _ = _primitives()
    if not callable(protect_fn):
        return ""
    try:
        ciphertext: Any = protect_fn(token.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 加密失败只降级，不影响登录
        _LOGGER.debug("remote token protect failed, keep memory-only: %s", exc)
        return ""
    return VAULT_PREFIX + base64.b64encode(ciphertext).decode("ascii")


def unprotect_remote_token(stored: str) -> str:
    """解密落库的远端令牌；失败/非本机加密/脏数据一律返回 ""。"""
    raw = str(stored or "")
    if not raw.startswith(VAULT_PREFIX):
        # 只接受带前缀的密文：历史空串、以及任何意外写入的明文都不当作令牌使用。
        return ""
    _, unprotect_fn = _primitives()
    if not callable(unprotect_fn):
        return ""
    try:
        ciphertext = base64.b64decode(raw[len(VAULT_PREFIX) :], validate=True)
    except Exception:  # noqa: BLE001 - base64 损坏
        return ""
    try:
        return unprotect_fn(ciphertext).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - 换用户/换机器时解密必然失败
        _LOGGER.debug("remote token unprotect failed (another user/machine?): %s", exc)
        return ""
