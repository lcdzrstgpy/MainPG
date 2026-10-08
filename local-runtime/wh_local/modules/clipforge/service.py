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
* 正常退出之外的路径也要收干净：Windows 上把 node 挂进 Job Object
  （``KILL_ON_JOB_CLOSE``），MainPG 被强杀时内核会自动回收整个后代树；pid 文件
  只在 Job Object 不可用时兜底，并且动手前必须确认目标真是 node。
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import socket
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

from .artifact import ClipForgeBuild, ClipForgeBuildState
from .health import wait_until_ready
from .process import popen_group_options, terminate_process_tree


ClipForgeRuntimeState = Literal["unavailable", "stopped", "starting", "ready", "failed", "stopping"]

_READY_TIMEOUT_SECONDS: Final[float] = 30.0
_MAX_LOG_FILES: Final[int] = 10

# 子进程崩溃后的自动重启：有限次 + 递增间隔。不做无限重启（那会掩盖真正的故障，
# 还可能和用户手动启动抢端口）；耗尽次数后停在 failed，让用户看到退出码再决定。
_MAX_AUTO_RESTARTS: Final[int] = 2
_RESTART_BACKOFF_SECONDS: Final[tuple[float, ...]] = (3.0, 10.0)

# 就绪探测必须绕过代理：用户设了 HTTP_PROXY 时 urlopen 会把 127.0.0.1 的探测请求
# 送给代理 → 被拒 → 超时 → start() 反而把健康的 node 杀掉并报"启动失败"。
# health 模块的默认 opener 会走系统代理，所以这里显式注入一个禁代理的。
_READY_HTTP_TIMEOUT_SECONDS: Final[float] = 1.0
_NO_PROXY_OPENER: Final = build_opener(ProxyHandler({}))

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


def _loopback_open(url: str):
    """探测专用 opener：禁代理 + 带超时（默认 opener 会把 loopback 请求送给代理）。"""
    return _NO_PROXY_OPENER.open(url, timeout=_READY_HTTP_TIMEOUT_SECONDS)


def _probe_readiness(
    base_url: str,
    process: object,
    instance_id: str,
    timeout_s: float = _READY_TIMEOUT_SECONDS,
) -> None:
    """默认就绪探针：严格校验 schema/instanceId，但绝不经由系统代理。"""
    wait_until_ready(base_url, process, instance_id, timeout_s, opener=_loopback_open)


