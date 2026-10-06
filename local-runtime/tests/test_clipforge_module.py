from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.app.main import _clipforge_build_resolver, _clipforge_node_binary
from wh_local.modules.clipforge import service as service_module
from wh_local.modules.clipforge.artifact import ClipForgeBuild, ClipForgeBuildState
from wh_local.modules.clipforge import health as health_module
from wh_local.modules.clipforge.health import ClipForgeHealthError
from wh_local.modules.clipforge.router import create_router
from wh_local.modules.clipforge.service import (
    ClipForgeService,
    _MAX_AUTO_RESTARTS,
    _bundled_media_environment,
    _node_style_platform,
    _resolve_ffprobe_binary,
)


class FakeProcess:
    """Minimal subprocess stand-in: no real child, deterministic exit control."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._exited = threading.Event()

    def poll(self) -> int | None:
        return self.returncode

    def exit(self, code: int = 0) -> None:
        self.returncode = code
        self._exited.set()

    def wait(self, timeout: float | None = None) -> int:
        if not self._exited.wait(timeout):
            raise subprocess.TimeoutExpired("node", timeout or 0)
        return int(self.returncode or 0)

    def terminate(self) -> None:  # pragma: no cover - parity with Popen
        self.exit(0)

    def kill(self) -> None:  # pragma: no cover - parity with Popen
        self.exit(-9)


class FakeProcessFactory:
    """Records every spawn instead of starting a real Node process."""

    def __init__(self) -> None:
        self.call_count = 0
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.created: list[FakeProcess] = []

    def __call__(self, args: list[str], **kwargs: Any) -> Any:
        self.call_count += 1
        self.calls.append((list(args), kwargs))
        process = FakeProcess(pid=5000 + self.call_count)
        self.created.append(process)
        return process


class ProbeControl:
    """A ready probe that blocks until the test releases it."""

    def __init__(self) -> None:
        self.probe_entered = threading.Event()
        self.probe_release = threading.Event()
        self.calls: list[tuple[str, str]] = []
        self.error: Exception | None = None

    def __call__(self, base_url: str, process: Any, instance_id: str, timeout_s: float = 30.0) -> None:
        self.calls.append((base_url, instance_id))
        self.probe_entered.set()
        if self.error is not None:
            raise self.error
        self.probe_release.wait(5)


class ServiceFactory:
    """Builds services whose child process and readiness probe are fully faked."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path

    def build(self, state: ClipForgeBuildState = "available", artifact_id: str = "artifact-a") -> ClipForgeBuild:
        if state != "available":
            return ClipForgeBuild(state=state, app_root=None, artifact_id=None, message="产物不可用")
        app_root = self.tmp_path / f"app-{artifact_id}"
        app_root.mkdir(parents=True, exist_ok=True)
        return ClipForgeBuild(state="available", app_root=app_root, artifact_id=artifact_id, message="产物已就绪")

    def make(
        self,
        build: ClipForgeBuild | None = None,
        *,
        probe: Any = None,
        process_factory: FakeProcessFactory | None = None,
    ) -> ClipForgeService:
        return ClipForgeService(
            build_resolver=lambda: build if build is not None else self.build(),
            data_root=self.tmp_path / "data",
            node_binary="node",
            process_factory=process_factory or FakeProcessFactory(),
            ready_probe=probe or (lambda *_args, **_kwargs: None),
        )

    def blocked_start(self) -> tuple[ClipForgeService, FakeProcessFactory]:
        popen = FakeProcessFactory()
        return self.make(probe=ProbeControl(), process_factory=popen), popen

    def with_probe_error(self, error: Exception) -> ClipForgeService:
        probe = ProbeControl()
        probe.error = error
        return self.make(probe=probe)

    def two_generation_service(self) -> tuple[ClipForgeService, ProbeControl, ProbeControl]:
        first = ProbeControl()
        second = ProbeControl()
        pending = [first, second]
        lock = threading.Lock()

        def probe(base_url: str, process: Any, instance_id: str, timeout_s: float = 30.0) -> None:
            with lock:
                control = pending.pop(0) if pending else second
            control(base_url, process, instance_id, timeout_s)

        return self.make(probe=probe), first, second


@pytest.fixture
def service_factory(tmp_path: Path) -> ServiceFactory:
    return ServiceFactory(tmp_path)


@pytest.fixture(autouse=True)
def terminated_processes(monkeypatch) -> list[Any]:
    """Fake children never react to signals, so record tree termination instead of waiting."""
    terminated: list[Any] = []
    monkeypatch.setattr(
        service_module,
        "terminate_process_tree",
        lambda process, *a, **k: terminated.append(process),
    )
    return terminated


