"""Start and supervise the vendored ClipForge Next sidecar.

The runtime is modeled as an explicit, observable state machine:

``unavailable -> (start) -> starting -> ready -> (exit) -> failed``
``ready/starting/failed -> (stop) -> stopping -> stopped``

Design constraints that this module must keep:

* ``start()`` never blocks: it performs only a short locked transition, then a
  daemon worker starts the child and runs the strict readiness probe.
* Every start gets a fresh ``instanceId``/generation; a stale worker can never
  write over a newer instance.
* ``failed`` is sticky: a plain ``status()`` read never downgrades it to
  ``stopped``, and the diagnostic id survives repeated reads.
* The child is started in its own process group so ``stop()`` can reap the whole
  Node/FFmpeg tree, and the same path is reused by the app-exit watchdog.
"""

from __future__ import annotations

import os
import platform
import subprocess
import threading
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
from uuid import uuid4

from .artifact import ClipForgeBuild, ClipForgeBuildState
from .health import wait_until_ready
from .process import popen_group_options, terminate_process_tree


ClipForgeRuntimeState = Literal["unavailable", "stopped", "starting", "ready", "failed", "stopping"]

_READY_TIMEOUT_SECONDS: Final[float] = 30.0
_MAX_LOG_FILES: Final[int] = 10

_ACTIVE_STATES: Final[frozenset[str]] = frozenset({"starting", "ready"})
# 只有这些状态会因为 sidecar 进程消失而变成 failed；failed 自身必须保持不动。
_EXIT_OBSERVABLE_STATES: Final[frozenset[str]] = frozenset({"starting", "ready"})
# 构建产物消失不得抹掉已经持久化的 failed/诊断编号。
_UNAVAILABLE_OVERRIDABLE_STATES: Final[frozenset[str]] = frozenset({"stopped", "stopping", "unavailable"})


@dataclass(frozen=True)
class ClipForgeError:
    code: str
    message: str
    retryable: bool
    exit_code: int | None = None
    diagnostic_id: str | None = None


@dataclass(frozen=True)
class ClipForgeStatus:
    state: ClipForgeRuntimeState
    build_state: ClipForgeBuildState
    available: bool
    url: str | None
    instance_id: str | None
    message: str
    error: ClipForgeError | None = None


def _platform_arch_tag(system: str, machine: str) -> str:
    """Map ``platform.system()/machine()`` to the @ffprobe-installer package suffix."""
    platform_name = {"Windows": "win32", "Darwin": "darwin"}.get(system, "linux")
    normalized = machine.lower()
    arch = {"x86_64": "x64", "amd64": "x64", "x64": "x64", "aarch64": "arm64"}.get(normalized, normalized)
    return f"{platform_name}-{arch}"


def _bundled_media_environment(
    app_root: Path, system: str | None = None, machine: str | None = None
) -> dict[str, str]:
    """Point FFMPEG_PATH/FFPROBE_PATH at the media binaries inside the artifact.

    Media binaries ship with the artifact, so an installed MainPG never depends on
    a machine-global ffmpeg. ``@ffprobe-installer`` may lose its execute bit when
    restored from a package store; fix it here (before the child starts).
    """
    system = system or platform.system()
    machine = machine or platform.machine()
    suffix = ".exe" if system == "Windows" else ""
    ffmpeg = Path(app_root) / "node_modules" / "ffmpeg-static" / f"ffmpeg{suffix}"
    ffprobe = (
        Path(app_root) / "node_modules" / "@ffprobe-installer" / _platform_arch_tag(system, machine) / f"ffprobe{suffix}"
    )
    environment: dict[str, str] = {}
    if ffmpeg.is_file():
        environment["FFMPEG_PATH"] = str(ffmpeg)
    if ffprobe.is_file():
        if system != "Windows":
            try:
                ffprobe.chmod(ffprobe.stat().st_mode | 0o111)
            except OSError:
                pass
        environment["FFPROBE_PATH"] = str(ffprobe)
    return environment


