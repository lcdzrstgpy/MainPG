"""Strict readiness probe for the ClipForge Next sidecar.

The probe treats the sidecar as ready only when it answers the health endpoint
with a schema-valid ClipForge payload whose mandatory checks all report ``ok``
and when the embedded start page actually returns HTML. A rejecting status code
(400/404/503) or a transport failure means "not ready yet" and is retried until
the deadline; a schema violation means the process is answering but is not the
ClipForge instance we started, so it fails immediately.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, NamedTuple, Protocol
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

if TYPE_CHECKING:
    import subprocess


_HTTP_TIMEOUT_SECONDS = 1.0
_PROBE_INTERVAL_SECONDS = 0.25

# 就绪探测必须绕过代理：用户设了 HTTP_PROXY 时 urlopen 会把 127.0.0.1 的探测请求
# 送给代理 → 被拒 → 超时 → start() 反而把健康的 node 杀掉并报"启动失败"。
_NO_PROXY_OPENER = build_opener(ProxyHandler({}))
# 保留 `urlopen` 这个模块级名字：既绑到禁代理的 opener，又让测试能 monkeypatch 它。
urlopen = _NO_PROXY_OPENER.open

_HEALTH_PATH = "/api/health"
_START_PATH = "/start?embed=mainpg"

_REQUIRED_CHECKS = ("database", "migrations", "dataDirWritable", "ffmpeg", "ffprobe")


class ClipForgeHealthError(Exception):
    """Raised when the sidecar answers but cannot be trusted as a ready ClipForge."""


class _ProcessLike(Protocol):
    """The minimum a supervised child has to expose for readiness polling."""

    pid: int
    returncode: int | None

    def poll(self) -> int | None: ...


class _Probe(NamedTuple):
    status: int | None
    content_type: str
    body: bytes


def _default_opener(url: str):
    return urlopen(url, timeout=_HTTP_TIMEOUT_SECONDS)  # noqa: S310 - loopback URL built by the caller


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def wait_until_ready(
    base_url: str,
    process: "subprocess.Popen[bytes] | _ProcessLike",
    instance_id: str,
    timeout_s: float = 30.0,
    *,
    opener=None,
) -> None:
    """Block until the sidecar is provably ready, or raise.

    ``opener`` defaults to a timeout-bounded ``urlopen``; callers may inject a
    replacement to avoid real network access. Probing always happens at least
    once, even with ``timeout_s=0``, so a rejecting instance is inspected before
    the deadline is evaluated.
    """
    open_url = opener if opener is not None else _default_opener
    root = base_url.rstrip("/")
    health_url = f"{root}{_HEALTH_PATH}"
    start_url = f"{root}{_START_PATH}"
    deadline = time.monotonic() + timeout_s

    while True:
        if process.poll() is not None:
            raise ClipForgeHealthError(
                f"clipforge process exited before readiness (returncode={process.returncode})"
            )
        if _health_is_ready(open_url, health_url, instance_id) and _start_page_is_ready(open_url, start_url):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("clipforge readiness probe timed out")
        _sleep(_PROBE_INTERVAL_SECONDS)


def _probe(open_url, url: str) -> _Probe:
    """Fetch ``url`` and normalise the answer; transport failures become ``status=None``."""
    try:
        response = open_url(url)
    except HTTPError as error:
        return _Probe(int(error.code), "", b"")
    except OSError:
        # URLError is an OSError subclass; both mean "could not connect".
        return _Probe(None, "", b"")
    try:
        status = int(getattr(response, "status", 200))
        body = response.read()
        return _Probe(status, _content_type(response), body or b"")
    except OSError:
        return _Probe(None, "", b"")
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _content_type(response) -> str:
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return ""
    return str(getter("content-type") or "")


def _health_is_ready(open_url, url: str, instance_id: str) -> bool:
    probe = _probe(open_url, url)
    if probe.status != 200:
        return False
    try:
        payload = json.loads(probe.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ClipForgeHealthError("health response is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ClipForgeHealthError("health response is not a JSON object")
    if payload.get("service") != "clipforge":
        raise ClipForgeHealthError("unexpected health service")
    if payload.get("schemaVersion") != 1:
        raise ClipForgeHealthError("unsupported health schema")
    if payload.get("instanceId") != instance_id:
        raise ClipForgeHealthError("health instance mismatch")
    if payload.get("status") != "ok":
        raise ClipForgeHealthError("health checks failed")
    checks = payload.get("checks", {})
    if not isinstance(checks, dict):
        raise ClipForgeHealthError("required health check failed")
    if any(
        not isinstance(checks.get(name), dict) or checks[name].get("status") != "ok" for name in _REQUIRED_CHECKS
    ):
        raise ClipForgeHealthError("required health check failed")
    return True


def _start_page_is_ready(open_url, url: str) -> bool:
    probe = _probe(open_url, url)
    # 连接失败与 503 都可能是启动瞬间的暂时状态，继续重试到 deadline；
    # 400/404 等确定性拒绝则立即失败，绝不当成 ready。
    if probe.status is None or probe.status == 503:
        return False
    if probe.status != 200:
        raise ClipForgeHealthError("start page returned an unexpected status")
    if "text/html" not in probe.content_type.lower():
        raise ClipForgeHealthError("start page is not HTML")
    if not probe.body.strip():
        raise ClipForgeHealthError("start page is empty")
    return True
