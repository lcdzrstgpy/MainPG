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
* A crash is auto-restarted a bounded number of times with a growing delay; the
  budget resets only on an explicit user start, so a crash loop can never keep
  restarting behind the user's back.
* Lifecycle hygiene: a pid file reaps a sidecar orphaned by a previous crashed
  MainPG, and on Windows the child also joins a kill-on-close Job Object so a
  hard kill of MainPG cannot leave ``node.exe`` resident.
"""

from __future__ import annotations

import ctypes
import os
import platform
import socket
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal
from uuid import uuid4

from .artifact import ClipForgeBuild, ClipForgeBuildState
from .health import wait_until_ready
from .process import popen_group_options, terminate_process_tree


ClipForgeRuntimeState = Literal["unavailable", "stopped", "starting", "ready", "failed", "stopping"]

_READY_TIMEOUT_SECONDS: Final[float] = 30.0
_MAX_LOG_FILES: Final[int] = 10
_LOG_MAX_BYTES: Final[int] = 5 * 1024 * 1024

# 子进程崩溃后的自动重启：有限次 + 递增间隔。不做无限重启（那会掩盖真正的故障，
# 还可能和用户手动启动抢端口）；耗尽次数后停在 failed，让用户看到退出码再决定。
_MAX_AUTO_RESTARTS: Final[int] = 2
_RESTART_BACKOFF_SECONDS: Final[tuple[float, ...]] = (3.0, 10.0)
# 端口是 bind(0) 临时占位后立刻释放的，node 真正绑定前存在被抢占的窗口（TOCTOU）。
# 就绪探测失败就换一个端口重试，把这个窗口变成无害。
_START_PORT_ATTEMPTS: Final[int] = 2

_ACTIVE_STATES: Final[frozenset[str]] = frozenset({"starting", "ready"})
# 只有这些状态会因为 sidecar 进程消失而变成 failed；failed 自身必须保持不动。
_EXIT_OBSERVABLE_STATES: Final[frozenset[str]] = frozenset({"starting", "ready"})
# 构建产物消失不得抹掉已经持久化的 failed/诊断编号。
_UNAVAILABLE_OVERRIDABLE_STATES: Final[frozenset[str]] = frozenset({"stopped", "stopping", "unavailable"})

_PROCESS_QUERY_LIMITED_INFORMATION: Final[int] = 0x1000
_PROCESS_TERMINATE: Final[int] = 0x0001


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


def _node_style_platform() -> str:
    """Best-effort Node-style platform-arch tuple (win32-x64) for directory matching."""
    system = platform.system().lower()
    plat = "win32" if system == "windows" else ("darwin" if system == "darwin" else "linux")
    machine = platform.machine().lower()
    arch = {
        "amd64": "x64",
        "x86_64": "x64",
        "x86": "ia32",
        "i386": "ia32",
        "i686": "ia32",
        "aarch64": "arm64",
        "arm": "arm",
    }.get(machine, machine)
    return f"{plat}-{arch}"


def _bundled_media_environment(
    app_root: Path, system: str | None = None, machine: str | None = None
) -> dict[str, str]:
    """Point FFMPEG_PATH/FFPROBE_PATH at the media binaries inside the artifact.

    Media binaries ship with the artifact, so an installed MainPG never depends on
    a machine-global ffmpeg. ``@ffprobe-installer`` may lose its execute bit when
    restored from a package store; fix it here (before the child starts).
    """
    resolved_system = system or platform.system()
    suffix = ".exe" if resolved_system == "Windows" else ""
    ffmpeg = Path(app_root) / "node_modules" / "ffmpeg-static" / f"ffmpeg{suffix}"
    ffprobe = _resolve_ffprobe_binary(Path(app_root), suffix)
    # npm may restore @ffprobe-installer's macOS/Linux binary without its execute bit.
    # Passing that path through as FFPROBE_PATH makes every media probe fail with EACCES.
    if resolved_system != "Windows" and ffprobe is not None:
        try:
            ffprobe.chmod(ffprobe.stat().st_mode | 0o111)
        except OSError:
            pass
    environment: dict[str, str] = {}
    if ffmpeg.is_file():
        environment["FFMPEG_PATH"] = str(ffmpeg)
    if ffprobe is not None:
        environment["FFPROBE_PATH"] = str(ffprobe)
    return environment


def _resolve_ffprobe_binary(app_root: Path, suffix: str) -> Path | None:
    """Locate @ffprobe-installer's platform directory without guessing the arch name.

    目录名用的是 Node 的 process.arch（win32-x64 / linux-arm64 …），而
    platform.machine() 返回的是 uname 风格（AMD64 / aarch64）；Windows x64 上
    两者差一个词就拼出 win32-amd64 这种不存在的目录，Windows/macOS 上跑仿真 Node
    时也会错。既然目录名是装机事实，直接枚举安装现场最稳。
    """
    installer_root = Path(app_root) / "node_modules" / "@ffprobe-installer"
    candidates: list[Path] = []
    if installer_root.is_dir():
        for entry in sorted(installer_root.iterdir()):
            binary = entry / f"ffprobe{suffix}"
            if entry.is_dir() and binary.is_file():
                candidates.append(binary)
    if not candidates:
        return None
    # 同机装了多份（如 win32-x64 + win32-arm64）时优先匹配本机 arch。
    wanted = _node_style_platform()
    for binary in candidates:
        if binary.parent.name == wanted:
            return binary
    return candidates[0]


class ClipForgeService:
    """Owns exactly one loopback-only ClipForge sidecar child process."""

    def __init__(
        self,
        build_resolver: Callable[[], ClipForgeBuild],
        data_root: Path,
        node_binary: str | Path = "node",
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
        # 崩溃自动重启预算：只在用户显式 start() 时重置。
        self._restarts = 0
        self._restart_timer: threading.Timer | None = None
        # 用户主动 stop() 期间必须彻底静音自动重启与旧 watcher。
        self._stopping = False
        # Windows Job Object 句柄（父亡子亡）；非 Windows 恒为 None。
        self._job: int | None = None

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

    def start(self, *, _auto: bool = False) -> ClipForgeStatus:
        with self._lock:
            self._stopping = False
            if not _auto:
                # 用户显式启动：重置自动重启预算，并取消在途的重启计时器
                self._restarts = 0
                self._cancel_restart_timer_locked()
            # 幂等：starting/ready 都是"已经有实例"，绝不启动第二个进程。
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

        worker = threading.Thread(
            target=self._start_worker,
            args=(generation, build, instance_id, diagnostic_id),
            name=f"clipforge-start-{diagnostic_id}",
            daemon=True,
        )
        worker.start()
        with self._lock:
            return self._status_locked()

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._cancel_restart_timer_locked()
            # 先递增 generation：在途 worker/旧 watcher 从此不再具备写回权限。
            self._generation += 1
            process = self._process
            self._process = None
            self._url = None
            self._instance_id = None
            self._close_job_locked()
            self._clear_pid_file()
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

    def _note_restart_locked(self, message: str) -> None:
        """把自动重启计划同时写进状态文案与错误体，保证 UI 两处一致。"""
        self._message = message
        if self._error is not None:
            self._error = replace(self._error, message=message)

    def _schedule_restart_locked(self) -> None:
        """崩溃后有限次自动重启；预算耗尽就停在 failed 并写明重启次数。"""
        if self._stopping or self._restart_timer is not None:
            return
        build = self._build
        if build is None or build.state != "available":
            return
        if self._restarts >= _MAX_AUTO_RESTARTS:
            self._note_restart_locked(f"{self._message}（已自动重启 {self._restarts} 次，仍失败）")
            return
        self._restarts += 1
        delay = _RESTART_BACKOFF_SECONDS[min(self._restarts - 1, len(_RESTART_BACKOFF_SECONDS) - 1)]
        self._note_restart_locked(
            f"{self._message}；{delay:.0f} 秒后自动重启（第 {self._restarts}/{_MAX_AUTO_RESTARTS} 次）"
        )
        timer = threading.Timer(delay, self._auto_restart)
        timer.name = "clipforge-restart"
        timer.daemon = True
        self._restart_timer = timer
        timer.start()

    def _cancel_restart_timer_locked(self) -> None:
        timer = self._restart_timer
        self._restart_timer = None
        if timer is not None:
            timer.cancel()

    def _auto_restart(self) -> None:
        with self._lock:
            self._restart_timer = None
            if self._stopping or self._state in _ACTIVE_STATES:
                return
        try:
            self.start(_auto=True)
        except Exception:  # noqa: BLE001 - 守护线程不允许把异常抛回解释器
            pass

    def _start_worker(
        self,
        generation: int,
        build: ClipForgeBuild,
        instance_id: str,
        diagnostic_id: str,
    ) -> None:
        app_root = build.app_root
        assert app_root is not None  # start() 只会在 app_root 存在时创建 worker
        log_path = self._open_log(diagnostic_id)
        self._reap_orphan_child()
        port = _free_loopback_port()

        for attempt in range(_START_PORT_ATTEMPTS):
            process = self._spawn_child(generation, app_root, instance_id, log_path, port)
            if process is None:
                return
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
                self._append_log(log_path, f"readiness failed: {exc}\n")
                with self._lock:
                    stale = generation != self._generation or self._process is not process
                if stale:
                    return
                if attempt + 1 < _START_PORT_ATTEMPTS:
                    with self._lock:
                        self._process = None
                        self._url = None
                    port = _free_loopback_port()
                    continue
                self._fail_generation(
                    generation,
                    "CLIPFORGE_HEALTHCHECK_FAILED",
                    "AI 视频服务启动后未通过健康检查",
                    process.poll(),
                )
                return

            with self._lock:
                if generation != self._generation or self._process is not process:
                    return
                if process.poll() is not None:
                    self._mark_failed_locked(
                        "CLIPFORGE_PROCESS_EXITED", "AI 视频服务启动后立即退出", process.poll()
                    )
                    self._schedule_restart_locked()
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
            return

    def _spawn_child(
        self,
        generation: int,
        app_root: Path,
        instance_id: str,
        log_path: Path,
        port: int,
    ) -> subprocess.Popen[bytes] | None:
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
            self._fail_generation(generation, "CLIPFORGE_PROCESS_SPAWN_FAILED", "AI 视频服务进程启动失败", None)
            self._append_log(log_path, f"spawn failed: {exc}\n")
            return None
        finally:
            if log_file is not None and not log_file.closed:
                log_file.close()
        self._kill_when_parent_exits(process)
        self._write_pid_file(process)
        return process

    def _watch_process(self, generation: int, process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait()
        except Exception:  # noqa: BLE001 - watcher 只是尽力观测
            return
        with self._lock:
            if generation != self._generation:
                return
            if self._process is process:
                self._mark_failed_locked("CLIPFORGE_PROCESS_EXITED", "AI 视频服务进程已退出", process.poll())
            # 即使 status() 先一步把状态标成 failed（_process 已被清空），也必须排上重启。
            self._schedule_restart_locked()

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

    # --- 日志 -------------------------------------------------------------

    def _open_log(self, diagnostic_id: str) -> Path:
        logs_dir = self._data_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{diagnostic_id}.log"
        self._rotate_log_if_needed(log_path)
        try:
            log_path.touch()
        except OSError:
            pass
        self._prune_logs(logs_dir, log_path)
        return log_path

    @staticmethod
    def _rotate_log_if_needed(log_path: Path) -> None:
        """单个日志超过上限就轮转一代（``.1``），避免一次长跑把磁盘写满。"""
        try:
            if log_path.is_file() and log_path.stat().st_size >= _LOG_MAX_BYTES:
                rotated = log_path.with_name(f"{log_path.name}.1")
                rotated.unlink(missing_ok=True)
                log_path.replace(rotated)
        except OSError:
            pass  # 轮转是卫生措施：失败就继续追加，不影响功能

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

    # --- 生命周期卫生：孤儿回收 / 父亡子亡 --------------------------------

    @property
    def _pid_file(self) -> Path:
        return self._data_root / "clipforge.pid"

    def _reap_orphan_child(self) -> None:
        """Kill a sidecar left behind by a previous crashed MainPG (pid file).

        pid 文件记录的是上一轮 sidecar 子进程的 pid：上一次 MainPG 被强杀时，
        没有 Job Object 兜底的平台会留下常驻 node.exe——它占着端口与 .next 文件锁，
        必须在新实例启动前回收。
        """
        try:
            pid = int(self._pid_file.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return
        if pid == os.getpid():
            return
        if _pid_alive(pid):
            _terminate_pid(pid)
        self._clear_pid_file()

    def _write_pid_file(self, process: subprocess.Popen[bytes]) -> None:
        try:
            self._data_root.mkdir(parents=True, exist_ok=True)
            self._pid_file.write_text(str(int(process.pid)), encoding="ascii")
        except OSError:
            pass

    def _clear_pid_file(self) -> None:
        try:
            self._pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _kill_when_parent_exits(self, process: subprocess.Popen[bytes]) -> None:
        """Windows：把子进程挂进 Job Object（KILL_ON_JOB_CLOSE），父进程一死系统自动回收。

        这是唯一能真正实现"父亡子亡"的机制：主程序被任务管理器强杀时，
        孤儿 node.exe 不会常驻占端口、也不会继续占着 .next 的文件锁。
        任何一步失败都降级为不启用（行为退化为只依赖 pid 文件回收），绝不影响启动。
        """
        if platform.system() != "Windows":
            return
        try:
            job = _create_kill_on_close_job()
            if job is None:
                return
            if not ctypes.windll.kernel32.AssignProcessToJobObject(job, process._handle):  # noqa: SLF001
                ctypes.windll.kernel32.CloseHandle(job)
                return
            self._job = job
        except Exception:  # noqa: BLE001 - ctypes 边界一律兜底
            self._job = None

    def _close_job_locked(self) -> None:
        job = self._job
        self._job = None
        if job is not None and platform.system() == "Windows":
            try:
                ctypes.windll.kernel32.CloseHandle(job)
            except Exception:  # noqa: BLE001
                pass


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _pid_alive(pid: int) -> bool:
    if platform.system() == "Windows":
        # OpenProcess 返回空句柄即进程不存在。
        try:
            handle = ctypes.windll.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_pid(pid: int) -> None:
    """结束遗留的 sidecar 子进程（Windows 需先 OpenProcess 拿句柄再 TerminateProcess）。"""
    try:
        if platform.system() == "Windows":
            handle = ctypes.windll.kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
            if not handle:
                return
            try:
                ctypes.windll.kernel32.TerminateProcess(handle, 1)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(pid, 15)  # noqa: S105 - 目标是上一轮遗留的自家子进程
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Windows Job Object（父亡子亡）。ctypes 结构体定义按 win32 ABI 手写，
# 任一步失败都由调用方兜底为"不启用"，因此这里不做过多防御。
# ---------------------------------------------------------------------------

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _create_kill_on_close_job() -> int | None:
    if platform.system() != "Windows":
        return None
    try:
        job = ctypes.windll.kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not ctypes.windll.kernel32.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            ctypes.windll.kernel32.CloseHandle(job)
            return None
        return int(job)
    except Exception:  # noqa: BLE001 - ctypes 边界一律兜底
        return None
