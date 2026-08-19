from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import wh_local.modules.product_processing.service as service_module
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.server_ai_proxy import remote_token, server_ai_context, usage_id
from wh_local.modules.product_processing.service import ProductProcessingService


class RecordingBillingClient:
    def __init__(
        self,
        *,
        fail_reserve_at: int = 0,
        fail_success_ids: set[str] | None = None,
        fail_failure_ids: set[str] | None = None,
    ) -> None:
        self.fail_reserve_at = fail_reserve_at
        self.fail_success_ids = set(fail_success_ids or set())
        self.fail_failure_ids = set(fail_failure_ids or set())
        self.reserved: list[tuple[str, dict[str, Any]]] = []
        self.succeeded: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str, str]] = []

    def reserve_ai_usage(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.reserved.append((token, payload))
        if self.fail_reserve_at and len(self.reserved) == self.fail_reserve_at:
            raise RuntimeError("reserve rejected")
        kind = "text" if payload["feature_key"].endswith(".text") else "image"
        return {"usage": {"usage_id": f"use-{kind}"}}

    def settle_ai_usage_success(
        self, token: str, usage: str, _payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.succeeded.append((token, usage))
        if usage in self.fail_success_ids:
            raise RuntimeError("success settlement unavailable")
        return {"ok": True}

    def settle_ai_usage_failure(
        self, token: str, usage: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.failed.append((token, usage, str(payload.get("error_message") or "")))
        if usage in self.fail_failure_ids:
            raise RuntimeError("failure settlement unavailable")
        return {"ok": True}


def _service(tmp_path: Path, *, file_database: bool = False) -> ProductProcessingService:
    database_url = (
        f"sqlite:///{tmp_path / 'workbench.sqlite3'}" if file_database else "sqlite:///:memory:"
    )
    return ProductProcessingService(
        ProductProcessingRepository(create_database(database_url)),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def _settings(*, scope: list[str]) -> dict[str, Any]:
    return {
        "_billing": {"source_ref": "task:test", "pricing_version": "v1"},
        "processing_scope": scope,
        "title_optimize": "title" in scope,
        "description": "details" in scope,
        "size": "product_dimensions" in scope,
        "grid_image": "four_grid" in scope,
        "image_rewrite": "sku_images" in scope,
    }


def _install_remote(
    monkeypatch: pytest.MonkeyPatch, remote: RecordingBillingClient
) -> None:
    monkeypatch.setattr(
        service_module,
        "CustomerAuthClient",
        lambda *_args, **_kwargs: remote,
        raising=False,
    )


def test_item_usage_reserves_selected_features_and_settles_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    remote = RecordingBillingClient()
    _install_remote(monkeypatch, remote)
    service._task_remote_tokens[7] = "remote-token"
    settings = _settings(scope=["title", "four_grid"])

    usage_ids = service._reserve_product_processing_item_usage(7, 11, settings)

    assert usage_ids == {"text": "use-text", "image_grid": "use-image"}
    assert [call[1]["feature_key"] for call in remote.reserved] == [
        "product_processing.text",
        "product_processing.image_grid_2k",
    ]
    service._settle_product_processing_item_success(7, 11, settings, {"ai_notes": ["ok"]})
    assert remote.succeeded == [
        ("remote-token", "use-text"),
        ("remote-token", "use-image"),
    ]
    assert service._reserved_usage_ids(7, 11) == {}


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (["title"], {"text": "use-text"}),
        (["four_grid"], {"image_grid": "use-image"}),
    ],
)
def test_item_usage_reserves_only_enabled_feature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: list[str],
    expected: dict[str, str],
) -> None:
    service = _service(tmp_path)
    remote = RecordingBillingClient()
    _install_remote(monkeypatch, remote)
    service._task_remote_tokens[7] = "remote-token"

    assert service._reserve_product_processing_item_usage(7, 11, _settings(scope=scope)) == expected


@pytest.mark.parametrize(
    "token,preflight",
    [("", False), ("remote-token", True)],
)
def test_item_usage_skips_reservation_without_token_or_for_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    token: str,
    preflight: bool,
) -> None:
    service = _service(tmp_path)
    remote = RecordingBillingClient()
    _install_remote(monkeypatch, remote)
    service._task_remote_tokens[7] = token
    settings = {**_settings(scope=["title", "four_grid"]), "preflight_only": preflight}

    assert service._reserve_product_processing_item_usage(7, 11, settings) == {}
    assert remote.reserved == []


