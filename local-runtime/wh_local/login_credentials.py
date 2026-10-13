"""登录页「账号管理」的本地凭据存储（仅本机）。

- 账号列表与可选保存的密码统一写入数据目录的 ``login-credentials.bin``；
- 文件整体经 Windows DPAPI 加密（CryptProtectData，用户级凭证）：密文绑定
  当前 Windows 登录用户，文件被拷贝到别的用户 / 机器后无法解密；
- DPAPI 不可用（非 Windows 环境）时写入抛出 ``LoginCredentialsUnavailable``，
  接口返回 503，前端静默降级为「无账号记忆」，不影响正常登录流程；
- 登录页展示时用户尚未登录，接口不挂业务会话鉴权；本地服务仅监听回环地址，
  与浏览器本地存储同属「本机信任域」。
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

_LOGGER = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

_MAX_ACCOUNTS = 8
_MAX_IDENTIFIER_LENGTH = 256
_MAX_PASSWORD_LENGTH = 512
_STORE_FILE_NAME = "login-credentials.bin"


class LoginCredentialsUnavailable(RuntimeError):
    """DPAPI 不可用或存储文件无法访问，功能整体降级。"""


class _DpapiDecryptError(RuntimeError):
    """密文无法在当前用户下解密（文件损坏或跨用户拷贝）。"""


# ---------------------------------------------------------------- DPAPI 封装


class _DataBlob(ctypes.Structure):
    # DWORD = 32 位无符号；直接声明等价类型，避免依赖 ctypes.wintypes 的平台导入。
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


_CRYPT32: tuple[Any, Any] | None = None
_CRYPT32_LOCK = threading.Lock()


def _load_crypt32() -> tuple[Any, Any]:
    """惰性加载 crypt32/kernel32 并固定 64 位安全的调用签名。"""
    if not _IS_WINDOWS:
        raise LoginCredentialsUnavailable("DPAPI is only available on Windows")
    global _CRYPT32
    if _CRYPT32 is not None:
        return _CRYPT32
    with _CRYPT32_LOCK:
        if _CRYPT32 is not None:
            return _CRYPT32
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_ptr = ctypes.POINTER(_DataBlob)
        crypt32.CryptProtectData.argtypes = [
            blob_ptr, ctypes.c_void_p, blob_ptr,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, blob_ptr,
        ]
        crypt32.CryptProtectData.restype = ctypes.c_int
        crypt32.CryptUnprotectData.argtypes = [
            blob_ptr, ctypes.c_void_p, blob_ptr,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, blob_ptr,
        ]
        crypt32.CryptUnprotectData.restype = ctypes.c_int
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        _CRYPT32 = (crypt32, kernel32)
        return _CRYPT32


def _blob_from(data: bytes, *, keep_alive: list[Any]) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data, max(len(data), 1))
    keep_alive.append(buffer)  # 生命周期挂到调用方，防止提前回收
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _dpapi_protect(data: bytes) -> bytes:
    crypt32, kernel32 = _load_crypt32()
    alive: list[Any] = []
    blob_in = _blob_from(data, keep_alive=alive)
    blob_out = _DataBlob()
    ok = crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise LoginCredentialsUnavailable(
            f"CryptProtectData failed (winerror={ctypes.get_last_error()})"
        )
    try:
        return bytes(ctypes.string_at(blob_out.pbData, blob_out.cbData))
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def _dpapi_unprotect(ciphertext: bytes) -> bytes:
    crypt32, kernel32 = _load_crypt32()
    alive: list[Any] = []
    blob_in = _blob_from(ciphertext, keep_alive=alive)
    blob_out = _DataBlob()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise _DpapiDecryptError(
            f"CryptUnprotectData failed (winerror={ctypes.get_last_error()})"
        )
    try:
        return bytes(ctypes.string_at(blob_out.pbData, blob_out.cbData))
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


# ---------------------------------------------------------------- 工具函数


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_identifier(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError("identifier is empty")
    if len(clean) > _MAX_IDENTIFIER_LENGTH:
        raise ValueError("identifier is too long")
    return clean


def _normalize_password(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    if len(text) > _MAX_PASSWORD_LENGTH:
        raise ValueError("password is too long")
    return text


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        with open(temporary, "xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


# ---------------------------------------------------------------- 存储


class LoginCredentialStore:
    """账号列表 + 可选密码的加密文件存储（线程安全）。"""

    def __init__(self, path: Path, *, max_accounts: int = _MAX_ACCOUNTS) -> None:
        self._path = Path(path)
        self._max_accounts = max(1, int(max_accounts))
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def list_entries(self) -> list[dict[str, Any]]:
        """账号列表（最近使用在前），绝不包含明文密码。"""
        with self._lock:
            document = self._load()
            return [self._public(item) for item in document["accounts"]]

    def save(
        self,
        identifier: str,
        *,
        password: str | None = None,
        remember: bool = False,
    ) -> dict[str, Any]:
        """登录成功后的 upsert。

        - ``remember=True`` 且提供了密码：保存 / 更新密码；
        - ``remember=False``：保留账号但清除此前保存的密码（用户取消记住）；
        - 每次保存都把该账号移到列表最前（最近登录优先），超出上限淘汰最旧。
        """
        ident = _normalize_identifier(identifier)
        pwd = _normalize_password(password if remember else None)
        with self._lock:
            document = self._load()
            now = _timestamp()
            record = next(
                (item for item in document["accounts"] if item.get("identifier") == ident),
                None,
            )
            if record is None:
                record = {
                    "identifier": ident,
                    "password": None,
                    "created_at": now,
                    "updated_at": now,
                }
                document["accounts"].insert(0, record)
            else:
                document["accounts"].remove(record)
                document["accounts"].insert(0, record)
                record["updated_at"] = now
            if remember and pwd is not None:
                record["password"] = pwd
            elif not remember:
                record["password"] = None
            del document["accounts"][self._max_accounts:]

            self._save(document)
            return self._public(record)

    def clear_password(self, identifier: str) -> bool:
        """清除某账号已保存的密码（重置密码成功后调用）；账号本身保留。"""
        ident = _normalize_identifier(identifier)
        with self._lock:
            document = self._load()
            record = next(
                (item for item in document["accounts"] if item.get("identifier") == ident),
                None,
            )
            if record is None or not record.get("password"):
                return False
            record["password"] = None
            self._save(document)
            return True

    def reveal(self, identifier: str) -> str | None:
        ident = _normalize_identifier(identifier)
        with self._lock:
            document = self._load()
            record = next(
                (item for item in document["accounts"] if item.get("identifier") == ident),
                None,
            )
            if record is None:
                return None
            value = record.get("password")
            return value if isinstance(value, str) and value else None

    def delete(self, identifier: str) -> bool:
        ident = _normalize_identifier(identifier)
        with self._lock:
            document = self._load()
            remaining = [
                item
                for item in document["accounts"]
                if item.get("identifier") != ident
            ]
            if len(remaining) == len(document["accounts"]):
                return False
            document["accounts"] = remaining
            self._save(document)
            return True

    # -- 内部 ----------------------------------------------------------

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "identifier": str(record.get("identifier") or ""),
            "has_password": bool(record.get("password")),
            "created_at": str(record.get("created_at") or ""),
            "updated_at": str(record.get("updated_at") or ""),
        }

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"version": 1, "accounts": []}
        try:
            raw = self._path.read_bytes()
        except OSError as exc:
            raise LoginCredentialsUnavailable(
                f"cannot read login credentials file: {exc}"
            ) from exc
        try:
            plaintext = _dpapi_unprotect(raw)
            document = json.loads(plaintext.decode("utf-8"))
            if not isinstance(document, dict) or not isinstance(
                document.get("accounts"), list
            ):
                raise ValueError("invalid login credentials document")
        except (
            _DpapiDecryptError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
        ):
            # 文件损坏或跨用户拷贝：隔离后按空库自愈，用户重新登录即可重建。
            self._quarantine_broken_file()
            return {"version": 1, "accounts": []}
        return document

    def _save(self, document: dict[str, Any]) -> None:
        plaintext = json.dumps(
            document, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        ciphertext = _dpapi_protect(plaintext)
        _atomic_write(self._path, ciphertext)

    def _quarantine_broken_file(self) -> None:
        backup = self._path.with_name(
            f"{self._path.name}.corrupt-{int(time.time())}"
        )
        try:
            self._path.replace(backup)
            _LOGGER.warning(
                "login credentials file unreadable; moved aside to %s", backup
            )
        except OSError:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass


# ---------------------------------------------------------------- 路由


class SaveLoginPayload(BaseModel):
    identifier: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)
    password: str | None = None
    remember: bool = False


class IdentifierPayload(BaseModel):
    identifier: str = Field(min_length=1, max_length=_MAX_IDENTIFIER_LENGTH)


def create_router(store: LoginCredentialStore) -> APIRouter:
    router = APIRouter(prefix="/api/login-credentials", tags=["login-credentials"])

    @router.get("")
    def list_saved_logins() -> dict[str, Any]:
        try:
            return {"ok": True, "accounts": store.list_entries()}
        except LoginCredentialsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @router.post("")
    def save_saved_login(payload: SaveLoginPayload) -> dict[str, Any]:
        try:
            account = store.save(
                payload.identifier,
                password=payload.password,
                remember=payload.remember,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LoginCredentialsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "account": account}

    @router.post("/clear-password")
    def clear_saved_password(payload: IdentifierPayload) -> dict[str, Any]:
        try:
            cleared = store.clear_password(payload.identifier)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LoginCredentialsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "cleared": cleared}

    @router.post("/reveal")
    def reveal_saved_password(payload: IdentifierPayload) -> dict[str, Any]:
        try:
            password = store.reveal(payload.identifier)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LoginCredentialsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "password": password}

    @router.delete("/{identifier}")
    def delete_saved_login(identifier: str) -> dict[str, Any]:
        try:
            removed = store.delete(identifier)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LoginCredentialsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "removed": removed}

    return router
