"""本地入口防护：Host 回环校验 + CORS 收敛（2026-11-20，来自队友安全审计的教训）。

只测「闸门」本身，不测业务鉴权（那是 session.py 的事）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from wh_local.app.main import create_app
from wh_local.local_host_guard import (
    cors_allow_origins,
    is_allowed_host,
    same_origin_of,
)


def test_is_allowed_host_loopback_only(monkeypatch) -> None:
    monkeypatch.delenv("WH_LOCAL_ALLOWED_HOSTS", raising=False)
    # 回环的各种写法都放行
    for host in ("127.0.0.1", "127.0.0.1:8010", "localhost:5173", "[::1]:8010", ""):
        assert is_allowed_host(host), host
    # 浏览器里能解析到的普通域名一律拒绝（DNS rebinding 的入口）
    for host in ("evil.example", "evil.example:80", "10.0.0.5:8010", "mainpg.local"):
        assert not is_allowed_host(host), host
    # 环境变量白名单可以追加
    monkeypatch.setenv("WH_LOCAL_ALLOWED_HOSTS", "studio.example")
    assert is_allowed_host("studio.example:8010")


def test_cors_allow_origins_loopback_and_env(monkeypatch) -> None:
    monkeypatch.delenv("WH_LOCAL_ALLOWED_ORIGINS", raising=False)
    origins = cors_allow_origins()
    assert "http://127.0.0.1:5173" in origins
    assert "http://localhost:5173" in origins
    assert "*" not in origins
    monkeypatch.setenv("WH_LOCAL_ALLOWED_ORIGINS", "https://studio.example/, http://10.0.0.9:8080")
    origins = cors_allow_origins()
    assert "https://studio.example" in origins  # 末尾斜杠会被吃掉
    assert "http://10.0.0.9:8080" in origins


def test_same_origin_of() -> None:
    assert same_origin_of("https://a.example/x?y=1") == "https://a.example"
    assert same_origin_of("http://127.0.0.1:5173/#/p") == "http://127.0.0.1:5173"
    assert same_origin_of("not a url") == ""
    assert same_origin_of("") == ""


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "workbench.sqlite3"), base_url="http://127.0.0.1:8010")


def test_host_guard_blocks_foreign_host(tmp_path: Path) -> None:
    client = _client(tmp_path)
    # 正常回环 Host：/themes 是公开接口，应 200
    ok = client.get("/themes")
    assert ok.status_code == 200, ok.text
    # 伪造 Host（DNS rebinding 场景）：403，且说清原因
    blocked = client.get("/themes", headers={"host": "evil.example"})
    assert blocked.status_code == 403
    assert "只接受本机访问" in blocked.json()["detail"]


def test_cors_preflight_only_loopback(tmp_path: Path) -> None:
    client = _client(tmp_path)
    good = client.options(
        "/themes",
        headers={
            "origin": "http://127.0.0.1:5173",
            "access-control-request-method": "GET",
        },
    )
    assert good.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"

    bad = client.options(
        "/themes",
        headers={
            "origin": "https://evil.example",
            "access-control-request-method": "GET",
        },
    )
    assert "access-control-allow-origin" not in bad.headers
