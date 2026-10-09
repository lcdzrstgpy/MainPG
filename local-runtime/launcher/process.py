"""主程序进程生命周期管理：启动 / 停止 / 重启 / 状态与资源观测。

参考 PrismLauncher（C++/Qt）与 HMCL 对子进程的持有方式，落在 Python 上：
  - 启动：``subprocess.Popen`` 持有句柄，``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP``
    让主程序脱离启动器的进程组独立存活（与旧 ``os.startfile`` 行为一致，但可观测、可回收）
  - 状态：以主程序监听的本地端口（默认 8010）为准；进程句柄存活仅用于区分「启动中」
  - 停止：先 ``taskkill`` 不带 ``/F``（等价于给窗口发 WM_CLOSE，触发优雅退出），
    超过宽限期再 ``taskkill /T /F`` 连同子进程树一起回收，避免残留进程继续占内存
  - 归属：优先用启动器持有的句柄；句柄不可用（例如实例是用户手动启动、或启动器
    重启过）时按监听端口反查 PID，所以「停止 / 重启」对非本启动器拉起的实例同样可用

不引入第三方依赖（无 psutil）：内存占用走 Windows 的 ``GetProcessMemoryInfo``，
其他平台返回 ``None``，UI 显示为占位符。
"""
from __future__ import annotations

import locale
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import console, core

# 运行状态
STATE_STOPPED = "stopped"
STATE_STARTING = "starting"
STATE_RUNNING = "running"

# 启动后等待端口监听的观察窗口（超时未就绪仍视为「启动中」）
START_POLL_TIMEOUT = 25.0
# 优雅停止的等待上限，超时转强制结束
STOP_GRACE_SECONDS = 8.0
# 端口反查 PID 的缓存时长，避免状态刷新时频繁拉起 netstat
_LISTENING_PID_TTL = 1.0

if sys.platform == "win32":  # pragma: no cover - 仅 Windows 走真实实现
    import ctypes
    from ctypes import wintypes

    class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _psapi.GetProcessMemoryInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
        wintypes.DWORD,
    )
    _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def process_memory_bytes(pid: int) -> int | None:
    """返回进程工作集（WorkingSetSize）字节数；查询失败或非 Windows 返回 None。"""
    if sys.platform != "win32":
        return None
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        if not _psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.WorkingSetSize)
    finally:
        _kernel32.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    """进程是否仍存活。"""
    if pid <= 0:
        return False
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def _windows_listening_pid(port: int) -> int | None:
    """用 netstat 解析正在 LISTENING 该端口的进程 PID。"""
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    # netstat 按控制台代码页输出（中文系统为 GBK），必须按本机编码解码
    try:
        encoding = locale.getencoding()  # Python 3.11+
    except AttributeError:
        encoding = locale.getpreferredencoding(False)
    stdout = (completed.stdout or b"").decode(encoding, errors="replace")
    suffix = f":{port}"
    for line in stdout.splitlines():
        parts = line.split()
        # 形如：TCP  127.0.0.1:8010  0.0.0.0:0  LISTENING  54508
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING" or not parts[1].endswith(suffix):
            continue
        if parts[4].isdigit() and int(parts[4]) > 0:
            return int(parts[4])
    return None


def _unix_listening_pid(port: int) -> int | None:
    """用 lsof 解析正在监听该端口的进程 PID（非 Windows 平台）。"""
    try:
        completed = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in completed.stdout.split():
        if line.isdigit() and int(line) > 0:
            return int(line)
    return None


def pid_listening_on(port: int) -> int | None:
    """返回监听该 TCP 端口的进程 PID（无论是否由本启动器拉起），查不到返回 None。"""
    if sys.platform == "win32":
        return _windows_listening_pid(port)
    return _unix_listening_pid(port)


