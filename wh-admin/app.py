# -*- coding: utf-8 -*-
"""电商工作台后台管理系统。

桌面程序保存 SSH 连接配置；部署到服务器时可通过本机模式直接访问数据库。
管理员账号、会话、邀请码和操作日志全部保存在 customer-auth SQLite 数据库中。
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import sqlite3
import re
import secrets
import string
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import paramiko
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from server_scripts import (
    SERVER_CHANGE_ADMIN_PASSWORD_SCRIPT,
    SERVER_CREATE_ADMIN_SCRIPT,
    SERVER_DASHBOARD_SCRIPT,
    SERVER_FORCE_LOGOUT_USER_SCRIPT,
    SERVER_GENERATE_INVITATIONS_SCRIPT,
    SERVER_LIST_ADMINS_SCRIPT,
    SERVER_LIST_AUDIT_SCRIPT,
    SERVER_LIST_INVITATIONS_SCRIPT,
    SERVER_LIST_USERS_SCRIPT,
    SERVER_LOGIN_SCRIPT,
    SERVER_LOGOUT_SCRIPT,
    SERVER_RESET_ADMIN_PASSWORD_SCRIPT,
    SERVER_RESET_USER_PASSWORD_SCRIPT,
    SERVER_SCHEMA_SCRIPT,
    SERVER_SET_ADMIN_STATUS_SCRIPT,
    SERVER_SET_USER_STATUS_SCRIPT,
    SERVER_VALIDATE_SESSION_SCRIPT,
    SERVER_VERIFY_ADMIN_PASSWORD_SCRIPT,
)


INITIAL_ADMIN_USERS = (
    "Yang123",
    "Shen123",
    "Liu123",
    "Dai123",
    "Ma123",
    "Shi123",
    "He123",
)
INITIAL_ADMIN_PASSWORD = "123456"
LOCAL_MODE = os.environ.get("WH_ADMIN_LOCAL_MODE", "").strip().lower() in {
    "1", "true", "yes", "on"
}

_AUTH_RUNTIME = Path("/opt/wh-workbench/MainPG/local-runtime")
_LEGACY_AI_ENV = Path("/etc/wh-workbench/ai.env")


def _credential_vault() -> Any:
    """Load the server-only credential vault lazily.

    The admin application deliberately imports no upstream credentials.  It can
    only call the vault's masked metadata and mutation APIs.
    """
    if str(_AUTH_RUNTIME) not in sys.path:
        sys.path.insert(0, str(_AUTH_RUNTIME))
    try:
        from wh_local.customer import credential_vault
    except Exception as exc:
        log.error("加载密钥库失败: %s", exc)
        raise HTTPException(status_code=503, detail="服务器密钥库暂不可用") from exc
    return credential_vault


def _legacy_ai_credentials() -> tuple[dict[str, str], list[str]]:
    """Read old environment entries only for a one-time encrypted migration."""
    if not _LEGACY_AI_ENV.exists():
        return {}, []
    try:
        lines = _LEGACY_AI_ENV.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="无法读取旧密钥配置") from exc
    values: dict[str, str] = {}
    for line in lines:
        match = re.match(r"^\s*(WH_TEXT_API_KEY|WH_WUYIN_IMAGE_API_KEY)\s*=\s*(.*?)\s*(?:\r?\n)?$", line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip("\"'")
    return values, lines


def _migrate_legacy_ai_credentials() -> None:
    """Move legacy plaintext env credentials into root-only encrypted storage."""
    vault = _credential_vault()
    values, lines = _legacy_ai_credentials()
    migrated: set[str] = set()
    try:
        if vault.import_legacy_credential(
            "text", values.get("WH_TEXT_API_KEY", ""), label="迁移的文本服务密钥"
        ) is not None:
            migrated.add("WH_TEXT_API_KEY")
        if vault.import_legacy_credential(
            "image", values.get("WH_WUYIN_IMAGE_API_KEY", ""), label="迁移的图片服务密钥"
        ) is not None:
            migrated.add("WH_WUYIN_IMAGE_API_KEY")
    except vault.CredentialVaultError as exc:
        raise HTTPException(status_code=503, detail="服务器密钥库迁移失败") from exc
    if not migrated:
        return
    retained = [
        line for line in lines
        if not re.match(r"^\s*(?:" + "|".join(re.escape(key) for key in migrated) + r")\s*=", line)
    ]
    temporary = _LEGACY_AI_ENV.with_suffix(".env.tmp")
    try:
        temporary.write_text("".join(retained), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, _LEGACY_AI_ENV)
        os.chmod(_LEGACY_AI_ENV, 0o600)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="旧密钥配置清理失败") from exc
    log.info("已将 %s 迁移至加密密钥库", ",".join(sorted(migrated)))


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _static_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")).resolve() / "static"
    return Path(__file__).resolve().parent / "static"


BASE_DIR = _app_dir()
CONFIG_PATH = BASE_DIR / "config.json"
STATIC_DIR = _static_dir()
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    filename=str(LOG_DIR / "tool.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("wh-admin-tool")

app = FastAPI(title="电商工作台 - 后台管理系统")


# ---------------------------------------------------------------- 配置
def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("读取配置文件失败: %s", exc)
        return {}


def save_config(config: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cfg_ok(config: dict[str, Any]) -> bool:
    if LOCAL_MODE:
        return bool(config.get("database_path"))
    return all(
        config.get(key)
        for key in ("server_ip", "server_port", "ssh_user", "ssh_pass", "database_path")
    )


# ---------------------------------------------------------------- SSH / 远端数据库
def _ssh_exec(script: str, args: list[Any]) -> str:
    """在服务器本机或通过 SSH 执行 Python 片段并返回 stdout。"""
    config = load_config()
    payload = base64.b64encode(
        json.dumps({"s": script, "a": args}, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    runner = (
        "import base64,json,sys;"
        "d=json.loads(base64.b64decode(sys.argv[1]));"
        "globals()['A']=d['a'];"
        "exec(compile(d['s'],'<server_script>','exec'),globals())"
    )
    if LOCAL_MODE:
        result = subprocess.run(
            [sys.executable, "-c", runner, payload],
            text=True,
            capture_output=True,
            timeout=60,
        )
        if result.returncode or result.stderr.strip():
            error = result.stderr.strip() or f"本机脚本退出码 {result.returncode}"
            log.error("服务器本机脚本错误: %s", error[-2000:])
            raise RuntimeError(f"服务器执行出错: {error[-500:]}")
        return result.stdout
    command = f'python3 -c "{runner}" {payload}'
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            config["server_ip"],
            port=int(config["server_port"]),
            username=config["ssh_user"],
            password=config["ssh_pass"],
            timeout=20,
        )
        _, stdout, stderr = client.exec_command(command, timeout=60)
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace")
        if error.strip():
            log.error("服务器脚本错误: %s", error.strip()[-2000:])
            raise RuntimeError(f"服务器执行出错: {error.strip()[-500:]}")
        return output
    finally:
        client.close()


def _remote(script: str, args: list[Any]) -> dict[str, Any]:
    database_path = load_config()["database_path"]
    output = _ssh_exec(script, [database_path, *args]).strip()
    try:
        result = json.loads(output or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"服务器返回了无法解析的数据: {output[-300:]}") from exc
    if not isinstance(result, dict):
        raise RuntimeError("服务器返回格式不正确")
    return result


def ensure_server_schema() -> dict[str, Any]:
    return _remote(
        SERVER_SCHEMA_SCRIPT,
        [list(INITIAL_ADMIN_USERS), INITIAL_ADMIN_PASSWORD],
    )


def _remote_error(result: dict[str, Any]) -> None:
    code = str(result.get("error", "remote_error"))
    messages = {
        "invalid_session": (401, "未登录或会话已过期"),
        "password_change_required": (403, "首次登录必须先修改密码"),
        "invalid_credentials": (401, "管理员账号或密码错误"),
        "account_disabled": (403, "管理员账号已被禁用"),
        "bad_current_password": (400, "当前密码错误"),
        "not_found": (404, "未找到目标账号"),
        "username_exists": (409, "管理员账号已存在"),
        "cannot_disable_self": (400, "不能禁用当前登录账号"),
        "last_active_admin": (400, "至少需要保留一个启用的管理员账号"),
    }
    status, message = messages.get(code, (502, f"服务器操作失败: {code}"))
    raise HTTPException(status_code=status, detail=message)


def _require_cfg() -> None:
    if not cfg_ok(load_config()):
        raise HTTPException(status_code=400, detail="未配置或配置不完整")


def _check_auth(
    token: str | None, *, allow_password_change: bool = False
) -> dict[str, Any]:
    _require_cfg()
    clean_token = (token or "").strip()
    if not clean_token:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    try:
        result = _remote(
            SERVER_VALIDATE_SESSION_SCRIPT,
            [clean_token, allow_password_change],
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("校验管理员会话失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"连接服务器失败: {exc}") from exc
    if not result.get("ok"):
        _remote_error(result)
    return dict(result["admin"])


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _require_password(password: str, *, username: str = "") -> str:
    if not 8 <= len(password) <= 128:
        raise HTTPException(status_code=400, detail="新密码长度必须为 8–128 位")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="新密码必须同时包含字母和数字")
    if username and password.lower() == username.lower():
        raise HTTPException(status_code=400, detail="密码不能与管理员账号相同")
    return password


def _require_admin_username(username: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise HTTPException(
            status_code=400,
            detail="账号须为 3–32 位，仅可包含字母、数字、点、下划线或短横线",
        )
    return username


def gen_password(length: int = 14) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    password = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    password += [secrets.choice(chars) for _ in range(length - 4)]
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


def _run_remote(script: str, args: list[Any], operation: str) -> dict[str, Any]:
    try:
        result = _remote(script, args)
    except HTTPException:
        raise
    except Exception as exc:
        log.error("%s失败: %s", operation, exc)
        raise HTTPException(status_code=502, detail=f"连接服务器失败: {exc}") from exc
    if not result.get("ok"):
        _remote_error(result)
    return result


# ---------------------------------------------------------------- 登录与设置
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"configured": cfg_ok(load_config())}


@app.post("/api/setup")
def setup(payload: dict[str, Any], x_auth_token: str | None = Header(default=None)) -> dict[str, bool]:
    current = load_config()
    if cfg_ok(current):
        _check_auth(x_auth_token)
    required = ("server_ip", "server_port", "ssh_user", "ssh_pass", "database_path")
    missing = [key for key in required if not str(payload.get(key, "")).strip()]
    if missing:
        raise HTTPException(status_code=400, detail=f"缺少字段: {', '.join(missing)}")
    save_config(
        {
            "server_ip": str(payload["server_ip"]).strip(),
            "server_port": int(payload["server_port"]),
            "ssh_user": str(payload["ssh_user"]).strip(),
            "ssh_pass": str(payload["ssh_pass"]),
            "database_path": str(payload["database_path"]).strip(),
        }
    )
    try:
        ensure_server_schema()
    except Exception as exc:
        log.error("初始化服务器管理表失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"配置已保存，但初始化服务器失败: {exc}") from exc
    return {"ok": True}


@app.post("/api/login")
def login(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _require_cfg()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    if not username or not password:
        raise HTTPException(status_code=400, detail="请输入管理员账号和密码")
    try:
        ensure_server_schema()
        result = _remote(
            SERVER_LOGIN_SCRIPT,
            [username, password, _client_ip(request), request.headers.get("user-agent", "")],
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("管理员登录失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"连接服务器失败: {exc}") from exc
    if not result.get("ok"):
        _remote_error(result)
    return result


@app.get("/api/me")
def me(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    return {"ok": True, "admin": _check_auth(x_auth_token, allow_password_change=True)}


@app.post("/api/logout")
def logout(request: Request, x_auth_token: str | None = Header(default=None)) -> dict[str, bool]:
    token = (x_auth_token or "").strip()
    if token and cfg_ok(load_config()):
        _run_remote(SERVER_LOGOUT_SCRIPT, [token, _client_ip(request)], "管理员退出")
    return {"ok": True}


@app.post("/api/change-password")
def change_password(
    payload: dict[str, Any],
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    admin = _check_auth(x_auth_token, allow_password_change=True)
    current_password = str(payload.get("current_password", ""))
    new_password = _require_password(
        str(payload.get("new_password", "")), username=admin["username"]
    )
    if current_password == new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    return _run_remote(
        SERVER_CHANGE_ADMIN_PASSWORD_SCRIPT,
        [(x_auth_token or "").strip(), current_password, new_password, _client_ip(request)],
        "修改管理员密码",
    )


# ---------------------------------------------------------------- 用户账号状态管理
@app.get("/api/users")
def users(
    limit: int = 100,
    offset: int = 0,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return _run_remote(SERVER_LIST_USERS_SCRIPT, [limit, offset], "读取用户列表")


@app.post("/api/users/{account_id}/status")
def user_status(
    account_id: str,
    payload: dict[str, Any],
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    status = str(payload.get("status", ""))
    if status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="状态只能是 active 或 disabled")
    return _run_remote(
        SERVER_SET_USER_STATUS_SCRIPT,
        [(x_auth_token or "").strip(), account_id, status, _client_ip(request)],
        "修改用户状态",
    )


@app.post("/api/users/{account_id}/reset-password")
def user_reset_password(
    account_id: str,
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    new_password = gen_password()
    result = _run_remote(
        SERVER_RESET_USER_PASSWORD_SCRIPT,
        [(x_auth_token or "").strip(), account_id, new_password, _client_ip(request)],
        "重置用户密码",
    )
    result["new_password"] = new_password
    return result


@app.post("/api/users/{account_id}/force-logout")
def user_force_logout(
    account_id: str,
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    return _run_remote(
        SERVER_FORCE_LOGOUT_USER_SCRIPT,
        [(x_auth_token or "").strip(), account_id, _client_ip(request)],
        "强制用户下线",
    )


# ---------------------------------------------------------------- 公告管理
# 公告数据存独立 sqlite（可被 ANNOUNCE_DB_PATH 覆盖），不依赖远程脚本、不动 customer-auth 主库。
ANNOUNCE_DB_PATH = os.environ.get("ANNOUNCE_DB_PATH", "/opt/wh-admin/announcements.sqlite3")


def _announce_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(ANNOUNCE_DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(ANNOUNCE_DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            active INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.commit()
    return con


def _now_beijing() -> str:
    """北京时间字符串，带 +08:00 时区，供前端 new Date() 正确解析。"""
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _serialize_announcement(row) -> dict[str, Any]:
    r = dict(row)
    return {
        "id": r["id"],
        "title": r["title"],
        "content": r["content"],
        "published_at": r["published_at"],
        "active": bool(r["active"]),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


@app.get("/api/announcements/public")
def public_announcements() -> dict[str, Any]:
    # 免登录：客户端工作台轮询拉取。仅返回 active=1 的公告，下线/删除的会被客户端撤回。
    con = _announce_db()
    try:
        rows = con.execute(
            "SELECT * FROM announcements WHERE active=1 ORDER BY id DESC"
        ).fetchall()
    finally:
        con.close()
    return {"announcements": [_serialize_announcement(r) for r in rows]}


@app.get("/api/announcements")
def list_announcements(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_auth_token)
    con = _announce_db()
    try:
        rows = con.execute(
            "SELECT * FROM announcements ORDER BY id DESC"
        ).fetchall()
    finally:
        con.close()
    return {"announcements": [_serialize_announcement(r) for r in rows]}


@app.post("/api/announcements")
def create_announcement(payload: dict[str, Any], x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_auth_token)
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="公告标题不能为空")
    content = (payload.get("content") or "").strip()
    now = _now_beijing()
    con = _announce_db()
    try:
        cur = con.execute(
            "INSERT INTO announcements(title, content, published_at, active, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (title, content, now, 1, now, now),
        )
        con.commit()
        new_id = cur.lastrowid
        row = con.execute("SELECT * FROM announcements WHERE id=?", (new_id,)).fetchone()
    finally:
        con.close()
    return {"ok": True, "announcement": _serialize_announcement(row)}


@app.patch("/api/announcements/{announcement_id}")
def update_announcement(announcement_id: int, payload: dict[str, Any], x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_auth_token)
    title = (payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="公告标题不能为空")
    content = (payload.get("content") or "").strip()
    now = _now_beijing()
    con = _announce_db()
    try:
        cur = con.execute(
            "UPDATE announcements SET title=?, content=?, updated_at=? WHERE id=?",
            (title, content, now, announcement_id),
        )
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="公告不存在")
        row = con.execute("SELECT * FROM announcements WHERE id=?", (announcement_id,)).fetchone()
    finally:
        con.close()
    return {"ok": True, "announcement": _serialize_announcement(row)}


@app.post("/api/announcements/{announcement_id}/publish")
def set_announcement_publish(announcement_id: int, payload: dict[str, Any], x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_auth_token)
    active = 1 if payload.get("active") else 0
    now = _now_beijing()
    con = _announce_db()
    try:
        cur = con.execute(
            "UPDATE announcements SET active=?, updated_at=? WHERE id=?",
            (active, now, announcement_id),
        )
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="公告不存在")
    finally:
        con.close()
    return {"ok": True, "active": bool(active)}


@app.delete("/api/announcements/{announcement_id}")
def delete_announcement(announcement_id: int, x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_auth_token)
    con = _announce_db()
    try:
        cur = con.execute("DELETE FROM announcements WHERE id=?", (announcement_id,))
        con.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="公告不存在")
    finally:
        con.close()
    return {"ok": True}


# ---------------------------------------------------------------- 数据看板
@app.get("/api/dashboard")
def dashboard(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_auth_token)
    return _run_remote(SERVER_DASHBOARD_SCRIPT, [0], "读取数据看板")


@app.post("/api/dashboard/revenue")
def dashboard_revenue(
    payload: dict[str, Any],
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    password = str(payload.get("password", ""))
    if not password:
        raise HTTPException(status_code=400, detail="请输入登录密码")
    try:
        verified = _remote(
            SERVER_VERIFY_ADMIN_PASSWORD_SCRIPT,
            [(x_auth_token or "").strip(), password],
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.error("校验管理员密码失败: %s", exc)
        raise HTTPException(status_code=502, detail=f"连接服务器失败: {exc}") from exc
    if not verified.get("ok"):
        if verified.get("error") == "invalid_password":
            raise HTTPException(status_code=403, detail="登录密码不正确")
        _remote_error(verified)
    return _run_remote(SERVER_DASHBOARD_SCRIPT, [1], "读取累计收入")


# ---------------------------------------------------------------- 邀请码管理
@app.get("/api/invitations")
def invitations(
    limit: int = 100,
    offset: int = 0,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return _run_remote(SERVER_LIST_INVITATIONS_SCRIPT, [limit, offset], "读取邀请码")


def _normalize_expiry(value: str) -> str:
    clean = value.strip()
    if not clean:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", clean):
        return f"{clean}T23:59:59+08:00"
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="邀请码到期时间格式不正确") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _beijing_time(value: Any) -> str:
    """Format stored UTC/offset timestamps consistently as Beijing time."""
    clean = str(value or "").strip()
    if not clean:
        return ""
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError:
        return clean
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    china_tz = timezone(timedelta(hours=8))
    return parsed.astimezone(china_tz).strftime("%Y-%m-%d %H:%M:%S")


@app.post("/api/invitations/generate")
def generate_invitations(
    payload: dict[str, Any],
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    try:
        count = int(payload.get("count", 1))
        max_uses = int(payload.get("max_uses", 1))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="生成数量和可用次数必须是整数") from exc
    if not 1 <= count <= 50:
        raise HTTPException(status_code=400, detail="每次可生成 1–50 个邀请码")
    if not 1 <= max_uses <= 100000:
        raise HTTPException(status_code=400, detail="每个邀请码可用次数须为 1–100000")
    expires_at = _normalize_expiry(str(payload.get("expires_at", "")))
    remark = str(payload.get("remark", "")).strip()
    if len(remark) > 100:
        raise HTTPException(status_code=400, detail="备注不能超过 100 个字符")
    return _run_remote(
        SERVER_GENERATE_INVITATIONS_SCRIPT,
        [
            (x_auth_token or "").strip(),
            count,
            max_uses,
            expires_at,
            _client_ip(request),
            remark,
        ],
        "生成邀请码",
    )


# ---------------------------------------------------------------- 管理员账号管理
@app.get("/api/admins")
def admins(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_auth_token)
    return _run_remote(SERVER_LIST_ADMINS_SCRIPT, [], "读取管理员列表")


@app.post("/api/admins")
def create_admin(
    payload: dict[str, Any],
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    username = _require_admin_username(str(payload.get("username", "")).strip())
    temporary_password = _require_password(
        str(payload.get("temporary_password", "")), username=username
    )
    return _run_remote(
        SERVER_CREATE_ADMIN_SCRIPT,
        [
            (x_auth_token or "").strip(),
            username,
            temporary_password,
            _client_ip(request),
        ],
        "创建管理员",
    )


@app.post("/api/admins/{admin_id}/status")
def admin_status(
    admin_id: str,
    payload: dict[str, Any],
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    status = str(payload.get("status", ""))
    if status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="状态只能是 active 或 disabled")
    return _run_remote(
        SERVER_SET_ADMIN_STATUS_SCRIPT,
        [(x_auth_token or "").strip(), admin_id, status, _client_ip(request)],
        "修改管理员状态",
    )


@app.post("/api/admins/{admin_id}/reset-password")
def admin_reset_password(
    admin_id: str,
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    temporary_password = gen_password()
    result = _run_remote(
        SERVER_RESET_ADMIN_PASSWORD_SCRIPT,
        [
            (x_auth_token or "").strip(),
            admin_id,
            temporary_password,
            _client_ip(request),
        ],
        "重置管理员密码",
    )
    result["temporary_password"] = temporary_password
    return result


# ---------------------------------------------------------------- 操作日志
@app.get("/api/audit-logs")
def audit_logs(
    limit: int = 200,
    offset: int = 0,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _check_auth(x_auth_token)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return _run_remote(SERVER_LIST_AUDIT_SCRIPT, [limit, offset], "读取操作日志")


# ---------------------------------------------------------------- Excel 导出
@app.get("/api/credentials")
def credentials(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_auth_token)
    _migrate_legacy_ai_credentials()
    vault = _credential_vault()
    try:
        items = vault.list_credentials()
    except vault.CredentialVaultError as exc:
        raise HTTPException(status_code=503, detail="服务器密钥库暂不可用") from exc
    return {"ok": True, "credentials": items}


@app.post("/api/credentials")
def create_credential(
    payload: dict[str, Any],
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    admin = _check_auth(x_auth_token)
    kind = str(payload.get("kind") or "").strip().lower()
    label = str(payload.get("label") or "").strip()
    secret_value = str(payload.get("secret") or "")
    vault = _credential_vault()
    try:
        item = vault.add_credential(
            kind,
            label,
            secret_value,
            activate=bool(payload.get("activate", True)),
            max_concurrency=payload.get("max_concurrency"),
        )
    except vault.CredentialVaultError as exc:
        raise HTTPException(status_code=400, detail="密钥保存失败，请检查类型和密钥格式") from exc
    log.info("管理员 %s 新增%s密钥 %s，IP=%s", admin.get("username"), kind, item["credential_id"], _client_ip(request))
    return {"ok": True, "credential": item}


@app.patch("/api/credentials/{credential_id}")
def update_credential(
    credential_id: str,
    payload: dict[str, Any],
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    admin = _check_auth(x_auth_token)
    vault = _credential_vault()
    secret_value = payload.get("secret")
    try:
        item = vault.update_credential(
            credential_id,
            label=str(payload["label"]) if "label" in payload else None,
            secret=str(secret_value) if secret_value is not None else None,
            max_concurrency=payload.get("max_concurrency"),
        )
    except vault.CredentialVaultError as exc:
        raise HTTPException(status_code=400, detail="密钥更新失败") from exc
    log.info("管理员 %s 更新密钥 %s，IP=%s", admin.get("username"), credential_id, _client_ip(request))
    return {"ok": True, "credential": item}


@app.post("/api/credentials/{credential_id}/activate")
def activate_credential(
    credential_id: str,
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    admin = _check_auth(x_auth_token)
    vault = _credential_vault()
    try:
        item = vault.activate_credential(credential_id)
    except vault.CredentialVaultError as exc:
        raise HTTPException(status_code=400, detail="启用密钥失败") from exc
    log.info("管理员 %s 启用密钥 %s，IP=%s", admin.get("username"), credential_id, _client_ip(request))
    return {"ok": True, "credential": item}


@app.delete("/api/credentials/{credential_id}")
def delete_credential(
    credential_id: str,
    request: Request,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    admin = _check_auth(x_auth_token)
    vault = _credential_vault()
    try:
        vault.delete_credential(credential_id)
    except vault.CredentialVaultError as exc:
        raise HTTPException(status_code=400, detail="删除失败：请先启用同类型的另一把密钥") from exc
    log.info("管理员 %s 删除密钥 %s，IP=%s", admin.get("username"), credential_id, _client_ip(request))
    return {"ok": True}


@app.get("/api/credentials/health")
def credentials_health(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_auth_token)
    vault = _credential_vault()
    try:
        items = vault.list_credentials()
    except vault.CredentialVaultError as exc:
        raise HTTPException(status_code=503, detail="服务器密钥库暂不可用") from exc
    return {
        "ok": True,
        "text_active": any(item["kind"] == "text" and item["active"] for item in items),
        "image_active": any(item["kind"] == "image" and item["active"] for item in items),
    }


@app.get("/api/export")
def export(request: Request, x_auth_token: str | None = Header(default=None)) -> Response:
    _check_auth(x_auth_token)
    data = _run_remote(SERVER_LIST_USERS_SCRIPT, [], "导出用户列表")
    user_rows = list(data.get("users", []))

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "用户列表"
    headers = ["用户名", "邮箱", "显示名", "账号所属", "角色", "工作区", "状态", "登录状态", "注册时间（北京时间）", "最近登录（北京时间）"]
    worksheet.append(headers)
    header_fill = PatternFill("solid", fgColor="4472C4")
    for column in range(1, len(headers) + 1):
        cell = worksheet.cell(row=1, column=column)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    status_map = {"active": "正常", "disabled": "已禁用"}
    login_map = {"online": "在线", "offline": "离线"}
    for user in user_rows:
        worksheet.append(
            [
                user.get("username", ""),
                user.get("email", ""),
                user.get("display_name", ""),
                user.get("invitation_code", "") or "历史未记录",
                user.get("role_name", user.get("role", "")),
                user.get("workspace_id", ""),
                status_map.get(user.get("account_status", ""), user.get("account_status", "")),
                login_map.get(user.get("login_status", ""), user.get("login_status", "")),
                _beijing_time(user.get("created_at", "")),
                _beijing_time(user.get("last_login", "")),
            ]
        )
    for column in range(1, len(headers) + 1):
        values = [headers[column - 1]] + [
            worksheet.cell(row=row, column=column).value or ""
            for row in range(2, worksheet.max_row + 1)
        ]
        width = max(len(str(value)) for value in values)
        worksheet.column_dimensions[get_column_letter(column)].width = min(
            max(width * 1.6 + 4, 12), 45
        )
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{worksheet.max_row}"

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"用户列表_{stamp}.xlsx"
    from urllib.parse import quote

    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="users_{stamp}.xlsx"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


# ---------------------------------------------------------------- 静态页面与启动
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/static/{filename}")
def static_file(filename: str) -> FileResponse:
    path = STATIC_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)


PORT_FILE = BASE_DIR / "port.txt"


def _is_alive(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def _existing_port() -> int | None:
    if PORT_FILE.exists():
        try:
            port = int(PORT_FILE.read_text(encoding="utf-8").strip())
            if _is_alive(port):
                return port
        except Exception:
            pass
    return None


def _open_browser(port: int) -> None:
    time.sleep(1.2)
    webbrowser.open(f"http://127.0.0.1:{port}/")


if __name__ == "__main__":
    import socket

    existing_port = _existing_port()
    if existing_port is not None:
        print(f"后台管理系统已在运行，直接打开: http://127.0.0.1:{existing_port}/")
        webbrowser.open(f"http://127.0.0.1:{existing_port}/")
        raise SystemExit(0)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    selected_port = listener.getsockname()[1]
    listener.close()
    try:
        PORT_FILE.write_text(str(selected_port), encoding="utf-8")
    except Exception:
        pass
    threading.Thread(target=_open_browser, args=(selected_port,), daemon=True).start()
    print(f"电商工作台后台管理系统已启动: http://127.0.0.1:{selected_port}/")
    print("关闭本窗口即退出管理系统。")
    uvicorn.run(app, host="127.0.0.1", port=selected_port, log_level="warning")


# ---------------------------------------------------------------- Billing control plane
def _billing_point_scale(db_path: Any) -> int:
    """读取服务端定价刻度（与 auth-api _display_billing_points 同源）。

    全部对外积分口径统一除以该刻度（10 单位 = 1 积分）；DB 原始单位值不动。
    """
    try:
        import sqlite3
        with sqlite3.connect(str(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT point_unit_scale FROM billing_pricing_rules WHERE rule_id = 1"
            ).fetchone()
            if row is not None and row["point_unit_scale"]:
                return max(1, int(row["point_unit_scale"]))
    except Exception:
        pass
    return 10


def _display_points(units: int, scale: int) -> int | float:
    value = int(units or 0) / scale
    return int(value) if value.is_integer() else value


_BATCH_TASK_ID_CACHE: dict[str, bool] = {}


def _batch_freezes_has_task_id(db_path: str) -> bool:
    """billing_batch_freezes.task_id 列是否存在。

    服务器 customer-auth 库可能落后于客户端 schema（该列由客户端迁移补加），
    缺列时查询退化为 '' 占位，避免整页 500。
    """
    key = str(db_path)
    if key not in _BATCH_TASK_ID_CACHE:
        try:
            import sqlite3
            with sqlite3.connect(key) as conn:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(billing_batch_freezes)")}
            _BATCH_TASK_ID_CACHE[key] = "task_id" in cols
        except Exception:
            _BATCH_TASK_ID_CACHE[key] = False
    return _BATCH_TASK_ID_CACHE[key]


@app.get("/api/billing")
def billing_records(limit: int = 200, x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(x_auth_token)
    import sqlite3
    db_path = load_config()["database_path"]
    scale = _billing_point_scale(db_path)
    limit = max(1, min(int(limit), 500))
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        wallets = conn.execute("SELECT a.account_id,a.username,a.email,a.workspace_id,COALESCE(w.points_balance,0) AS points_balance,COALESCE(w.locked_points,0) AS locked_points,COALESCE(w.manual_frozen_points,0) AS manual_frozen_points,w.updated_at,(SELECT COUNT(1) FROM billing_batch_items b JOIN billing_batch_freezes f ON f.freeze_id=b.freeze_id WHERE f.account_id=a.account_id AND b.feature_key='title' AND b.status='success') AS success_usage,(SELECT COALESCE(SUM(charged_points),0) FROM billing_batch_freezes f WHERE f.account_id=a.account_id AND f.status='settled') AS charged_points FROM auth_accounts a LEFT JOIN billing_wallets w ON w.account_id=a.account_id ORDER BY COALESCE(w.updated_at,a.updated_at) DESC LIMIT ?", (limit,)).fetchall()
    # 消费流水已拆分到 /api/billing/usage 独立游标分页；此处只返回钱包列表。
    wallet_rows = []
    for row in wallets:
        item = dict(row)
        for key in ("points_balance", "locked_points", "manual_frozen_points", "charged_points"):
            item[key] = _display_points(int(item.get(key) or 0), scale)
        wallet_rows.append(item)
    return {"ok": True, "point_unit_scale": scale, "wallets": wallet_rows}


def _usage_encode_cursor(created_at: str, usage_id: str) -> str:
    """把 (created_at, usage_id) 打包为不透明游标，避免暴露内部主键。"""
    raw = json.dumps([created_at, usage_id], ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _usage_decode_cursor(cursor: str) -> tuple[str, str] | None:
    """解析不透明游标；非法/为空返回 None（表示第一页）。"""
    if not cursor:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        if isinstance(data, list) and len(data) == 2:
            return str(data[0]), str(data[1])
    except Exception:
        pass
    return None


@app.get("/api/billing/usage")
def billing_usage_records(
    limit: int = 100,
    cursor: str = "",
    start: str = "",
    end: str = "",
    q: str = "",
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """用户消费流水：服务端游标分页 + 日期/搜索过滤。

    消费流水 = billing_ai_usage_events ∪ billing_batch_freezes，
    统一按 created_at DESC, usage_id DESC 排序（batch 的 usage_id = 'batch:'+freeze_id，全局唯一）。
    游标为 (created_at, usage_id)，键集翻页，避免 OFFSET 深翻页性能退化与重复。
    """
    _check_auth(x_auth_token)
    import sqlite3
    db_path = load_config()["database_path"]
    scale = _billing_point_scale(db_path)
    limit = max(1, min(int(limit), 200))
    cur = _usage_decode_cursor(cursor)
    q = (q or "").strip()
    like = "%" + q + "%"

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row

        # ---- billing_ai_usage_events ----
        ev_conds: list[str] = []
        ev_params: list[Any] = []
        if start:
            ev_conds.append("u.created_at >= ?")
            ev_params.append(start)
        if end:
            ev_conds.append("u.created_at <= ?")
            ev_params.append(end)
        if cur is not None:
            t, k = cur
            ev_conds.append("(u.created_at < ? OR (u.created_at = ? AND u.usage_id < ?))")
            ev_params.extend([t, t, k])
        if q:
            ev_conds.append("(COALESCE(a.username,'') LIKE ? OR u.account_id LIKE ? OR u.feature_key LIKE ?)")
            ev_params.extend([like, like, like])
        ev_where = "WHERE " + " AND ".join(ev_conds) if ev_conds else ""
        usage = conn.execute(
            f"SELECT u.usage_id,u.account_id,COALESCE(a.username,'') AS username,u.feature_key,u.reserved_points,"
            f"u.charged_points,u.refunded_points,u.provider,u.provider_task_id,u.model,u.status,u.error_message,"
            f"u.created_at,u.settled_at,COALESCE(w.points_balance,0) AS points_balance,"
            f"COALESCE(w.locked_points,0) AS locked_points,COALESCE(w.manual_frozen_points,0) AS manual_frozen_points,"
            f"u.metadata_json FROM billing_ai_usage_events u LEFT JOIN auth_accounts a ON a.account_id=u.account_id "
            f"LEFT JOIN billing_wallets w ON w.account_id=u.account_id {ev_where} "
            f"ORDER BY u.created_at DESC,u.usage_id DESC LIMIT ?",
            (*ev_params, limit + 1),
        ).fetchall()

        # ---- billing_batch_freezes（消费流水取 'batch:'+freeze_id 作为全局唯一 usage_id）----
        task_col = "f.task_id" if _batch_freezes_has_task_id(db_path) else "'' AS task_id"
        bf_conds: list[str] = []
        bf_params: list[Any] = []
        if start:
            bf_conds.append("f.created_at >= ?")
            bf_params.append(start)
        if end:
            bf_conds.append("f.created_at <= ?")
            bf_params.append(end)
        if cur is not None:
            t, k = cur
            bf_conds.append("(f.created_at < ? OR (f.created_at = ? AND ('batch:'||f.freeze_id) < ?))")
            bf_params.extend([t, t, k])
        if q:
            bf_conds.append("(COALESCE(a.username,'') LIKE ? OR f.account_id LIKE ? OR f.billing_profile LIKE ?)")
            bf_params.extend([like, like, like])
        bf_where = "WHERE " + " AND ".join(bf_conds) if bf_conds else ""
        batch = conn.execute(
            f"SELECT ('batch:'||f.freeze_id) AS usage_id,f.freeze_id,f.account_id,COALESCE(a.username,'') AS username,"
            f"f.billing_profile,{task_col},f.link_count,f.frozen_points,f.charged_points,f.refunded_points,f.status,"
            f"f.rule_version,f.created_at,f.settled_at,COALESCE(w.points_balance,0) AS points_balance,"
            f"COALESCE(w.locked_points,0) AS locked_points,COALESCE(w.manual_frozen_points,0) AS manual_frozen_points "
            f"FROM billing_batch_freezes f LEFT JOIN auth_accounts a ON a.account_id=f.account_id "
            f"LEFT JOIN billing_wallets w ON w.account_id=f.account_id {bf_where} "
            f"ORDER BY f.created_at DESC,('batch:'||f.freeze_id) DESC LIMIT ?",
            (*bf_params, limit + 1),
        ).fetchall()

    # 统一对外口径：单位 → 积分（与客户端 auth-api 展示规则一致），DB 原始值不动。
    items: list[dict[str, Any]] = []
    for row in usage:
        item = dict(row)
        for key in ("reserved_points", "charged_points", "refunded_points", "points_balance", "locked_points", "manual_frozen_points"):
            item[key] = _display_points(int(item.get(key) or 0), scale)
        item["feature_key"] = str(item.get("feature_key") or "")
        items.append(item)
    for row in batch:
        is_pod = str(row["billing_profile"] or "product_processing") == "pod_random_v1"
        raw_status = str(row["status"] or "")
        if raw_status == "settled":
            status = "succeeded"
        elif raw_status == "released":
            status = "failed"
        else:
            status = "frozen"
        link_count = int(row["link_count"] or 0)
        item = {
            "usage_id": str(row["usage_id"]),
            "freeze_id": str(row["freeze_id"]),
            "account_id": str(row["account_id"]),
            "username": str(row["username"]),
            "feature_key": "pod_customization.batch" if is_pod else "product_processing.batch",
            "reserved_points": _display_points(int(row["frozen_points"] or 0), scale),
            "charged_points": _display_points(int(row["charged_points"] or 0), scale),
            "refunded_points": _display_points(int(row["refunded_points"] or 0), scale),
            "provider": "POD 定制结算" if is_pod else "批量链接结算",
            "provider_task_id": str(row["task_id"] or ""),
            "model": (f"{link_count} 款创作" if is_pod else f"{link_count} 条链接"),
            "status": status,
            "error_message": "",
            "created_at": str(row["created_at"] or ""),
            "settled_at": str(row["settled_at"] or ""),
            "points_balance": _display_points(int(row["points_balance"] or 0), scale),
            "locked_points": _display_points(int(row["locked_points"] or 0), scale),
            "manual_frozen_points": _display_points(int(row["manual_frozen_points"] or 0), scale),
            "metadata_json": "",
            "rule_version": int(row["rule_version"] or 0),
        }
        items.append(item)

    items.sort(key=lambda it: (str(it.get("created_at") or ""), str(it.get("usage_id") or "")), reverse=True)
    has_more = len(items) > limit
    items = items[:limit]
    next_cursor = ""
    if has_more and items:
        last = items[-1]
        next_cursor = _usage_encode_cursor(str(last["created_at"]), str(last["usage_id"]))
    return {"ok": True, "point_unit_scale": scale, "usage": items, "next_cursor": next_cursor, "has_more": has_more}


@app.get("/api/billing/batch/open")
def billing_batch_open_freezes(
    account_id: str = "",
    task_id: str = "",
    limit: int = 200,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """列出仍处于冻结（未结算/未释放）的批次，供客服定位滞留积分。

    支持按账号或任务号过滤；返回冻结金额、到期时间与已冻结时长，
    与客户端消费流水「处理中」的记录一一对应。
    """
    _check_auth(x_auth_token)
    import sqlite3
    db_path = load_config()["database_path"]
    scale = _billing_point_scale(db_path)
    limit = max(1, min(int(limit), 500))
    has_task_id = _batch_freezes_has_task_id(db_path)
    task_col = "f.task_id" if has_task_id else "'' AS task_id"
    where = ["f.status = 'frozen'"]
    args: list[Any] = []
    if str(account_id or "").strip():
        where.append("f.account_id = ?")
        args.append(str(account_id).strip())
    if str(task_id or "").strip() and has_task_id:
        where.append("f.task_id = ?")
        args.append(str(task_id).strip())
    clause = " AND ".join(where)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT f.freeze_id, f.account_id, COALESCE(a.username, '') AS username,
                   f.workspace_id, {task_col}, f.billing_profile, f.link_count,
                   f.frozen_points, f.charged_points, f.refunded_points,
                   f.created_at, f.expires_at, f.settled_at
            FROM billing_batch_freezes f
            LEFT JOIN auth_accounts a ON a.account_id = f.account_id
            WHERE {clause}
            ORDER BY f.created_at DESC, f.freeze_id DESC
            LIMIT ?
            """,
            (*args, limit),
        ).fetchall()
    now = datetime.now(timezone.utc)
    items = []
    for row in rows:
        is_pod = str(row["billing_profile"] or "product_processing") == "pod_random_v1"
        created_at = str(row["created_at"] or "")
        expires_at = str(row["expires_at"] or "")
        age_days = ""
        if created_at:
            try:
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_days = f"{(now - created).total_seconds() / 86400:.1f}"
            except (ValueError, TypeError):
                age_days = ""
        items.append({
            "freeze_id": str(row["freeze_id"]),
            "account_id": str(row["account_id"]),
            "username": str(row["username"]),
            "workspace_id": str(row["workspace_id"]),
            "task_id": str(row["task_id"] or ""),
            "billing_profile": str(row["billing_profile"] or "product_processing"),
            "feature_key": "pod_customization.batch" if is_pod else "product_processing.batch",
            "link_count": int(row["link_count"] or 0),
            "frozen_points": _display_points(int(row["frozen_points"] or 0), scale),
            "charged_points": _display_points(int(row["charged_points"] or 0), scale),
            "refunded_points": _display_points(int(row["refunded_points"] or 0), scale),
            "created_at": created_at,
            "expires_at": expires_at,
            "age_days": age_days,
        })
    return {"ok": True, "point_unit_scale": scale, "count": len(items), "items": items}