def wait_for_state(service: ClipForgeService, state: str, timeout_s: float = 5.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        current = service.status()
        if current.state == state:
            return current
        time.sleep(0.01)
    raise AssertionError(f"state {state!r} not reached, last={service.status()}")


def wait_until(predicate, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition not reached in time")


# --- 状态机 -----------------------------------------------------------------


def test_start_returns_starting_without_waiting_for_probe(service_factory: ServiceFactory) -> None:
    probe = ProbeControl()
    service = service_factory.make(probe=probe)

    status = service.start()

    assert status.state == "starting"
    assert status.build_state == "available"
    assert status.available is True
    assert status.instance_id
    assert probe.probe_entered.wait(1)
    probe.probe_release.set()


def test_repeated_start_is_idempotent(service_factory: ServiceFactory) -> None:
    service, popen = service_factory.blocked_start()
    first = service.start()
    wait_until(lambda: popen.call_count == 1)
    second = service.start()

    assert second.instance_id == first.instance_id
    assert second.state == "starting"
    assert popen.call_count == 1


def test_start_reaches_ready_and_exposes_url_and_instance(service_factory: ServiceFactory) -> None:
    service = service_factory.make()

    service.start()
    ready = wait_for_state(service, "ready")

    assert ready.url is not None
    assert ready.url.startswith("http://127.0.0.1:")
    assert ready.instance_id
    assert ready.error is None
    assert ready.available is True


def test_invalid_build_is_unavailable_and_never_spawns(service_factory: ServiceFactory) -> None:
    popen = FakeProcessFactory()
    service = service_factory.make(service_factory.build("invalid"), process_factory=popen)

    status = service.start()

    assert status.state == "unavailable"
    assert status.build_state == "invalid"
    assert status.available is False
    assert status.instance_id is None
    assert status.error is not None
    assert popen.call_count == 0
    assert service.status().state == "unavailable"


def test_missing_build_is_unavailable(service_factory: ServiceFactory) -> None:
    service = service_factory.make(service_factory.build("missing"))

    assert service.status().state == "unavailable"
    assert service.status().build_state == "missing"


def test_failed_state_persists_across_status_reads(service_factory: ServiceFactory) -> None:
    service = service_factory.with_probe_error(ClipForgeHealthError("bad health"))

    service.start()
    failed = wait_for_state(service, "failed")
    assert failed.error is not None
    diagnostic = failed.error.diagnostic_id
    assert diagnostic

    current = service.status()
    assert current.state == "failed"
    assert current.error is not None
    assert current.error.diagnostic_id == diagnostic
    assert current.error.code == "CLIPFORGE_HEALTHCHECK_FAILED"
    assert current.error.retryable is True


def test_old_generation_cannot_overwrite_restarted_instance(service_factory: ServiceFactory) -> None:
    service, generation_one, generation_two = service_factory.two_generation_service()
    first = service.start()
    assert generation_one.probe_entered.wait(1)

    service.stop()
    second = service.start()
    generation_two.probe_release.set()
    ready = wait_for_state(service, "ready")
    assert ready.instance_id == second.instance_id
    assert ready.instance_id != first.instance_id

    generation_one.probe_release.set()
    assert service.status().instance_id == second.instance_id
    assert service.status().state == "ready"


def test_process_exit_after_ready_is_reported_as_failed(service_factory: ServiceFactory) -> None:
    popen = FakeProcessFactory()
    service = service_factory.make(process_factory=popen)
    service.start()
    wait_for_state(service, "ready")

    popen.created[0].exit(3)

    failed = wait_for_state(service, "failed")
    assert failed.error is not None
    assert failed.error.code == "CLIPFORGE_PROCESS_EXITED"
    assert failed.error.exit_code == 3
    assert failed.error.diagnostic_id
    # 诊断编号必须持续保留，重复读取状态不得把它清掉。
    current = service.status()
    assert current.state == "failed"
    assert current.error is not None
    assert current.error.diagnostic_id == failed.error.diagnostic_id


def test_stop_terminates_the_whole_process_tree(
    service_factory: ServiceFactory, terminated_processes: list[Any]
) -> None:
    popen = FakeProcessFactory()
    service = service_factory.make(process_factory=popen)
    service.start()
    wait_for_state(service, "ready")

    service.stop()

    assert terminated_processes == [popen.created[0]]
    assert service.status().state == "stopped"
    assert service.status().url is None


def test_stop_is_idempotent_when_never_started(service_factory: ServiceFactory) -> None:
    service = service_factory.make()

    service.stop()
    service.stop()

    assert service.status().state == "stopped"


def test_each_start_uses_its_own_log_and_keeps_only_ten(service_factory: ServiceFactory) -> None:
    service = service_factory.make()

    for _ in range(12):
        service.start()
        wait_for_state(service, "ready")
        service.stop()

    logs = sorted((service_factory.tmp_path / "data" / "logs").glob("*.log"))
    assert len(logs) == 10


def test_worker_spawns_node_server_js_in_the_app_root(service_factory: ServiceFactory) -> None:
    popen = FakeProcessFactory()
    build = service_factory.build()
    service = service_factory.make(build, process_factory=popen)

    service.start()
    wait_for_state(service, "ready")

    args, kwargs = popen.calls[0]
    app_root = build.app_root
    assert app_root is not None
    assert args == ["node", str(app_root / "server.js")]
    assert kwargs["cwd"] == str(app_root)
    environment = kwargs["env"]
    for name in (
        "NODE_ENV",
        "HOSTNAME",
        "PORT",
        "APP_DATA_DIR",
        "APP_MIGRATIONS_DIR",
        "MAINPG_CLIPFORGE_INSTANCE_ID",
    ):
        assert name in environment
    assert environment["HOSTNAME"] == "127.0.0.1"
    assert environment["NODE_ENV"] == "production"
    assert environment["APP_MIGRATIONS_DIR"] == str(app_root / "drizzle")
    assert environment["APP_DATA_DIR"] == str(service_factory.tmp_path / "data" / "data")
    # 子进程必须自成进程组，停止时才能连同 Node/FFmpeg 后代一起回收。
    if os.name == "nt":
        assert kwargs.get("creationflags")
    else:
        assert kwargs.get("start_new_session") is True


# --- 路由 -------------------------------------------------------------------


def _client(service: ClipForgeService) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(service))
    return TestClient(app)


def test_status_route_always_returns_200_with_the_build_state(service_factory: ServiceFactory) -> None:
    response = _client(service_factory.make(service_factory.build("missing"))).get("/api/clipforge/status")

    assert response.status_code == 200
    assert response.json() == {
        "state": "unavailable",
        "buildState": "missing",
        "available": False,
        "url": None,
        "instanceId": None,
        "message": "产物不可用",
        "error": None,
    }


def test_start_route_returns_202_while_starting(service_factory: ServiceFactory) -> None:
    service = service_factory.make(probe=ProbeControl())

    response = _client(service).post("/api/clipforge/start")

    assert response.status_code == 202
    assert response.json()["state"] == "starting"


def test_start_route_returns_200_when_already_ready(service_factory: ServiceFactory) -> None:
    service = service_factory.make()
    service.start()
    wait_for_state(service, "ready")

    response = _client(service).post("/api/clipforge/start")

    assert response.status_code == 200
    assert response.json()["state"] == "ready"


def test_start_route_returns_409_for_an_unavailable_build(service_factory: ServiceFactory) -> None:
    service = service_factory.make(service_factory.build("invalid"))

    response = _client(service).post("/api/clipforge/start")

    assert response.status_code == 409
    body = response.json()
    assert body["state"] == "unavailable"
    assert body["buildState"] == "invalid"
    assert body["error"]["code"] == "CLIPFORGE_BUILD_UNAVAILABLE"
    assert body["error"]["retryable"] is False


def test_start_route_returns_500_with_a_stable_error_without_leaking_details(
    service_factory: ServiceFactory, monkeypatch
) -> None:
    service = service_factory.make()

    def explode() -> None:
        raise RuntimeError("secret internal detail /Users/local/path")

    monkeypatch.setattr(service, "start", explode)

    response = _client(service).post("/api/clipforge/start")

    assert response.status_code == 500
    assert "secret internal detail" not in response.text
    assert "/Users/local/path" not in response.text
    assert response.json()["error"]["code"] == "CLIPFORGE_START_FAILED"


# --- 环境与路径映射 ---------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "platform_dir", "suffix"),
    [
        ("Darwin", "darwin-arm64", ""),
        ("Linux", "linux-x64", ""),
        ("Windows", "win32-x64", ".exe"),
    ],
)
def test_bundled_media_environment_points_at_artifact_binaries(
    tmp_path: Path, system: str, platform_dir: str, suffix: str
) -> None:
    """@ffprobe-installer 的目录名由枚举安装现场得到，不再靠 platform.machine() 猜。"""
    app_root = tmp_path / f"{system}-{platform_dir}"
    ffmpeg = app_root / "node_modules" / "ffmpeg-static" / f"ffmpeg{suffix}"
    ffprobe = app_root / "node_modules" / "@ffprobe-installer" / platform_dir / f"ffprobe{suffix}"
    ffmpeg.parent.mkdir(parents=True, exist_ok=True)
    ffprobe.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.write_bytes(b"fixture")
    ffprobe.write_bytes(b"fixture")
    ffprobe.chmod(0o644)

    environment = _bundled_media_environment(app_root, system)

    assert environment["FFMPEG_PATH"] == str(ffmpeg)
    assert environment["FFPROBE_PATH"] == str(ffprobe)
    # Windows 文件系统不记录 POSIX 执行位（chmod 0o111 是 no-op），
    # 所以这条断言只在真正跑在 Linux/macOS 上时成立。
    if system != "Windows" and sys.platform != "win32":
        # 从包仓恢复的 ffprobe 常常缺失执行位，导出的路径必须已经被 chmod 过。
        assert ffprobe.stat().st_mode & 0o111 == 0o111


