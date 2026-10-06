"""Cross-platform process tree management contract for the ClipForge sidecar."""

from __future__ import annotations

import signal
import subprocess

import pytest

from wh_local.modules.clipforge import process as process_module
from wh_local.modules.clipforge.process import popen_group_options, terminate_process_tree


PID = 4321


class FakeProcess:
    """Records poll/wait behaviour so escalation can be tested without real waits."""

    def __init__(self, *, exit_during_grace: bool = True, poll_code: int | None = None) -> None:
        self.pid = PID
        self.returncode = poll_code
        self._exit_during_grace = exit_during_grace
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if not self._exit_during_grace:
            raise subprocess.TimeoutExpired(cmd="clipforge", timeout=timeout)
        self.returncode = 0
        return 0


class KillRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def __call__(self, pid: int, sig: int) -> None:
        self.calls.append((pid, sig))


def test_posix_popen_options_detach_a_new_session(monkeypatch) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: False)

    assert popen_group_options() == {"start_new_session": True}


def test_windows_popen_options_create_a_process_group(monkeypatch) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: True)
    monkeypatch.setattr(process_module.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, raising=False)

    options = popen_group_options()

    assert options["creationflags"] == 0x00000200


def test_posix_terminates_the_process_group_without_escalating(monkeypatch) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: False)
    killpg = KillRecorder()
    kill = KillRecorder()
    monkeypatch.setattr(process_module.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(process_module.os, "kill", kill, raising=False)
    process = FakeProcess(exit_during_grace=True)

    terminate_process_tree(process)

    assert killpg.calls == [(PID, signal.SIGTERM)]
    assert kill.calls == []


def test_posix_escalates_to_sigkill_when_grace_expires(monkeypatch) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: False)
    killpg = KillRecorder()
    monkeypatch.setattr(process_module.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(process_module.os, "kill", KillRecorder(), raising=False)
    process = FakeProcess(exit_during_grace=False)

    terminate_process_tree(process, grace_s=0.1)

    assert killpg.calls == [(PID, signal.SIGTERM), (PID, signal.SIGKILL)]


def test_posix_tolerates_a_vanished_process_group(monkeypatch) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: False)

    def missing_killpg(_pid, _sig):
        raise ProcessLookupError("no such process group")

    monkeypatch.setattr(process_module.os, "killpg", missing_killpg, raising=False)
    monkeypatch.setattr(process_module.os, "kill", missing_killpg, raising=False)

    terminate_process_tree(FakeProcess())


def test_windows_uses_taskkill_tree_then_force(monkeypatch) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: True)
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process_module.subprocess, "run", fake_run)

    terminate_process_tree(FakeProcess(exit_during_grace=True))

    assert commands == [["taskkill", "/PID", str(PID), "/T"]]


def test_windows_escalates_taskkill_when_grace_expires(monkeypatch) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: True)
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process_module.subprocess, "run", fake_run)

    terminate_process_tree(FakeProcess(exit_during_grace=False), grace_s=0.1)

    assert commands == [["taskkill", "/PID", str(PID), "/T"], ["taskkill", "/PID", str(PID), "/T", "/F"]]


def test_windows_tolerates_taskkill_process_lookup_error(monkeypatch) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: True)

    def missing_taskkill(_command, **_kwargs):
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(process_module.subprocess, "run", missing_taskkill)

    terminate_process_tree(FakeProcess())


def test_already_exited_process_is_a_noop(monkeypatch) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: False)
    killpg = KillRecorder()
    kill = KillRecorder()
    monkeypatch.setattr(process_module.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(process_module.os, "kill", kill, raising=False)
    windows_commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        windows_commands.append(list(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process_module.subprocess, "run", fake_run)
    process = FakeProcess(poll_code=0)

    terminate_process_tree(process)

    assert killpg.calls == []
    assert kill.calls == []
    assert windows_commands == []
    assert process.wait_calls == []


def test_never_uses_a_shell(monkeypatch) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: True)
    seen_kwargs: list[dict] = []

    def fake_run(command, **kwargs):
        seen_kwargs.append(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(process_module.subprocess, "run", fake_run)

    terminate_process_tree(FakeProcess())

    assert seen_kwargs
    assert all(kwargs.get("shell") is not True for kwargs in seen_kwargs)


@pytest.mark.parametrize("grace_s", [0.0, 5.0])
def test_posix_default_and_custom_grace_are_forwarded(monkeypatch, grace_s) -> None:
    monkeypatch.setattr(process_module, "_is_windows", lambda: False)
    killpg = KillRecorder()
    monkeypatch.setattr(process_module.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(process_module.os, "kill", KillRecorder(), raising=False)
    process = FakeProcess(exit_during_grace=True)

    terminate_process_tree(process, grace_s=grace_s)

    assert process.wait_calls == [grace_s]
    assert killpg.calls == [(PID, signal.SIGTERM)]
