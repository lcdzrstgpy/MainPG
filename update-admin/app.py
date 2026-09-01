from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


APP_DIR = Path(__file__).resolve().parent
MANIFEST_FIELDS = (
    "version",
    "mandatory",
    "installer_url",
    "sha256",
    "release_notes",
    "published_at",
)
PATCH_CONTRACT_VERSION = "wh-patch-manifest-v1"
PATCH_MANIFEST_FIELDS = (
    "contract_version",
    "from_version",
    "to_version",
    "published_at",
    "file_base_url",
    "files",
)
PATCH_EXCLUDED_REL_PATHS = frozenset({"version.json", "MainPG-Updater.exe"})
EMBEDDED_PATCH_CONTRACT_VERSION = "mainpg-embedded-patch-v1"
EMBEDDED_PATCH_COMMENT = b"MAINPG_EMBEDDED_PATCH_V1"
EMBEDDED_PATCH_ROOT = "mainpg-patch"
EMBEDDED_PATCH_DESCRIPTOR = f"{EMBEDDED_PATCH_ROOT}/descriptor.json"
CURRENT_CLIENT_PUBLIC_KEY_B64 = "qsK3rFMm732q6oZFG8m938ewHkFGj3EoxjRGq3YmHo0="
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,31}$")
PUBLISH_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
DOWNLOAD_CHANNELS = frozenset({"update_only", "internal", "public"})
DOWNLOAD_CHANNEL_LABELS = {
    "update_only": "仅软件更新",
    "internal": "内测版",
    "public": "公共版",
}
DOWNLOAD_CHANNEL_URLS = {
    "internal": "/internal-downloads/MainPG-Internal-Setup.exe",
    "public": "/downloads/MainPG-Setup.exe",
}
SEEDED_USERNAMES = ("He123", "Liu123", "Dai123", "Yang123", "Shen123")
LOGGER = logging.getLogger("mainpg.update_admin")
UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime | None = None) -> str:
    return (value or utc_now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def normalize_download_channel(value: str) -> Literal["update_only", "internal", "public"]:
    channel = value.strip().lower()
    if channel not in DOWNLOAD_CHANNELS:
        raise api_error(
            422,
            "invalid_download_channel",
            "发布方式只能选择仅软件更新、内测版官网或公共版官网",
        )
    return channel  # type: ignore[return-value]


def website_download_target(
    settings: "Settings",
    channel: Literal["update_only", "internal", "public"],
) -> Path | None:
    if channel == "update_only":
        return None
    target = (
        settings.internal_download_path
        if channel == "internal"
        else settings.public_download_path
    )
    if target is None:
        raise api_error(
            503,
            "download_channel_not_configured",
            f"服务器尚未配置{DOWNLOAD_CHANNEL_LABELS[channel]}官网下载文件路径",
        )
    return target


@dataclass(frozen=True)
class Settings:
    db_path: Path
    staging_dir: Path
    publish_dir: Path
    public_base_url: str
    signing_key_path: Path | None
    expected_public_key_b64: str
    initial_password: str
    boss_initial_password: str | None
    internal_download_path: Path | None = None
    public_download_path: Path | None = None
    cookie_name: str = "mainpg_update_admin"
    cookie_path: str = "/"
    secure_cookie: bool = True
    session_hours: int = 12
    max_upload_bytes: int = 1_500_000_000
    require_authenticode: bool = False
    expected_authenticode_publisher: str = ""
    evsign_api_url: str = "https://api.evsign.cn/v1"
    evsign_license_key: str = field(default="", repr=False)
    evsign_required: bool = False
    evsign_timeout_seconds: int = 900
    patch_enabled: bool = True
    innoextract_path: str = "innoextract"
    patch_extract_timeout_seconds: int = 600
    patch_max_extracted_bytes: int = 4_000_000_000

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("UPDATE_ADMIN_DATA_DIR", APP_DIR / "data")).resolve()
        signing_path = os.environ.get("UPDATE_SIGNING_KEY_PATH", "").strip()
        internal_download_path = os.environ.get("UPDATE_INTERNAL_DOWNLOAD_PATH", "").strip()
        public_download_path = os.environ.get("UPDATE_PUBLIC_DOWNLOAD_PATH", "").strip()
        return cls(
            db_path=Path(os.environ.get("UPDATE_ADMIN_DB_PATH", data_dir / "update-admin.sqlite3")).resolve(),
            staging_dir=Path(os.environ.get("UPDATE_ADMIN_STAGING_DIR", data_dir / "staging")).resolve(),
            publish_dir=Path(os.environ.get("UPDATE_PUBLISH_DIR", data_dir / "published" / "windows")).resolve(),
            public_base_url=os.environ.get(
                "UPDATE_PUBLIC_BASE_URL",
                "https://workbench.haocoming.top/mainpg/windows",
            ).strip().rstrip("/"),
            signing_key_path=Path(signing_path).resolve() if signing_path else None,
            expected_public_key_b64=os.environ.get(
                "UPDATE_EXPECTED_PUBLIC_KEY_B64",
                CURRENT_CLIENT_PUBLIC_KEY_B64,
            ).strip(),
            initial_password=os.environ.get("UPDATE_ADMIN_INITIAL_PASSWORD", "123456"),
            boss_initial_password=os.environ.get("UPDATE_ADMIN_BOSS_INITIAL_PASSWORD") or None,
            internal_download_path=(
                Path(internal_download_path).resolve() if internal_download_path else None
            ),
            public_download_path=(
                Path(public_download_path).resolve() if public_download_path else None
            ),
            cookie_name=os.environ.get("UPDATE_ADMIN_COOKIE_NAME", "mainpg_update_admin").strip(),
            cookie_path=os.environ.get("UPDATE_ADMIN_COOKIE_PATH", "/").strip() or "/",
            secure_cookie=os.environ.get("UPDATE_ADMIN_SECURE_COOKIE", "1").strip() != "0",
            session_hours=max(1, int(os.environ.get("UPDATE_ADMIN_SESSION_HOURS", "12"))),
            max_upload_bytes=max(1, int(os.environ.get("UPDATE_ADMIN_MAX_UPLOAD_BYTES", "1500000000"))),
            require_authenticode=os.environ.get("UPDATE_REQUIRE_AUTHENTICODE", "0").strip() == "1",
            expected_authenticode_publisher=os.environ.get(
                "UPDATE_EXPECTED_AUTHENTICODE_PUBLISHER",
                "",
            ).strip(),
            evsign_api_url=os.environ.get(
                "EVSIGN_API_URL",
                "https://api.evsign.cn/v1",
            ).strip(),
            evsign_license_key=os.environ.get("EVSIGN_LICENSE_KEY", "").strip(),
            evsign_required=os.environ.get("EVSIGN_REQUIRED", "0").strip() == "1",
            evsign_timeout_seconds=max(30, int(os.environ.get("EVSIGN_TIMEOUT_SECONDS", "900"))),
            patch_enabled=os.environ.get("UPDATE_PATCH_ENABLED", "1").strip() != "0",
            innoextract_path=os.environ.get("UPDATE_INNOEXTRACT_PATH", "innoextract").strip() or "innoextract",
            patch_extract_timeout_seconds=max(
                30,
                int(os.environ.get("UPDATE_PATCH_EXTRACT_TIMEOUT_SECONDS", "600")),
            ),
            patch_max_extracted_bytes=max(
                1,
                int(os.environ.get("UPDATE_PATCH_MAX_EXTRACTED_BYTES", "4000000000")),
            ),
        )