def test_ffprobe_resolution_prefers_the_node_style_platform_directory(tmp_path: Path) -> None:
    installer = tmp_path / "node_modules" / "@ffprobe-installer"
    wanted = _node_style_platform()
    other = "linux-x64" if wanted != "linux-x64" else "win32-x64"
    (installer / other).mkdir(parents=True)
    (installer / other / "ffprobe").write_bytes(b"fixture")
    (installer / wanted).mkdir(parents=True)
    preferred = installer / wanted / "ffprobe"
    preferred.write_bytes(b"fixture")

    assert _resolve_ffprobe_binary(tmp_path, "") == preferred


def test_ffprobe_resolution_falls_back_and_reports_missing(tmp_path: Path) -> None:
    only = tmp_path / "node_modules" / "@ffprobe-installer" / "linux-arm64"
    only.mkdir(parents=True)
    binary = only / "ffprobe"
    binary.write_bytes(b"fixture")

    assert _resolve_ffprobe_binary(tmp_path, "") == binary
    assert _resolve_ffprobe_binary(tmp_path / "missing-app", "") is None


def test_bundled_media_environment_is_empty_without_media_modules(tmp_path: Path) -> None:
    assert _bundled_media_environment(tmp_path, "Darwin", "arm64") == {}


# --- main.py 接线 -----------------------------------------------------------


