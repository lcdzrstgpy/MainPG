"""独立公告发布后台（服务器端）。

首次访问自动进入「创建管理员」模式；创建后仅管理员可登录维护公告。
客户端（本地工作台）通过公开接口拉取已上线的公告，无需登录。

部署：
    uvicorn app:app --host 127.0.0.1 --port 8013
配置：
    ANNOUNCE_DB_PATH  SQLite 路径（默认本目录 announcements.sqlite3）
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

CN_TZ = timezone(timedelta(hours=8))
TOKEN_TTL_SECONDS = 12 * 3600
PBKDF2_ITERATIONS = 200_000
APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = (
    os.environ.get("ANNOUNCE_DB_PATH", "").strip()
    or str(APP_DIR / "announcements.sqlite3")
)


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


# ---------------- database ----------------
def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 20000")
    return con


def init_db(db_path: str) -> None:
    con = _connect(db_path)
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS admins (
                admin_id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                algorithm TEXT NOT NULL DEFAULT 'pbkdf2_sha256',
                iterations INTEGER NOT NULL DEFAULT 200000,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        con.commit()
    finally:
        con.close()


def make_password_hash(password: str) -> tuple[str, str, int]:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return digest.hex(), salt, PBKDF2_ITERATIONS


def verify_password(password: str, salt: str, iterations: int, expected_hash: str) -> bool:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations)
    )
    return hmac.compare_digest(digest.hex(), expected_hash)


# ---------------- sessions ----------------
class SessionStore:
    def __init__(self) -> None:
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._tokens[token] = time.time() + TOKEN_TTL_SECONDS
        return token

    def valid(self, token: str) -> bool:
        with self._lock:
            expires = self._tokens.get(token)
            if expires is None:
                return False
            if time.time() > expires:
                self._tokens.pop(token, None)
                return False
            return True


# ---------------- request models ----------------
class LoginRequest(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")


class SetupRequest(BaseModel):
    username: str = Field(default="")
    password: str = Field(default="")


class AnnouncementCreate(BaseModel):
    title: str = Field(default="")
    content: str = Field(default="")


class AnnouncementUpdate(BaseModel):
    title: str | None = None
    content: str | None = None


class PublishRequest(BaseModel):
    active: bool


# ---------------- app ----------------
def create_app() -> FastAPI:
    db_path = DEFAULT_DB_PATH
    init_db(db_path)
    sessions = SessionStore()

    app = FastAPI(title="公告发布后台", version="1.0.0")

    def admin_count() -> int:
        con = _connect(db_path)
        try:
            row = con.execute("SELECT COUNT(*) AS c FROM admins").fetchone()
            return int(row["c"])
        finally:
            con.close()

    def require_admin(authorization: str = Header(default="")) -> None:
        token = authorization.removeprefix("Bearer ").strip()
        if not token or not sessions.valid(token):
            raise HTTPException(status_code=401, detail="登录状态已过期，请重新登录")

    def serialize(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "content": row["content"],
            "published_at": row["published_at"],
            "active": bool(row["active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ---- 首次启动设置 / 登录 ----
    @app.get("/api/setup-status")
    def setup_status() -> dict[str, bool]:
        return {"needs_setup": admin_count() == 0}

    @app.post("/api/setup")
    def setup(payload: SetupRequest) -> dict[str, Any]:
        if admin_count() > 0:
            raise HTTPException(status_code=403, detail="管理员已存在，禁止重复初始化")
        username = payload.username.strip()
        password = payload.password
        if len(username) < 2:
            raise HTTPException(status_code=400, detail="账号至少 2 个字符")
        if len(password) < 6:
            raise HTTPException(status_code=400, detail="密码至少 6 位")
        password_hash, salt, iterations = make_password_hash(password)
        admin_id = "adm_" + secrets.token_hex(8)
        con = _connect(db_path)
        try:
            con.execute(
                """
                INSERT INTO admins (
                    admin_id, username, password_hash, salt, algorithm,
                    iterations, created_at
                ) VALUES (?, ?, ?, ?, 'pbkdf2_sha256', ?, ?)
                """,
                (admin_id, username, password_hash, salt, iterations, now_iso()),
            )
            con.commit()
        finally:
            con.close()
        return {"ok": True, "token": sessions.create()}

    @app.post("/api/login")
    def login(payload: LoginRequest) -> dict[str, Any]:
        con = _connect(db_path)
        try:
            row = con.execute(
                "SELECT * FROM admins WHERE username = ?", (payload.username.strip(),)
            ).fetchone()
        finally:
            con.close()
        if row is None or not verify_password(
            payload.password, row["salt"], row["iterations"], row["password_hash"]
        ):
            raise HTTPException(status_code=401, detail="账号或密码不正确")
        return {"token": sessions.create()}

    # ---- 客户端公开接口（本地工作台轮询用，免登录）----
    @app.get("/api/announcements/public")
    def public_announcements() -> dict[str, Any]:
        con = _connect(db_path)
        try:
            rows = con.execute(
                """
                SELECT id, title, content, published_at
                FROM announcements
                WHERE active = 1
                ORDER BY published_at DESC, id DESC
                """
            ).fetchall()
            return {"announcements": [dict(row) for row in rows]}
        finally:
            con.close()

    # ---- 管理端接口 ----
    @app.get("/api/announcements", dependencies=[Depends(require_admin)])
    def list_announcements() -> dict[str, Any]:
        con = _connect(db_path)
        try:
            rows = con.execute(
                """
                SELECT * FROM announcements
                ORDER BY published_at DESC, id DESC
                """
            ).fetchall()
            return {"announcements": [serialize(row) for row in rows]}
        finally:
            con.close()

    @app.post("/api/announcements", dependencies=[Depends(require_admin)])
    def create_announcement(payload: AnnouncementCreate) -> dict[str, Any]:
        title = payload.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="标题不能为空")
        content = payload.content.strip()
        now = now_iso()
        con = _connect(db_path)
        try:
            cur = con.execute(
                """
                INSERT INTO announcements (
                    title, content, published_at, active, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?)
                """,
                (title, content, now, now, now),
            )
            con.commit()
            row = con.execute(
                "SELECT * FROM announcements WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
            return {"announcement": serialize(row)}
        finally:
            con.close()

    @app.patch("/api/announcements/{announcement_id}", dependencies=[Depends(require_admin)])
    def update_announcement(announcement_id: int, payload: AnnouncementUpdate) -> dict[str, Any]:
        con = _connect(db_path)
        try:
            row = con.execute(
                "SELECT * FROM announcements WHERE id = ?", (announcement_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="公告不存在")
            title = payload.title if payload.title is not None else row["title"]
            content = payload.content if payload.content is not None else row["content"]
            if not str(title).strip():
                raise HTTPException(status_code=400, detail="标题不能为空")
            con.execute(
                """
                UPDATE announcements SET title = ?, content = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(title).strip(), str(content).strip(), now_iso(), announcement_id),
            )
            con.commit()
            row = con.execute(
                "SELECT * FROM announcements WHERE id = ?", (announcement_id,)
            ).fetchone()
            return {"announcement": serialize(row)}
        finally:
            con.close()

    @app.delete("/api/announcements/{announcement_id}", dependencies=[Depends(require_admin)])
    def delete_announcement(announcement_id: int) -> dict[str, Any]:
        con = _connect(db_path)
        try:
            row = con.execute(
                "SELECT id FROM announcements WHERE id = ?", (announcement_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="公告不存在")
            con.execute("DELETE FROM announcements WHERE id = ?", (announcement_id,))
            con.commit()
            return {"ok": True}
        finally:
            con.close()

    @app.post("/api/announcements/{announcement_id}/publish", dependencies=[Depends(require_admin)])
    def set_publish(announcement_id: int, payload: PublishRequest) -> dict[str, Any]:
        con = _connect(db_path)
        try:
            row = con.execute(
                "SELECT id FROM announcements WHERE id = ?", (announcement_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="公告不存在")
            con.execute(
                "UPDATE announcements SET active = ?, updated_at = ? WHERE id = ?",
                (1 if payload.active else 0, now_iso(), announcement_id),
            )
            con.commit()
            row = con.execute(
                "SELECT * FROM announcements WHERE id = ?", (announcement_id,)
            ).fetchone()
            return {"announcement": serialize(row)}
        finally:
            con.close()

    # ---- 管理页面 ----
    static_dir = APP_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    def home() -> Any:
        index = static_dir / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse("<h1>公告后台前端未部署</h1>", status_code=200)

    return app


app = create_app()