@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]
    raw: str

    @classmethod
    def parse(cls, raw: str) -> "SemVer":
        value = raw.strip()
        match = SEMVER_RE.fullmatch(value)
        if not match:
            raise ValueError("版本号必须符合 SemVer，例如 1.3.4 或 1.3.4-beta.1")
        prerelease = tuple((match.group(4) or "").split(".")) if match.group(4) else ()
        for item in prerelease:
            if item.isdigit() and len(item) > 1 and item.startswith("0"):
                raise ValueError("预发布版本中的数字标识不能以 0 开头")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease, value)

    def _compare_prerelease(self, other: "SemVer") -> int:
        if not self.prerelease and not other.prerelease:
            return 0
        if not self.prerelease:
            return 1
        if not other.prerelease:
            return -1
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            left_numeric = left.isdigit()
            right_numeric = right.isdigit()
            if left_numeric and right_numeric:
                return -1 if int(left) < int(right) else 1
            if left_numeric != right_numeric:
                return -1 if left_numeric else 1
            return -1 if left < right else 1
        if len(self.prerelease) == len(other.prerelease):
            return 0
        return -1 if len(self.prerelease) < len(other.prerelease) else 1

    def compare(self, other: "SemVer") -> int:
        left = (self.major, self.minor, self.patch)
        right = (other.major, other.minor, other.patch)
        if left != right:
            return -1 if left < right else 1
        return self._compare_prerelease(other)


class LoginBody(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)


class PublishJobCreateBody(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    channel: Literal["update_only", "internal", "public"] = "update_only"
    mandatory: bool = False
    release_notes: str = Field(default="", max_length=10_000)
    installer_filename: str = Field(min_length=1, max_length=255)
    total_bytes: int = Field(ge=1)


class PublishJobProgressBody(BaseModel):
    uploaded_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=1)


@dataclass(frozen=True)
class AdminPrincipal:
    username: str
    role: str
    must_change_password: bool
    token_hash: str


class Database:
    def __init__(self, path: Path, password_hasher: PasswordHasher, settings: Settings):
        self.path = path
        self.password_hasher = password_hasher
        self.settings = settings

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'admin',
                    must_change_password INTEGER NOT NULL DEFAULT 1,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL REFERENCES admins(username) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at);
                CREATE TABLE IF NOT EXISTS releases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL UNIQUE,
                    channel TEXT NOT NULL DEFAULT 'internal',
                    mandatory INTEGER NOT NULL,
                    release_notes TEXT NOT NULL,
                    installer_filename TEXT NOT NULL,
                    installer_url TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    signature TEXT NOT NULL,
                    authenticode_status TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by TEXT NOT NULL REFERENCES admins(username),
                    created_at TEXT NOT NULL,
                    published_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_releases_published_at ON releases(published_at DESC);
                CREATE TABLE IF NOT EXISTS publish_jobs (
                    id TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    channel TEXT NOT NULL DEFAULT 'internal',
                    mandatory INTEGER NOT NULL,
                    release_notes TEXT NOT NULL,
                    installer_filename TEXT NOT NULL,
                    uploaded_bytes INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    failed_phase TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL REFERENCES admins(username),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_publish_jobs_updated_at ON publish_jobs(updated_at DESC);
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    ip_address TEXT NOT NULL DEFAULT '',
                    details_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC);
                """
            )
            release_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(releases)").fetchall()
            }
            release_migrations = {
                "channel": "TEXT NOT NULL DEFAULT 'internal'",
                "patch_status": "TEXT NOT NULL DEFAULT 'not_available'",
                "patch_from_version": "TEXT NOT NULL DEFAULT ''",
                "patch_file_count": "INTEGER NOT NULL DEFAULT 0",
                "patch_total_bytes": "INTEGER NOT NULL DEFAULT 0",
                "patch_error": "TEXT NOT NULL DEFAULT ''",
            }
            for column, declaration in release_migrations.items():
                if column not in release_columns:
                    connection.execute(f"ALTER TABLE releases ADD COLUMN {column} {declaration}")
            publish_job_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(publish_jobs)").fetchall()
            }
            if "failed_phase" not in publish_job_columns:
                connection.execute(
                    "ALTER TABLE publish_jobs ADD COLUMN failed_phase TEXT NOT NULL DEFAULT ''"
                )
            if "channel" not in publish_job_columns:
                connection.execute(
                    "ALTER TABLE publish_jobs ADD COLUMN channel TEXT NOT NULL DEFAULT 'internal'"
                )
            # A process restart cannot resume an in-flight EV Sign HTTP stream.
            # Preserve the durable record and make the interruption explicit.
            connection.execute(
                """
                UPDATE publish_jobs
                SET failed_phase = phase, phase = 'failed', message = '服务重启，发布任务已中断',
                    error = '服务重启，发布任务已中断，请重新上传',
                    updated_at = ?, completed_at = ?
                WHERE phase NOT IN ('completed', 'failed')
                """,
                (iso_utc(), iso_utc()),
            )
            existing = {
                row["username"]
                for row in connection.execute("SELECT username FROM admins").fetchall()
            }
            created_at = iso_utc()
            for username in SEEDED_USERNAMES:
                if username in existing:
                    continue
                connection.execute(
                    """
                    INSERT INTO admins(username, password_hash, role, must_change_password, created_at, updated_at)
                    VALUES (?, ?, 'admin', 1, ?, ?)
                    """,
                    (username, self.password_hasher.hash(self.settings.initial_password), created_at, created_at),
                )
            if "boss" not in existing:
                if not self.settings.boss_initial_password:
                    raise RuntimeError(
                        "首次启动前必须设置 UPDATE_ADMIN_BOSS_INITIAL_PASSWORD；该值只用于生成密码哈希，不写入数据库明文。"
                    )
                connection.execute(
                    """
                    INSERT INTO admins(username, password_hash, role, must_change_password, created_at, updated_at)
                    VALUES ('boss', ?, 'admin', 0, ?, ?)
                    """,
                    (self.password_hasher.hash(self.settings.boss_initial_password), created_at, created_at),
                )
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (iso_utc(),))

    def audit(
        self,
        username: str,
        action: str,
        *,
        target: str = "",
        ip_address: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            self.audit_in_connection(
                connection,
                username,
                action,
                target=target,
                ip_address=ip_address,
                details=details,
            )

    @staticmethod
    def audit_in_connection(
        connection: sqlite3.Connection,
        username: str,
        action: str,
        *,
        target: str = "",
        ip_address: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        safe_details = json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))
        connection.execute(
            """
            INSERT INTO audit_logs(created_at, username, action, target, ip_address, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (iso_utc(), username, action, target, ip_address, safe_details),
        )