def test_second_reservation_failure_releases_first_before_reraising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    remote = RecordingBillingClient(fail_reserve_at=2)
    _install_remote(monkeypatch, remote)
    service._task_remote_tokens[7] = "remote-token"

    with pytest.raises(RuntimeError, match="reserve rejected"):
        service._reserve_product_processing_item_usage(
            7, 11, _settings(scope=["title", "four_grid"])
        )

    assert remote.failed == [("remote-token", "use-text", "reserve rejected")]
    assert service._reserved_usage_ids(7, 11) == {}


def test_processing_failure_releases_every_reserved_usage_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    remote = RecordingBillingClient()
    _install_remote(monkeypatch, remote)
    service._task_remote_tokens[7] = "remote-token"
    service._store_reserved_usage_ids(
        7, 11, {"text": "use-text", "image_grid": "use-image"}
    )
    failed = {"status": "failed", "reason": "provider failed"}

    service._settle_product_processing_item_failure_for_item(7, 11, failed)
    service._settle_product_processing_item_failure_for_item(7, 11, failed)

    assert [(token, usage) for token, usage, _reason in remote.failed] == [
        ("remote-token", "use-text"),
        ("remote-token", "use-image"),
    ]
    assert service._reserved_usage_ids(7, 11) == {}


def test_success_settlement_failure_preserves_only_unsettled_usage_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    remote = RecordingBillingClient(fail_success_ids={"use-image"})
    _install_remote(monkeypatch, remote)
    service._task_remote_tokens[7] = "remote-token"
    service._store_reserved_usage_ids(
        7, 11, {"text": "use-text", "image_grid": "use-image"}
    )

    with pytest.raises(RuntimeError, match="settlement unavailable"):
        service._settle_product_processing_item_success(7, 11, {}, {})

    assert service._reserved_usage_ids(7, 11) == {"image_grid": "use-image"}
    remote.fail_success_ids.clear()
    service._settle_product_processing_item_success(7, 11, {}, {})
    assert remote.succeeded == [
        ("remote-token", "use-text"),
        ("remote-token", "use-image"),
        ("remote-token", "use-image"),
    ]
    assert service._reserved_usage_ids(7, 11) == {}


def test_failure_settlement_preserves_only_unreleased_usage_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    remote = RecordingBillingClient(fail_failure_ids={"use-image"})
    _install_remote(monkeypatch, remote)
    service._task_remote_tokens[7] = "remote-token"
    service._store_reserved_usage_ids(
        7, 11, {"text": "use-text", "image_grid": "use-image"}
    )

    service._settle_product_processing_item_failure_for_item(
        7, 11, {"status": "failed", "reason": "provider failed"}
    )

    assert service._reserved_usage_ids(7, 11) == {"image_grid": "use-image"}


def test_submit_with_context_exposes_only_corresponding_usage_ids_in_media_worker() -> None:
    def inspect_context() -> tuple[str, str, str]:
        return remote_token(), usage_id("text"), usage_id("image_grid")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with server_ai_context(
            "remote-token", {"text": "use-text", "image_grid": "use-image"}
        ):
            result = service_module._submit_with_context(executor, inspect_context).result()

    assert result == ("remote-token", "use-text", "use-image")
    assert remote_token() == ""
    assert usage_id("text") == ""


