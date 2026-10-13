from __future__ import annotations

from wh_local.messages.repository import MessagesRepository


def _announcement(
    server_id: int,
    title: str,
    content: str,
    published_at: str,
) -> dict[str, object]:
    return {
        "id": server_id,
        "title": title,
        "content": content,
        "published_at": published_at,
    }


def test_existing_announcement_is_updated_without_resetting_read_state(tmp_path) -> None:
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    original = _announcement(1, "测试", "旧正文", "2026-08-27T16:10:09+08:00")
    assert repository.upsert_server_announcements([original]) == 1

    local_message_id = repository.list_messages()[0]["id"]
    assert repository.mark_read(local_message_id) is True

    edited = _announcement(1, "测试3", "新正文", "2026-08-27T16:10:09+08:00")
    new_item = _announcement(2, "测试2", "测试2", "2026-08-27T16:27:30+08:00")
    assert repository.upsert_server_announcements([edited, new_item]) == 1

    messages = {item["server_id"]: item for item in repository.list_messages()}
    assert messages[1]["title"] == "测试3"
    assert messages[1]["content"] == "新正文"
    assert messages[1]["read"] == 1
    assert messages[2]["read"] == 0


def test_repeated_sync_does_not_count_existing_announcement_as_new(tmp_path) -> None:
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    item = _announcement(1, "公告", "正文", "2026-08-27T16:10:09+08:00")

    assert repository.upsert_server_announcements([item]) == 1
    assert repository.upsert_server_announcements([item]) == 0


def test_pruned_announcement_is_hidden_from_list_and_unread(tmp_path) -> None:
    """服务端撤回的消息转为隐藏：列表与未读数都不再包含它。"""
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    keep = _announcement(1, "保留", "正文", "2026-08-27T16:10:09+08:00")
    retract = _announcement(2, "撤回", "正文", "2026-08-27T16:11:09+08:00")
    repository.upsert_server_announcements([keep, retract])

    hidden = repository.prune_retracted([1])

    assert hidden == 1
    assert [m["server_id"] for m in repository.list_messages()] == [1]
    # 被隐藏的消息即使未读也不计入未读数（用户看不到它，不该有红点）。
    assert repository.unread_count() == 1


def test_reissued_announcement_restores_read_state_without_red_dot(tmp_path) -> None:
    """撤回-重发后已读保持、不产生红点（红点复活问题的回归用例）。

    场景：服务端在令牌降级轮只返回全员公告 → 定向公告被 prune 隐藏 →
    重新登录后公告重新下发 → 必须恢复显示且保持已读。
    """
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    item = _announcement(7, "定向公告", "正文", "2026-10-09T17:36:49+08:00")
    repository.upsert_server_announcements([item])
    repository.mark_read(repository.list_messages()[0]["id"])

    repository.prune_retracted([99])  # 身份降级的一轮：7 不在在线列表
    assert repository.list_messages() == []
    assert repository.unread_count() == 0

    repository.upsert_server_announcements([item])  # 有效身份轮：7 重新下发
    messages = repository.list_messages()
    assert len(messages) == 1
    assert messages[0]["read"] == 1
    assert repository.unread_count() == 0


def test_unread_announcement_stays_unread_after_retract_and_reissue(tmp_path) -> None:
    """从未看过的公告撤回-重发后仍是未读（用户确实没见过，应该提示）。"""
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    item = _announcement(8, "没看过", "正文", "2026-10-09T17:36:49+08:00")
    repository.upsert_server_announcements([item])

    repository.prune_retracted([99])
    repository.upsert_server_announcements([item])

    assert repository.unread_count() == 1


def test_prune_skips_empty_active_list(tmp_path) -> None:
    """空在线列表不撤回（避免按身份过滤为空的场景误隐藏全部消息）。"""
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    repository.upsert_server_announcements(
        [_announcement(1, "公告", "正文", "2026-08-27T16:10:09+08:00")]
    )

    assert repository.prune_retracted([]) == 0
    assert len(repository.list_messages()) == 1


def test_feedback_reply_user_delete_still_removes_and_blacklists(tmp_path) -> None:
    """反馈回复的用户主动删除仍是物理删除 + 黑名单（不受隐藏改造影响）。"""
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    reply = _announcement(1_000_000_123, "回复", "内容", "2026-08-27T16:10:09+08:00")
    repository.upsert_server_announcements([reply], kind="feedback_reply")
    message_id = repository.list_messages()[0]["id"]

    assert repository.delete_message(message_id) is True
    assert repository.list_messages() == []
    repository.upsert_server_announcements([reply], kind="feedback_reply")
    assert repository.list_messages() == []


def test_legacy_database_without_hidden_column_is_migrated(tmp_path) -> None:
    """老库（无 hidden 列）打开时自动补列，已有消息默认可见。"""
    import sqlite3

    db = tmp_path / "messages.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE messages (
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
        INSERT INTO messages (server_id, title, content, published_at, read)
            VALUES (5, '旧消息', '', '2026-08-01T10:00:00+08:00', 1);
        """
    )
    con.commit()
    con.close()

    repository = MessagesRepository(db)

    messages = repository.list_messages()
    assert len(messages) == 1
    assert messages[0]["read"] == 1
    assert repository.unread_count() == 0

