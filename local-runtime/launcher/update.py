"""轻量版启动器更新检测（独立于主程序，不引入 wh_local）。

launcher 是独立 onefile（windowed），无法直接复用 wh_local.app_update
（后者依赖 fastapi 及整个主程序）。本模块仅实现「启动检测更新弹窗」所需的
核心能力，并与 wh_local.app_update 保持一致的：
  - 同一套 Ed25519 公钥（UPDATE_ED25519_PUBLIC_KEY_B64）
  - 同一套 manifest URL 与 allowlist（UPDATE_MANIFEST_*）
  - 同一套 SemanticVersion 比较规则
  - 同一套 canonical_manifest_payload() 确定性签名载荷
  - 同一套 snooze 文件约定（%APPDATA%\\MainPG\\update-snooze.json）

常量务必与 wh_local/config.py 保持同步。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from functools import total_ordering
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlparse
from urllib.request import urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# ---- 与 wh_local/config.py 保持同步 --------------------------------------- #
APP_VERSION = "1.4.1"
UPDATE_RELEASE_HOST = "workbench.haocoming.top"
UPDATE_MANIFEST_URL = f"https://{UPDATE_RELEASE_HOST}/mainpg/windows/manifest.json"
UPDATE_MANIFEST_ALLOWED_HOSTS = frozenset({UPDATE_RELEASE_HOST})
UPDATE_ED25519_PUBLIC_KEY_B64 = "qsK3rFMm732q6oZFG8m938ewHkFGj3EoxjRGq3YmHo0="
# --------------------------------------------------------------------------- #

MANIFEST_FIELDS = (
    "version",
    "mandatory",
    "installer_url",
    "sha256",
    "release_notes",
    "published_at",
)
ACCEPTED_MANIFEST_FIELDS = frozenset((*MANIFEST_FIELDS, "signature"))
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9A-Za-z-]+)(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]+))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SNOOZE_FILE_NAME = "update-snooze.json"


class UpdateCheckError(ValueError):
    """更新检测/验证失败（网络、签名、字段校验等）。"""


@dataclass(frozen=True)
@total_ordering
class SemanticVersion:
    """与 wh_local.app_update.SemanticVersion 完全一致的 SemVer 解析与比较。"""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "SemanticVersion":
        match = _SEMVER_RE.fullmatch(value)
        if not match:
            raise ValueError(f"invalid semantic version: {value!r}")
        prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
        has_leading_zero = any(
            identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
            for identifier in prerelease
        )
        if has_leading_zero:
            raise ValueError(f"invalid semantic version: {value!r}")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if core != other_core:
            return core < other_core
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    mandatory: bool
    installer_url: str
    sha256: str
    release_notes: str
    published_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "mandatory": self.mandatory,
            "installer_url": self.installer_url,
            "sha256": self.sha256,
            "release_notes": self.release_notes,
            "published_at": self.published_at,
        }


def canonical_manifest_payload(manifest: Mapping[str, object]) -> bytes:
    """与 wh_local.app_update 一致的确定性签名载荷（不含 signature）。"""
    return json.dumps(
        {name: manifest.get(name) for name in MANIFEST_FIELDS},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def runtime_root() -> Path:
    """数据根目录：冻结时 %APPDATA%\\MainPG，源码运行时 cwd。"""
    if getattr(sys, "frozen", False):
        appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return appdata / "MainPG"
    return Path.cwd()


def install_root() -> Path:
    """安装目录（version.json / patch 目标所在）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def current_version() -> str:
    """当前版本：读 version.json 兜底 APP_VERSION（复用 config 逻辑）。"""
    try:
        data = json.loads((install_root() / "version.json").read_text(encoding="utf-8"))
        value = str(data.get("version") or "").strip()
        if value:
            return value
    except (OSError, ValueError):
        pass
    return APP_VERSION


class _Verifier:
    """封装 Ed25519 公钥与 manifest 校验，与 UpdateManager._validate_manifest 对齐。"""

    def __init__(self, public_key_b64: str = UPDATE_ED25519_PUBLIC_KEY_B64) -> None:
        try:
            raw = base64.b64decode(public_key_b64, validate=True)
            self._public_key = Ed25519PublicKey.from_public_bytes(raw)
        except (ValueError, binascii.Error) as error:
            raise UpdateCheckError("无效的系统更新公钥") from error

    def validate_manifest(self, manifest: Mapping[str, object]) -> UpdateRelease:
        if set(manifest) != ACCEPTED_MANIFEST_FIELDS:
            raise UpdateCheckError("更新清单字段与签名模式不一致")
        if not isinstance(manifest["signature"], str):
            raise UpdateCheckError("更新清单签名无效")
        if not isinstance(manifest["version"], str):
            raise UpdateCheckError("更新清单版本无效")
        SemanticVersion.parse(manifest["version"])
        if not isinstance(manifest["mandatory"], bool):
            raise UpdateCheckError("更新清单强制标记无效")
        for field in ("installer_url", "sha256", "release_notes", "published_at"):
            if not isinstance(manifest[field], str):
                raise UpdateCheckError(f"更新清单 {field} 无效")
        self._validate_allowed_url(manifest["installer_url"])
        if not _SHA256_RE.fullmatch(manifest["sha256"]):
            raise UpdateCheckError("更新清单 SHA-256 无效")
        try:
            published = manifest["published_at"].replace("Z", "+00:00")
            if datetime.fromisoformat(published).tzinfo is None:
                raise ValueError
        except ValueError as error:
            raise UpdateCheckError("更新清单发布时间无效") from error
        try:
            signature = base64.b64decode(manifest["signature"], validate=True)
            self._public_key.verify(signature, canonical_manifest_payload(manifest))
        except (InvalidSignature, ValueError, binascii.Error) as error:
            raise UpdateCheckError("更新清单签名验证失败") from error
        return UpdateRelease(
            version=manifest["version"],
            mandatory=manifest["mandatory"],
            installer_url=manifest["installer_url"],
            sha256=manifest["sha256"].lower(),
            release_notes=manifest["release_notes"],
            published_at=manifest["published_at"],
        )

    @staticmethod
    def _validate_allowed_url(value: str) -> None:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or not host
            or host not in {item.lower() for item in UPDATE_MANIFEST_ALLOWED_HOSTS}
            or parsed.username
            or parsed.password
        ):
            raise UpdateCheckError("更新地址必须使用白名单内的 HTTPS 主机")


