"""POD 日志每日静默上传（pod_log_upload）：契约 / 去重 / 跳过 / 异常隔离。"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from wh_local.customer.contracts import CustomerAuthResult, CustomerBillingPermissionError
from wh_local.customer.remote_client import CustomerAuthClient
from wh_local.pod_log_upload import (
    LOG_NAME,
    MAX_UPLOAD_BYTES,
    TRUNCATION_MARKER,
    PodLogUploadTask,
    build_payload,
    read_log_tail,
)


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class _FakeClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error = error

    def upload_pod_log(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.calls.append((token, payload))
        return {"ok": True}


def _write_log(directory: Path, text: str = "2026-10-12 INFO POD 批次开始\n") -> Path:
    path = directory / LOG_NAME
    path.write_text(text, encoding="utf-8")
    return path


def _task(
    directory: Path,
    *,
    clock: _Clock,
    client: _FakeClient,
    token: str = "remote-token",
    token_error: Exception | None = None,
    upload_hour: int = 12,
) -> PodLogUploadTask:
    def _provider() -> str:
        if token_error is not None:
            raise token_error
        return token

    return PodLogUploadTask(
        log_dir=directory,
        token_provider=_provider,
        client_factory=lambda: client,
        upload_hour=upload_hour,
        now=clock,
    )


# ---- build_payload / read_log_tail ----


def test_build_payload_wraps_log_as_base64_json(tmp_path: Path) -> None:
    _write_log(tmp_path, "hello pod\n")
    payload = build_payload(tmp_path / LOG_NAME, app_version="9.9.9", platform="macos")

    assert payload is not None
    assert payload["log_name"] == LOG_NAME
    assert payload["app_version"] == "9.9.9"
    assert payload["platform"] == "macos"
    assert base64.b64decode(payload["content_b64"]) == b"hello pod\n"


def test_build_payload_returns_none_for_missing_or_empty_log(tmp_path: Path) -> None:
    assert build_payload(tmp_path / LOG_NAME) is None
    _write_log(tmp_path, "")
    assert build_payload(tmp_path / LOG_NAME) is None


def test_read_log_tail_truncates_at_line_boundary(tmp_path: Path) -> None:
    path = _write_log(tmp_path, "".join(f"line-{index:05d}-{'x' * 50}\n" for index in range(400)))
    raw = read_log_tail(path, max_bytes=1024)

    assert raw.startswith(TRUNCATION_MARKER.encode("utf-8"))
    body = raw[len(TRUNCATION_MARKER.encode("utf-8")) :]
    # 必须从行首开始，不能拦腰截断一行。
    assert body.startswith(b"line-")
    assert len(raw) <= 1024 + len(TRUNCATION_MARKER.encode("utf-8"))


def test_default_upload_cap_stays_under_server_limit() -> None:
    # 服务端按 base64 解码后的字节数限制 16MB；本地上限必须留余量。
    assert MAX_UPLOAD_BYTES < 16 * 1024 * 1024


# ---- due check / 去重 ----


def test_before_upload_hour_is_not_due(tmp_path: Path) -> None:
    _write_log(tmp_path)
    client = _FakeClient()
    task = _task(tmp_path, clock=_Clock(datetime(2026, 10, 12, 9, 0)), client=client)

    assert task.run_if_due() == "skipped:not-due"
    assert client.calls == []


def test_after_upload_hour_uploads_once_per_day(tmp_path: Path) -> None:
    _write_log(tmp_path)
    client = _FakeClient()
    clock = _Clock(datetime(2026, 10, 12, 13, 0))
    task = _task(tmp_path, clock=clock, client=client)

    assert task.run_if_due() == "uploaded"
    assert len(client.calls) == 1
    token, payload = client.calls[0]
    assert token == "remote-token"
    assert payload["log_name"] == LOG_NAME

    # 同一天再触发：直接跳过，不重复上传。
    assert task.run_if_due() == "skipped:not-due"
    assert len(client.calls) == 1


def test_daily_dedup_survives_process_restart(tmp_path: Path) -> None:
    _write_log(tmp_path)
    client = _FakeClient()
    clock = _Clock(datetime(2026, 10, 12, 13, 0))

    assert _task(tmp_path, clock=clock, client=client).run_if_due() == "uploaded"

    # 模拟重启：新实例读同一份落盘状态，同一天不再上传。
    restarted = _task(tmp_path, clock=clock, client=client)
    assert restarted.uploaded_today() is True
    assert restarted.run_if_due() == "skipped:not-due"
    assert len(client.calls) == 1

    # 跨天恢复上传。
    clock.value = datetime(2026, 10, 13, 12, 30)
    assert restarted.run_if_due() == "uploaded"
    assert len(client.calls) == 2


def test_state_file_records_only_the_date(tmp_path: Path) -> None:
    _write_log(tmp_path)
    clock = _Clock(datetime(2026, 10, 12, 13, 0))
    _task(tmp_path, clock=clock, client=_FakeClient()).run_if_due()

    state = json.loads((tmp_path / "pod_log_upload_state.json").read_text(encoding="utf-8"))
    assert state == {"last_success_date": "2026-10-12"}


# ---- 取不到 token / 没有日志 / 上报失败 ----


def test_without_remote_token_skips_without_marking_the_day(tmp_path: Path) -> None:
    _write_log(tmp_path)
    client = _FakeClient()
    clock = _Clock(datetime(2026, 10, 12, 13, 0))
    task = _task(tmp_path, clock=clock, client=client, token="")

    assert task.run_if_due() == "skipped:no-token"
    assert client.calls == []
    assert task.uploaded_today() is False

    # 登录后再触发（同一天）应当补传成功。
    retry = _task(tmp_path, clock=clock, client=client, token="later-token")
    assert retry.run_if_due() == "uploaded"
    assert client.calls[0][0] == "later-token"


def test_token_provider_failure_is_treated_as_no_token(tmp_path: Path) -> None:
    _write_log(tmp_path)
    task = _task(
        tmp_path,
        clock=_Clock(datetime(2026, 10, 12, 13, 0)),
        client=_FakeClient(),
        token_error=RuntimeError("session store down"),
    )

    assert task.run_if_due() == "skipped:no-token"


def test_missing_log_file_is_skipped(tmp_path: Path) -> None:
    task = _task(tmp_path, clock=_Clock(datetime(2026, 10, 12, 13, 0)), client=_FakeClient())

    assert task.run_if_due() == "skipped:no-log"


def test_upload_failure_never_raises_and_allows_retry(tmp_path: Path) -> None:
    _write_log(tmp_path)
    clock = _Clock(datetime(2026, 10, 12, 13, 0))
    failing = _task(tmp_path, clock=clock, client=_FakeClient(error=RuntimeError("boom")))

    assert failing.run_if_due() == "failed"
    assert failing.uploaded_today() is False

    client = _FakeClient()
    assert _task(tmp_path, clock=clock, client=client).run_if_due() == "uploaded"
    assert len(client.calls) == 1


def test_upload_hour_zero_uploads_at_startup(tmp_path: Path) -> None:
    _write_log(tmp_path)
    client = _FakeClient()
    task = _task(
        tmp_path, clock=_Clock(datetime(2026, 10, 12, 9, 0)), client=client, upload_hour=0
    )

    assert task.run_if_due() == "uploaded"
    assert len(client.calls) == 1


# ---- 远端客户端契约（POD 专用接口 + 可配置路径） ----


def test_upload_pod_log_uses_dedicated_configurable_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = CustomerAuthClient("https://example.test")
    captured: dict[str, Any] = {}

    def _fake_post(path: str, payload: dict[str, Any], headers: dict[str, str] | None = None):
        captured.update(path=path, payload=payload, headers=headers or {})
        return {"ok": True}

    monkeypatch.setattr(client, "_post", _fake_post)
    monkeypatch.delenv("WH_LOCAL_POD_LOG_UPLOAD_PATH", raising=False)

    client.upload_pod_log("token-1", {"log_name": LOG_NAME})
    assert captured["path"] == "/api/customer/pod-log"
    assert captured["headers"]["Authorization"] == "Bearer token-1"

    monkeypatch.setenv("WH_LOCAL_POD_LOG_UPLOAD_PATH", "/api/customer/pod-log-v2")
    client.upload_pod_log("token-2", {"log_name": LOG_NAME})
    assert captured["path"] == "/api/customer/pod-log-v2"


def test_upload_pod_log_requires_remote_token() -> None:
    client = CustomerAuthClient("https://example.test")

    with pytest.raises(CustomerBillingPermissionError):
        client.upload_pod_log("", {"log_name": LOG_NAME})


# ---- 登录成功钩子（触发点接线）----
#
# 回归护栏：login 的 BackgroundTasks 参数注解必须就是 ``BackgroundTasks``。
# 若写成 ``BackgroundTasks | None``，FastAPI 会把它当普通参数（恒 None）、钩子永不触发，
# 且 app.openapi() 会抛 PydanticUserError（曾真实踩到）。


class _StubLoginAuth:
    def login(self, payload: dict[str, Any]) -> CustomerAuthResult:
        return CustomerAuthResult(
            customer_id="customer-1", username="hook-user", remote_token="remote-token"
        )


def _login_app(on_login_success: Any = None) -> Any:
    from fastapi import FastAPI

    from wh_local.customer.local_session import LocalSessionService
    from wh_local.customer.routes import create_customer_router

    app = FastAPI()
    app.include_router(
        create_customer_router(
            _StubLoginAuth(), LocalSessionService(), on_login_success=on_login_success
        )
    )
    return app


def test_login_success_runs_the_hook() -> None:
    from fastapi.testclient import TestClient

    fired: list[str] = []
    client = TestClient(_login_app(lambda: fired.append("hit")))

    response = client.post("/api/customer/login", json={"username": "u", "password": "p"})

    assert response.status_code == 200
    assert fired == ["hit"]


def test_login_without_hook_still_works() -> None:
    from fastapi.testclient import TestClient

    response = TestClient(_login_app()).post(
        "/api/customer/login", json={"username": "u", "password": "p"}
    )

    assert response.status_code == 200


def test_openapi_schema_generates_with_login_hook() -> None:
    # 注解写错时这里会抛 PydanticUserError（FastAPI 生成 schema 失败）。
    schema = _login_app(lambda: None).openapi()

    assert "/api/customer/login" in schema["paths"]
