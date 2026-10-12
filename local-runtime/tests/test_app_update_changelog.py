"""「更新日志」客户端：ChangelogManager 抓取/校验/缓存/失败兜底 + /api/app-update/changelog 路由。"""
from urllib.parse import urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.app_update import (
    CHANGELOG_CONTRACT_VERSION,
    ChangelogManager,
    ChangelogSettings,
    ChangelogValidationError,
    create_changelog_router,
)


def _settings() -> ChangelogSettings:
    return ChangelogSettings(
        changelog_url="https://updates.example.com/mainpg/windows/changelog.json",
        allowed_hosts=frozenset({"updates.example.com"}),
    )


def _payload(releases):
    return {
        "contract_version": CHANGELOG_CONTRACT_VERSION,
        "generated_at": "2026-10-12T00:00:00Z",
        "releases": releases,
    }


def _release(version, notes="说明", published_at="2026-10-11T09:25:00Z"):
    return {"version": version, "published_at": published_at, "release_notes": notes}


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_url_must_use_allowlisted_https_host():
    with pytest.raises(ChangelogValidationError):
        ChangelogManager(
            ChangelogSettings(
                changelog_url="http://updates.example.com/changelog.json",
                allowed_hosts=frozenset({"updates.example.com"}),
            )
        )
    with pytest.raises(ChangelogValidationError):
        ChangelogManager(
            ChangelogSettings(
                changelog_url="https://evil.example.com/changelog.json",
                allowed_hosts=frozenset({"updates.example.com"}),
            )
        )


def test_snapshot_parses_releases_and_caches():
    calls = []

    def fetcher(url):
        calls.append(url)
        return _payload([_release("1.5.3"), _release("1.5.2", notes="旧版说明")])

    manager = ChangelogManager(_settings(), fetcher=fetcher)
    snapshot = manager.snapshot()
    assert snapshot["error"] is None
    assert [item["version"] for item in snapshot["releases"]] == ["1.5.3", "1.5.2"]
    assert snapshot["releases"][1]["release_notes"] == "旧版说明"
    assert calls == [_settings().changelog_url]

    # 缓存窗口内重复读取不再发起网络请求
    manager.snapshot()
    assert len(calls) == 1


def test_cache_expires_and_refetches():
    clock = _FakeClock()
    calls = []

    def fetcher(url):
        calls.append(url)
        return _payload([_release(f"1.5.{len(calls)}")])

    manager = ChangelogManager(_settings(), fetcher=fetcher, clock=clock, cache_seconds=600.0)
    manager.snapshot()
    clock.now = 599.0
    manager.snapshot()
    assert len(calls) == 1
    clock.now = 601.0
    manager.snapshot()
    assert len(calls) == 2


def test_failure_keeps_stale_data_then_recovers():
    clock = _FakeClock()
    state = {"fail": False}

    def fetcher(url):
        if state["fail"]:
            raise OSError("offline")
        return _payload([_release("1.5.3")])

    manager = ChangelogManager(
        _settings(), fetcher=fetcher, clock=clock, cache_seconds=600.0, retry_seconds=60.0
    )
    first = manager.snapshot()
    assert first["error"] is None and len(first["releases"]) == 1

    state["fail"] = True
    clock.now = 601.0
    stale = manager.snapshot()
    assert stale["error"] is not None
    assert [item["version"] for item in stale["releases"]] == ["1.5.3"]  # 保留旧数据

    state["fail"] = False
    clock.now = 601.0 + 61.0
    recovered = manager.snapshot()
    assert recovered["error"] is None
    assert [item["version"] for item in recovered["releases"]] == ["1.5.3"]


def test_failure_without_cache_returns_empty_error_state():
    def fetcher(url):
        raise OSError("offline")

    manager = ChangelogManager(_settings(), fetcher=fetcher)
    snapshot = manager.snapshot()
    assert snapshot["releases"] == []
    assert "更新日志" in str(snapshot["error"])


def test_invalid_payload_counts_as_failure():
    bad_payloads = [
        "not json at all",
        {"contract_version": "unknown-v9", "releases": []},
        {"contract_version": CHANGELOG_CONTRACT_VERSION, "releases": "oops"},
    ]
    for payload in bad_payloads:
        manager = ChangelogManager(_settings(), fetcher=lambda url, p=payload: p)
        snapshot = manager.snapshot()
        assert snapshot["releases"] == []
        assert snapshot["error"] is not None, payload


def test_bad_items_are_skipped_but_valid_ones_survive():
    def fetcher(url):
        return _payload(
            [
                _release("1.5.3"),
                {"version": "", "published_at": "x", "release_notes": ""},
                {"version": "1.5.2"},
                "not-a-dict",
                {**_release("1.5.1"), "release_notes": "长" * 20_001},
            ]
        )

    manager = ChangelogManager(_settings(), fetcher=fetcher)
    snapshot = manager.snapshot()
    assert [item["version"] for item in snapshot["releases"]] == ["1.5.3"]
    assert snapshot["error"] is None


def test_router_serves_snapshot():
    manager = ChangelogManager(
        _settings(),
        fetcher=lambda url: _payload([_release("1.5.3", notes="首行\n第二行")]),
    )
    app = FastAPI()
    app.include_router(create_changelog_router(manager))
    client = TestClient(app)

    response = client.get("/api/app-update/changelog")
    assert response.status_code == 200
    body = response.json()
    assert body["error"] is None
    assert body["releases"][0]["version"] == "1.5.3"
    assert body["releases"][0]["release_notes"] == "首行\n第二行"


def test_config_changelog_url_uses_official_host():
    from wh_local.config import (
        UPDATE_CHANGELOG_URL,
        UPDATE_MANIFEST_ALLOWED_HOSTS,
        UPDATE_RELEASE_HOST,
    )

    parsed = urlparse(UPDATE_CHANGELOG_URL)
    assert UPDATE_RELEASE_HOST == "workbench.haocoming.top"
    assert UPDATE_CHANGELOG_URL == "https://workbench.haocoming.top/mainpg/windows/changelog.json"
    assert parsed.scheme == "https"
    assert parsed.hostname in UPDATE_MANIFEST_ALLOWED_HOSTS
