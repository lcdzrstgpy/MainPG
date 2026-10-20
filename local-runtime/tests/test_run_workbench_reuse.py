"""run_workbench：端口已被「本程序」占用时应当复用，而不是杀掉占用者。

背景（2026-10-16）：旧逻辑只看「端口被占」就弹窗让用户 taskkill 掉占用者，于是
「再启动一次」变成「把正在用的那个杀掉」——前一个页面的请求全部失败、立刻弹
「本地服务掉线了」。真正的残留进程早已退出、端口会自动释放，轮不到这里杀。
新逻辑：先探测端口上是否已有本程序后端在正常应答 /health，是则复用（打开页面指向
它，本实例直接退出，不启后端、不杀进程）。
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import run_workbench


def _fake_open(opened: list[str]):
    """替身：记录 URL 并返回 True —— 必须返回真值，复用路径会检查打开是否成功。"""

    def _open(url: str) -> bool:
        opened.append(url)
        return True

    return _open


@contextlib.contextmanager
def _serve(payload: object, *, raw: str | None = None):
    """起一个只回 /health 的临时服务，yield 出它的端口号。"""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = (raw if raw is not None else json.dumps(payload)).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def test_probe_returns_database_path_for_running_workbench() -> None:
    """返回 (数据目录, 版本号)：版本号用来识别「端口上跑的是旧版本」。"""
    with _serve(
        {"ok": True, "database_path": "D:/data/workbench.sqlite3", "app_version": "1.5.7"}
    ) as port:
        assert run_workbench._probe_running_workbench("127.0.0.1", port) == (
            "D:/data/workbench.sqlite3",
            "1.5.7",
        )


def test_probe_returns_empty_string_when_database_path_missing() -> None:
    with _serve({"ok": True}) as port:
        assert run_workbench._probe_running_workbench("127.0.0.1", port) == ("", "")


def test_probe_returns_none_for_foreign_service() -> None:
    with _serve({"hello": "world"}) as port:
        assert run_workbench._probe_running_workbench("127.0.0.1", port) is None
    with _serve({"ok": False}) as port:
        assert run_workbench._probe_running_workbench("127.0.0.1", port) is None


def test_probe_returns_none_for_non_json_response() -> None:
    with _serve(None, raw="<html>not the workbench</html>") as port:
        assert run_workbench._probe_running_workbench("127.0.0.1", port) is None


def test_probe_returns_none_when_nothing_listens() -> None:
    with _serve({"ok": True}) as port:
        pass  # 服务已关闭，端口不再监听
    assert run_workbench._probe_running_workbench("127.0.0.1", port) is None


def test_same_path_ignores_case_and_separators() -> None:
    assert run_workbench._same_path(r"D:\Data\workbench.sqlite3", "d:/data/workbench.sqlite3")
    assert not run_workbench._same_path(r"D:\Data\a.sqlite3", r"D:\Data\b.sqlite3")


def test_reuse_opens_page_and_skips_notice_for_same_data_dir(monkeypatch) -> None:
    opened: list[str] = []
    notices: list[str] = []
    monkeypatch.setattr(run_workbench.webbrowser, "open", _fake_open(opened))
    monkeypatch.setattr(run_workbench, "_show_notice_box", notices.append)
    monkeypatch.setattr(run_workbench, "_expected_database_path", lambda: r"D:\Data\wh.sqlite3")

    class _Args:
        host = "127.0.0.1"
        port = 8010
        no_browser = False

    run_workbench._reuse_running_workbench(_Args(), r"D:\Data\wh.sqlite3")

    assert notices == []
    assert len(opened) == 1
    assert opened[0].startswith("http://127.0.0.1:8010/")


def test_reuse_warns_when_data_dir_differs(monkeypatch) -> None:
    opened: list[str] = []
    notices: list[str] = []
    monkeypatch.setattr(run_workbench.webbrowser, "open", _fake_open(opened))
    monkeypatch.setattr(run_workbench, "_show_notice_box", notices.append)
    monkeypatch.setattr(run_workbench, "_expected_database_path", lambda: r"D:\Mine\wh.sqlite3")

    class _Args:
        host = "127.0.0.1"
        port = 8010
        no_browser = False

    run_workbench._reuse_running_workbench(_Args(), r"D:\Other\wh.sqlite3")

    assert len(notices) == 1
    assert "另一套数据目录" in notices[0]
    assert r"D:\Other" in notices[0] and r"D:\Mine" in notices[0]
    assert len(opened) == 1


def test_reuse_respects_no_browser(monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(run_workbench.webbrowser, "open", _fake_open(opened))
    monkeypatch.setattr(run_workbench, "_show_notice_box", lambda message: None)
    monkeypatch.setattr(run_workbench, "_expected_database_path", lambda: None)

    class _Args:
        host = "127.0.0.1"
        port = 8010
        no_browser = True

    run_workbench._reuse_running_workbench(_Args(), "")

    assert opened == []


def _patch_main_common(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(run_workbench, "ensure_url_protocol", lambda: False)
    monkeypatch.setattr(run_workbench, "_configure_runtime_logging", lambda: tmp_path / "runtime.log")
    monkeypatch.setattr(run_workbench, "_show_fatal_box", lambda message: None)
    monkeypatch.setattr(run_workbench, "_show_notice_box", lambda message: None)
    monkeypatch.setattr(sys, "argv", ["run_workbench.py", "--no-browser"])


def test_main_reuses_running_instance_without_releasing_port(monkeypatch, tmp_path) -> None:
    """端口上有本程序在服务 → 复用；绝不调用释放/杀进程，也不启动第二个后端。"""
    calls = {"release": 0, "bind": 0, "reuse": 0}
    _patch_main_common(monkeypatch, tmp_path)
    monkeypatch.setattr(run_workbench, "_port_in_use", lambda host, port: True)
    monkeypatch.setattr(
        run_workbench,
        "_probe_running_workbench",
        lambda host, port: ("D:/data/workbench.sqlite3", "1.5.6"),
    )
    monkeypatch.setattr(
        run_workbench,
        "_release_default_port",
        lambda host, port: calls.__setitem__("release", calls["release"] + 1),
    )
    monkeypatch.setattr(
        run_workbench,
        "_bind_server_socket",
        lambda host, port: calls.__setitem__("bind", calls["bind"] + 1),
    )
    monkeypatch.setattr(
        run_workbench,
        "_reuse_running_workbench",
        lambda args, db, version="": calls.__setitem__("reuse", calls["reuse"] + 1),
    )

    assert run_workbench.main() == 0
    assert calls == {"release": 0, "bind": 0, "reuse": 1}


def test_main_never_kills_a_foreign_port_holder(monkeypatch, tmp_path) -> None:
    """端口被别的程序占着 → 不杀它，也不弹窗，直接顺延端口。"""
    calls = {"kill": 0, "reuse": 0}
    _patch_main_common(monkeypatch, tmp_path)
    monkeypatch.setattr(run_workbench, "_port_in_use", lambda host, port: True)
    monkeypatch.setattr(run_workbench, "_probe_running_workbench", lambda host, port: None)
    monkeypatch.setattr(run_workbench, "_port_owners", lambda port: [4242])
    monkeypatch.setattr(run_workbench, "_process_name", lambda pid: "chrome.exe")
    monkeypatch.setattr(
        run_workbench, "_kill_pid", lambda pid: calls.__setitem__("kill", calls["kill"] + 1)
    )
    monkeypatch.setattr(
        run_workbench, "_resolve_port_conflict", lambda host, port, card: False
    )
    monkeypatch.setattr(
        run_workbench,
        "_reuse_running_workbench",
        lambda args, db, version="": calls.__setitem__("reuse", calls["reuse"] + 1),
    )

    def _no_port(host: str, port: int):
        raise OSError("no free port available in range")

    monkeypatch.setattr(run_workbench, "_bind_server_socket", _no_port)

    assert run_workbench.main() == 1
    assert calls == {"kill": 0, "reuse": 0}


def test_reuse_notifies_when_browser_open_fails(monkeypatch) -> None:
    """打开浏览器失败时不能静默退出：否则用户双击后毫无反应、以为程序坏了。"""
    notices: list[str] = []
    monkeypatch.setattr(run_workbench.webbrowser, "open", lambda url: False)
    monkeypatch.setattr(run_workbench, "_show_notice_box", notices.append)
    monkeypatch.setattr(run_workbench, "_expected_database_path", lambda: None)

    class _Args:
        host = "127.0.0.1"
        port = 8010
        no_browser = False

    run_workbench._reuse_running_workbench(_Args(), "")

    assert len(notices) == 1
    assert "手动在浏览器打开" in notices[0]
    assert "http://127.0.0.1:8010/" in notices[0]


def test_workbench_url_carries_a_fresh_nonce_each_launch() -> None:
    """启动 URL 必须每次都不一样。

    webbrowser.open 对「已打开的同 URL 标签」只会切到前台、不重新加载 —— 用户
    「叉掉浏览器再点启动」看到关闭前那一屏（含停在掉线遮罩上的冻结页面）就是这个
    原因（2026-10-19 用户反馈）。nonce 保证每次都是新导航。
    """
    import time as _time

    first = run_workbench._workbench_url("127.0.0.1", 8010, "1.5.7")
    _time.sleep(0.002)
    second = run_workbench._workbench_url("127.0.0.1", 8010, "1.5.7")

    assert first.startswith("http://127.0.0.1:8010/?app_version=1.5.7")
    assert "&t=" in first, "缺少一次性 nonce 会导致浏览器复用旧标签"
    assert first != second, "两次启动的 URL 必须不同"
