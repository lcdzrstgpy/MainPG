"""Cross-platform process tree management for the ClipForge sidecar.

The sidecar spawns Node and FFmpeg descendants. Terminating only the direct
child leaks those descendants, so children are started in their own process
group/session and the whole tree is signalled, escalating from a graceful
request to a forced kill once the grace period expires.
"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Protocol


_TERMINATE_GRACE_SECONDS = 5.0
_CREATE_NEW_PROCESS_GROUP = 0x00000200
# 无窗口控制台：node.exe 是控制台子系统程序，而 MainPG.exe 是 PyInstaller windowed
# （console=False）构建、本身没有控制台，spawn 时不给这个标志 Windows 就会为 node
# 单独分配一个可见控制台窗口——标题由 Next standalone 的 server.js 设成
# "next-server (vX.Y.Z)"，用户看到的就是这个关不掉的黑色终端。
# stdout/stderr 已重定向到日志文件、stdin 为 DEVNULL，隐藏这个窗口不影响任何功能。
_CREATE_NO_WINDOW = 0x08000000
# Windows 的 signal 模块不提供全部 POSIX 信号（没有 SIGKILL）。这两个常量只会在
# "模拟 POSIX 分支"的测试里被求值，但拿不到时不应该直接 AttributeError，所以给出兜底值。
_SIGTERM = int(getattr(signal, "SIGTERM", 15))
_SIGKILL = int(getattr(signal, "SIGKILL", 9))


class _ProcessLike(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def _is_windows() -> bool:
    return os.name == "nt"


def popen_group_options() -> dict[str, object]:
    """Popen kwargs that put the sidecar in its own group so descendants can be reaped.

    Windows 上同时置 ``CREATE_NO_WINDOW``：sidecar 是控制台程序（node.exe），宿主
    MainPG.exe 却是无控制台的 windowed 进程，不隐藏就会弹出一个独立的黑色终端。
    """
    if _is_windows():
        flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", _CREATE_NEW_PROCESS_GROUP))
        flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW))
        return {"creationflags": flags}
    return {"start_new_session": True}


def terminate_process_tree(process, grace_s: float = _TERMINATE_GRACE_SECONDS) -> None:
    """Stop ``process`` and everything it spawned, escalating after ``grace_s``.

    A ``ProcessLookupError`` means the tree is already gone and is swallowed.
    """
    if process.poll() is not None:
        return
    pid = int(process.pid)
    if _is_windows():
        _run_taskkill(pid, force=False)
        if _wait_for_exit(process, grace_s):
            return
        _run_taskkill(pid, force=True)
        return
    _signal_posix_group(pid, _SIGTERM)
    if _wait_for_exit(process, grace_s):
        return
    _signal_posix_group(pid, _SIGKILL)


def _wait_for_exit(process, grace_s: float) -> bool:
    try:
        process.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        return False
    return True


def _signal_posix_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return
    except OSError:
        # Without a process group (for example when it was never detached) fall
        # back to signalling the direct child so shutdown still makes progress.
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return


def _run_taskkill(pid: int, *, force: bool) -> None:
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    try:
        subprocess.run(command, check=False, capture_output=True)
    except ProcessLookupError:
        return
    except OSError:
        return