def test_process_drafts_strips_remote_token_before_database_and_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, file_database=True)
    draft, _created = service.create_draft(
        {"source_type": "manual", "title": "token-safe", "image_url": "https://example.test/a.jpg"}
    )
    monkeypatch.setattr(service, "_launch_background_execute", lambda *_args: True)
    secret = "remote-token-must-not-leak"

    response = service.process_drafts(
        {
            "draft_ids": [draft["id"]],
            "async_mode": True,
            "processing_scope": ["title"],
            "_billing": {
                "remote_token": secret,
                "source_ref": "test",
                "pricing_version": "v1",
            },
        }
    )

    task_id = int(response["task_id"])
    persisted = service.repository.get_task(task_id)
    history = service.task_history(10)
    outputs = service.task_outputs(task_id)
    assert service._task_remote_token(task_id) == secret
    assert secret not in json.dumps(response, default=str)
    assert secret not in json.dumps(persisted, default=str)
    assert secret not in json.dumps(history, default=str)
    assert secret not in json.dumps(outputs, default=str)
    assert secret.encode() not in (tmp_path / "workbench.sqlite3").read_bytes()


def test_terminal_cleanup_removes_token_and_empty_item_usage_state(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._task_remote_tokens[7] = "remote-token"
    service._server_usage_ids[(7, 11)] = {}
    service._server_usage_ids[(8, 12)] = {"text": "other-task"}

    service._cleanup_terminal_billing_state(7)

    assert 7 not in service._task_remote_tokens
    assert (7, 11) not in service._server_usage_ids
    assert service._server_usage_ids[(8, 12)] == {"text": "other-task"}


def test_terminal_cleanup_keeps_token_when_settlement_needs_retry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._task_remote_tokens[7] = "remote-token"
    service._server_usage_ids[(7, 11)] = {"image_grid": "use-image"}

    service._cleanup_terminal_billing_state(7)

    assert service._task_remote_tokens[7] == "remote-token"
    assert service._server_usage_ids[(7, 11)] == {"image_grid": "use-image"}


def test_background_terminal_failure_cleans_token_when_no_usage_is_unsettled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    secret = "remote-token"
    service._task_remote_tokens[7] = secret
    recorded = threading.Event()
    recorded_reasons: list[str] = []
    monkeypatch.setattr(
        service,
        "_execute_task",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(f"provider rejected {secret}")),
    )

    def record_failure(_task_id: int, reason: str, _workspace_id: str) -> None:
        recorded_reasons.append(reason)
        recorded.set()

    monkeypatch.setattr(
        service.repository,
        "fail_task_execution",
        record_failure,
    )

    assert service._launch_background_execute(7, "local") is True
    assert recorded.wait(timeout=2)
    deadline = time.monotonic() + 2
    while 7 in service._task_remote_tokens and time.monotonic() < deadline:
        time.sleep(0.01)

    assert 7 not in service._task_remote_tokens
    assert recorded_reasons == ["provider rejected [redacted]"]


def test_error_redaction_happens_before_message_truncation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._task_remote_tokens[7] = "remote-token-sensitive"

    reason = service._task_safe_error_reason(
        7,
        RuntimeError("x" * 190 + "remote-token-sensitive"),
    )

    assert "remote-token" not in reason
    assert reason.endswith("[redacted]")


def test_retry_attention_keeps_reacquired_token_only_in_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    task = {
        "id": 7,
        "status": "failed",
        "settings": {"async_mode": True},
        "items": [
            {
                "status": "failed",
                "product_draft_id": 11,
                "result": {"retryable": True},
            }
        ],
    }
    monkeypatch.setattr(service, "_require_task", lambda *_args: task)
    monkeypatch.setattr(
        service,
        "_task_response",
        lambda _task, message="": {"task_id": 7, "message": message},
    )
    monkeypatch.setattr(service.repository, "reset_failed_items", lambda *_args, **_kwargs: None)
    launched: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        service,
        "_launch_background_execute",
        lambda task_id, workspace_id: launched.append(
            (task_id, workspace_id, service._task_remote_token(task_id))
        )
        or True,
    )

    response = service.retry_attention(
        7,
        "local",
        draft_ids=[11],
        remote_token="reacquired-token",
    )

    assert response["async_mode"] is True
    assert launched == [(7, "local", "reacquired-token")]
    assert "reacquired-token" not in json.dumps(task, default=str)