def test_packaged_clipforge_node_binary_takes_precedence(tmp_path: Path) -> None:
    bundled = tmp_path / "clipforge"
    bundled.mkdir(parents=True)
    (bundled / "node.exe").touch()

    assert _clipforge_node_binary(tmp_path) == bundled / "node.exe"


def test_clipforge_node_binary_falls_back_to_path(tmp_path: Path) -> None:
    assert _clipforge_node_binary(tmp_path) == "node"


def test_clipforge_build_resolver_reports_a_missing_build(tmp_path: Path) -> None:
    build = _clipforge_build_resolver(tmp_path, tmp_path / "data")()

    assert build.state == "missing"
    assert build.app_root is None


# --- 崩溃自动重启 -----------------------------------------------------------


def test_unexpected_exit_schedules_a_bounded_auto_restart(
    service_factory: ServiceFactory, monkeypatch
) -> None:
    """崩溃后必须排上有限次自动重启，并把计划同时写进状态文案与错误体。"""
    monkeypatch.setattr(service_module, "_RESTART_BACKOFF_SECONDS", (30.0, 30.0))
    factory = FakeProcessFactory()
    service = service_factory.make(process_factory=factory)
    service.start()
    wait_for_state(service, "ready")

    factory.created[0].exit(3)
    wait_until(lambda: "自动重启" in service.status().message)

    status = service.status()
    assert status.state == "failed"
    assert "第 1/2 次" in status.message
    assert status.error is not None and status.error.exit_code == 3
    assert status.error.message == status.message
    service.stop()


def test_auto_restart_actually_relaunches_the_sidecar(
    service_factory: ServiceFactory, monkeypatch
) -> None:
    monkeypatch.setattr(service_module, "_RESTART_BACKOFF_SECONDS", (0.01, 0.01))
    factory = FakeProcessFactory()
    service = service_factory.make(process_factory=factory)
    service.start()
    wait_for_state(service, "ready")

    factory.created[0].exit(1)
    wait_until(lambda: factory.call_count == 2)
    wait_for_state(service, "ready")

    assert service._restarts == 1
    service.stop()