def _snooze_path() -> Path:
    return runtime_root() / _SNOOZE_FILE_NAME


def is_snoozed(version: str) -> bool:
    try:
        stored = json.loads(_snooze_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    stored_version = stored.get("version") if isinstance(stored, Mapping) else None
    if not isinstance(stored_version, str):
        return False
    try:
        SemanticVersion.parse(stored_version)
    except ValueError:
        return False
    return stored_version == version


def snooze_version(version: str) -> None:
    """暂缓当前可选版本（强制更新不可暂缓）。"""
    _snooze_path().parent.mkdir(parents=True, exist_ok=True)
    temporary = _snooze_path().with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"version": version}, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, _snooze_path())


def _fetch_manifest(url: str, timeout: float) -> Mapping[str, object]:
    try:
        with urlopen(url, timeout=timeout) as response:  # nosec B310: allowlist checked
            _validate_response_url(response)
            body = response.read()
    except OSError as error:
        raise UpdateCheckError(f"无法获取更新清单：{error}") from error
    try:
        decoded = json.loads(body)
    except (TypeError, ValueError) as error:
        raise UpdateCheckError("更新清单不是合法 JSON") from error
    if not isinstance(decoded, Mapping):
        raise UpdateCheckError("更新清单必须是对象")
    return decoded


def _validate_response_url(response: Any) -> None:
    get_url = getattr(response, "geturl", None)
    if callable(get_url):
        final_url = get_url()
        if not isinstance(final_url, str):
            raise UpdateCheckError("更新响应地址无效")
        _Verifier._validate_allowed_url(final_url)


def check_for_update(timeout: float = 10.0) -> UpdateRelease | None:
    """检测是否存在比当前版本更新的已签名发布。

    返回新版本 UpdateRelease；版本不高于当前版本、已被暂缓、或非 Windows
    时返回 None。遇到网络/签名/字段错误则抛出 UpdateCheckError。"""
    if sys.platform != "win32":
        return None
    manifest = _fetch_manifest(UPDATE_MANIFEST_URL, timeout)
    release = _Verifier().validate_manifest(manifest)
    if SemanticVersion.parse(release.version) <= SemanticVersion.parse(current_version()):
        return None
    if is_snoozed(release.version):
        return None
    return release


def download_release(
    release: UpdateRelease,
    on_progress: Callable[[int, int | None, float | None], None] | None = None,
) -> Path:
    """下载并校验安装包，返回本地路径。on_progress(downloaded, total, percentage)。"""
    updates_dir = runtime_root() / "updates"
    updates_dir.mkdir(parents=True, exist_ok=True)
    destination = updates_dir / f"MainPG-{release.version}.exe"
    partial = destination.with_suffix(".exe.part")
    digest = hashlib.sha256()
    downloaded = 0
    try:
        source = urlopen(release.installer_url, timeout=30)  # nosec B310: manifest-validated
    except OSError as error:
        raise UpdateCheckError(f"无法下载安装包：{error}") from error
    try:
        _validate_response_url(source)
        total = _content_length(source)
        with partial.open("wb") as output:
            for chunk in _download_chunks(source):
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
                percentage = round(downloaded * 100 / total, 2) if total else None
                if on_progress:
                    on_progress(downloaded, total, percentage)
        if digest.hexdigest().lower() != release.sha256:
            raise UpdateCheckError("下载的安装包 SHA-256 与签名清单不一致")
        os.replace(partial, destination)
        return destination
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()


def launch_installer(path: Path) -> None:
    """拉起安装器（静默安装参数，与主程序一致）。"""
    flags = 0
    if sys.platform == "win32":
        flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    subprocess.Popen(
        [str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        cwd=str(path.parent),
        creationflags=flags,
    )


def _download_chunks(source: Any) -> Iterable[bytes]:
    while chunk := source.read(1024 * 256):
        yield chunk


def _content_length(source: Any) -> int | None:
    headers = getattr(source, "headers", None)
    raw = headers.get("Content-Length") if headers else None
    try:
        return int(raw) if raw else None
    except (TypeError, ValueError):
        return None
