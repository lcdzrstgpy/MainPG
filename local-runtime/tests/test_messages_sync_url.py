from __future__ import annotations

import httpx

from wh_local.messages.repository import MessagesRepository
from wh_local.messages.service import (
    AnnouncementSyncService,
    FeedbackReplySyncService,
)


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"announcements": []}


def _capture_urls(monkeypatch) -> list[str]:
    captured: list[str] = []

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        captured.append(url)
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    return captured


def test_announcement_sync_appends_account_id_exactly_once(tmp_path, monkeypatch) -> None:
    """回归：合并事故曾把 account_id 拼装块重复了一遍。

    URL 会变成 ``?account_id=X?account_id=X``，服务端解析出的 account_id 是
    ``"X?account_id=X"``，永远匹配不到定向收件人 → 定向公告一条都发不下来。
    """
    captured = _capture_urls(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = AnnouncementSyncService(
        repository,
        "https://example.test",
        account_id_provider=lambda: "acc-1",
    )

    assert service.sync_once() == 0
    assert captured == [
        "https://example.test/api/announcements/public?account_id=acc-1"
    ]


def test_feedback_reply_sync_appends_account_id_exactly_once(tmp_path, monkeypatch) -> None:
    captured = _capture_urls(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = FeedbackReplySyncService(
        repository,
        "https://example.test",
        account_id_provider=lambda: "acc-1",
    )

    assert service.sync_once() == 0
    assert captured == [
        "https://example.test/api/feedback-replies/public?account_id=acc-1"
    ]


def test_sync_without_account_id_has_no_query_string(tmp_path, monkeypatch) -> None:
    captured = _capture_urls(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = AnnouncementSyncService(repository, "https://example.test")

    assert service.sync_once() == 0
    assert captured == ["https://example.test/api/announcements/public"]


def test_account_id_provider_failure_degrades_to_public_only(tmp_path, monkeypatch) -> None:
    """身份查询异常时退化为仅拉全员公告，不应把同步整体打断。"""

    def boom() -> str:
        raise RuntimeError("db locked")

    captured = _capture_urls(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = AnnouncementSyncService(
        repository, "https://example.test", account_id_provider=boom
    )

    assert service.sync_once() == 0
    assert captured == ["https://example.test/api/announcements/public"]