class ClipForgeService:
    """Owns exactly one loopback-only ClipForge sidecar child process."""

    def __init__(
        self,
        build_resolver: Callable[[], ClipForgeBuild],
        data_root: Path,
        node_binary: str = "node",
        *,
        process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        ready_probe: Callable[..., None] = wait_until_ready,
    ) -> None:
        self._build_resolver = build_resolver
        self._data_root = Path(data_root)
        self._node_binary = node_binary
        self._process_factory = process_factory
        self._ready_probe = ready_probe
        self._lock = threading.RLock()
        self._build: ClipForgeBuild | None = None
        self._state: ClipForgeRuntimeState = "stopped"
        self._message = "AI 视频服务未启动"
        self._error: ClipForgeError | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._url: str | None = None
        self._instance_id: str | None = None
        # 当前 generation 的诊断编号：状态与失败原因必须能靠它定位到单独一份日志。
        self._diagnostic_id: str | None = None
        self._generation = 0

    # --- 对外状态 ---------------------------------------------------------

    def status(self) -> ClipForgeStatus:
        build = self._resolve_build()
        with self._lock:
            self._build = build
            if build.state != "available":
                if self._state in _UNAVAILABLE_OVERRIDABLE_STATES:
                    self._state = "unavailable"
                    self._message = build.message
                return self._status_locked()
            self._observe_exit_locked()
            if self._state == "unavailable":
                self._state = "stopped"
                self._message = "AI 视频服务未启动"
                self._error = None
            return self._status_locked()

    def start(self) -> ClipForgeStatus:
        # 幂等：starting/ready 都是"已经有实例"，绝不启动第二个进程。
        with self._lock:
            if self._state in _ACTIVE_STATES:
                return self._status_locked()

        build = self._resolve_build()
        with self._lock:
            if self._state in _ACTIVE_STATES:
                return self._status_locked()
            self._build = build
            if build.state != "available" or build.app_root is None:
                self._state = "unavailable"
                self._message = build.message
                self._error = ClipForgeError(
                    code="CLIPFORGE_BUILD_UNAVAILABLE",
                    message=build.message,
                    retryable=False,
                )
                return self._status_locked()
            self._generation += 1
            generation = self._generation
            instance_id = uuid4().hex
            diagnostic_id = uuid4().hex[:12]
            self._state = "starting"
            self._message = "AI 视频服务正在启动"
            self._error = None
            self._instance_id = instance_id
            self._diagnostic_id = diagnostic_id
            self._url = None
            self._process = None

        port = _free_loopback_port()
        worker = threading.Thread(
            target=self._start_worker,
            args=(generation, build, instance_id, diagnostic_id, port),
            name=f"clipforge-start-{diagnostic_id}",
            daemon=True,
        )
        worker.start()
        with self._lock:
            return self._status_locked()

    def stop(self) -> None:
        with self._lock:
            # 先递增 generation：在途 worker/旧 watcher 从此不再具备写回权限。
            self._generation += 1
            process = self._process
            self._process = None
            self._url = None
            self._instance_id = None
            if self._state in {"ready", "starting", "failed"}:
                self._state = "stopping"
                self._message = "AI 视频服务正在关闭"
        if process is not None:
            terminate_process_tree(process)
        with self._lock:
            if self._state == "stopping":
                self._state = "stopped"
                self._message = "AI 视频服务已停止"
                self._error = None

    # --- 内部实现 ---------------------------------------------------------

    def _resolve_build(self) -> ClipForgeBuild:
        try:
            return self._build_resolver()
        except Exception:  # noqa: BLE001 - 解析失败必须变成稳定状态，不能打崩状态查询
            return ClipForgeBuild("invalid", None, None, "AI 视频服务产物无效，请重新构建并发布")

    def _status_locked(self) -> ClipForgeStatus:
        build_state: ClipForgeBuildState = self._build.state if self._build is not None else "missing"
        return ClipForgeStatus(
            state=self._state,
            build_state=build_state,
            available=build_state == "available",
            url=self._url,
            instance_id=self._instance_id,
            message=self._message,
            error=self._error,
        )

    def _observe_exit_locked(self) -> None:
        """安全网：进程在 watcher 之外退出时也能进入 failed（但绝不重置 failed）。"""
        process = self._process
        if process is None or self._state not in _EXIT_OBSERVABLE_STATES:
            return
        if process.poll() is None:
            return
        self._mark_failed_locked("CLIPFORGE_PROCESS_EXITED", "AI 视频服务进程已退出", process.poll())

    def _mark_failed_locked(self, code: str, message: str, exit_code: int | None) -> None:
        self._process = None
        self._state = "failed"
        self._message = message
        self._error = ClipForgeError(
            code=code,
            message=message,
            retryable=True,
            exit_code=exit_code,
            diagnostic_id=self._diagnostic_id,
        )

    def _start_worker(
        self,
        generation: int,
        build: ClipForgeBuild,
        instance_id: str,
        diagnostic_id: str,
        port: int,
    ) -> None:
        app_root = build.app_root
        assert app_root is not None  # start() 只会在 app_root 存在时创建 worker
        log_path = self._open_log(diagnostic_id)
        log_file = None
        try:
            log_file = log_path.open("ab")
            process = self._process_factory(
                [self._node_binary, str(app_root / "server.js")],
                cwd=str(app_root),
                env=self._child_environment(app_root, instance_id, port),
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                **popen_group_options(),
            )
        except OSError as exc:
            if log_file is not None:
                log_file.close()
            self._fail_generation(generation, "CLIPFORGE_PROCESS_SPAWN_FAILED", "AI 视频服务进程启动失败", None)
            self._append_log(log_path, f"spawn failed: {exc}\n")
            return
        finally:
            if log_file is not None and not log_file.closed:
                log_file.close()

        url = f"http://127.0.0.1:{port}"
        with self._lock:
            if generation != self._generation:
                terminate_process_tree(process)
                return
            self._process = process
            self._url = url

        try:
            # 严格探针必须在锁外执行：它最长等待 30 秒，不能阻塞状态查询。
            self._ready_probe(url, process, instance_id, _READY_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - 任何失败都收敛成 failed 状态
            terminate_process_tree(process)
            self._fail_generation(
                generation,
                "CLIPFORGE_HEALTHCHECK_FAILED",
                "AI 视频服务启动后未通过健康检查",
                process.poll(),
            )
            self._append_log(log_path, f"readiness failed: {exc}\n")
            return

        with self._lock:
            if generation != self._generation or self._process is not process:
                return
            if process.poll() is not None:
                self._mark_failed_locked(
                    "CLIPFORGE_PROCESS_EXITED", "AI 视频服务启动后立即退出", process.poll()
                )
                return
            self._state = "ready"
            self._message = "AI 视频服务已就绪"
            self._error = None

        watcher = threading.Thread(
            target=self._watch_process,
            args=(generation, process),
            name=f"clipforge-watch-{diagnostic_id}",
            daemon=True,
        )
        watcher.start()

    def _watch_process(self, generation: int, process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait()
        except Exception:  # noqa: BLE001 - watcher 只是尽力观测
            return
        with self._lock:
            if generation != self._generation or self._process is not process:
                return
            self._mark_failed_locked("CLIPFORGE_PROCESS_EXITED", "AI 视频服务进程已退出", process.poll())

    def _fail_generation(
        self, generation: int, code: str, message: str, exit_code: int | None
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._mark_failed_locked(code, message, exit_code)

    def _child_environment(self, app_root: Path, instance_id: str, port: int) -> dict[str, str]:
        return {
            **os.environ,
            "NODE_ENV": "production",
            "HOSTNAME": "127.0.0.1",
            "PORT": str(port),
            "APP_DATA_DIR": str(self._data_root / "data"),
            "APP_MIGRATIONS_DIR": str(app_root / "drizzle"),
            "MAINPG_CLIPFORGE_INSTANCE_ID": instance_id,
            **_bundled_media_environment(app_root),
        }

    def _open_log(self, diagnostic_id: str) -> Path:
        logs_dir = self._data_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{diagnostic_id}.log"
        try:
            log_path.touch()
        except OSError:
            pass
        self._prune_logs(logs_dir, log_path)
        return log_path

    def _prune_logs(self, logs_dir: Path, current: Path) -> None:
        """Keep the current log plus the newest ``_MAX_LOG_FILES - 1``; log dir must not grow forever."""
        stale = [path for path in logs_dir.glob("*.log") if path != current]
        stale.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        for path in stale[_MAX_LOG_FILES - 1 :]:
            try:
                path.unlink()
            except OSError:
                continue

    @staticmethod
    def _append_log(log_path: Path, message: str) -> None:
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(message)
        except OSError:
            pass


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
