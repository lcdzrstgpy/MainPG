# -*- coding: utf-8 -*-
"""隐私政策本地入口：转发平台服务器页面 + 断网兜底。

产品约定（2026-11-03）：
- 客户端仍然「新标签页打开 /privacy」（URL 与改造前一致）；
- 联网时展示服务端最新正文；
- 断网时展示本地留存的上一份整页 HTML。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wh_local.app import main as app_main


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        app_main.create_app(tmp_path / "workbench.sqlite3"),
        base_url="http://127.0.0.1:8000",
    )


def _reset(html: str | None = None, at: float = 0.0) -> None:
    app_main._privacy_page_cache["html"] = html
    app_main._privacy_page_cache["at"] = at


class _FakeResponse:
    status_code = 200
    text = "<html><body><h2>前言</h2></body></html>"


def test_serves_remote_page_and_caches_it(tmp_path: Path, monkeypatch) -> None:
    _reset()
    calls = {"count": 0}

    def fake_get(*_args, **_kwargs):
        calls["count"] += 1
        return _FakeResponse()

    # 打桩在 requests 层：这样能真正走到 _fetch_privacy_page 里的 5 分钟缓存
    monkeypatch.setattr("requests.get", fake_get)

    with _client(tmp_path) as client:
        first = client.get("/privacy")
        second = client.get("/privacy")

    assert first.status_code == 200
    assert "前言" in first.text
    assert second.status_code == 200
    # 5 分钟缓存：第二次不再打远端
    assert calls["count"] == 1


def test_falls_back_to_cached_page_when_remote_fails(tmp_path: Path, monkeypatch) -> None:
    _reset(html="<html><body>旧副本正文</body></html>", at=0.0)  # 已过期，但可兜底
    monkeypatch.setattr(app_main, "_fetch_privacy_page", lambda: None)

    with _client(tmp_path) as client:
        response = client.get("/privacy")

    assert response.status_code == 200
    assert "旧副本正文" in response.text


def test_returns_503_when_remote_fails_and_no_cache(tmp_path: Path, monkeypatch) -> None:
    _reset()
    monkeypatch.setattr(app_main, "_fetch_privacy_page", lambda: None)

    with _client(tmp_path) as client:
        response = client.get("/privacy")

    assert response.status_code == 503
    assert "暂时不可用" in response.text
