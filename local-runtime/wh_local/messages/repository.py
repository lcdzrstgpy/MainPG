from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    read INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL DEFAULT 'announcement',
    image_count INTEGER NOT NULL DEFAULT 0,
    image_rev INTEGER NOT NULL DEFAULT 0,
    images TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_messages_read
    ON messages (read, published_at DESC);
CREATE TABLE IF NOT EXISTS message_deletions (
    server_id INTEGER PRIMARY KEY,
    deleted_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _ensure_kind_column(con: sqlite3.Connection) -> None:
    """旧库迁移：messages 表缺 kind 列时补上（默认 announcement）。"""
    cols = {row[1] for row in con.execute("PRAGMA table_info(messages)")}
    if "kind" not in cols:
        con.execute(
            "ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'announcement'"
        )


def _ensure_image_columns(con: sqlite3.Connection) -> None:
    """旧库迁移：补公告图片相关列。

    images 存图片本体（JSON 数组，元素含 base64），按需从服务端拉取后本地缓存；
    image_rev 是服务端图片版本号，变化说明图片被改过，需重新拉取。
    """
    cols = {row[1] for row in con.execute("PRAGMA table_info(messages)")}
    if "image_count" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN image_count INTEGER NOT NULL DEFAULT 0")
    if "image_rev" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN image_rev INTEGER NOT NULL DEFAULT 0")
    if "images" not in cols:
        con.execute("ALTER TABLE messages ADD COLUMN images TEXT NOT NULL DEFAULT '[]'")


def _load_images(raw: Any) -> list[dict[str, Any]]:
    try:
        items = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


class MessagesRepository:
    """本地消息表：保存从服务器同步来的公告及本机已读状态。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.executescript(SCHEMA_SQL)
            _ensure_kind_column(con)
            _ensure_image_columns(con)
            con.commit()
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.database_path, timeout=20)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout = 20000")
        return con

    def upsert_server_announcements(
        self, items: list[dict[str, Any]], kind: str = "announcement"
    ) -> int:
        """按 server_id 同步服务器消息，返回新增条数（新消息默认未读）。

        已存在的消息会更新服务端字段，但保留本机 ``read`` 状态。
        ``kind`` 区分来源：announcement（公告）或 feedback_reply（反馈回复）。
        用户主动删除过的 server_id 记录在黑名单里，同步时跳过，避免删了又回来。
        """
        con = self._connect()
        try:
            new_count = 0
            deleted_ids = {
                int(row["server_id"])
                for row in con.execute("SELECT server_id FROM message_deletions")
            }
            for item in items:
                server_id = int(item.get("id") or 0)
                if server_id <= 0:
                    continue
                if server_id in deleted_ids:
                    continue
                title = str(item.get("title") or "").strip()
                content = str(item.get("content") or "")
                published_at = str(item.get("published_at") or "")
                image_count = int(item.get("image_count") or 0)
                image_rev = int(item.get("image_rev") or 0)
                cur = con.execute(
                    """
                    INSERT INTO messages (
                        server_id, title, content, published_at, read, kind,
                        image_count, image_rev, images
                    ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, '[]')
                    ON CONFLICT(server_id) DO NOTHING
                    """,
                    (server_id, title, content, published_at, kind, image_count, image_rev),
                )
                if cur.rowcount > 0:
                    new_count += 1
                    continue
                # 图片版本号变了说明服务端改过图：清空本地缓存，交给按需拉取重新下载。
                con.execute(
                    """
                    UPDATE messages
                    SET title = ?, content = ?, published_at = ?, kind = ?,
                        image_count = ?, image_rev = ?,
                        images = CASE WHEN image_rev = ? THEN images ELSE '[]' END
                    WHERE server_id = ?
                    """,
                    (
                        title,
                        content,
                        published_at,
                        kind,
                        image_count,
                        image_rev,
                        image_rev,
                        server_id,
                    ),
                )
            con.commit()
            return new_count
        finally:
            con.close()

    def list_messages(self, *, with_images: bool = False) -> list[dict[str, Any]]:
        con = self._connect()
        try:
            rows = con.execute(
                """
                SELECT id, server_id, title, content, published_at, read, kind,
                       image_count, image_rev, images
                FROM messages
                ORDER BY published_at DESC, id DESC
                """
            ).fetchall()
            messages: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                # 图片 base64 体积大：默认只给数量，弹窗需要时才带上本体。
                item["images"] = _load_images(item.get("images")) if with_images else []
                messages.append(item)
            return messages
        finally:
            con.close()

    def messages_missing_images(self, limit: int = 10) -> list[dict[str, Any]]:
        """待按需拉取图片本体的公告（有图但本地缓存为空）。"""
        con = self._connect()
        try:
            rows = con.execute(
                """
                SELECT server_id, image_rev FROM messages
                WHERE kind = 'announcement' AND image_count > 0 AND images = '[]'
                ORDER BY published_at DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            con.close()

    def save_message_images(
        self, server_id: int, image_rev: int, images: list[dict[str, Any]]
    ) -> None:
        """写入按需拉取到的图片本体（含服务端版本号，供下次判断是否过期）。"""
        con = self._connect()
        try:
            con.execute(
                "UPDATE messages SET images = ?, image_rev = ? WHERE server_id = ?",
                (json.dumps(images, ensure_ascii=False), int(image_rev), int(server_id)),
            )
            con.commit()
        finally:
            con.close()

    def unread_count(self) -> int:
        con = self._connect()
        try:
            row = con.execute("SELECT COUNT(*) AS c FROM messages WHERE read = 0").fetchone()
            return int(row["c"])
        finally:
            con.close()

    def mark_read(self, message_id: int) -> bool:
        con = self._connect()
        try:
            cur = con.execute(
                "UPDATE messages SET read = 1 WHERE id = ? AND read = 0",
                (message_id,),
            )
            con.commit()
            return cur.rowcount > 0
        finally:
            con.close()

    def mark_all_read(self) -> int:
        con = self._connect()
        try:
            cur = con.execute("UPDATE messages SET read = 1 WHERE read = 0")
            con.commit()
            return cur.rowcount
        finally:
            con.close()

    def delete_message(self, message_id: int) -> bool:
        """删除一条本地消息（用户主动删除消息中心里的消息）。

        仅允许删除反馈回复（kind='feedback_reply'）；公告类消息与后台同步，
        由 prune_retracted 按服务端在线列表管理，不允许用户手动删除。
        同时把该消息的 ``server_id`` 记入黑名单，后续同步跳过它，避免"删了又回来"。
        """
        con = self._connect()
        try:
            row = con.execute(
                "SELECT server_id, kind FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
            if row is None:
                return False
            if row["kind"] != "feedback_reply":
                return False
            server_id = int(row["server_id"])
            if server_id > 0:
                con.execute(
                    "INSERT OR IGNORE INTO message_deletions (server_id) VALUES (?)",
                    (server_id,),
                )
            con.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            con.commit()
            return True
        finally:
            con.close()

    def prune_retracted(self, active_server_ids: list[int], kind: str = "announcement") -> int:
        """按服务器在线消息 id 列表撤回本地消息。

        服务器上已下线/已删除的消息，本地对应消息一并移除（含已读状态）。
        仅在同步成功、拿到完整在线列表时调用；服务器不可达时不得调用，
        避免断网误删本地消息。

        ``kind`` 限定撤回的消息类型：公告与反馈回复各自独立撤回，
        不会因公告在线列表把反馈回复误删（反之亦然）。
        """
        ids = [int(value) for value in active_server_ids if int(value) > 0]
        con = self._connect()
        try:
            # 只撤回公告类消息（kind='announcement'），反馈回复由独立通道管理，
            # 不随公告在线列表被误删。
            if not ids:
                cur = con.execute(
                    "DELETE FROM messages WHERE server_id > 0 AND kind = ?",
                    (kind,),
                )
            else:
                placeholders = ",".join("?" * len(ids))
                cur = con.execute(
                    f"DELETE FROM messages WHERE server_id > 0 AND kind = ? "
                    f"AND server_id NOT IN ({placeholders})",
                    [kind, *ids],
                )
            con.commit()
            return cur.rowcount
        finally:
            con.close()
