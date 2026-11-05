"""店铺账号密码的本机加密存储。

密钥文件与工作台数据库放在同一目录（`store-accounts.key`），只在登录采集时
解密使用；接口只返回手机号后四位，避免密码通过 HTTP 明文回传。
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_KEY_NAME = "store-accounts.key"


class AccountVaultError(RuntimeError):
    """账号密码无法加密/解密时抛出。"""


class AccountVault:
    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._fernet = Fernet(self._load_key())

    def _load_key(self) -> bytes:
        self._directory.mkdir(parents=True, exist_ok=True)
        key_path = self._directory / _KEY_NAME
        if key_path.exists():
            return key_path.read_bytes().strip()
        key = Fernet.generate_key()
        temporary = key_path.with_name(f".{_KEY_NAME}.{secrets.token_hex(6)}.tmp")
        with open(temporary, "xb") as handle:
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, key_path)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            # Windows 上 chmod 不影响 ACL，写入正确即可。
            pass
        return key

    def encrypt(self, text: str) -> str:
        value = str(text or "")
        if not value:
            return ""
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        value = str(token or "")
        if not value:
            return ""
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as error:
            raise AccountVaultError("账号密码解密失败：密钥文件可能已更换，请重新录入密码") from error
