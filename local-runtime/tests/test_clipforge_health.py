"""Strict readiness probe contract for the ClipForge Next sidecar."""

from __future__ import annotations

from urllib.error import HTTPError, URLError

import pytest

from wh_local.modules.clipforge import health as health_module
from wh_local.modules.clipforge.health import ClipForgeHealthError, wait_until_ready


BASE_URL = "http://127.0.0.1:54321"
INSTANCE_ID = "i"

HEALTHY_PAYLOAD = (
    b'{"service":"clipforge","schemaVersion":1,"instanceId":"i","status":"ok",'
    b'"checks":{"database":{"status":"ok"},"migrations":{"status":"ok"},'
    b'"dataDirWritable":{"status":"ok"},"ffmpeg":{"status":"ok"},"ffprobe":{"status":"ok"}}}'
)


class RunningProcess:
    """Minimal stand-in for a live sidecar child process."""

    pid = 4242
    returncode = None

    def poll(self):
        return None


class ExitedProcess:
    pid = 4243
    returncode = 7

    def poll(self):
        return 7


class Response:
    def __init__(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.status = status
        self.headers = {"content-type": content_type}
        self._body = body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:  # pragma: no cover - mirrors urlopen's response
        return None


class SequenceOpener:
    """Serialised opener that pops responses in order and records requested URLs."""

    def __init__(self, outcomes) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[str] = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if not self._outcomes:
            raise URLError("probe budget exhausted")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not-json",
        b'{"service":"wrong","schemaVersion":1,"instanceId":"i","status":"ok","checks":{}}',
        b'{"service":"clipforge","schemaVersion":1,"instanceId":"old","status":"ok","checks":{}}',
        b'{"service":"clipforge","schemaVersion":1,"instanceId":"i","status":"error","checks":{}}',
    ],
)
def test_probe_rejects_false_ready_payloads(payload: bytes) -> None:
    process = RunningProcess()
    opener = SequenceOpener([Response(200, payload)])

    with pytest.raises(ClipForgeHealthError):
        wait_until_ready(BASE_URL, process, INSTANCE_ID, timeout_s=0, opener=opener)


def test_probe_requires_matching_health_and_non_empty_start_html() -> None:
    opener = SequenceOpener([Response(200, HEALTHY_PAYLOAD), Response(200, b"<html>ready</html>", "text/html")])

    wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID, opener=opener)

    assert opener.calls == [f"{BASE_URL}/api/health", f"{BASE_URL}/start?embed=mainpg"]


def test_probe_rejects_missing_required_check() -> None:
    payload = (
        b'{"service":"clipforge","schemaVersion":1,"instanceId":"i","status":"ok",'
        b'"checks":{"database":{"status":"ok"},"migrations":{"status":"ok"},'
        b'"dataDirWritable":{"status":"ok"},"ffmpeg":{"status":"ok"}}}'
    )
    opener = SequenceOpener([Response(200, payload)])

    with pytest.raises(ClipForgeHealthError):
        wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID, timeout_s=0, opener=opener)


def test_probe_rejects_failing_required_check() -> None:
    payload = HEALTHY_PAYLOAD.replace(b'"ffprobe":{"status":"ok"}', b'"ffprobe":{"status":"error"}')
    opener = SequenceOpener([Response(200, payload)])

    with pytest.raises(ClipForgeHealthError):
        wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID, timeout_s=0, opener=opener)


def test_http_400_is_not_ready() -> None:
    opener = SequenceOpener([HTTPError(BASE_URL, 400, "Bad Request", None, None)])

    with pytest.raises(TimeoutError):
        wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID, timeout_s=0, opener=opener)

    assert opener.calls


def test_http_404_is_not_ready() -> None:
    opener = SequenceOpener([HTTPError(BASE_URL, 404, "Not Found", None, None)])

    with pytest.raises(TimeoutError):
        wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID, timeout_s=0, opener=opener)

    assert opener.calls


def test_http_503_is_not_ready() -> None:
    opener = SequenceOpener([HTTPError(BASE_URL, 503, "Service Unavailable", None, None)])

    with pytest.raises(TimeoutError):
        wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID, timeout_s=0, opener=opener)


def test_connection_error_is_not_ready() -> None:
    opener = SequenceOpener([URLError("connection refused")])

    with pytest.raises(TimeoutError):
        wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID, timeout_s=0, opener=opener)

    assert opener.calls


def test_empty_start_html_is_not_ready() -> None:
    opener = SequenceOpener([Response(200, HEALTHY_PAYLOAD), Response(200, b"   ", "text/html")])

    with pytest.raises(ClipForgeHealthError):
        wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID, opener=opener)


def test_non_html_start_body_is_not_ready() -> None:
    opener = SequenceOpener([Response(200, HEALTHY_PAYLOAD), Response(200, b"<html>ready</html>", "application/json")])

    with pytest.raises(ClipForgeHealthError):
        wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID, opener=opener)


def test_start_page_connection_error_keeps_retrying_until_deadline() -> None:
    opener = SequenceOpener([Response(200, HEALTHY_PAYLOAD), URLError("connection reset")])

    with pytest.raises(TimeoutError):
        wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID, timeout_s=0, opener=opener)


def test_exited_process_is_not_ready() -> None:
    opener = SequenceOpener([Response(200, HEALTHY_PAYLOAD)])

    with pytest.raises(ClipForgeHealthError) as error:
        wait_until_ready(BASE_URL, ExitedProcess(), INSTANCE_ID, timeout_s=0, opener=opener)

    assert "7" in str(error.value)


def test_default_opener_uses_urlopen_with_timeout(monkeypatch) -> None:
    opener = SequenceOpener([Response(200, HEALTHY_PAYLOAD), Response(200, b"<html>ready</html>", "text/html")])
    recorded: list[tuple[str, object]] = []
    timeouts: list[object] = []

    def fake_urlopen(url, timeout=None):
        timeouts.append(timeout)
        recorded.append((url, timeout))
        return opener(url, timeout)

    monkeypatch.setattr(health_module, "urlopen", fake_urlopen)

    wait_until_ready(BASE_URL, RunningProcess(), INSTANCE_ID)

    assert [url for url, _timeout in recorded] == [f"{BASE_URL}/api/health", f"{BASE_URL}/start?embed=mainpg"]
    assert all(isinstance(timeout, (int, float)) and timeout > 0 for timeout in timeouts)