def test_auto_restart_budget_stops_at_the_cap(service_factory: ServiceFactory, monkeypatch) -> None:
    """预算耗尽后停在 failed，不再排新计时器。"""
    monkeypatch.setattr(service_module, "_RESTART_BACKOFF_SECONDS", (30.0, 30.0))
    service = service_factory.make()
    service.status()

    with service._lock:
        for _ in range(_MAX_AUTO_RESTARTS + 1):
            service._cancel_restart_timer_locked()
            service._schedule_restart_locked()

    assert service._restarts == _MAX_AUTO_RESTARTS
    assert service._restart_timer is None
    assert service.status().message.endswith(f"（已自动重启 {_MAX_AUTO_RESTARTS} 次，仍失败）")


def test_manual_start_resets_the_auto_restart_budget(service_factory: ServiceFactory, monkeypatch) -> None:
    monkeypatch.setattr(service_module, "_RESTART_BACKOFF_SECONDS", (30.0, 30.0))
    service = service_factory.make()
    service.status()
    with service._lock:
        service._restarts = _MAX_AUTO_RESTARTS

    service.start()
    wait_for_state(service, "ready")

    assert service._restarts == 0
    service.stop()


def test_stop_cancels_a_pending_auto_restart(service_factory: ServiceFactory, monkeypatch) -> None:
    monkeypatch.setattr(service_module, "_RESTART_BACKOFF_SECONDS", (30.0, 30.0))
    service = service_factory.make()
    service.status()
    with service._lock:
        service._mark_failed_locked("CLIPFORGE_PROCESS_EXITED", "AI 视频服务进程已退出", 1)
        service._schedule_restart_locked()
    assert service._restart_timer is not None

    service.stop()

    assert service._restart_timer is None
    assert service.status().state == "stopped"


# --- 生命周期卫生：孤儿回收 / 父亡子亡 --------------------------------------


def test_orphan_sidecar_from_a_previous_crash_is_reaped(
    service_factory: ServiceFactory, monkeypatch
) -> None:
    service = service_factory.make()
    pid_file = service._pid_file
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("31337", encoding="ascii")
    killed: list[int] = []
    monkeypatch.setattr(service_module, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(service_module, "_terminate_pid", lambda pid: killed.append(pid))

    service._reap_orphan_child()

    assert killed == [31337]
    assert not pid_file.exists()


def test_stale_pid_file_is_dropped_without_killing_anything(
    service_factory: ServiceFactory, monkeypatch
) -> None:
    service = service_factory.make()
    pid_file = service._pid_file
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text("31337", encoding="ascii")
    killed: list[int] = []
    monkeypatch.setattr(service_module, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(service_module, "_terminate_pid", lambda pid: killed.append(pid))

    service._reap_orphan_child()

    assert killed == []
    assert not pid_file.exists()


def test_spawn_writes_the_sidecar_pid_and_stop_clears_it(service_factory: ServiceFactory) -> None:
    factory = FakeProcessFactory()
    service = service_factory.make(process_factory=factory)

    service.start()
    wait_for_state(service, "ready")

    assert service._pid_file.read_text(encoding="ascii").strip() == str(factory.created[0].pid)

    service.stop()

    assert not service._pid_file.exists()


def test_parent_death_guard_is_disabled_off_windows(
    service_factory: ServiceFactory, monkeypatch
) -> None:
    """非 Windows 平台不启用 Job Object；任何失败都降级为不启用，不影响启动。"""
    monkeypatch.setattr(service_module.platform, "system", lambda: "Darwin")
    service = service_factory.make()

    service._kill_when_parent_exits(FakeProcess())

    assert service._job is None


# --- 日志卫生 ---------------------------------------------------------------


def test_current_log_rotates_once_it_exceeds_the_size_cap(
    service_factory: ServiceFactory, monkeypatch
) -> None:
    monkeypatch.setattr(service_module, "_LOG_MAX_BYTES", 16)
    service = service_factory.make()
    log_path = service._open_log("diag-1")
    log_path.write_bytes(b"x" * 32)

    service._open_log("diag-1")

    rotated = log_path.with_name(f"{log_path.name}.1")
    assert rotated.read_bytes() == b"x" * 32
    assert log_path.exists() and log_path.stat().st_size == 0


# --- 就绪探测 ---------------------------------------------------------------


def test_readiness_probe_ignores_the_proxy_environment(monkeypatch) -> None:
    """HTTP_PROXY 存在时，127.0.0.1 的探测也必须直连。

    否则代理会把本地探测拒掉 → 探针超时 → start() 反过来把健康的 node 杀掉并报
    "启动失败"。这里把代理指向一个必然连不上的地址：探测只要走了代理就必然失败。
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _OkHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args: object) -> None:  # 测试输出里不要刷访问日志
            return

    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    try:
        with health_module._default_opener(f"http://127.0.0.1:{server.server_port}/api/health") as response:
            assert int(response.status) == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
