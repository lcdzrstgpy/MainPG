"""Start and supervise the vendored ClipForge Next standalone server."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import platform
from shutil import copytree
import socket
import subprocess
import threading
import time
from typing import Final
from urllib.error import HTTPError
from urllib.request import urlopen


_READY_TIMEOUT_SECONDS: Final[float] = 30.0


@dataclass(frozen=True)
class ClipForgeStatus:
    available: bool
    state: str
    url: str | None
    message: str


class ClipForgeService:
    """Owns exactly one loopback-only ClipForge standalone child process."""

    def __init__(self, source_root: Path, data_root: Path, node_binary: str = "node") -> None:
        # Popen changes cwd to the standalone directory, so the script argument
        # must never remain relative to the parent process's working directory.
        self._source_root = Path(source_root).resolve()
        self._data_root = Path(data_root)
        self._node_binary = node_binary
        self._process: subprocess.Popen[bytes] | None = None
        self._url: str | None = None
        self._message = "ClipForge standalone build is not available"
        self._lock = threading.RLock()

    @property
    def standalone_entry(self) -> Path:
        return self._source_root / ".next" / "standalone" / "server.js"

    def status(self) -> ClipForgeStatus:
        with self._lock:
            if not self.standalone_entry.is_file():
                return ClipForgeStatus(False, "unavailable", None, self._message)
            if self._process is None:
                return ClipForgeStatus(True, "stopped", None, "ClipForge service is ready to start")
            if self._process.poll() is not None:
                self._message = f"ClipForge service exited with code {self._process.returncode}"
                self._process = None
                self._url = None
                return ClipForgeStatus(False, "failed", None, self._message)
            return ClipForgeStatus(True, "ready", self._url, "ClipForge service is running")

    def start(self) -> ClipForgeStatus:
        with self._lock:
            current = self.status()
            if current.state == "ready":
                return current
            if not self.standalone_entry.is_file():
                return current
            _ensure_standalone_static_assets(self._source_root)
            port = _free_loopback_port()
            self._data_root.mkdir(parents=True, exist_ok=True)
            log_path = self._data_root / "clipforge-server.log"
            log_file = log_path.open("ab")
            env = {
                **os.environ,
                "NODE_ENV": "production",
                "HOSTNAME": "127.0.0.1",
                "PORT": str(port),
                "APP_DATA_DIR": str(self._data_root / "data"),
                "APP_MIGRATIONS_DIR": str(self._source_root / "drizzle"),
                **_bundled_media_environment(self._source_root),
            }
            try:
                self._process = subprocess.Popen(
                    [self._node_binary, str(self.standalone_entry)],
                    cwd=str(self.standalone_entry.parent),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
            except OSError as exc:
                log_file.close()
                self._message = f"ClipForge service could not start: {exc}"
                return ClipForgeStatus(False, "failed", None, self._message)
            log_file.close()
            self._url = f"http://127.0.0.1:{port}"
            try:
                _wait_ready(self._url, self._process)
            except Exception as exc:
                self._message = f"ClipForge service failed to start: {exc}"
                self.stop()
                return ClipForgeStatus(False, "failed", None, self._message)
            return ClipForgeStatus(True, "ready", self._url, "ClipForge service is running")

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
            self._url = None
            if process is None or process.poll() is not None:
                return
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _ensure_standalone_static_assets(source_root: Path) -> None:
    """Keep Next's hashed static assets beside the standalone server.

    Next build leaves .next/static at the project root while the standalone
    server resolves them relative to .next/standalone. Copy on every sidecar
    start so a fresh build cannot leave the embedded UI unstyled.
    """
    source = source_root / ".next" / "static"
    if not source.is_dir():
        return
    target = source_root / ".next" / "standalone" / ".next" / "static"
    copytree(source, target, dirs_exist_ok=True)


def _wait_ready(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + _READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"process exited with code {process.returncode}")
        try:
            with urlopen(url, timeout=1) as response:  # noqa: S310 - loopback URL created above
                if int(response.status) < 500:
                    return
        except HTTPError as error:
            # Next may reject the bare probe with 400 because it validates the
            # Host header, but that response proves the local server is bound
            # and able to serve requests. Only 5xx means it is not ready.
            if error.code < 500:
                return
            time.sleep(0.25)
        except OSError:
            time.sleep(0.25)
    raise TimeoutError("readiness probe timed out")


def _bundled_media_environment(source_root: Path) -> dict[str, str]:
    """Prefer media binaries copied with the standalone app, then let ClipForge fall back to PATH."""
    is_windows = platform.system() == "Windows"
    suffix = ".exe" if is_windows else ""
    ffmpeg = source_root / "node_modules" / "ffmpeg-static" / f"ffmpeg{suffix}"
    platform_name = "win32" if is_windows else ("darwin" if platform.system() == "Darwin" else "linux")
    ffprobe = source_root / "node_modules" / "@ffprobe-installer" / f"{platform_name}-{platform.machine().lower()}" / f"ffprobe{suffix}"
    # npm may restore @ffprobe-installer's macOS/Linux binary without its execute bit.
    # Passing that path through as FFPROBE_PATH makes every media probe fail with EACCES.
    if not is_windows and ffprobe.is_file():
        ffprobe.chmod(ffprobe.stat().st_mode | 0o111)
    return {
        **({"FFMPEG_PATH": str(ffmpeg)} if ffmpeg.is_file() else {}),
        **({"FFPROBE_PATH": str(ffprobe)} if ffprobe.is_file() else {}),
    }