class UpdateAdminService:
    def __init__(self, settings: Settings, password_hasher: PasswordHasher):
        self.settings = settings
        self.password_hasher = password_hasher
        self.db = Database(settings.db_path, password_hasher, settings)
        self.db.initialize()
        self.dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))
        self.publish_lock = asyncio.Lock()
        settings.staging_dir.mkdir(parents=True, exist_ok=True)
        settings.publish_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def client_ip(request: Request) -> str:
        return request.client.host if request.client else ""

    def verify_password(self, password_hash: str, password: str) -> bool:
        try:
            return bool(self.password_hasher.verify(password_hash, password))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def authenticate(self, username: str, password: str, ip_address: str) -> tuple[dict[str, Any], str]:
        username = username.strip()
        if not USERNAME_RE.fullmatch(username):
            self.verify_password(self.dummy_password_hash, password)
            raise api_error(401, "invalid_credentials", "账号或密码错误")
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM admins WHERE username = ?", (username,)).fetchone()
            if row is None:
                self.verify_password(self.dummy_password_hash, password)
                self.db.audit_in_connection(connection, username, "login_failed", ip_address=ip_address)
                connection.commit()
                raise api_error(401, "invalid_credentials", "账号或密码错误")
            locked_until = parse_utc(row["locked_until"])
            if locked_until and locked_until > utc_now():
                raise api_error(429, "account_locked", "登录失败次数过多，请 15 分钟后重试")
            if not self.verify_password(row["password_hash"], password):
                failed_attempts = int(row["failed_attempts"]) + 1
                next_locked_until = None
                if failed_attempts >= 5:
                    failed_attempts = 0
                    next_locked_until = iso_utc(utc_now() + timedelta(minutes=15))
                connection.execute(
                    "UPDATE admins SET failed_attempts = ?, locked_until = ?, updated_at = ? WHERE username = ?",
                    (failed_attempts, next_locked_until, iso_utc(), username),
                )
                self.db.audit_in_connection(connection, username, "login_failed", ip_address=ip_address)
                connection.commit()
                raise api_error(401, "invalid_credentials", "账号或密码错误")
            if self.password_hasher.check_needs_rehash(row["password_hash"]):
                connection.execute(
                    "UPDATE admins SET password_hash = ?, updated_at = ? WHERE username = ?",
                    (self.password_hasher.hash(password), iso_utc(), username),
                )
            connection.execute(
                """
                UPDATE admins
                SET failed_attempts = 0, locked_until = NULL, last_login_at = ?, updated_at = ?
                WHERE username = ?
                """,
                (iso_utc(), iso_utc(), username),
            )
            token = secrets.token_urlsafe(48)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            connection.execute(
                "INSERT INTO sessions(token_hash, username, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (
                    token_hash,
                    username,
                    iso_utc(),
                    iso_utc(utc_now() + timedelta(hours=self.settings.session_hours)),
                ),
            )
            principal = {
                "username": username,
                "role": row["role"],
                "must_change_password": bool(row["must_change_password"]),
            }
        self.db.audit(username, "login_succeeded", ip_address=ip_address)
        return principal, token

    def principal_for_token(self, token: str | None) -> AdminPrincipal:
        if not token:
            raise api_error(401, "not_authenticated", "请先登录")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT s.token_hash, s.expires_at, a.username, a.role, a.must_change_password
                FROM sessions s
                JOIN admins a ON a.username = s.username
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                raise api_error(401, "session_expired", "登录已失效，请重新登录")
            expires_at = parse_utc(row["expires_at"])
            if expires_at is None or expires_at <= utc_now():
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
                raise api_error(401, "session_expired", "登录已过期，请重新登录")
        return AdminPrincipal(
            username=row["username"],
            role=row["role"],
            must_change_password=bool(row["must_change_password"]),
            token_hash=token_hash,
        )

    def logout(self, principal: AdminPrincipal, ip_address: str) -> None:
        with self.db.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (principal.token_hash,))
        self.db.audit(principal.username, "logout", ip_address=ip_address)

    def change_password(
        self,
        principal: AdminPrincipal,
        current_password: str,
        new_password: str,
        ip_address: str,
    ) -> None:
        if len(new_password) < 10:
            raise api_error(422, "weak_password", "新密码至少需要 10 个字符")
        if new_password == self.settings.initial_password:
            raise api_error(422, "weak_password", "新密码不能继续使用初始密码")
        if principal.username.lower() in new_password.lower():
            raise api_error(422, "weak_password", "新密码不能包含管理员账号")
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM admins WHERE username = ?",
                (principal.username,),
            ).fetchone()
            if row is None or not self.verify_password(row["password_hash"], current_password):
                raise api_error(401, "invalid_current_password", "当前密码错误")
            if self.verify_password(row["password_hash"], new_password):
                raise api_error(422, "same_password", "新密码不能与当前密码相同")
            connection.execute(
                """
                UPDATE admins
                SET password_hash = ?, must_change_password = 0, failed_attempts = 0,
                    locked_until = NULL, updated_at = ?
                WHERE username = ?
                """,
                (self.password_hasher.hash(new_password), iso_utc(), principal.username),
            )
            connection.execute("DELETE FROM sessions WHERE username = ?", (principal.username,))
        self.db.audit(principal.username, "password_changed", ip_address=ip_address)

    def list_releases(self, page: int, page_size: int = 10) -> dict[str, Any]:
        offset = (page - 1) * page_size
        with self.db.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM releases").fetchone()[0])
            rows = connection.execute(
                """
                SELECT version, channel, mandatory, release_notes, installer_filename, installer_url,
                       sha256, file_size, authenticode_status, status, created_by, published_at,
                       patch_status, patch_from_version, patch_file_count, patch_total_bytes,
                       patch_error
                FROM releases
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ).fetchall()
        return {
            "items": [
                {
                    **dict(row),
                    "mandatory": bool(row["mandatory"]),
                }
                for row in rows
            ],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    def get_release(self, version: str) -> dict[str, Any] | None:
        """Return one exact release so the UI can reconcile a lost POST response."""
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT version, channel, mandatory, release_notes, installer_filename, installer_url,
                       sha256, file_size, authenticode_status, status, created_by, published_at,
                       patch_status, patch_from_version, patch_file_count, patch_total_bytes,
                       patch_error
                FROM releases
                WHERE version = ?
                LIMIT 1
                """,
                (version,),
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "mandatory": bool(row["mandatory"])}

    def latest_release(self) -> dict[str, Any] | None:
        with self.db.connect() as connection:
            row = connection.execute(
                """
                SELECT version, installer_filename
                FROM releases
                WHERE status = 'published'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row is not None else None

    def update_patch_result(
        self,
        version: str,
        *,
        status: str,
        from_version: str = "",
        file_count: int = 0,
        total_bytes: int = 0,
        error: str = "",
    ) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE releases
                SET patch_status = ?, patch_from_version = ?, patch_file_count = ?,
                    patch_total_bytes = ?, patch_error = ?
                WHERE version = ?
                """,
                (status, from_version, file_count, total_bytes, error[:1000], version),
            )

    @staticmethod
    def _publish_job_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            **dict(row),
            "mandatory": bool(row["mandatory"]),
            "uploaded_bytes": int(row["uploaded_bytes"]),
            "total_bytes": int(row["total_bytes"]),
        }

    def create_publish_job(
        self,
        *,
        version: str,
        channel: Literal["update_only", "internal", "public"],
        mandatory: bool,
        release_notes: str,
        installer_filename: str,
        total_bytes: int,
        created_by: str,
    ) -> dict[str, Any]:
        if total_bytes <= 0 or total_bytes > self.settings.max_upload_bytes:
            raise api_error(413, "upload_too_large", "安装包体积超过服务器允许上限")
        safe_filename = Path(installer_filename).name
        if not safe_filename.lower().endswith(".exe"):
            raise api_error(422, "invalid_installer", "只能上传 Windows EXE 安装包")
        job_id = secrets.token_hex(16)
        now = iso_utc()
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO publish_jobs(
                    id, version, channel, mandatory, release_notes, installer_filename,
                    uploaded_bytes, total_bytes, phase, failed_phase, message, error,
                    created_by, created_at, updated_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'uploading', '', '正在上传安装包', '', ?, ?, ?, NULL)
                """,
                (
                    job_id,
                    version,
                    channel,
                    int(bool(mandatory)),
                    release_notes,
                    safe_filename,
                    total_bytes,
                    created_by,
                    now,
                    now,
                ),
            )
            row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise api_error(500, "publish_job_missing", "发布任务创建失败")
        return self._publish_job_payload(row)

    def get_publish_job(self, job_id: str) -> dict[str, Any] | None:
        if not PUBLISH_JOB_ID_RE.fullmatch(job_id):
            return None
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM publish_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._publish_job_payload(row) if row is not None else None

    def list_publish_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM publish_jobs ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [self._publish_job_payload(row) for row in rows]

    def require_publish_job(
        self,
        job_id: str,
        principal: AdminPrincipal,
        *,
        version: str | None = None,
    ) -> dict[str, Any]:
        job = self.get_publish_job(job_id)
        if job is None:
            raise api_error(404, "publish_job_not_found", "发布任务不存在")
        if job["created_by"] != principal.username:
            raise api_error(403, "publish_job_forbidden", "不能操作其他管理员的发布任务")
        if version is not None and job["version"] != version:
            raise api_error(409, "publish_job_mismatch", "发布任务版本与上传版本不一致")
        return job

    def update_publish_job(
        self,
        job_id: str,
        *,
        phase: str,
        message: str,
        uploaded_bytes: int | None = None,
        total_bytes: int | None = None,
        error: str = "",
    ) -> None:
        terminal = phase in {"completed", "failed"}
        assignments = ["phase = ?", "message = ?", "error = ?", "updated_at = ?"]
        values: list[Any] = [phase, message[:500], error[:1000], iso_utc()]
        if phase == "failed":
            assignments.append("failed_phase = phase")
        elif phase == "completed":
            assignments.append("failed_phase = ''")
        if uploaded_bytes is not None:
            assignments.append("uploaded_bytes = ?")
            values.append(max(0, int(uploaded_bytes)))
        if total_bytes is not None:
            assignments.append("total_bytes = ?")
            values.append(max(1, int(total_bytes)))
        if terminal:
            assignments.append("completed_at = ?")
            values.append(iso_utc())
        values.append(job_id)
        with self.db.connect() as connection:
            connection.execute(
                f"UPDATE publish_jobs SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def update_publish_upload_progress(
        self,
        job_id: str,
        *,
        uploaded_bytes: int,
        total_bytes: int,
    ) -> None:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT phase, total_bytes FROM publish_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None or row["phase"] != "uploading":
                return
            expected_total = int(row["total_bytes"])
            safe_total = expected_total if expected_total > 0 else total_bytes
            safe_uploaded = min(max(0, uploaded_bytes), safe_total)
            connection.execute(
                """
                UPDATE publish_jobs
                SET uploaded_bytes = ?, total_bytes = ?, message = ?, updated_at = ?
                WHERE id = ? AND phase = 'uploading'
                """,
                (
                    safe_uploaded,
                    safe_total,
                    f"正在上传安装包：{safe_uploaded} / {safe_total} 字节",
                    iso_utc(),
                    job_id,
                ),
            )

    def list_audit_logs(self, page: int, page_size: int = 50) -> dict[str, Any]:
        offset = (page - 1) * page_size
        with self.db.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0])
            rows = connection.execute(
                """
                SELECT created_at, username, action, target, ip_address, details_json
                FROM audit_logs
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (page_size, offset),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except json.JSONDecodeError:
                item["details"] = {}
                item.pop("details_json", None)
            result.append(item)
        return {
            "items": result,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    def assert_new_version(self, candidate: SemVer) -> None:
        with self.db.connect() as connection:
            rows = connection.execute("SELECT version FROM releases WHERE status = 'published'").fetchall()
        existing_versions = {str(row["version"]) for row in rows}
        live_manifest = self.settings.publish_dir / "manifest.json"
        if live_manifest.is_file():
            try:
                current_payload = json.loads(live_manifest.read_text(encoding="utf-8"))
                existing_versions.add(str(current_payload["version"]))
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise api_error(
                    503,
                    "live_manifest_invalid",
                    "线上 manifest.json 无法识别；为避免误降级，已停止发布",
                ) from exc
        for raw_version in existing_versions:
            try:
                existing = SemVer.parse(raw_version)
            except ValueError as exc:
                raise api_error(
                    503,
                    "published_version_invalid",
                    "已有发布记录包含无效版本号；为避免误降级，已停止发布",
                ) from exc
            if candidate.compare(existing) <= 0:
                raise api_error(
                    409,
                    "version_not_newer",
                    f"版本 {candidate.raw} 必须高于已发布版本 {existing.raw}",
                )


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    unsigned = {field: manifest[field] for field in MANIFEST_FIELDS}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_patch_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    unsigned = {field: manifest[field] for field in PATCH_MANIFEST_FIELDS}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def resolve_innoextract(settings: Settings) -> str:
    configured = settings.innoextract_path
    candidate = Path(configured)
    if candidate.is_absolute() and candidate.is_file():
        return str(candidate)
    resolved = shutil.which(configured)
    if not resolved:
        raise RuntimeError("服务器未安装 innoextract，无法自动生成增量补丁")
    return resolved


def extract_installer_bundle(
    installer: Path,
    output_dir: Path,
    expected_version: str,
    settings: Settings,
) -> Path:
    extractor = resolve_innoextract(settings)
    output_dir.mkdir(parents=True, exist_ok=False)
    try:
        completed = subprocess.run(
            [extractor, "--extract", "--output-dir", str(output_dir), str(installer)],
            check=False,
            capture_output=True,
            text=True,
            timeout=settings.patch_extract_timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("安装包提取失败") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-1000:]
        raise RuntimeError(f"安装包提取失败：{detail or 'innoextract 未返回原因'}")

    runtime = output_dir / "app"
    if not (runtime / "MainPG.exe").is_file() or not (runtime / "MainPG-Updater.exe").is_file():
        raise RuntimeError("安装包中缺少 MainPG.exe 或 MainPG-Updater.exe")
    try:
        version_payload = json.loads((runtime / "version.json").read_text(encoding="utf-8"))
        bundled_version = str(version_payload.get("version") or "").strip()
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("安装包中的 version.json 无法识别") from exc
    if bundled_version != expected_version:
        raise RuntimeError(
            f"安装包内版本 {bundled_version or '为空'} 与填写版本 {expected_version} 不一致"
        )

    extracted_bytes = 0
    for path in runtime.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("安装包包含不允许的符号链接")
        if path.is_file():
            extracted_bytes += path.stat().st_size
            if extracted_bytes > settings.patch_max_extracted_bytes:
                raise RuntimeError("安装包解压后的体积超过安全上限")
    return runtime


def collect_bundle_files(root: Path) -> dict[str, tuple[Path, str, int]]:
    result: dict[str, tuple[Path, str, int]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if (
            relative in PATCH_EXCLUDED_REL_PATHS
            or relative == "updates"
            or relative.startswith("updates/")
        ):
            continue
        if any(part in {"", ".", ".."} for part in relative.split("/")):
            raise RuntimeError("安装包包含不安全的文件路径")
        sha256, size = hash_file(path)
        result[relative] = (path, sha256, size)
    return result


class EmbeddedPatchMissing(RuntimeError):
    """The installer is valid but was built without an embedded patch payload."""


def patch_path_safe(value: str) -> bool:
    if not value or "\\" in value or ":" in value or value.startswith("/"):
        return False
    return all(
        part not in {"", ".", ".."}
        and all(character.isalnum() or character in "._-" for character in part)
        for part in value.split("/")
    )


def prepare_embedded_patch_payload(
    *,
    installer: Path,
    from_version: str,
    to_version: str,
    published_at: str,
    public_base_url: str,
    signing_key: Ed25519PrivateKey,
    output_dir: Path,
    max_payload_bytes: int,
) -> tuple[dict[str, Any], int]:
    if not zipfile.is_zipfile(installer):
        raise EmbeddedPatchMissing("安装包没有内嵌增量补丁")
    try:
        with zipfile.ZipFile(installer) as archive:
            if archive.comment != EMBEDDED_PATCH_COMMENT:
                raise EmbeddedPatchMissing("安装包没有 MainPG 增量补丁标记")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise RuntimeError("内嵌补丁包含重复文件名")
            try:
                descriptor_info = archive.getinfo(EMBEDDED_PATCH_DESCRIPTOR)
            except KeyError as exc:
                raise RuntimeError("内嵌补丁缺少 descriptor.json") from exc
            if descriptor_info.file_size <= 0 or descriptor_info.file_size > 1024 * 1024:
                raise RuntimeError("内嵌补丁描述文件体积异常")
            try:
                descriptor = json.loads(archive.read(descriptor_info).decode("utf-8"))
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError("内嵌补丁描述文件无法识别") from exc
            if not isinstance(descriptor, dict) or set(descriptor) != {
                "contract_version",
                "from_version",
                "to_version",
                "files",
            }:
                raise RuntimeError("内嵌补丁描述字段不完整")
            if descriptor["contract_version"] != EMBEDDED_PATCH_CONTRACT_VERSION:
                raise RuntimeError("内嵌补丁协议版本不受支持")
            if descriptor["from_version"] != from_version or descriptor["to_version"] != to_version:
                raise RuntimeError("内嵌补丁版本与服务器发布链不一致")
            files = descriptor["files"]
            if not isinstance(files, list) or not files:
                raise RuntimeError("内嵌补丁没有文件差异")

            output_dir.mkdir(parents=True, exist_ok=False)
            public_entries: list[dict[str, Any]] = []
            allowed_archive_names = {EMBEDDED_PATCH_DESCRIPTOR}
            total_bytes = 0
            for raw_entry in files:
                if not isinstance(raw_entry, dict) or set(raw_entry) != {"path", "action", "sha256", "size"}:
                    raise RuntimeError("内嵌补丁文件条目不完整")
                relative = raw_entry["path"]
                action = raw_entry["action"]
                if not isinstance(relative, str) or not patch_path_safe(relative):
                    raise RuntimeError("内嵌补丁包含不安全的文件路径")
                if relative in PATCH_EXCLUDED_REL_PATHS or relative.startswith("updates/"):
                    raise RuntimeError(f"内嵌补丁尝试更新受保护文件：{relative}")
                if action == "delete":
                    if raw_entry["sha256"] or raw_entry["size"] not in {0, "0"}:
                        raise RuntimeError("内嵌补丁删除条目格式错误")
                    public_entries.append({"path": relative, "action": "delete", "sha256": "", "size": 0})
                    continue
                if action not in {"add", "replace"}:
                    raise RuntimeError("内嵌补丁包含未知操作")
                archive_name = f"{EMBEDDED_PATCH_ROOT}/files/{relative}"
                allowed_archive_names.add(archive_name)
                try:
                    info = archive.getinfo(archive_name)
                except KeyError as exc:
                    raise RuntimeError(f"内嵌补丁缺少文件：{relative}") from exc
                try:
                    declared_size = int(raw_entry["size"])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("内嵌补丁文件体积无效") from exc
                if declared_size <= 0 or info.file_size != declared_size:
                    raise RuntimeError(f"内嵌补丁文件体积不一致：{relative}")
                total_bytes += declared_size
                if total_bytes > max_payload_bytes:
                    raise RuntimeError("内嵌补丁解压体积超过安全上限")

                target = output_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                written = 0
                with archive.open(info) as source, target.open("xb") as destination:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
                actual_hash = digest.hexdigest()
                if written != declared_size or actual_hash != str(raw_entry["sha256"]).lower():
                    raise RuntimeError(f"内嵌补丁文件 SHA-256 不一致：{relative}")
                public_entries.append(
                    {
                        "path": relative,
                        "action": action,
                        "sha256": actual_hash,
                        "size": written,
                    }
                )

            unexpected = set(names) - allowed_archive_names
            if unexpected:
                raise RuntimeError("内嵌补丁包含描述清单之外的文件")
    except zipfile.BadZipFile as exc:
        raise RuntimeError("安装包内嵌补丁 ZIP 已损坏") from exc

    manifest: dict[str, Any] = {
        "contract_version": PATCH_CONTRACT_VERSION,
        "from_version": from_version,
        "to_version": to_version,
        "published_at": published_at,
        "file_base_url": f"{public_base_url}/patch/{quote(to_version, safe='')}",
        "files": public_entries,
    }
    signature = signing_key.sign(canonical_patch_manifest_bytes(manifest))
    signing_key.public_key().verify(signature, canonical_patch_manifest_bytes(manifest))
    manifest["signature"] = base64.b64encode(signature).decode("ascii")
    atomic_write_json(output_dir / "patch-manifest.json", manifest)
    return manifest, total_bytes


def build_patch_payload(
    *,
    old_root: Path,
    new_root: Path,
    from_version: str,
    to_version: str,
    published_at: str,
    public_base_url: str,
    signing_key: Ed25519PrivateKey,
    output_dir: Path,
) -> tuple[dict[str, Any], int]:
    old_files = collect_bundle_files(old_root)
    new_files = collect_bundle_files(new_root)
    entries: list[dict[str, Any]] = []
    total_bytes = 0
    output_dir.mkdir(parents=True, exist_ok=False)

    for relative in sorted(set(old_files) | set(new_files)):
        old = old_files.get(relative)
        new = new_files.get(relative)
        if old is not None and new is not None and old[1] == new[1]:
            continue
        if new is None:
            entries.append({"path": relative, "action": "delete", "sha256": "", "size": 0})
            continue
        if new[2] <= 0:
            raise RuntimeError(f"补丁暂不支持空文件：{relative}")
        action = "add" if old is None else "replace"
        entries.append({"path": relative, "action": action, "sha256": new[1], "size": new[2]})
        total_bytes += new[2]
        atomic_copy(new[0], output_dir / relative)

    if not entries:
        raise RuntimeError("新旧安装包没有可生成补丁的文件差异")
    manifest: dict[str, Any] = {
        "contract_version": PATCH_CONTRACT_VERSION,
        "from_version": from_version,
        "to_version": to_version,
        "published_at": published_at,
        "file_base_url": f"{public_base_url}/patch/{quote(to_version, safe='')}",
        "files": entries,
    }
    signature = signing_key.sign(canonical_patch_manifest_bytes(manifest))
    signing_key.public_key().verify(signature, canonical_patch_manifest_bytes(manifest))
    manifest["signature"] = base64.b64encode(signature).decode("ascii")
    atomic_write_json(output_dir / "patch-manifest.json", manifest)
    return manifest, total_bytes


def publish_patch_payload(
    source_dir: Path,
    manifest: dict[str, Any],
    publish_dir: Path,
    version: str,
) -> None:
    patch_root = publish_dir / "patch"
    patch_root.mkdir(parents=True, exist_ok=True)
    destination = patch_root / version
    if destination.exists():
        raise RuntimeError("该版本的补丁发布目录已存在")
    temporary = patch_root / f".{version}.{secrets.token_hex(8)}.tmp"
    try:
        shutil.copytree(source_dir, temporary)
        os.replace(temporary, destination)
        # The fixed manifest URL is replaced last. Clients therefore never see
        # a manifest before every referenced patch file is publicly available.
        atomic_write_json(publish_dir / "patch-manifest.json", manifest)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def load_signing_key(settings: Settings) -> Ed25519PrivateKey:
    path = settings.signing_key_path
    if path is None or not path.is_file():
        raise api_error(503, "signing_key_unavailable", "发布签名密钥尚未配置，当前不能发布更新")
    password_value = os.environ.get("UPDATE_SIGNING_KEY_PASSWORD")
    try:
        key = serialization.load_pem_private_key(
            path.read_bytes(),
            password=password_value.encode("utf-8") if password_value else None,
        )
    except Exception as exc:
        raise api_error(503, "signing_key_invalid", "发布签名密钥无法读取") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise api_error(503, "signing_key_invalid", "发布签名密钥不是 Ed25519 私钥")
    public_raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_b64 = base64.b64encode(public_raw).decode("ascii")
    if settings.expected_public_key_b64 and not secrets.compare_digest(
        public_b64,
        settings.expected_public_key_b64,
    ):
        raise api_error(503, "signing_key_mismatch", "发布私钥与客户端内置公钥不匹配")
    return key


def validate_evsign_settings(settings: Settings) -> None:
    parsed = urlparse(settings.evsign_api_url)
    if parsed.scheme != "https" or parsed.hostname != "api.evsign.cn" or parsed.path.rstrip("/") != "/v1":
        raise RuntimeError("EVSIGN_API_URL 必须是 EV Sign 官方 HTTPS API 地址")
    if settings.evsign_required and not settings.evsign_license_key:
        raise RuntimeError("EVSIGN_REQUIRED=1 时必须配置 EVSIGN_LICENSE_KEY")


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def sign_with_evsign(path: Path, filename: str, settings: Settings) -> tuple[Path, dict[str, str]]:
    if not settings.evsign_license_key:
        if settings.evsign_required:
            raise api_error(503, "evsign_unavailable", "EV Sign 许可证尚未配置，当前不能发布更新")
        return path, {"status": "disabled", "message": "EV Sign 自动签名未启用"}

    signed = settings.staging_dir / f"{secrets.token_hex(16)}.signed.part"
    input_size = path.stat().st_size
    max_signed_size = settings.max_upload_bytes + 32 * 1024 * 1024
    headers = {
        "X-Key": settings.evsign_license_key,
        "X-Action": "api-sign",
        "X-Algorithm": "sha256",
        "X-File-Name": quote(Path(filename).name, safe=""),
        "X-Timestamp": "auto",
        "X-Append": "no",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(input_size),
    }
    timeout = httpx.Timeout(
        connect=30,
        read=settings.evsign_timeout_seconds,
        write=settings.evsign_timeout_seconds,
        pool=30,
    )
    try:
        with path.open("rb") as source, httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream("POST", settings.evsign_api_url, headers=headers, content=source) as response:
                if response.status_code != 200:
                    error_body = response.read()[:4096].decode("utf-8", errors="replace").strip()
                    raise api_error(
                        502,
                        "evsign_rejected",
                        f"EV Sign 签名失败（HTTP {response.status_code}）：{error_body or '未返回原因'}",
                    )
                size = 0
                header = b""
                with signed.open("xb") as target:
                    for chunk in response.iter_bytes(1024 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_signed_size:
                            raise api_error(502, "evsign_response_too_large", "EV Sign 返回文件体积异常")
                        if len(header) < 2:
                            header += chunk[: 2 - len(header)]
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if size < 2 or header != b"MZ":
                    raise api_error(502, "evsign_invalid_response", "EV Sign 未返回有效的 Windows EXE")
    except HTTPException:
        signed.unlink(missing_ok=True)
        raise
    except (OSError, httpx.HTTPError) as exc:
        signed.unlink(missing_ok=True)
        LOGGER.warning("EV Sign request failed: %s", type(exc).__name__)
        raise api_error(502, "evsign_request_failed", "无法连接 EV Sign 签名服务，请稍后重试") from exc
    return signed, {"status": "signed", "message": "EV Sign 已返回签名安装包"}


def verify_authenticode(path: Path, settings: Settings) -> dict[str, str]:
    if os.name == "nt":
        script = (
            "$s=Get-AuthenticodeSignature -LiteralPath $args[0];"
            "$subject='';if($s.SignerCertificate){$subject=[string]$s.SignerCertificate.Subject};"
            "$o=[pscustomobject]@{status=[string]$s.Status;subject=$subject;"
            "message=[string]$s.StatusMessage};"
            "$o|ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script, str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            payload = json.loads(completed.stdout.strip()) if completed.returncode == 0 else {}
            status = str(payload.get("status") or "error")
            result = {
                "status": status,
                "subject": str(payload.get("subject") or ""),
                "message": str(payload.get("message") or completed.stderr.strip() or "检查失败"),
            }
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            result = {"status": "error", "subject": "", "message": str(exc)}
    else:
        verifier = shutil.which("osslsigncode")
        if not verifier:
            result = {"status": "unavailable", "subject": "", "message": "服务器尚未安装 osslsigncode"}
        else:
            try:
                command = [verifier, "verify"]
                ca_bundle = Path("/etc/ssl/certs/ca-certificates.crt")
                if ca_bundle.is_file():
                    command.extend(["-CAfile", str(ca_bundle), "-TSA-CAfile", str(ca_bundle)])
                command.extend(["-in", str(path)])
                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
                subject_match = re.search(r"^\s*Subject:\s*(.+)$", output, flags=re.MULTILINE | re.IGNORECASE)
                result = {
                    "status": "Valid" if completed.returncode == 0 else "error",
                    "subject": subject_match.group(1).strip() if subject_match else "",
                    "message": output[-2000:] if output else "osslsigncode 未返回检查信息",
                }
            except (OSError, subprocess.SubprocessError) as exc:
                result = {"status": "error", "subject": "", "message": str(exc)}
    valid = result["status"].lower() == "valid"
    publisher_ok = not settings.expected_authenticode_publisher or (
        settings.expected_authenticode_publisher.lower() in result["subject"].lower()
    )
    if settings.require_authenticode and (not valid or not publisher_ok):
        raise api_error(422, "authenticode_invalid", "安装包未通过 Authenticode 签名校验")
    if valid and not publisher_ok:
        result["status"] = "publisher_mismatch"
    return result


async def stage_upload(upload: UploadFile, settings: Settings) -> tuple[Path, str, int]:
    original_name = Path(upload.filename or "").name
    if not original_name.lower().endswith(".exe"):
        raise api_error(422, "invalid_file_type", "只允许上传 .exe 安装包")
    staged = settings.staging_dir / f"{secrets.token_hex(16)}.part"
    digest = hashlib.sha256()
    size = 0
    header = b""
    try:
        with staged.open("xb") as target:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise api_error(413, "file_too_large", "安装包超过后台允许的最大体积")
                if len(header) < 2:
                    header += chunk[: 2 - len(header)]
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if size < 2 or header != b"MZ":
            raise api_error(422, "invalid_executable", "文件不是有效的 Windows EXE")
        return staged, digest.hexdigest(), size
    except Exception:
        staged.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    try:
        with source.open("rb") as input_file, temporary.open("xb") as output_file:
            shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def stage_atomic_download_alias(source: Path, destination: Path) -> Path:
    """Prepare a verified replacement beside the live website download file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    try:
        try:
            os.link(source, temporary)
        except OSError:
            with source.open("rb") as input_file, temporary.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
                output_file.flush()
                os.fsync(output_file.fileno())
        source_hash, source_size = hash_file(source)
        staged_hash, staged_size = hash_file(temporary)
        if source_hash != staged_hash or source_size != staged_size:
            raise RuntimeError("官网下载文件复制后校验不一致")
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def commit_atomic_download_alias(staged: Path, destination: Path) -> None:
    """Atomically expose a fully signed and verified installer on the website."""
    os.replace(staged, destination)


def atomic_write_json(destination: Path, payload: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with temporary.open("xb") as output_file:
            output_file.write(encoded)
            output_file.flush()
            os.fsync(output_file.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def validate_public_base_url(value: str) -> None:
    parsed = urlparse(value)
    is_local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    if (parsed.scheme != "https" and not is_local_http) or not parsed.netloc:
        raise RuntimeError("UPDATE_PUBLIC_BASE_URL 必须使用 HTTPS；仅 localhost 测试允许 HTTP")


def create_app(
    settings: Settings | None = None,
    password_hasher: PasswordHasher | None = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    validate_public_base_url(resolved.public_base_url)
    validate_evsign_settings(resolved)
    hasher = password_hasher or PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
    service = UpdateAdminService(resolved, hasher)
    app = FastAPI(title="MainPG 更新发布后台", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.update_admin = service

    static_dir = APP_DIR / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def ensure_same_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if not origin:
            return
        parsed = urlparse(origin)
        if parsed.netloc.lower() != request.headers.get("host", "").lower():
            raise api_error(403, "origin_rejected", "请求来源不受信任")

    def current_admin(request: Request) -> AdminPrincipal:
        return service.principal_for_token(request.cookies.get(resolved.cookie_name))

    def ready_admin(principal: AdminPrincipal = Depends(current_admin)) -> AdminPrincipal:
        if principal.must_change_password:
            raise api_error(403, "password_change_required", "首次登录必须先修改密码")
        return principal

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "signing_key_configured": bool(resolved.signing_key_path and resolved.signing_key_path.is_file()),
            "evsign_configured": bool(resolved.evsign_license_key),
            "authenticode_verifier": "powershell" if os.name == "nt" else ("osslsigncode" if shutil.which("osslsigncode") else "unavailable"),
            "incremental_patch_enabled": resolved.patch_enabled,
            "embedded_patch_supported": True,
            "innoextract_configured": bool(shutil.which(resolved.innoextract_path)),
        }

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict[str, Any]:
        ensure_same_origin(request)
        principal, token = service.authenticate(body.username, body.password, service.client_ip(request))
        response.set_cookie(
            resolved.cookie_name,
            token,
            max_age=resolved.session_hours * 3600,
            httponly=True,
            secure=resolved.secure_cookie,
            samesite="strict",
            path=resolved.cookie_path,
        )
        return {"user": principal}

    @app.get("/api/auth/me")
    def me(principal: AdminPrincipal = Depends(current_admin)) -> dict[str, Any]:
        return {
            "user": {
                "username": principal.username,
                "role": principal.role,
                "must_change_password": principal.must_change_password,
            }
        }

    @app.post("/api/auth/logout")
    def logout(
        request: Request,
        response: Response,
        principal: AdminPrincipal = Depends(current_admin),
    ) -> dict[str, bool]:
        ensure_same_origin(request)
        service.logout(principal, service.client_ip(request))
        response.delete_cookie(resolved.cookie_name, path=resolved.cookie_path)
        return {"ok": True}

    @app.post("/api/auth/change-password")
    def change_password(
        body: ChangePasswordBody,
        request: Request,
        response: Response,
        principal: AdminPrincipal = Depends(current_admin),
    ) -> dict[str, Any]:
        ensure_same_origin(request)
        service.change_password(
            principal,
            body.current_password,
            body.new_password,
            service.client_ip(request),
        )
        response.delete_cookie(resolved.cookie_name, path=resolved.cookie_path)
        return {"ok": True, "requires_relogin": True}

    @app.get("/api/releases")
    def releases(
        page: int = Query(1, ge=1),
        principal: AdminPrincipal = Depends(ready_admin),
    ) -> dict[str, Any]:
        return {**service.list_releases(page, 10), "username": principal.username}

    @app.get("/api/releases/status/{version}")
    def release_status(
        version: str,
        principal: AdminPrincipal = Depends(ready_admin),
    ) -> dict[str, Any]:
        try:
            semantic_version = SemVer.parse(version)
        except ValueError as exc:
            raise api_error(422, "invalid_version", str(exc)) from exc
        release = service.get_release(semantic_version.raw)
        return {
            "published": bool(release and release["status"] == "published"),
            "release": release,
            "username": principal.username,
        }

    @app.get("/api/audit-logs")
    def audit_logs(
        page: int = Query(1, ge=1),
        principal: AdminPrincipal = Depends(ready_admin),
    ) -> dict[str, Any]:
        return {**service.list_audit_logs(page, 50), "username": principal.username}

    @app.post("/api/publish-jobs")
    def create_publish_job(
        body: PublishJobCreateBody,
        request: Request,
        principal: AdminPrincipal = Depends(ready_admin),
    ) -> dict[str, Any]:
        ensure_same_origin(request)
        try:
            semantic_version = SemVer.parse(body.version)
        except ValueError as exc:
            raise api_error(422, "invalid_version", str(exc)) from exc
        website_download_target(resolved, body.channel)
        notes = body.release_notes.strip()
        service.assert_new_version(semantic_version)
        job = service.create_publish_job(
            version=semantic_version.raw,
            channel=body.channel,
            mandatory=body.mandatory,
            release_notes=notes,
            installer_filename=body.installer_filename,
            total_bytes=body.total_bytes,
            created_by=principal.username,
        )
        return {"job": job}

    @app.get("/api/publish-jobs")
    def publish_jobs(principal: AdminPrincipal = Depends(ready_admin)) -> dict[str, Any]:
        return {"items": service.list_publish_jobs(), "username": principal.username}

    @app.get("/api/publish-jobs/{job_id}")
    def publish_job(
        job_id: str,
        principal: AdminPrincipal = Depends(ready_admin),
    ) -> dict[str, Any]:
        job = service.get_publish_job(job_id)
        if job is None:
            raise api_error(404, "publish_job_not_found", "发布任务不存在")
        return {"job": job, "username": principal.username}

    @app.post("/api/publish-jobs/{job_id}/upload-progress")
    def publish_job_upload_progress(
        job_id: str,
        body: PublishJobProgressBody,
        request: Request,
        principal: AdminPrincipal = Depends(ready_admin),
    ) -> dict[str, Any]:
        ensure_same_origin(request)
        service.require_publish_job(job_id, principal)
        service.update_publish_upload_progress(
            job_id,
            uploaded_bytes=body.uploaded_bytes,
            total_bytes=body.total_bytes,
        )
        return {"ok": True}

    @app.post("/api/releases/publish")
    async def publish_release(
        request: Request,
        version: str = Form(...),
        channel: str = Form("update_only"),
        mandatory: bool = Form(False),
        release_notes: str = Form(""),
        job_id: str = Form(""),
        installer: UploadFile = File(...),
        principal: AdminPrincipal = Depends(ready_admin),
    ) -> dict[str, Any]:
        ensure_same_origin(request)
        try:
            semantic_version = SemVer.parse(version)
        except ValueError as exc:
            raise api_error(422, "invalid_version", str(exc)) from exc
        normalized_channel = normalize_download_channel(channel)
        download_target = website_download_target(resolved, normalized_channel)
        notes = release_notes.strip()
        if len(notes) > 10_000:
            raise api_error(422, "release_notes_too_long", "更新说明不能超过 10000 个字符")
        original_filename = Path(installer.filename or "installer.exe").name
        resolved_job_id = job_id.strip()
        if resolved_job_id:
            job = service.require_publish_job(
                resolved_job_id,
                principal,
                version=semantic_version.raw,
            )
            if job["phase"] != "uploading":
                raise api_error(409, "publish_job_not_uploading", "发布任务当前不能接收安装包")
            if bool(job["mandatory"]) != bool(mandatory) or job["release_notes"] != notes:
                raise api_error(409, "publish_job_mismatch", "发布任务参数与上传参数不一致")
            if job["channel"] != normalized_channel:
                raise api_error(409, "publish_job_mismatch", "发布任务渠道与上传渠道不一致")
            if job["installer_filename"] != original_filename:
                raise api_error(409, "publish_job_mismatch", "发布任务文件名与上传文件不一致")
        else:
            total_hint = int(getattr(installer, "size", 0) or 1)
            job = service.create_publish_job(
                version=semantic_version.raw,
                channel=normalized_channel,
                mandatory=mandatory,
                release_notes=notes,
                installer_filename=original_filename,
                total_bytes=total_hint,
                created_by=principal.username,
            )
            resolved_job_id = job["id"]
        staged: Path | None = None
        signed_staged: Path | None = None
        final_installer: Path | None = None
        version_dir: Path | None = None
        patch_work_dir: Path | None = None
        website_alias_staged: Path | None = None
        database_release_inserted = False
        manifest_published = False
        async with service.publish_lock:
            try:
                service.assert_new_version(semantic_version)
                staged, _, uploaded_size = await stage_upload(installer, resolved)
                service.update_publish_job(
                    resolved_job_id,
                    phase="evsign",
                    message="安装包上传完成，正在进行 EV Sign 签名",
                    uploaded_bytes=uploaded_size,
                    total_bytes=uploaded_size,
                )
                publish_source, evsign = await run_in_threadpool(
                    sign_with_evsign,
                    staged,
                    original_filename,
                    resolved,
                )
                if publish_source != staged:
                    signed_staged = publish_source
                service.update_publish_job(
                    resolved_job_id,
                    phase="authenticode",
                    message="EV Sign 已返回，正在验证 Authenticode 签名与时间戳",
                )
                authenticode = await run_in_threadpool(verify_authenticode, publish_source, resolved)
                sha256, file_size = await run_in_threadpool(hash_file, publish_source)
                service.update_publish_job(
                    resolved_job_id,
                    phase="patching",
                    message="代码签名验证通过，正在生成并校验增量补丁",
                )
                signing_key = load_signing_key(resolved)
                filename = f"MainPG-Setup-{semantic_version.raw}.exe"
                published_at = iso_utc()
                manifest: dict[str, Any] = {
                    "version": semantic_version.raw,
                    "mandatory": bool(mandatory),
                    "installer_url": f"{resolved.public_base_url}/{quote(filename)}",
                    "sha256": sha256,
                    "release_notes": notes,
                    "published_at": published_at,
                }
                signature_bytes = signing_key.sign(canonical_manifest_bytes(manifest))
                signing_key.public_key().verify(signature_bytes, canonical_manifest_bytes(manifest))
                manifest["signature"] = base64.b64encode(signature_bytes).decode("ascii")

                patch_manifest: dict[str, Any] | None = None
                patch_result: dict[str, Any] = {
                    "status": "not_available",
                    "from_version": "",
                    "file_count": 0,
                    "total_bytes": 0,
                    "error": "",
                }
                previous_release = service.latest_release()
                if resolved.patch_enabled and previous_release is not None:
                    patch_result["from_version"] = previous_release["version"]
                    patch_work_dir = resolved.staging_dir / f"{secrets.token_hex(16)}.patch"
                    patch_work_dir.mkdir(parents=True, exist_ok=False)
                    previous_installer = resolved.publish_dir / previous_release["installer_filename"]
                    try:
                        try:
                            patch_manifest, patch_total_bytes = await run_in_threadpool(
                                prepare_embedded_patch_payload,
                                installer=staged,
                                from_version=previous_release["version"],
                                to_version=semantic_version.raw,
                                published_at=published_at,
                                public_base_url=resolved.public_base_url,
                                signing_key=signing_key,
                                output_dir=patch_work_dir / "payload",
                                max_payload_bytes=resolved.patch_max_extracted_bytes,
                            )
                        except EmbeddedPatchMissing:
                            # Compatibility fallback for installers that can be
                            # extracted by the server. Current MainPG builds use
                            # the embedded payload and do not depend on Inno's
                            # private compression format.
                            if not previous_installer.is_file():
                                raise RuntimeError("上一版本安装包不存在，无法生成补丁")
                            old_root = await run_in_threadpool(
                                extract_installer_bundle,
                                previous_installer,
                                patch_work_dir / "old",
                                previous_release["version"],
                                resolved,
                            )
                            new_root = await run_in_threadpool(
                                extract_installer_bundle,
                                publish_source,
                                patch_work_dir / "new",
                                semantic_version.raw,
                                resolved,
                            )
                            patch_manifest, patch_total_bytes = await run_in_threadpool(
                                build_patch_payload,
                                old_root=old_root,
                                new_root=new_root,
                                from_version=previous_release["version"],
                                to_version=semantic_version.raw,
                                published_at=published_at,
                                public_base_url=resolved.public_base_url,
                                signing_key=signing_key,
                                output_dir=patch_work_dir / "payload",
                            )
                        patch_result.update(
                            status="prepared",
                            file_count=len(patch_manifest["files"]),
                            total_bytes=patch_total_bytes,
                        )
                    except Exception as exc:
                        LOGGER.exception(
                            "incremental patch preparation failed: %s -> %s",
                            previous_release["version"],
                            semantic_version.raw,
                        )
                        patch_manifest = None
                        patch_result.update(status="failed", error=str(exc) or "补丁生成失败")
                elif not resolved.patch_enabled:
                    patch_result.update(status="disabled", error="服务器已关闭增量补丁")

                service.update_publish_job(
                    resolved_job_id,
                    phase="publishing",
                    message="增量补丁处理完成，正在发布官网文件和签名清单",
                )
                version_dir = resolved.publish_dir / "releases" / semantic_version.raw
                final_installer = resolved.publish_dir / filename
                if final_installer.exists() or version_dir.exists():
                    raise api_error(409, "release_files_exist", "该版本的发布文件已存在，请使用更高版本号")
                version_dir.mkdir(parents=True, exist_ok=False)
                atomic_copy(publish_source, final_installer)
                atomic_write_json(version_dir / "manifest.json", manifest)
                if download_target is not None:
                    website_alias_staged = await run_in_threadpool(
                        stage_atomic_download_alias,
                        final_installer,
                        download_target,
                    )

                with service.db.connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO releases(
                            version, channel, mandatory, release_notes, installer_filename, installer_url,
                            sha256, file_size, signature, authenticode_status, status,
                            created_by, created_at, published_at, patch_status,
                            patch_from_version, patch_file_count, patch_total_bytes, patch_error
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            semantic_version.raw,
                            normalized_channel,
                            int(bool(mandatory)),
                            notes,
                            filename,
                            manifest["installer_url"],
                            sha256,
                            file_size,
                            manifest["signature"],
                            authenticode["status"],
                            principal.username,
                            published_at,
                            published_at,
                            patch_result["status"],
                            patch_result["from_version"],
                            patch_result["file_count"],
                            patch_result["total_bytes"],
                            patch_result["error"][:1000],
                        ),
                    )
                database_release_inserted = True
                # manifest.json 最后替换；客户端只会看到完整的新清单。
                atomic_write_json(resolved.publish_dir / "manifest.json", manifest)
                manifest_published = True

                if patch_manifest is not None and patch_work_dir is not None:
                    try:
                        await run_in_threadpool(
                            publish_patch_payload,
                            patch_work_dir / "payload",
                            patch_manifest,
                            resolved.publish_dir,
                            semantic_version.raw,
                        )
                        patch_result["status"] = "published"
                    except Exception as exc:
                        LOGGER.exception("full release published but incremental patch publish failed")
                        patch_result.update(status="failed", error=str(exc) or "补丁发布失败")
                try:
                    service.update_patch_result(
                        semantic_version.raw,
                        status=patch_result["status"],
                        from_version=patch_result["from_version"],
                        file_count=patch_result["file_count"],
                        total_bytes=patch_result["total_bytes"],
                        error=patch_result["error"],
                    )
                except sqlite3.Error:
                    LOGGER.exception("release published but patch status update failed")
                if website_alias_staged is not None and download_target is not None:
                    await run_in_threadpool(
                        commit_atomic_download_alias,
                        website_alias_staged,
                        download_target,
                    )
                    website_alias_staged = None
                try:
                    service.db.audit(
                        principal.username,
                        "release_published",
                        target=semantic_version.raw,
                        ip_address=service.client_ip(request),
                        details={
                            "channel": normalized_channel,
                            "website_download_url": DOWNLOAD_CHANNEL_URLS.get(normalized_channel, ""),
                            "mandatory": bool(mandatory),
                            "sha256": sha256,
                            "file_size": file_size,
                            "evsign_status": evsign["status"],
                            "authenticode_status": authenticode["status"],
                            "authenticode_subject": authenticode["subject"],
                            "patch_status": patch_result["status"],
                            "patch_from_version": patch_result["from_version"],
                            "patch_file_count": patch_result["file_count"],
                            "patch_total_bytes": patch_result["total_bytes"],
                        },
                    )
                except sqlite3.Error:
                    # 发布已原子生效时，审计写入异常不能把成功响应伪装成失败，
                    # 否则管理员可能重复上传同一版本。
                    LOGGER.exception("release published but audit insert failed: %s", semantic_version.raw)
                if normalized_channel == "update_only":
                    completion_message = "更新清单签名完成，版本已发布；官网下载文件未变更"
                else:
                    completion_message = (
                        f"更新清单签名完成，{DOWNLOAD_CHANNEL_LABELS[normalized_channel]}"
                        "官网下载文件已同步"
                    )
                if patch_result["status"] == "failed":
                    completion_message += "；增量补丁失败，客户端将使用完整安装包"
                service.update_publish_job(
                    resolved_job_id,
                    phase="completed",
                    message=completion_message,
                )
                return {
                    "ok": True,
                    "job": service.get_publish_job(resolved_job_id),
                    "release": manifest,
                    "channel": normalized_channel,
                    "website_download_url": DOWNLOAD_CHANNEL_URLS.get(normalized_channel, ""),
                    "file_size": file_size,
                    "evsign": evsign,
                    "authenticode": authenticode,
                    "patch": patch_result,
                }
            except Exception as exc:
                detail = getattr(exc, "detail", None)
                if isinstance(detail, dict):
                    failure_message = str(detail.get("message") or "发布失败")
                elif isinstance(detail, str):
                    failure_message = detail
                else:
                    failure_message = str(exc) or "发布失败"
                try:
                    service.update_publish_job(
                        resolved_job_id,
                        phase="failed",
                        message="发布失败",
                        error=failure_message,
                    )
                except sqlite3.Error:
                    LOGGER.exception("publish failed and job status update also failed: %s", resolved_job_id)
                if not manifest_published:
                    if database_release_inserted:
                        with service.db.connect() as connection:
                            connection.execute("DELETE FROM releases WHERE version = ?", (semantic_version.raw,))
                    if final_installer is not None:
                        final_installer.unlink(missing_ok=True)
                    if version_dir is not None and version_dir.exists():
                        shutil.rmtree(version_dir, ignore_errors=True)
                raise
            finally:
                if staged is not None:
                    staged.unlink(missing_ok=True)
                if signed_staged is not None:
                    signed_staged.unlink(missing_ok=True)
                if patch_work_dir is not None and patch_work_dir.exists():
                    shutil.rmtree(patch_work_dir, ignore_errors=True)
                if website_alias_staged is not None:
                    website_alias_staged.unlink(missing_ok=True)

    return app


def app_factory() -> FastAPI:
    """Uvicorn factory entrypoint: uvicorn app:app_factory --factory."""
    return create_app()
