from __future__ import annotations

import httpx

from wh_local.messages.repository import MessagesRepository
from wh_local.messages.service import (
    AnnouncementSyncService,
    FeedbackReplySyncService,
)


class _FakeResponse:
    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload if payload is not None else {"announcements": []}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _capture(monkeypatch) -> list[tuple[str, dict]]:
    """捕获 httpx.get 的 (url, kwargs)，kwargs 里含 headers，用于断言身份头。"""
    captured: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        captured.append((url, dict(kwargs)))
        return _FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    return captured


def _headers_of(call: tuple[str, dict]) -> dict[str, str]:
    return dict(call[1].get("headers") or {})


def test_announcement_sync_appends_account_id_exactly_once(tmp_path, monkeypatch) -> None:
    """回归：合并事故曾把 account_id 拼装块重复了一遍。

    URL 会变成 ``?account_id=X?account_id=X``，服务端解析出的 account_id 是
    ``"X?account_id=X"``，永远匹配不到定向收件人 → 定向公告一条都发不下来。
    """
    captured = _capture(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = AnnouncementSyncService(
        repository,
        "https://example.test",
        account_id_provider=lambda: "acc-1",
    )

    assert service.sync_once() == 0
    assert [url for url, _ in captured] == [
        "https://example.test/api/announcements/public?account_id=acc-1"
    ]


def test_feedback_reply_sync_appends_account_id_exactly_once(tmp_path, monkeypatch) -> None:
    captured = _capture(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = FeedbackReplySyncService(
        repository,
        "https://example.test",
        account_id_provider=lambda: "acc-1",
    )

    assert service.sync_once() == 0
    assert [url for url, _ in captured] == [
        "https://example.test/api/feedback-replies/public?account_id=acc-1"
    ]


def test_sync_without_account_id_has_no_query_string(tmp_path, monkeypatch) -> None:
    captured = _capture(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = AnnouncementSyncService(repository, "https://example.test")

    assert service.sync_once() == 0
    assert [url for url, _ in captured] == ["https://example.test/api/announcements/public"]


def test_account_id_provider_failure_degrades_to_public_only(tmp_path, monkeypatch) -> None:
    """身份查询异常时退化为仅拉全员公告，不应把同步整体打断。"""

    def boom() -> str:
        raise RuntimeError("db locked")

    captured = _capture(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = AnnouncementSyncService(
        repository, "https://example.test", account_id_provider=boom
    )

    assert service.sync_once() == 0
    assert [url for url, _ in captured] == ["https://example.test/api/announcements/public"]


# ---- 身份头（x-auth-token）：后台按它解析账号，是定向过滤的真正依据 ----


def test_sync_sends_auth_token_header_from_provider(tmp_path, monkeypatch) -> None:
    captured = _capture(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = AnnouncementSyncService(
        repository,
        "https://example.test",
        auth_token_provider=lambda: "remote-tok-1",
    )

    assert service.sync_once() == 0
    assert _headers_of(captured[0]).get("x-auth-token") == "remote-tok-1"


def test_explicit_auth_token_wins_over_provider(tmp_path, monkeypatch) -> None:
    """请求级同步能精确定位当前会话的令牌，不应被 provider 的启发式值覆盖。"""
    captured = _capture(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = AnnouncementSyncService(
        repository,
        "https://example.test",
        auth_token_provider=lambda: "stale-token",
    )

    assert service.sync_once(auth_token="exact-token") == 0
    assert _headers_of(captured[0]).get("x-auth-token") == "exact-token"


def test_sync_without_token_provider_sends_no_auth_header(tmp_path, monkeypatch) -> None:
    captured = _capture(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = AnnouncementSyncService(repository, "https://example.test")

    assert service.sync_once() == 0
    assert "x-auth-token" not in _headers_of(captured[0])


def test_auth_token_provider_failure_sends_no_auth_header(tmp_path, monkeypatch) -> None:
    def boom() -> str:
        raise RuntimeError("memory map unavailable")

    captured = _capture(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = AnnouncementSyncService(
        repository, "https://example.test", auth_token_provider=boom
    )

    assert service.sync_once() == 0
    assert "x-auth-token" not in _headers_of(captured[0])


def test_feedback_reply_sync_sends_auth_token_header(tmp_path, monkeypatch) -> None:
    captured = _capture(monkeypatch)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    service = FeedbackReplySyncService(
        repository,
        "https://example.test",
        auth_token_provider=lambda: "remote-tok-2",
    )

    assert service.sync_once() == 0
    assert _headers_of(captured[0]).get("x-auth-token") == "remote-tok-2"


def test_image_fetch_carries_auth_header(tmp_path, monkeypatch) -> None:
    """定向公告的图片后台也按身份校验，按需拉图必须带同一份身份头。"""
    captured: list[tuple[str, dict]] = []

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        captured.append((url, dict(kwargs)))
        # 列表接口必须回这条公告：回空列表会被 prune_retracted 当成"全部撤回"
        # 把本地行删光，按需拉图就无从触发（见 test_empty_list_never_wipes_local_messages）。
        return _FakeResponse(
            {
                "announcements": [
                    {
                        "id": 20,
                        "title": "带图定向公告",
                        "content": "",
                        "published_at": "2026-10-06T10:00:00+08:00",
                        "image_count": 2,
                        "image_rev": 1,
                    }
                ]
            }
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    # image_count>0 且本地无图 → 会被 messages_missing_images 挑出来按需拉取
    repository.upsert_server_announcements(
        [
            {
                "id": 20,
                "title": "带图定向公告",
                "content": "",
                "published_at": "2026-10-06T10:00:00+08:00",
                "image_count": 2,
                "image_rev": 1,
            }
        ]
    )
    service = AnnouncementSyncService(
        repository,
        "https://example.test",
        auth_token_provider=lambda: "remote-tok-3",
    )

    assert service.sync_once() == 0
    urls = [url for url, _ in captured]
    assert urls == [
        "https://example.test/api/announcements/public",
        "https://example.test/api/announcements/20/images",
    ]
    assert _headers_of(captured[1]).get("x-auth-token") == "remote-tok-3"


def test_empty_list_never_wipes_local_messages(tmp_path, monkeypatch) -> None:
    """空列表一律不撤回。

    空既可能是"服务端一条在线消息都没有"，也可能是"该接口按身份过滤后为空"
    （匿名查反馈回复就是合法空列表）。两者无法区分，而批量删除不可逆 ——
    宁可留下一条已下线的残留（换号/重新登录时会清），也不能误删整类消息。
    """
    _capture(monkeypatch)  # 返回 {"announcements": []}
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    repository.upsert_server_announcements(
        [{"id": 1, "title": "a", "content": "", "published_at": "2026-10-06T10:00:00+08:00"}]
    )
    service = AnnouncementSyncService(
        repository, "https://example.test", auth_token_provider=lambda: "tok"
    )

    assert service.sync_once() == 0
    assert [m["server_id"] for m in repository.list_messages()] == [1]


def test_anonymous_sync_keeps_directional_announcements(tmp_path, monkeypatch) -> None:
    """匿名轮不得撤回：服务端匿名只回全员公告，按它撤回会把定向公告当"已下架"删掉。

    远端令牌只存在进程内存里（重启即空），所以**每次重启后的第一轮必然是匿名的** ——
    旧实现在这一刻会把用户收件箱里的定向公告清掉（真实发生过的数据丢失）。
    """
    _capture(monkeypatch)  # 匿名：服务端只返回全员公告
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    repository.upsert_server_announcements(
        [
            {"id": 19, "title": "全员公告", "content": "", "published_at": "p"},
            {"id": 20, "title": "定向公告", "content": "", "published_at": "p"},
        ]
    )
    service = AnnouncementSyncService(repository, "https://example.test")  # 无令牌

    service.sync_once()

    assert sorted(m["server_id"] for m in repository.list_messages()) == [19, 20]


def test_anonymous_feedback_sync_keeps_local_replies(tmp_path, monkeypatch) -> None:
    """匿名查反馈回复会拿到合法空列表 —— 绝不能按它把本地回复清空。"""
    _capture(monkeypatch)  # 返回 {"announcements": []}
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    repository.upsert_server_announcements(
        [{"id": 1_000_000_002, "title": "管理员回复", "content": "", "published_at": "p"}],
        kind="feedback_reply",
    )
    service = FeedbackReplySyncService(repository, "https://example.test")  # 无令牌

    service.sync_once()

    assert [m["server_id"] for m in repository.list_messages()] == [1_000_000_002]


def test_authenticated_sync_still_prunes_retracted_announcements(tmp_path, monkeypatch) -> None:
    """带身份时必须保留撤回能力，否则服务端下架的消息会永远留在本地。"""

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        return _FakeResponse(
            {"announcements": [{"id": 19, "title": "全员公告", "content": "", "published_at": "p"}]}
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    repository = MessagesRepository(tmp_path / "messages.sqlite3")
    repository.upsert_server_announcements(
        [
            {"id": 19, "title": "全员公告", "content": "", "published_at": "p"},
            {"id": 20, "title": "已下架", "content": "", "published_at": "p"},
        ]
    )
    service = AnnouncementSyncService(
        repository, "https://example.test", auth_token_provider=lambda: "tok"
    )

    service.sync_once()

    assert [m["server_id"] for m in repository.list_messages()] == [19]