def _platform_arch_tag(system: str, machine: str) -> str:
    """Map ``platform.system()/machine()`` to the @ffprobe-installer package suffix."""
    platform_name = {"Windows": "win32", "Darwin": "darwin"}.get(system, "linux")
    normalized = machine.lower()
    arch = {
        "x86_64": "x64",
        "amd64": "x64",
        "x64": "x64",
        "x86": "ia32",
        "i386": "ia32",
        "i686": "ia32",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(normalized, normalized)
    return f"{platform_name}-{arch}"


def _node_style_platform(system: str | None = None, machine: str | None = None) -> str:
    """Best-effort Node-style platform-arch tuple (win32-x64) for directory matching."""
    return _platform_arch_tag(system or platform.system(), machine or platform.machine())


def _resolve_ffprobe_binary(root: Path, suffix: str, system: str, machine: str) -> Path | None:
    """Locate @ffprobe-installer's binary, preferring the arch this host really runs.

    ``@ffprobe-installer`` 的目录名用的是 Node 的 process.arch（win32-x64 /
    linux-arm64 …），而 ``platform.machine()`` 返回的是 uname 风格（AMD64 /
    aarch64）：Windows x64 上差一个词就拼出 win32-amd64 这种不存在的目录，
    Windows ARM64 上跑 x64 Node（仿真）时也会错。目录名是装机事实，先按算出来的
    名字直取，拿不到再枚举安装现场，最后才放弃。
    """
    installer_root = root / "node_modules" / "@ffprobe-installer"
    direct = installer_root / _platform_arch_tag(system, machine) / f"ffprobe{suffix}"
    if direct.is_file():
        return direct
    if not installer_root.is_dir():
        return None
    candidates = [
        entry / f"ffprobe{suffix}"
        for entry in sorted(installer_root.iterdir())
        if (entry / f"ffprobe{suffix}").is_file()
    ]
    if not candidates:
        return None
    wanted = _node_style_platform(system, machine)
    for binary in candidates:
        if binary.parent.name == wanted:
            return binary
    # 同机装了多份（如 win32-x64 + win32-arm64）却都匹配不上时，退而取第一份。
    return candidates[0]


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
    root = Path(app_root)
    ffmpeg = root / "node_modules" / "ffmpeg-static" / f"ffmpeg{suffix}"
    ffprobe = _resolve_ffprobe_binary(root, suffix, system, machine)
    environment: dict[str, str] = {}
    if ffmpeg.is_file():
        environment["FFMPEG_PATH"] = str(ffmpeg)
    if ffprobe is not None and ffprobe.is_file():
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
        reference_config_resolver: Callable[[], Path | None] | None = None,
        process_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        ready_probe: Callable[..., None] = _probe_readiness,
    ) -> None:
        self._build_resolver = build_resolver
        self._data_root = Path(data_root)
        self._node_binary = node_binary
        # 参考图公网中转用的对象存储配置（cos.local.json）位置；sidecar 用它把本地图换成
        # 上游能抓的临时 URL（速创这类异步接口不接受内联图）。拿不到就注入空，sidecar 会明确报错。
        self._reference_config_resolver = reference_config_resolver
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
        # 进程治理：Windows Job Object 句柄、自动重启预算与定时器。
        self._job: int | None = None
        self._restarts = 0
        self._restart_timer: threading.Timer | None = None

    # --- 对外状态 ---------------------------------------------------------

    @property
    def _pid_file(self) -> Path:
        return self._data_root / "clipforge.pid"

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
            # 用户显式启动 → 重置自动重启预算，并取消上一轮待执行的重启。
            self._restarts = 0
            self._cancel_restart_timer_locked()

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
            return self._launch_locked(build)

    def _launch_locked(self, build: ClipForgeBuild) -> ClipForgeStatus:
        """递增 generation 并起一个后台 worker；调用方必须持锁且已校验 build 可用。"""
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
        return self._status_locked()

    def stop(self) -> None:
        with self._lock:
            # 先递增 generation：在途 worker/旧 watcher 从此不再具备写回权限。
            self._generation += 1
            self._cancel_restart_timer_locked()
            self._restarts = 0
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
        # 子进程已经不在（或刚被 terminate），句柄与 pid 标记都不能留着。
        self._close_job_locked()
        self._clear_pid_file()

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
        # 上一轮 MainPG 被强杀时留下的 sidecar 必须先收掉，否则它会占着端口和 .next 文件锁。
        self._reap_orphan_child()
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
            # 父亡子亡：MainPG 被任务管理器强杀时，Windows 内核会连同这个 node 及其
            # 后代（ffmpeg 等）一起回收 —— 这是唯一覆盖"强杀"路径的机制。
            self._kill_when_parent_exits_locked(process)
            self._write_pid_file(process)

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
            # 意外退出才自动重启；健康检查失败/构建不可用都不重试（重试也没意义）。
            self._schedule_auto_restart_locked()

    def _schedule_auto_restart_locked(self) -> None:
        """进程意外退出后有限次自动重启；预算耗尽就停在 failed，把退出码留给用户。"""
        if self._restart_timer is not None:
            return
        if self._restarts >= _MAX_AUTO_RESTARTS:
            self._message = f"{self._message}（已自动重启 {self._restarts} 次，仍失败）"
            return
        build = self._build
        if build is None or build.state != "available" or build.app_root is None:
            return
        self._restarts += 1
        delay = _RESTART_BACKOFF_SECONDS[min(self._restarts - 1, len(_RESTART_BACKOFF_SECONDS) - 1)]
        self._message = f"{self._message}；{delay:.0f} 秒后自动重启（第 {self._restarts}/{_MAX_AUTO_RESTARTS} 次）"
        timer = threading.Timer(delay, self._auto_restart)
        timer.daemon = True
        self._restart_timer = timer
        timer.start()

    def _auto_restart(self) -> None:
        with self._lock:
            self._restart_timer = None
            # stop() 或用户手动 start() 已经接管的话，定时器什么都不做。
            if self._process is not None or self._state in _ACTIVE_STATES:
                return
            build = self._build
            if build is None or build.state != "available" or build.app_root is None:
                return
            try:
                self._launch_locked(build)
            except Exception:  # noqa: BLE001 - 守护线程不允许把异常抛回解释器
                pass

    def _cancel_restart_timer_locked(self) -> None:
        timer = self._restart_timer
        self._restart_timer = None
        if timer is not None:
            timer.cancel()

    def _fail_generation(
        self, generation: int, code: str, message: str, exit_code: int | None
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._mark_failed_locked(code, message, exit_code)

    def _child_environment(self, app_root: Path, instance_id: str, port: int) -> dict[str, str]:
        reference_config = self._reference_config_path()
        return {
            **os.environ,
            "NODE_ENV": "production",
            "HOSTNAME": "127.0.0.1",
            "PORT": str(port),
            "APP_DATA_DIR": str(self._data_root / "data"),
            "APP_MIGRATIONS_DIR": str(app_root / "drizzle"),
            "MAINPG_CLIPFORGE_INSTANCE_ID": instance_id,
            # 只传路径不传凭据：sidecar 自己读 cos.local.json，密钥不进环境变量
            **({"WH_MEDIA_COS_CONFIG": str(reference_config)} if reference_config else {}),
            **_bundled_media_environment(app_root),
        }

    def _reference_config_path(self) -> Path | None:
        """参考图中转要用的 cos.local.json；解析不出来就返回 None（sidecar 会明确报缺配置）。"""
        resolver = self._reference_config_resolver
        if resolver is None:
            return None
        try:
            candidate = resolver()
        except Exception:  # noqa: BLE001 - 配置解析失败绝不能拖住 sidecar 启动
            return None
        if candidate is None:
            return None
        path = Path(candidate)
        return path if path.is_file() else None

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

    # ------------------------------------------------------------------
    # 生命周期卫生：父亡子亡 / 孤儿清理
    # ------------------------------------------------------------------

    def _kill_when_parent_exits_locked(self, process: subprocess.Popen[bytes]) -> None:
        """Windows：把子进程挂进 Job Object（KILL_ON_JOB_CLOSE），父进程一死系统自动回收。

        这是唯一能真正实现"父亡子亡"的机制：主程序被任务管理器强杀时，
        孤儿 node.exe 不会常驻占端口、也不会继续占着 .next 的文件锁。
        任何一步失败都降级为不启用（行为退化为旧版），绝不影响启动。
        """
        if platform.system() != "Windows":
            return
        try:
            job = _create_kill_on_close_job()
            if job is None:
                return
            handle = getattr(process, "_handle", None)
            if handle is None or not ctypes.windll.kernel32.AssignProcessToJobObject(job, handle):
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

    def _reap_orphan_child(self) -> None:
        """回收上一轮崩溃遗留的 sidecar（Windows 上通常已被 Job Object 收走，这是兜底）。

        pid 会被系统复用，所以只有"记录里的父进程已经不在 + 目标确实是 node"
        才会动手；任何一条不成立都只是清掉标记文件，宁可留着孤儿也不误杀。
        """
        parent_pid, child_pid = _read_pid_file(self._pid_file)
        parent_gone = parent_pid <= 0 or not _pid_alive(parent_pid)
        if child_pid and child_pid != os.getpid() and parent_gone and _pid_is_node(child_pid):
            _terminate_pid(child_pid)
        self._clear_pid_file()

    def _write_pid_file(self, process: subprocess.Popen[bytes]) -> None:
        try:
            self._data_root.mkdir(parents=True, exist_ok=True)
            self._pid_file.write_text(
                json.dumps({"parent": os.getpid(), "child": int(process.pid)}),
                encoding="ascii",
            )
        except (OSError, TypeError, ValueError):
            pass

    def _clear_pid_file(self) -> None:
        try:
            self._pid_file.unlink(missing_ok=True)
        except OSError:
            pass


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# pid 文件：记录 (父进程, 子进程) 两个 pid，用于回收上一轮遗留的 sidecar
# ---------------------------------------------------------------------------


def _read_pid_file(path: Path) -> tuple[int, int]:
    """Return ``(parent_pid, child_pid)``; 0 means unknown. 兼容旧版单 pid 文件。"""
    try:
        raw = path.read_text(encoding="ascii").strip()
    except OSError:
        return (0, 0)
    try:
        payload = json.loads(raw)
    except ValueError:
        # 旧格式只写一个 pid：父进程未知 → 只能靠 _pid_is_node 兜底。
        return (0, _as_pid(raw))
    if not isinstance(payload, dict):
        return (0, 0)
    return (_as_pid(payload.get("parent")), _as_pid(payload.get("child")))


def _as_pid(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        # OpenProcess 返回空句柄即进程不存在。
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
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


def _pid_is_node(pid: int) -> bool:
    """目标进程是不是 node 本体。false 一律不动手（pid 复用会误杀无关进程）。

    macOS 上没有 /proc，这里会返回 False —— 宁可漏收一个孤儿，也不冒误杀的风险。
    """
    if pid <= 0 or pid == os.getpid():
        return False
    if platform.system() == "Windows":
        try:
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                buffer = ctypes.create_unicode_buffer(1024)
                size = ctypes.c_uint32(len(buffer))
                if not ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size)
                ):
                    return False
                return Path(buffer.value).name.lower() in {"node", "node.exe"}
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            return False
    try:
        argv0 = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0", 1)[0].decode("utf-8", "replace")
    except OSError:
        return False
    return Path(argv0).name.lower() in {"node", "node.exe"}


def _terminate_pid(pid: int) -> None:
    """终止**已确认是自家 node** 的遗留进程（调用方负责身份校验）。"""
    try:
        if platform.system() == "Windows":
            handle = ctypes.windll.kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
            if not handle:
                return
            try:
                ctypes.windll.kernel32.TerminateProcess(handle, 1)  # noqa: S105 - 目标是上一轮遗留的自家子进程
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        else:
            os.kill(pid, 15)
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
