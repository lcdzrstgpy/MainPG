"""T0.1：ClipForge 同源代理与运维默认值接口。

用真实的 loopback HTTP server 当 sidecar，覆盖 JSON、二进制/Range、4xx/5xx 透传、
未就绪 503、非法路径 400 与断开连接 502。
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.modules.clipforge.router import create_router
from wh_local.modules.clipforge.service import ClipForgeStatus

_MEDIA = bytes(range(256)) * 8  # 2048 bytes


class _UpstreamHandler(BaseHTTPRequestHandler):
    """极简 sidecar：/api/health、/api/output/*（支持 Range）、其余回显。"""

    protocol_version = "HTTP/1.1"
    seen: list[dict[str, object]] = []

    def log_message(self, *_args: object) -> None:  # 测试输出保持干净
        pass

    def _record(self, body: bytes = b"") -> None:
        type(self).seen.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": {name.lower(): value for name, value in self.headers.items()},
                "body": body,
            }
        )

    def _send_json(self, status: int, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._record()
        if self.path == "/api/health":
            self._send_json(200, {"service": "clipforge"})
            return
        if self.path.startswith("/api/output/"):
            self._send_media()
            return
        if self.path.startswith("/api/missing"):
            self._send_json(404, {"error": "项目不存在"})
            return
        self._send_json(200, {"path": self.path})

    def _send_media(self) -> None:
        if self.headers.get("Range") == "bytes=0-1023":
            chunk = _MEDIA[:1024]
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("Content-Range", f"bytes 0-1023/{len(_MEDIA)}")
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(_MEDIA)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(_MEDIA)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_POST(self) -> None:  # noqa: N802
        body = self._read_body()
        self._record(body)
        self._send_json(201, {"path": self.path, "body": body.decode("utf-8")})

    do_PUT = do_POST  # noqa: N815
    do_PATCH = do_POST  # noqa: N815

    def do_DELETE(self) -> None:  # noqa: N802
        self._record()
        self._send_json(500, {"error": "删除失败"})

    def do_HEAD(self) -> None:  # noqa: N802
        self._record()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(_MEDIA)))
        self.end_headers()


class FakeService:
    """只实现 router/proxy 需要的两个方法。"""

    def __init__(self, url: str | None = None, *, state: str = "ready", config: Path | None = None) -> None:
        self.url = url
        self.state = state
        self.config = config

    def status(self) -> ClipForgeStatus:
        return ClipForgeStatus(
            state=self.state,  # type: ignore[arg-type]
            build_state="available",
            available=True,
            url=self.url,
            instance_id="instance-1",
            message="",
        )

    def _configured_settings_path(self) -> Path | None:
        return self.config

    def start(self) -> ClipForgeStatus:  # pragma: no cover - 代理不触发启动
        raise AssertionError("proxy must not start the sidecar")


@pytest.fixture
def upstream() -> Iterator[str]:
    _UpstreamHandler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _client(service: FakeService) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(service))  # type: ignore[arg-type]
    return TestClient(app)


def _closed_port_url() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    return f"http://127.0.0.1:{port}"


# --- 转发与透传 -------------------------------------------------------------


def test_proxy_forwards_json_query_and_language(upstream: str) -> None:
    response = _client(FakeService(upstream)).get(
        "/api/clipforge/proxy/api/project",
        params={"status": "active"},
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"path": "/api/project?status=active"}
    seen = _UpstreamHandler.seen[-1]
    assert seen["method"] == "GET"
    assert seen["headers"]["accept-language"] == "zh-CN"  # type: ignore[index]
    assert "authorization" not in seen["headers"]  # type: ignore[operator]


def test_proxy_streams_range_requests_with_passthrough_headers(upstream: str) -> None:
    response = _client(FakeService(upstream)).get(
        "/api/clipforge/proxy/api/output/demo.mp4",
        headers={"Range": "bytes=0-1023"},
    )

    assert response.status_code == 206
    assert response.content == _MEDIA[:1024]
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["content-length"] == "1024"
    assert response.headers["content-range"] == f"bytes 0-1023/{len(_MEDIA)}"
    assert response.headers["accept-ranges"] == "bytes"
    assert _UpstreamHandler.seen[-1]["headers"]["range"] == "bytes=0-1023"  # type: ignore[index]


def test_proxy_forwards_post_body_and_keeps_upstream_status(upstream: str) -> None:
    response = _client(FakeService(upstream)).post(
        "/api/clipforge/proxy/api/project",
        json={"topic": "夏季连衣裙"},
    )

    assert response.status_code == 201
    assert response.json()["path"] == "/api/project"
    assert json.loads(str(response.json()["body"])) == {"topic": "夏季连衣裙"}


def test_proxy_supports_head(upstream: str) -> None:
    response = _client(FakeService(upstream)).head("/api/clipforge/proxy/api/output/demo.mp4")

    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(_MEDIA))
    assert response.content == b""


def test_proxy_passes_upstream_errors_through_unchanged(upstream: str) -> None:
    client = _client(FakeService(upstream))

    missing = client.get("/api/clipforge/proxy/api/missing")
    failing = client.delete("/api/clipforge/proxy/api/project/1")

    assert missing.status_code == 404
    assert missing.json() == {"error": "项目不存在"}
    assert failing.status_code == 500
    assert failing.json() == {"error": "删除失败"}


# --- 未就绪 / 非法路径 / 上游断开 -------------------------------------------


@pytest.mark.parametrize(("state", "url"), [("stopped", None), ("starting", None), ("failed", None)])
def test_proxy_returns_503_until_the_sidecar_is_ready(state: str, url: str | None) -> None:
    response = _client(FakeService(url, state=state)).get("/api/clipforge/proxy/api/health")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {"code": "CLIPFORGE_NOT_READY", "message": "AI 视频服务未就绪"}
    }


@pytest.mark.parametrize("path", ["/api/clipforge/proxy/project", "/api/clipforge/proxy/api/..%2Fsecret"])
def test_proxy_rejects_paths_outside_the_sidecar_api(path: str) -> None:
    response = _client(FakeService(_closed_port_url())).get(path)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "CLIPFORGE_PROXY_PATH_INVALID"


def test_proxy_refuses_a_non_loopback_sidecar_url() -> None:
    response = _client(FakeService("http://10.0.0.8:51234")).get("/api/clipforge/proxy/api/health")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CLIPFORGE_NOT_READY"


def test_proxy_maps_a_dead_upstream_to_502() -> None:
    response = _client(FakeService(_closed_port_url())).get("/api/clipforge/proxy/api/health")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "CLIPFORGE_UPSTREAM_FAILED"


# --- 运维默认值 -------------------------------------------------------------


def test_defaults_returns_only_the_consumed_keys(tmp_path: Path) -> None:
    config = tmp_path / "clipforge.local.json"
    config.write_text(
        json.dumps(
            {
                "providers": {"suchuang": {"enabled": True, "apiKey": "k"}},
                "llm": {"provider": "DeepSeek", "apiKey": "sk-x"},
                "defaultImageModel": "image_gpt",
                "defaultVideoModel": "video_minimax_h3",
                "unrelated": "ignored",
            }
        ),
        encoding="utf-8",
    )

    response = _client(FakeService(config=config)).get("/api/clipforge/defaults")

    assert response.status_code == 200
    assert response.json() == {
        "providers": {"suchuang": {"enabled": True, "apiKey": "k"}},
        "llm": {"provider": "DeepSeek", "apiKey": "sk-x"},
        "defaultImageModel": "image_gpt",
        "defaultVideoModel": "video_minimax_h3",
    }


@pytest.mark.parametrize("content", [None, "{not json", "[1, 2, 3]"])
def test_defaults_is_empty_when_the_file_is_missing_or_unreadable(
    tmp_path: Path, content: str | None
) -> None:
    config = None
    if content is not None:
        config = tmp_path / "clipforge.local.json"
        config.write_text(content, encoding="utf-8")

    response = _client(FakeService(config=config)).get("/api/clipforge/defaults")

    assert response.status_code == 200
    assert response.json() == {}
