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


class _ProcessLike(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


def _is_windows() -> bool:
    return os.name == "nt"


def popen_group_options() -> dict[str, object]:
    """Popen kwargs that put the sidecar in its own group so descendants can be reaped."""
    if _is_windows():
        return {"creationflags": int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", _CREATE_NEW_PROCESS_GROUP))}
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
    _signal_posix_group(pid, signal.SIGTERM)
    if _wait_for_exit(process, grace_s):
        return
    _signal_posix_group(pid, signal.SIGKILL)


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
