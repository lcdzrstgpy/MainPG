from __future__ import annotations

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
    kind TEXT NOT NULL DEFAULT 'announcement'
);
CREATE INDEX IF NOT EXISTS idx_messages_read
    ON messages (read, published_at DESC);
"""


def _ensure_kind_column(con: sqlite3.Connection) -> None:
    """旧库迁移：messages 表缺 kind 列时补上（默认 announcement）。"""
    cols = {row[1] for row in con.execute("PRAGMA table_info(messages)")}
    if "kind" not in cols:
        con.execute(
            "ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'announcement'"
        )


class MessagesRepository:
    """本地消息表：保存从服务器同步来的公告及本机已读状态。"""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.executescript(SCHEMA_SQL)
            _ensure_kind_column(con)
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
        """
        con = self._connect()
        try:
            new_count = 0
            for item in items:
                server_id = int(item.get("id") or 0)
                if server_id <= 0:
                    continue
                title = str(item.get("title") or "").strip()
                content = str(item.get("content") or "")
                published_at = str(item.get("published_at") or "")
                cur = con.execute(
                    """
                    INSERT INTO messages (
                        server_id, title, content, published_at, read, kind
                    ) VALUES (?, ?, ?, ?, 0, ?)
                    ON CONFLICT(server_id) DO NOTHING
                    """,
                    (server_id, title, content, published_at, kind),
                )
                if cur.rowcount > 0:
                    new_count += 1
                    continue
                con.execute(
                    """
                    UPDATE messages
                    SET title = ?, content = ?, published_at = ?, kind = ?
                    WHERE server_id = ?
                    """,
                    (title, content, published_at, kind, server_id),
                )
            con.commit()
            return new_count
        finally:
            con.close()

    def list_messages(self) -> list[dict[str, Any]]:
        con = self._connect()
        try:
            rows = con.execute(
                """
                SELECT id, server_id, title, content, published_at, read, kind
                FROM messages
                ORDER BY published_at DESC, id DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]
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

    def prune_retracted(self, active_server_ids: list[int]) -> int:
        """按服务器在线公告 id 列表撤回本地消息。

        服务器上已下线/已删除的公告，本地对应消息一并移除（含已读状态）。
        仅在同步成功、拿到完整在线列表时调用；服务器不可达时不得调用，
        避免断网误删本地消息。
        """
        ids = [int(value) for value in active_server_ids if int(value) > 0]
        con = self._connect()
        try:
            # 只撤回公告类消息（kind='announcement'），反馈回复由独立通道管理，
            # 不随公告在线列表被误删。
            if not ids:
                cur = con.execute(
                    "DELETE FROM messages WHERE server_id > 0 AND kind = 'announcement'"
                )
            else:
                placeholders = ",".join("?" * len(ids))
                cur = con.execute(
                    f"DELETE FROM messages WHERE server_id > 0 AND kind = 'announcement' "
                    f"AND server_id NOT IN ({placeholders})",
                    ids,
                )
            con.commit()
            return cur.rowcount
        finally:
            con.close()
