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