def _creation_flags() -> int:
    """让子进程脱离启动器进程组独立存活；非 Windows 返回 0。"""
    if sys.platform != "win32":
        return 0
    return (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def _terminate(pid: int, force: bool = False) -> None:
    """结束进程。Windows 用 taskkill，``/T`` 保证子进程树一并回收。"""
    if sys.platform == "win32":
        args = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            args.append("/F")
        try:
            subprocess.run(
                args,
                capture_output=True,
                timeout=15,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except OSError:
        pass


class ProductProcess:
    """持有主程序子进程句柄，提供启动 / 停止 / 重启 / 状态查询。

    所有方法都是同步的，等待类操作（``stop`` / ``restart`` / ``wait_until_ready``）
    由调用方放到后台线程执行，避免阻塞 GUI。
    """

    def __init__(self, port: int = core.DEFAULT_PORT) -> None:
        self._port = port
        self._popen: subprocess.Popen | None = None
        self._exe: Path | None = None
        self._started_at: float = 0.0
        self._last_memory: int | None = None
        self._peak_memory: int | None = None
        # 端口反查 PID 要起 netstat，按 TTL 缓存，避免每次刷新都拉一次
        self._listening_pid_cache: tuple[float, int | None] = (0.0, None)

    # ------------------------------------------------------------- 状态查询
    @property
    def port(self) -> int:
        return self._port

    @property
    def exe(self) -> Path | None:
        """启动器已知的主程序路径，未启动过则回退到自动定位。"""
        return self._exe if self._exe is not None else core.find_product()

    def owned_pid(self) -> int | None:
        """本启动器拉起且仍存活的 PID；未持有或已退出则返回 None。"""
        if self._popen is None:
            return None
        return self._popen.pid if self._popen.poll() is None else None

    def is_owned(self) -> bool:
        return self.owned_pid() is not None

    def listening_pid(self) -> int | None:
        """监听端口的主程序 PID（含用户手动启动的实例），带短 TTL 缓存。"""
        cached_at, cached = self._listening_pid_cache
        if time.time() - cached_at < _LISTENING_PID_TTL:
            return cached
        pid = pid_listening_on(self._port)
        self._listening_pid_cache = (time.time(), pid)
        return pid

    def active_pid(self) -> int | None:
        """当前主程序 PID：优先本启动器持有的句柄，其次按端口反查。"""
        pid = self.owned_pid()
        if pid is not None:
            return pid
        return self.listening_pid()

    def invalidate_pid_cache(self) -> None:
        self._listening_pid_cache = (0.0, None)

    def is_listening(self) -> bool:
        """主程序是否已监听本地端口（含用户手动启动的实例）。"""
        return console.is_product_running(port=self._port, timeout=0.4)

    def state(self) -> str:
        if self.is_listening():
            return STATE_RUNNING
        if self.is_owned():
            return STATE_STARTING
        return STATE_STOPPED

    def uptime(self) -> float:
        """本次托管的运行时长（秒）；未托管过返回 0。"""
        return max(0.0, time.time() - self._started_at) if self._started_at else 0.0

    def memory_bytes(self) -> int | None:
        """当前内存占用（工作集），同时维护峰值。"""
        pid = self.active_pid()
        if pid is None:
            return None
        value = process_memory_bytes(pid)
        if value is not None:
            self._last_memory = value
            self._peak_memory = value if self._peak_memory is None else max(self._peak_memory, value)
        return value

    def peak_memory_bytes(self) -> int | None:
        return self._peak_memory

    def saved_memory_bytes(self) -> int | None:
        """最近一次观测到的占用，用于停止后展示「已释放」量。"""
        return self._last_memory

    # ----------------------------------------------------------------- 操作
    def start(self, exe: Path | None = None) -> tuple[bool, str]:
        """启动主程序。已在运行时拦截，避免重复拉起双实例。"""
        if self.is_listening():
            return False, "主程序已在运行中，无需重复启动。"
        if self.is_owned():
            return False, "主程序进程仍在启动中，请稍候。"
        target = Path(exe) if exe is not None else core.find_product()
        if target is None or not target.is_file():
            return False, "未找到 MainPG.exe。请确认产品已安装，或用 WH_APP_EXE 指定路径。"
        try:
            self._popen = subprocess.Popen(
                [str(target)],
                cwd=str(target.parent),
                close_fds=True,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            self._popen = None
            return False, f"启动失败：{exc}"
        self._exe = target
        self._started_at = time.time()
        self._last_memory = None
        self._peak_memory = None
        self.invalidate_pid_cache()
        return True, f"已启动 {target.name}（PID {self._popen.pid}）"

    def wait_until_ready(self, timeout: float = START_POLL_TIMEOUT) -> str:
        """等待端口就绪；进程提前退出或超时则返回当前状态。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.is_listening():
                return STATE_RUNNING
            if self._popen is None or self._popen.poll() is not None:
                break
            time.sleep(0.4)
        return self.state()

    def stop(self) -> tuple[bool, str]:
        """结束主程序：先优雅通知，超时后强制回收进程树。

        句柄可用时走句柄；句柄不可用（用户手动启动的实例、启动器重启过）时按端口
        反查 PID，保证「停止」始终可用。
        """
        pid = self.active_pid()
        if pid is None:
            if self.is_listening():
                return False, f"端口 {self._port} 被占用但无法定位对应进程，请手动关闭。"
            return True, "主程序当前未运行。"
        self.memory_bytes()
        _terminate(pid, force=False)
        deadline = time.time() + STOP_GRACE_SECONDS
        while time.time() < deadline:
            if not process_alive(pid):
                self.invalidate_pid_cache()
                return True, f"主程序已正常退出（PID {pid}）。"
            time.sleep(0.25)
        _terminate(pid, force=True)
        deadline = time.time() + 6.0
        while time.time() < deadline:
            if not process_alive(pid):
                self.invalidate_pid_cache()
                return True, f"主程序未响应退出请求，已强制结束（PID {pid}）。"
            time.sleep(0.25)
        return False, f"无法结束主程序（PID {pid}），请手动关闭。"

    def restart(self) -> tuple[bool, str]:
        """重启主程序：先回收旧实例，待端口释放后重新拉起。"""
        target = self.exe
        ok, message = self.stop()
        if not ok:
            return False, message
        deadline = time.time() + 10.0
        while time.time() < deadline and self.is_listening():
            time.sleep(0.4)
        started, start_message = self.start(target)
        if not started:
            return False, start_message
        return True, f"已重启：{start_message}"