@app.post("/api/billing/batch/{freeze_id}/release")
def billing_batch_release(freeze_id: str, payload: dict[str, Any], request: Request, x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """客服/管理员手工释放一个冻结批次（全额解锁）。

    仅当批次仍为 frozen 时才释放；已结算/已释放的批次原样返回。
    释放按冻结全额解锁并记账（admin_batch_release），账本可追溯。
    """
    admin = _check_auth(x_auth_token)
    reason = str(payload.get("reason") or "").strip()
    if not 3 <= len(reason) <= 240:
        raise HTTPException(status_code=400, detail="释放原因需为 3 到 240 个字符")
    sys.path.insert(0, "/opt/wh-workbench/MainPG/local-runtime")
    from wh_local.billing import _append_ledger
    from wh_local.db import transaction
    db_path = Path(load_config()["database_path"])
    scale = _billing_point_scale(db_path)
    freeze_id = str(freeze_id or "").strip()
    if not freeze_id:
        raise HTTPException(status_code=400, detail="freeze_id 必填")
    with transaction(db_path) as conn:
        freeze = conn.execute(
            "SELECT * FROM billing_batch_freezes WHERE freeze_id = ?", (freeze_id,)
        ).fetchone()
        if freeze is None:
            raise HTTPException(status_code=404, detail="批次冻结记录不存在")
        current = str(freeze["status"] or "")
        if current == "settled":
            return {"ok": True, "freeze_id": freeze_id, "status": "settled", "released_points": 0, "point_unit_scale": scale, "already_released": False}
        if current == "released":
            return {"ok": True, "freeze_id": freeze_id, "status": "released", "released_points": 0, "point_unit_scale": scale, "already_released": True}
        frozen_units = int(freeze["frozen_points"])
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute(
            "UPDATE billing_batch_freezes SET status = 'released', settled_at = ? WHERE freeze_id = ?",
            (now, freeze_id),
        )
        _append_ledger(
            conn,
            account_id=str(freeze["account_id"]),
            workspace_id=str(freeze["workspace_id"] or "default"),
            direction="unlock",
            points_delta=frozen_units,
            source_type="admin_batch_release",
            source_id=freeze_id,
            idempotency_key=f"admin-batch-release:{freeze_id}",
            metadata={
                "link_count": int(freeze["link_count"]),
                "reason": reason,
                "admin": str(admin.get("username") or ""),
                "client_ip": _client_ip(request),
            },
        )
        conn.execute(
            """
            UPDATE billing_wallets
            SET locked_points = locked_points - ?,
                version = version + 1,
                updated_at = ?
            WHERE account_id = ?
            """,
            (frozen_units, now, str(freeze["account_id"])),
        )
    from wh_local import cache as _cache
    _cache.invalidate_wallet(str(freeze["account_id"]))
    _cache.invalidate_admin_summary()
    return {
        "ok": True,
        "freeze_id": freeze_id,
        "status": "released",
        "released_points": _display_points(frozen_units, scale),
        "point_unit_scale": scale,
        "already_released": False,
    }


@app.post("/api/billing/{account_id}/task-release")
def release_task_frozen_points(account_id: str, payload: dict[str, Any], request: Request, x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """按数量释放任务冻结（locked_points）中的积分。

    与整批释放不同：管理员输入数值 X，从该账户当前任务冻结总额（locked_points）
    中释放 X。为保证 billing_batch_freezes 各 frozen 批次合计与钱包 locked_points
    始终一致，此接口按冻结时间自旧到新抵扣各冻结批次：能整批覆盖则标记 released，
    最后一个批次不足整批时仅下调其 frozen_points（该批次仍为 frozen）。
    释放只是解锁（冻结时不占用 points_balance），因此不会回补余额。
    """
    admin = _check_auth(x_auth_token)
    try:
        release_points = int(payload.get("release_points"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="释放积分必须为整数") from exc
    reason = str(payload.get("reason") or "").strip()
    if not 1 <= release_points <= 1_000_000:
        raise HTTPException(status_code=400, detail="释放积分范围为 1 到 1,000,000")
    if not 3 <= len(reason) <= 240:
        raise HTTPException(status_code=400, detail="释放原因需为 3 到 240 个字符")
    sys.path.insert(0, "/opt/wh-workbench/MainPG/local-runtime")
    from wh_local.billing import _append_ledger
    from wh_local.db import transaction
    db_path = Path(load_config()["database_path"])
    scale = _billing_point_scale(db_path)
    release_units = release_points * scale
    with transaction(db_path) as conn:
        account = conn.execute(
            "SELECT workspace_id FROM auth_accounts WHERE account_id = ?", (account_id,)
        ).fetchone()
        if account is None:
            raise HTTPException(status_code=404, detail="账户不存在")
        wallet = conn.execute(
            "SELECT locked_points FROM billing_wallets WHERE account_id = ?", (account_id,)
        ).fetchone()
        if wallet is None:
            raise HTTPException(status_code=409, detail="该账户尚未创建积分钱包")
        locked_units = int(wallet["locked_points"])
        if release_units > locked_units:
            raise HTTPException(
                status_code=409,
                detail=f"释放数量不能超过当前任务冻结的 {locked_units // scale} 积分",
            )
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        batches = conn.execute(
            """
            SELECT freeze_id, frozen_points
            FROM billing_batch_freezes
            WHERE account_id = ? AND status = 'frozen'
            ORDER BY created_at ASC, freeze_id ASC
            """,
            (account_id,),
        ).fetchall()
        remaining = release_units
        affected: list[str] = []
        for batch in batches:
            if remaining <= 0:
                break
            frozen_units = int(batch["frozen_points"] or 0)
            if remaining >= frozen_units:
                conn.execute(
                    "UPDATE billing_batch_freezes SET status = 'released', settled_at = ? WHERE freeze_id = ?",
                    (now, str(batch["freeze_id"])),
                )
                remaining -= frozen_units
                affected.append(str(batch["freeze_id"]))
            else:
                conn.execute(
                    "UPDATE billing_batch_freezes SET frozen_points = ? WHERE freeze_id = ?",
                    (frozen_units - remaining, str(batch["freeze_id"])),
                )
                affected.append(str(batch["freeze_id"]))
                remaining = 0
        new_locked = locked_units - release_units
        conn.execute(
            """
            UPDATE billing_wallets
            SET locked_points = ?, version = version + 1, updated_at = ?
            WHERE account_id = ?
            """,
            (new_locked, now, account_id),
        )
        _append_ledger(
            conn,
            account_id=account_id,
            workspace_id=str(account["workspace_id"] or "default"),
            direction="unlock",
            points_delta=release_units,
            source_type="admin_task_release",
            source_id=f"admin:{admin.get('username') or 'unknown'}",
            idempotency_key=f"admin-task-release:{secrets.token_urlsafe(18)}",
            metadata={
                "reason": reason,
                "admin": str(admin.get("username") or ""),
                "client_ip": _client_ip(request),
                "locked_points": new_locked,
                "affected_batches": affected,
            },
        )
    from wh_local import cache as _cache
    _cache.invalidate_wallet(account_id)
    _cache.invalidate_admin_summary()
    return {
        "ok": True,
        "account_id": account_id,
        "released_points": release_points,
        "locked_points": new_locked // scale,
        "affected_batches": affected,
        "point_unit_scale": scale,
    }


@app.get("/api/billing/summary")
def billing_summary(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """全平台积分总览：所有用户合计积分、已消耗、充值订单与用户钱包排行。

    积分字段统一按 point_unit_scale 折算为积分（与 auth-api 客户端口径一致），
    DB 原始单位值不动。消耗口径为批量链接结算（billing_batch_freezes settled）。
    """
    _check_auth(x_auth_token)
    # 汇总指标为展示型数据，缓存 30 秒；调账/冻结/入账写路径会主动失效。
    sys.path.insert(0, str(_AUTH_RUNTIME))
    from wh_local import cache as _cache  # noqa: PLC0415
    cached = _cache.cache_get("admin:billing:summary")
    if cached is not None:
        return cached
    import sqlite3
    db_path = load_config()["database_path"]
    scale = _billing_point_scale(db_path)
    # settled_at 存储为 UTC ISO；北京时间今日 00:00 对应的 UTC 起始时刻。
    beijing_now = datetime.now(timezone.utc) + timedelta(hours=8)
    today_utc_start = (
        beijing_now.replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(hours=8)
    )
    today_start_iso = today_utc_start.isoformat(timespec="seconds")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        wallet_totals = conn.execute(
            """
            SELECT COALESCE(SUM(points_balance), 0)       AS total_balance,
                   COALESCE(SUM(locked_points), 0)        AS total_locked,
                   COALESCE(SUM(manual_frozen_points), 0) AS total_manual_frozen,
                   COUNT(*)                               AS wallet_count,
                   COUNT(CASE WHEN points_balance > 0 THEN 1 END) AS funded_count
            FROM billing_wallets
            """
        ).fetchone()
        consumed = conn.execute(
            """
            SELECT COALESCE(SUM(charged_points), 0) AS charged_points,
                   COUNT(DISTINCT account_id)       AS charged_accounts,
                   COALESCE(SUM(CASE WHEN settled_at >= ? THEN charged_points END), 0) AS today_charged
            FROM billing_batch_freezes
            WHERE status = 'settled'
            """,
            (today_start_iso,),
        ).fetchone()
        topup = conn.execute(
            """
            SELECT COALESCE(SUM(amount_cents), 0) AS amount_cents,
                   COUNT(*)                       AS orders,
                   COUNT(DISTINCT account_id)     AS accounts
            FROM billing_payment_orders
            WHERE status = 'paid'
            """
        ).fetchone()
        users = conn.execute(
            """
            SELECT COUNT(*)                                        AS total_users,
                   COUNT(CASE WHEN account_status = 'active' THEN 1 END) AS active_users,
                   COUNT(CASE WHEN login_status = 'online' THEN 1 END)   AS online_users
            FROM auth_accounts
            """
        ).fetchone()
        ranking = conn.execute(
            """
            SELECT a.account_id, a.username, a.email,
                   COALESCE(w.points_balance, 0) AS points_balance,
                   COALESCE(w.locked_points, 0) AS locked_points,
                   COALESCE(w.manual_frozen_points, 0) AS manual_frozen_points,
                   (SELECT COALESCE(SUM(f.charged_points), 0) FROM billing_batch_freezes f
                    WHERE f.account_id = a.account_id AND f.status = 'settled') AS charged_points
            FROM auth_accounts a
            LEFT JOIN billing_wallets w ON w.account_id = a.account_id
            ORDER BY COALESCE(w.points_balance, 0) DESC
            LIMIT 20
            """
        ).fetchall()
    total_balance = int(wallet_totals["total_balance"])
    total_locked = int(wallet_totals["total_locked"])
    total_manual_frozen = int(wallet_totals["total_manual_frozen"])

    def dpoints(units: int) -> int | float:
        return _display_points(units, scale)

    ranking_rows: list[dict[str, Any]] = []
    for row in ranking:
        balance = int(row["points_balance"] or 0)
        locked = int(row["locked_points"] or 0)
        manual = int(row["manual_frozen_points"] or 0)
        item = dict(row)
        item["points_balance"] = dpoints(balance)
        item["locked_points"] = dpoints(locked)
        item["manual_frozen_points"] = dpoints(manual)
        item["charged_points"] = dpoints(int(row["charged_points"] or 0))
        item["available_points"] = dpoints(balance - locked - manual)
        ranking_rows.append(item)
    return {
        "ok": True,
        "point_unit_scale": scale,
        "generated_at": _beijing_time(datetime.now(timezone.utc).isoformat(timespec="seconds")),
        "wallet": {
            "total_points": dpoints(total_balance),
            "available_points": dpoints(total_balance - total_locked - total_manual_frozen),
            "task_locked_points": dpoints(total_locked),
            "manual_frozen_points": dpoints(total_manual_frozen),
            "wallet_count": int(wallet_totals["wallet_count"]),
            "funded_accounts": int(wallet_totals["funded_count"]),
        },
        "consumed": {
            "charged_points": dpoints(int(consumed["charged_points"] or 0)),
            "today_charged_points": dpoints(int(consumed["today_charged"] or 0)),
            "charged_accounts": int(consumed["charged_accounts"] or 0),
        },
        "topup": {
            "amount_cny": round(int(topup["amount_cents"] or 0) / 100, 2),
            "orders": int(topup["orders"] or 0),
            "accounts": int(topup["accounts"] or 0),
        },
        "users": {
            "total": int(users["total_users"] or 0),
            "active": int(users["active_users"] or 0),
            "online": int(users["online_users"] or 0),
        },
        "ranking": ranking_rows,
    }
    _cache.cache_set("admin:billing:summary", payload, ttl=30)
    return payload

@app.get("/api/billing/multipliers")
def billing_multipliers(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """价格倍率页数据：当前倍率/单条价值 + 审计历史 + 定价基准预览。"""
    _check_auth(x_auth_token)
    sys.path.insert(0, "/opt/wh-workbench/MainPG/local-runtime")
    from wh_local.billing import active_multipliers, multiplier_changelog, pricing_items
    from wh_local.pod_billing import pod_pricing_items
    db_path = Path(load_config()["database_path"])
    return {
        "ok": True,
        "multipliers": active_multipliers(db_path),
        "changelog": multiplier_changelog(db_path, limit=200),
        "pricing": {
            "ai": pricing_items(db_path),
            "pod": pod_pricing_items(db_path, require_configured=False),
        },
        "note": (
            "扣费按「冻结时快照」结算：调整单条价值后仅对新冻结的任务生效，"
            "已冻结任务仍按冻结时的倍率结算，不受中途调价影响。"
        ),
    }


@app.post("/api/billing/multipliers")
def update_billing_multipliers(
    request: Request,
    payload: dict[str, Any],
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """调整 AI / POD 单条价值（整数积分）或倍率（百分比），写审计日志。"""
    admin = _check_auth(x_auth_token)
    sys.path.insert(0, "/opt/wh-workbench/MainPG/local-runtime")
    from wh_local.billing import multiplier_changelog, update_multipliers
    db_path = Path(load_config()["database_path"])
    updated = update_multipliers(
        db_path,
        ai_points_per_unit=payload.get("ai_points_per_unit"),
        pod_points_per_unit=payload.get("pod_points_per_unit"),
        ai_multiplier_percent=payload.get("ai_multiplier_percent"),
        pod_multiplier_percent=payload.get("pod_multiplier_percent"),
        reset_ai=bool(payload.get("reset_ai") or False),
        reset_pod=bool(payload.get("reset_pod") or False),
        updated_by=str(admin.get("username") or admin.get("account_id") or "admin"),
        change_reason=str(payload.get("change_reason") or ""),
    )
    return {
        "ok": True,
        "multipliers": updated,
        "changelog": multiplier_changelog(db_path, limit=200),
    }

@app.post("/api/billing/{account_id}/adjust")
def adjust_billing_points(account_id: str, payload: dict[str, Any], request: Request, x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    admin = _check_auth(x_auth_token)
    try:
        points_delta = int(payload.get("points_delta"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="??????????") from exc
    reason = str(payload.get("reason") or "").strip()
    if not -1_000_000 <= points_delta <= 1_000_000 or points_delta == 0:
        raise HTTPException(status_code=400, detail="??????? -1,000,000 ? 1,000,000????? 0")
    if not 3 <= len(reason) <= 240:
        raise HTTPException(status_code=400, detail="??? 3?240 ??????")
    sys.path.insert(0, "/opt/wh-workbench/MainPG/local-runtime")
    from wh_local.billing import _append_ledger
    from wh_local.db import transaction
    db_path = Path(load_config()["database_path"])
    scale = _billing_point_scale(db_path)
    units_delta = points_delta * scale
    with transaction(db_path) as conn:
        row = conn.execute("SELECT workspace_id FROM auth_accounts WHERE account_id = ?", (account_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="?????")
        workspace_id = str(row["workspace_id"] or "default")
        wallet = conn.execute("SELECT points_balance, locked_points, manual_frozen_points FROM billing_wallets WHERE account_id = ?", (account_id,)).fetchone()
        balance = int(wallet["points_balance"]) if wallet else 0
        unavailable = (int(wallet["locked_points"]) + int(wallet["manual_frozen_points"])) if wallet else 0
        if balance + units_delta < unavailable:
            raise HTTPException(status_code=409, detail="????????? 0")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if wallet is None:
            conn.execute("INSERT INTO billing_wallets(account_id,workspace_id,points_balance,locked_points,version,created_at,updated_at) VALUES (?, ?, 0, 0, 0, ?, ?)", (account_id, workspace_id, now, now))
        conn.execute("UPDATE billing_wallets SET points_balance = points_balance + ?, version = version + 1, updated_at = ? WHERE account_id = ?", (units_delta, now, account_id))
        _append_ledger(conn, account_id=account_id, workspace_id=workspace_id, direction="credit" if units_delta > 0 else "debit", points_delta=abs(units_delta), source_type="admin_adjustment", source_id=f"admin:{admin.get('username') or 'unknown'}", idempotency_key=f"admin-adjust:{secrets.token_urlsafe(18)}", metadata={"reason": reason, "admin": str(admin.get("username") or ""), "client_ip": _client_ip(request)})
    from wh_local import cache as _cache
    _cache.invalidate_wallet(account_id)
    _cache.invalidate_admin_summary()
    return {"ok": True, "account_id": account_id, "points_delta": points_delta, "point_unit_scale": scale}


@app.post("/api/billing/{account_id}/freeze")
def set_manual_billing_freeze(account_id: str, payload: dict[str, Any], request: Request, x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Set the administrator-held portion of a wallet; task reservations stay separate."""
    admin = _check_auth(x_auth_token)
    try:
        target_points = int(payload.get("frozen_points"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="冻结积分必须为整数") from exc
    reason = str(payload.get("reason") or "").strip()
    if not 0 <= target_points <= 1_000_000:
        raise HTTPException(status_code=400, detail="冻结积分范围为 0 到 1,000,000")
    if not 3 <= len(reason) <= 240:
        raise HTTPException(status_code=400, detail="冻结原因需为 3 到 240 个字符")
    sys.path.insert(0, "/opt/wh-workbench/MainPG/local-runtime")
    from wh_local.billing import _append_ledger
    from wh_local.db import transaction
    db_path = Path(load_config()["database_path"])
    scale = _billing_point_scale(db_path)
    target_units = target_points * scale
    with transaction(db_path) as conn:
        account = conn.execute("SELECT workspace_id FROM auth_accounts WHERE account_id = ?", (account_id,)).fetchone()
        if account is None:
            raise HTTPException(status_code=404, detail="账户不存在")
        wallet = conn.execute("SELECT points_balance, locked_points, manual_frozen_points FROM billing_wallets WHERE account_id = ?", (account_id,)).fetchone()
        if wallet is None:
            raise HTTPException(status_code=409, detail="该账户尚未创建积分钱包")
        balance = int(wallet["points_balance"])
        task_locked = int(wallet["locked_points"])
        current = int(wallet["manual_frozen_points"])
        # 余额、锁定与管理员冻结均以内部单位存储；对外输入的是积分，换算为单位后比较/写库/记账。
        if target_units > balance - task_locked:
            raise HTTPException(status_code=409, detail="管理员冻结积分不能超过当前未被任务占用的余额")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn.execute("UPDATE billing_wallets SET manual_frozen_points = ?, version = version + 1, updated_at = ? WHERE account_id = ?", (target_units, now, account_id))
        delta = target_units - current
        if delta:
            _append_ledger(conn, account_id=account_id, workspace_id=str(account["workspace_id"] or "default"), direction="lock" if delta > 0 else "unlock", points_delta=abs(delta), source_type="admin_manual_freeze", source_id=f"admin:{admin.get('username') or 'unknown'}", idempotency_key=f"admin-freeze:{secrets.token_urlsafe(18)}", metadata={"reason": reason, "admin": str(admin.get("username") or ""), "client_ip": _client_ip(request), "manual_frozen_points": target_units})
    from wh_local import cache as _cache
    _cache.invalidate_wallet(account_id)
    _cache.invalidate_admin_summary()
    return {"ok": True, "account_id": account_id, "manual_frozen_points": target_points, "point_unit_scale": scale}


@app.post("/api/billing/{account_id}/release")
def release_manual_billing_freeze(account_id: str, payload: dict[str, Any], request: Request, x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """按数量释放管理员持有的人工冻结积分（任务预留 separate）。

    与 set_manual_billing_freeze 相反：输入要释放的积分数量，从该账户的
    manual_frozen_points 中扣减并回补到可用余额，账本记 unlock。
    """
    admin = _check_auth(x_auth_token)
    try:
        release_points = int(payload.get("release_points"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="释放积分必须为整数") from exc
    reason = str(payload.get("reason") or "").strip()
    if not 1 <= release_points <= 1_000_000:
        raise HTTPException(status_code=400, detail="释放积分范围为 1 到 1,000,000")
    if not 3 <= len(reason) <= 240:
        raise HTTPException(status_code=400, detail="释放原因需为 3 到 240 个字符")
    sys.path.insert(0, "/opt/wh-workbench/MainPG/local-runtime")
    from wh_local.billing import _append_ledger
    from wh_local.db import transaction
    db_path = Path(load_config()["database_path"])
    scale = _billing_point_scale(db_path)
    release_units = release_points * scale
    with transaction(db_path) as conn:
        account = conn.execute("SELECT workspace_id FROM auth_accounts WHERE account_id = ?", (account_id,)).fetchone()
        if account is None:
            raise HTTPException(status_code=404, detail="账户不存在")
        wallet = conn.execute("SELECT points_balance, locked_points, manual_frozen_points FROM billing_wallets WHERE account_id = ?", (account_id,)).fetchone()
        if wallet is None:
            raise HTTPException(status_code=409, detail="该账户尚未创建积分钱包")
        current = int(wallet["manual_frozen_points"])
        if release_units > current:
            raise HTTPException(status_code=409, detail=f"释放数量不能超过当前人工冻结的 {current // scale} 积分")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new_units = current - release_units
        conn.execute("UPDATE billing_wallets SET manual_frozen_points = ?, version = version + 1, updated_at = ? WHERE account_id = ?", (new_units, now, account_id))
        _append_ledger(conn, account_id=account_id, workspace_id=str(account["workspace_id"] or "default"), direction="unlock", points_delta=release_units, source_type="admin_manual_freeze", source_id=f"admin:{admin.get('username') or 'unknown'}", idempotency_key=f"admin-release:{secrets.token_urlsafe(18)}", metadata={"reason": reason, "admin": str(admin.get("username") or ""), "client_ip": _client_ip(request), "manual_frozen_points": new_units})
    from wh_local import cache as _cache
    _cache.invalidate_wallet(account_id)
    _cache.invalidate_admin_summary()
    return {"ok": True, "account_id": account_id, "released_points": release_points, "manual_frozen_points": new_units // scale, "point_unit_scale": scale}


# ---------------------------------------------------------------- 产品处理失败日志管理
def _to_sqlite_datetime(value: str) -> str:
    """把前端传入的 ISO 时间统一转成与 DB 一致的 UTC 'YYYY-MM-DD HH:MM:SS'。"""
    clean = str(value or "").strip()
    if not clean:
        return ""
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError:
        return clean
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _failure_log_where(params: dict[str, str]) -> tuple[str, list[Any]]:
    where: list[str] = []
    args: list[Any] = []
    if str(params.get("account") or "").strip():
        like = f"%{str(params['account']).strip()}%"
        where.append("(account_id LIKE ? OR username LIKE ?)")
        args += [like, like]
    if str(params.get("app_version") or "").strip():
        where.append("app_version LIKE ?")
        args.append(f"%{str(params['app_version']).strip()}%")
    if str(params.get("task_status") or "").strip():
        where.append("task_status = ?")
        args.append(str(params["task_status"]).strip())
    if str(params.get("target_site") or "").strip():
        where.append("target_site = ?")
        args.append(str(params["target_site"]).strip())
    for key in ("start", "end"):
        converted = _to_sqlite_datetime(str(params.get(key) or ""))
        if converted:
            where.append("created_at >= ?" if key == "start" else "created_at <= ?")
            args.append(converted)
    return (f"WHERE {' AND '.join(where)}" if where else ""), args


def _decode_failure_log(row: Any) -> dict[str, Any]:
    record = dict(row)
    try:
        record["items"] = json.loads(record.pop("items_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        record["items"] = []
    try:
        record["processing_scope"] = json.loads(record.get("processing_scope") or "[]")
    except (json.JSONDecodeError, TypeError):
        record["processing_scope"] = []
    return record


@app.get("/api/failure-logs/summary")
def failure_logs_summary(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """产品处理失败诊断统计：上报次数、失败/待确认商品数、错误类型分布。"""
    _check_auth(x_auth_token)
    import sqlite3
    db_path = load_config()["database_path"]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        totals = conn.execute(
            "SELECT COUNT(*) AS reports,"
            " COALESCE(SUM(failed_count), 0) AS failed_items,"
            " COALESCE(SUM(attention_required_count), 0) AS attention_items,"
            " COALESCE(SUM(total_count), 0) AS total_products,"
            " COALESCE(SUM(auto_repull_rounds), 0) AS repull_rounds"
            " FROM product_processing_failure_logs"
        ).fetchone()
        by_version = conn.execute(
            "SELECT app_version, COUNT(*) AS reports, COALESCE(SUM(failed_count), 0) AS failed_items"
            " FROM product_processing_failure_logs GROUP BY app_version ORDER BY reports DESC, app_version DESC"
        ).fetchall()
        by_task_status = conn.execute(
            "SELECT task_status, COUNT(*) AS reports FROM product_processing_failure_logs"
            " GROUP BY task_status ORDER BY reports DESC"
        ).fetchall()
        recent = conn.execute(
            "SELECT items_json FROM product_processing_failure_logs ORDER BY id DESC LIMIT 500"
        ).fetchall()
    error_types: dict[str, int] = {}
    failure_classes: dict[str, int] = {}
    for row in recent:
        try:
            parsed_items = json.loads(row["items_json"] or "[]")
        except json.JSONDecodeError:
            parsed_items = []
        for item in parsed_items:
            error_type = str(item.get("error_type") or "").strip() or "未知"
            error_types[error_type] = error_types.get(error_type, 0) + 1
            failure_class = str(item.get("failure_class") or "").strip() or "未知"
            failure_classes[failure_class] = failure_classes.get(failure_class, 0) + 1
    return {
        "ok": True,
        "total_reports": int(totals["reports"] or 0),
        "total_failed_items": int(totals["failed_items"] or 0),
        "total_attention_items": int(totals["attention_items"] or 0),
        "total_products": int(totals["total_products"] or 0),
        "repull_rounds": int(totals["repull_rounds"] or 0),
        "by_version": [dict(row) for row in by_version],
        "by_task_status": [dict(row) for row in by_task_status],
        "error_type_distribution": sorted(error_types.items(), key=lambda kv: -kv[1]),
        "failure_class_distribution": sorted(failure_classes.items(), key=lambda kv: -kv[1]),
    }


@app.get("/api/failure-logs")
def failure_logs(
    limit: int = 100,
    offset: int = 0,
    account: str = "",
    app_version: str = "",
    task_status: str = "",
    target_site: str = "",
    start: str = "",
    end: str = "",
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """产品处理失败诊断日志列表（服务端筛选 + 分页）。"""
    _check_auth(x_auth_token)
    import sqlite3
    db_path = load_config()["database_path"]
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    clause, args = _failure_log_where({
        "account": account, "app_version": app_version, "task_status": task_status,
        "target_site": target_site, "start": start, "end": end,
    })
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(
            f"SELECT COUNT(*) FROM product_processing_failure_logs {clause}", args
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM product_processing_failure_logs {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
    return {
        "ok": True,
        "total": int(total),
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(rows)) < total,
        "logs": [_decode_failure_log(row) for row in rows],
    }


@app.get("/api/failure-logs/export")
def failure_logs_export(
    account: str = "",
    app_version: str = "",
    task_status: str = "",
    target_site: str = "",
    start: str = "",
    end: str = "",
    x_auth_token: str | None = Header(default=None),
) -> Response:
    """按当前筛选导出失败诊断日志 Excel（失败商品明细逐行展开）。"""
    _check_auth(x_auth_token)
    import sqlite3
    db_path = load_config()["database_path"]
    clause, args = _failure_log_where({
        "account": account, "app_version": app_version, "task_status": task_status,
        "target_site": target_site, "start": start, "end": end,
    })
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM product_processing_failure_logs {clause} ORDER BY id DESC", args
        ).fetchall()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "失败日志"
    headers = [
        "上报时间（北京时间）", "账号", "账号ID", "应用版本", "任务ID", "任务状态",
        "商品总数", "成功", "失败", "待确认", "跳过", "自动补跑轮次", "补跑说明",
        "站点", "语言", "SKC", "SPU", "商品标题", "商品状态", "失败原因",
        "错误类型", "失败类别", "操作提示", "排错细节", "AI备注",
        "提供方状态", "尝试次数", "阶段耗时(ms)", "被拒原图",
    ]
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="C0504D")
    for column in range(1, len(headers) + 1):
        cell = sheet.cell(row=1, column=column)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in rows:
        record = _decode_failure_log(row)
        base = [
            _beijing_time(record.get("created_at", "")),
            record.get("username", ""),
            record.get("account_id", ""),
            record.get("app_version", ""),
            record.get("task_id", ""),
            record.get("task_status", ""),
            record.get("total_count", ""),
            record.get("success_count", ""),
            record.get("failed_count", ""),
            record.get("attention_required_count", ""),
            record.get("skipped_count", ""),
            record.get("auto_repull_rounds", ""),
            record.get("auto_repull_message", ""),
            record.get("target_site", ""),
            record.get("target_language", ""),
        ]
        items = record.get("items") or []
        if not items:
            sheet.append([*base, "", "", "", "", "", "", "", "", "", "", "", "", ""])
        for item in items:
            provider_status = " | ".join(
                f"{key}={value}"
                for key, value in (item.get("provider_status_classes") or {}).items()
            )
            provider_attempts = " | ".join(
                f"{key}={value}"
                for key, value in (item.get("provider_attempts") or {}).items()
            )
            stage_timings = " | ".join(
                f"{key}={value}ms"
                for key, value in (item.get("stage_timings_ms") or {}).items()
            )
            rejected = len(item.get("rejected_image_paths") or [])
            sheet.append([
                *base,
                item.get("skc", ""),
                item.get("spu", ""),
                item.get("title", ""),
                item.get("status", ""),
                item.get("reason", ""),
                item.get("error_type", ""),
                item.get("failure_class", ""),
                item.get("operator_hint", ""),
                item.get("debug_hint", ""),
                " | ".join(str(note) for note in (item.get("ai_notes") or [])),
                provider_status,
                provider_attempts,
                stage_timings,
                rejected if rejected else "",
            ])
    for column in range(1, len(headers) + 1):
        values = [headers[column - 1]] + [
            sheet.cell(row=r, column=column).value or "" for r in range(2, sheet.max_row + 1)
        ]
        width = max(len(str(value)) for value in values)
        sheet.column_dimensions[get_column_letter(column)].width = min(max(width * 1.6 + 4, 12), 60)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{sheet.max_row}"

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"失败日志_{stamp}.xlsx"
    from urllib.parse import quote
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="failure_logs_{stamp}.xlsx"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


def _log_upload_where(params: dict[str, str]) -> tuple[str, list[Any]]:
    """启动器日志上传筛选：账号 / 应用版本 / 平台 / 时间范围。"""
    where: list[str] = []
    args: list[Any] = []
    if str(params.get("account") or "").strip():
        like = f"%{str(params['account']).strip()}%"
        where.append("(account_id LIKE ? OR username LIKE ?)")
        args += [like, like]
    if str(params.get("app_version") or "").strip():
        where.append("app_version LIKE ?")
        args.append(f"%{str(params['app_version']).strip()}%")
    if str(params.get("platform") or "").strip():
        where.append("platform LIKE ?")
        args.append(f"%{str(params['platform']).strip()}%")
    for key in ("start", "end"):
        converted = _to_sqlite_datetime(str(params.get(key) or ""))
        if converted:
            where.append("created_at >= ?" if key == "start" else "created_at <= ?")
            args.append(converted)
    return (f"WHERE {' AND '.join(where)}" if where else ""), args


def _decode_log_upload(row: Any, *, with_content: bool = False) -> dict[str, Any]:
    """解码启动器日志上传记录；列表接口剥离 base64，详情接口解码为纯文本。"""
    record = dict(row)
    if not with_content:
        record.pop("content_b64", None)
    else:
        content_b64 = record.pop("content_b64", "") or ""
        try:
            record["content"] = base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except (ValueError, binascii.Error):
            record["content"] = ""
    return record


@app.get("/api/log-uploads/summary")
def log_uploads_summary(x_auth_token: str | None = Header(default=None)) -> dict[str, Any]:
    """启动器日志上传统计：上报次数、总大小、按版本/平台/账号分布。"""
    _check_auth(x_auth_token)
    import sqlite3
    db_path = load_config()["database_path"]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        totals = conn.execute(
            "SELECT COUNT(*) AS reports, COALESCE(SUM(log_size), 0) AS total_size"
            " FROM launcher_log_uploads"
        ).fetchone()
        by_version = conn.execute(
            "SELECT app_version, COUNT(*) AS reports, COALESCE(SUM(log_size), 0) AS total_size"
            " FROM launcher_log_uploads GROUP BY app_version ORDER BY reports DESC, app_version DESC"
        ).fetchall()
        by_platform = conn.execute(
            "SELECT platform, COUNT(*) AS reports FROM launcher_log_uploads"
            " GROUP BY platform ORDER BY reports DESC"
        ).fetchall()
        by_user = conn.execute(
            "SELECT username, COUNT(*) AS reports FROM launcher_log_uploads"
            " GROUP BY username ORDER BY reports DESC LIMIT 20"
        ).fetchall()
    return {
        "ok": True,
        "total_reports": int(totals["reports"] or 0),
        "total_size": int(totals["total_size"] or 0),
        "by_version": [dict(row) for row in by_version],
        "by_platform": [dict(row) for row in by_platform],
        "by_user": [dict(row) for row in by_user],
    }


@app.get("/api/log-uploads")
def log_uploads(
    limit: int = 100,
    offset: int = 0,
    account: str = "",
    app_version: str = "",
    platform: str = "",
    start: str = "",
    end: str = "",
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """启动器日志上传列表（服务端筛选 + 分页，列表不返回日志内容）。"""
    _check_auth(x_auth_token)
    import sqlite3
    db_path = load_config()["database_path"]
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    clause, args = _log_upload_where({
        "account": account, "app_version": app_version, "platform": platform,
        "start": start, "end": end,
    })
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(
            f"SELECT COUNT(*) FROM launcher_log_uploads {clause}", args
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM launcher_log_uploads {clause} ORDER BY created_at DESC, upload_id DESC"
            f" LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
    return {
        "ok": True,
        "total": int(total),
        "offset": offset,
        "limit": limit,
        "has_more": (offset + len(rows)) < total,
        "logs": [_decode_log_upload(row) for row in rows],
    }


@app.get("/api/log-uploads/export")
def log_uploads_export(
    account: str = "",
    app_version: str = "",
    platform: str = "",
    start: str = "",
    end: str = "",
    x_auth_token: str | None = Header(default=None),
) -> Response:
    """按当前筛选导出启动器日志上传记录 Excel。"""
    _check_auth(x_auth_token)
    import sqlite3
    db_path = load_config()["database_path"]
    clause, args = _log_upload_where({
        "account": account, "app_version": app_version, "platform": platform,
        "start": start, "end": end,
    })
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT * FROM launcher_log_uploads {clause} ORDER BY created_at DESC, upload_id DESC", args
        ).fetchall()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "日志上传"
    headers = [
        "上传时间（北京时间）", "账号", "账号ID", "工作空间", "应用版本", "平台",
        "日志文件名", "日志大小(B)", "上传人IP",
    ]
    sheet.append(headers)
    header_fill = PatternFill("solid", fgColor="4472C4")
    for column in range(1, len(headers) + 1):
        cell = sheet.cell(row=1, column=column)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in rows:
        record = dict(row)
        sheet.append([
            _beijing_time(record.get("created_at", "")),
            record.get("username", ""),
            record.get("account_id", ""),
            record.get("workspace_id", ""),
            record.get("app_version", ""),
            record.get("platform", ""),
            record.get("log_name", ""),
            record.get("log_size", ""),
            record.get("client_ip", ""),
        ])
    for column in range(1, len(headers) + 1):
        values = [headers[column - 1]] + [
            sheet.cell(row=r, column=column).value or "" for r in range(2, sheet.max_row + 1)
        ]
        width = max(len(str(value)) for value in values)
        sheet.column_dimensions[get_column_letter(column)].width = min(max(width * 1.6 + 4, 12), 60)
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{sheet.max_row}"

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"日志上传_{stamp}.xlsx"
    from urllib.parse import quote
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="log_uploads_{stamp}.xlsx"; '
                f"filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@app.get("/api/log-uploads/{upload_id}")
def log_upload_detail(
    upload_id: str,
    x_auth_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """启动器日志上传详情：返回解码后的日志正文。"""
    _check_auth(x_auth_token)
    import sqlite3
    db_path = load_config()["database_path"]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM launcher_log_uploads WHERE upload_id = ?", (upload_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="log upload not found")
    return {"ok": True, "log": _decode_log_upload(row, with_content=True)}

