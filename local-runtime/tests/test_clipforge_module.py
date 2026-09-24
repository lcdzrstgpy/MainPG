import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.app.main import _clipforge_node_binary, _clipforge_source_root
from wh_local.modules.clipforge import service as service_module
from wh_local.modules.clipforge.router import create_router
from wh_local.modules.clipforge.service import (
    ClipForgeService,
    _bundled_media_environment,
    _ensure_standalone_static_assets,
    _wait_ready,
)


def test_status_is_unavailable_when_standalone_entry_has_not_been_built(tmp_path: Path) -> None:
    service = ClipForgeService(
        source_root=tmp_path / "clipforge",
        data_root=tmp_path / "runtime-data",
        node_binary="node",
    )

    status = service.status()

    assert status.available is False
    assert status.state == "unavailable"
    assert status.url is None
    assert "standalone" in status.message


def test_status_route_exposes_only_loopback_service_state(tmp_path: Path) -> None:
    service = ClipForgeService(tmp_path / "clipforge", tmp_path / "runtime-data")
    app = FastAPI()
    app.include_router(create_router(service))

    response = TestClient(app).get("/api/clipforge/status")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "state": "unavailable",
        "url": None,
        "message": "ClipForge standalone build is not available",
    }


def test_packaged_clipforge_assets_take_precedence_over_vendored_source(tmp_path: Path, monkeypatch) -> None:
    bundled = tmp_path / "clipforge" / "app"
    bundled.mkdir(parents=True)
    (tmp_path / "clipforge" / "node.exe").touch()
    monkeypatch.delenv("WH_CLIPFORGE_SOURCE_ROOT", raising=False)

    assert _clipforge_source_root(tmp_path) == bundled
    assert _clipforge_node_binary(bundled) == tmp_path / "clipforge" / "node.exe"


def test_service_resolves_a_relative_source_root_before_starting_a_child(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "clipforge"
    (source / ".next" / "standalone").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    service = ClipForgeService(Path("clipforge"), tmp_path / "runtime-data")

    assert service.standalone_entry == source / ".next" / "standalone" / "server.js"


def test_service_copies_next_static_assets_into_the_standalone_server(tmp_path: Path) -> None:
    source = tmp_path / "clipforge"
    css = source / ".next" / "static" / "chunks" / "app.css"
    css.parent.mkdir(parents=True)
    css.write_text("body { color: rebeccapurple; }")

    _ensure_standalone_static_assets(source)

    copied_css = source / ".next" / "standalone" / ".next" / "static" / "chunks" / "app.css"
    assert copied_css.read_text() == "body { color: rebeccapurple; }"


def test_readiness_accepts_an_http_400_response_from_next(monkeypatch) -> None:
    class RunningProcess:
        returncode = None

        def poll(self):
            return None

    def response_with_bad_request(*_args, **_kwargs):
        raise HTTPError("http://127.0.0.1:8010", 400, "Bad Request", None, None)

    monkeypatch.setattr(service_module, "urlopen", response_with_bad_request)
    clock = iter((0.0, 0.0, 31.0))
    monkeypatch.setattr(service_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(service_module.time, "sleep", lambda _seconds: None)

    _wait_ready("http://127.0.0.1:8010", RunningProcess())


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows 文件系统不记录 POSIX 执行位（chmod 0o111 是 no-op），此断言只在 Linux/macOS 有意义",
)
def test_bundled_media_environment_makes_macos_ffprobe_executable(tmp_path: Path, monkeypatch) -> None:
    ffprobe = tmp_path / "node_modules" / "@ffprobe-installer" / "darwin-arm64" / "ffprobe"
    ffprobe.parent.mkdir(parents=True)
    ffprobe.write_bytes(b"fixture")
    ffprobe.chmod(0o644)
    monkeypatch.setattr(service_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(service_module.platform, "machine", lambda: "arm64")

    environment = _bundled_media_environment(tmp_path)

    assert environment["FFPROBE_PATH"] == str(ffprobe)
    assert ffprobe.stat().st_mode & 0o111 == 0o111
