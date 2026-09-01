from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import wh_local.modules.product_processing.service as service_module
from wh_local.customer.contracts import CustomerAuthUnavailable, CustomerBillingProtocolError
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.server_ai_proxy import remote_token, server_ai_context, usage_id
from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.modules.product_processing.service import ProductProcessingNotFound


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
        return {
            "usage": {
                "usage_id": f"use-{kind}",
                "status": "reserved",
                "feature_key": payload["feature_key"],
            }
        }

    def settle_ai_usage_success(
        self, token: str, usage: str, _payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.succeeded.append((token, usage))
        if usage in self.fail_success_ids:
            raise RuntimeError("success settlement unavailable")
        return {"ok": True, "usage": {"usage_id": usage, "status": "succeeded"}}

    def settle_ai_usage_failure(
        self, token: str, usage: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.failed.append((token, usage, str(payload.get("error_message") or "")))
        if usage in self.fail_failure_ids:
            raise RuntimeError("failure settlement unavailable")
        return {"ok": True, "usage_id": usage, "status": "failed"}


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


def _task_with_item(
    service: ProductProcessingService,
    *,
    account_id: str = "account-1",
    preflight_only: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    draft, _created = service.create_draft(
        {
            "source_type": "manual",
            "title": "billing-ledger-product",
            "image_url": "https://example.test/product.jpg",
        }
    )
    task = service.repository.create_task(
        title="billing-ledger",
        preflight_only=preflight_only,
        settings={
            **_settings(scope=["title"]),
            "async_mode": True,
            "_billing": {
                "account_id": account_id,
                "source_ref": "task:test",
                "pricing_version": "v1",
            },
        },
        drafts=[draft],
        idempotency_key=None,
    )
    return task, task["items"][0]


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


def test_exact_text_cache_hit_releases_text_usage_but_charges_generated_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    remote = RecordingBillingClient()
    _install_remote(monkeypatch, remote)
    service._task_remote_tokens[7] = "remote-token"
    settings = _settings(scope=["title", "four_grid"])
    service._reserve_product_processing_item_usage(7, 11, settings)

    service._settle_product_processing_item_success(
        7,
        11,
        settings,
        {
            "ai_notes": ["subject_identity:cache-hit", "structured_text:cache-hit"],
            "billing_skipped_kinds": ["text"],
        },
    )

    assert remote.failed == [
        (
            "remote-token",
            "use-text",
            "exact AI stage cache hit; provider was not called",
        )
    ]
    assert remote.succeeded == [("remote-token", "use-image")]
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

    with pytest.raises(RuntimeError, match="settlement unavailable"):
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
                "account_id": "account-token-safe",
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


@pytest.mark.parametrize(
    ("first_account", "replay_account"),
    [
        ("account-a", "account-b"),
        ("account-a", ""),
        ("", "account-b"),
    ],
)
def test_idempotent_replay_rejects_cross_account_before_response_or_token_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_account: str,
    replay_account: str,
) -> None:
    service = _service(tmp_path)
    owner_draft, _ = service.create_draft(
        {"source_type": "manual", "title": "account-a-secret-title"},
        workspace_id="shared",
    )
    other_draft, _ = service.create_draft(
        {"source_type": "manual", "title": "account-b-own-title"},
        workspace_id="shared",
    )
    launches: list[int] = []
    monkeypatch.setattr(
        service,
        "_launch_background_execute",
        lambda task_id, *_args: launches.append(task_id) or True,
    )
    monkeypatch.setattr(
        service_module,
        "CustomerAuthClient",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("idempotent replay must not call remote billing")
        ),
    )

    first_payload: dict[str, Any] = {
        "draft_ids": [owner_draft["id"]],
        "async_mode": True,
        "processing_scope": ["title"],
    }
    if first_account:
        first_payload["_billing"] = {
            "account_id": first_account,
            "remote_token": "token-a",
        }
    first = service.process_drafts(
        first_payload,
        idempotency_key="shared-cross-account-replay",
        workspace_id="shared",
    )
    original_token = service._task_remote_token(first["task_id"])

    replay_payload: dict[str, Any] = {
        "draft_ids": [other_draft["id"]],
        "async_mode": True,
        "processing_scope": ["title"],
        "remote_token": "top-level-token-b",
    }
    if replay_account:
        replay_payload["_billing"] = {
            "account_id": replay_account,
            "remote_token": "nested-token-b",
        }

    with pytest.raises(ProductProcessingNotFound, match="not found"):
        service.process_drafts(
            replay_payload,
            idempotency_key="shared-cross-account-replay",
            workspace_id="shared",
        )

    assert launches == [first["task_id"]]
    assert service._task_remote_token(first["task_id"]) == original_token


def test_idempotent_replay_allows_legacy_unbilled_task_without_token_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    draft, _ = service.create_draft(
        {"source_type": "manual", "title": "legacy-unbilled"},
        workspace_id="shared",
    )
    monkeypatch.setattr(service, "_launch_background_execute", lambda *_args: True)
    first = service.process_drafts(
        {"draft_ids": [draft["id"]], "async_mode": True},
        idempotency_key="legacy-unbilled-replay",
        workspace_id="shared",
    )

    replay = service.process_drafts(
        {
            "draft_ids": [draft["id"]],
            "async_mode": True,
            "remote_token": "legacy-top-level-token-must-not-bind",
        },
        idempotency_key="legacy-unbilled-replay",
        workspace_id="shared",
    )

    assert replay["task_id"] == first["task_id"]
    assert service._task_remote_token(first["task_id"]) == ""


@pytest.mark.parametrize("entrypoint", ["workbook", "single"])
def test_cross_account_replay_is_rejected_before_all_submission_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    service = _service(tmp_path)
    owner_draft, _ = service.create_draft(
        {"source_type": "manual", "title": "owner-secret-title"},
        workspace_id="shared",
    )
    monkeypatch.setattr(service, "_launch_background_execute", lambda *_args: True)
    first = service.process_drafts(
        {
            "draft_ids": [owner_draft["id"]],
            "async_mode": True,
            "title": "owner-secret-task-title",
            "_billing": {"account_id": "account-a", "remote_token": "token-a"},
        },
        idempotency_key="shared-all-entrypoints",
        workspace_id="shared",
    )
    task_id = int(first["task_id"])
    original_token = service._task_remote_token(task_id)
    before_drafts = len(service.repository.list_drafts(None, 100, 0, workspace_id="shared")[0])
    side_effects: list[str] = []
    monkeypatch.setattr(
        service_module,
        "CustomerAuthClient",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cross-account replay must not call remote billing")
        ),
    )

    if entrypoint == "workbook":
        monkeypatch.setattr(
            service,
            "import_workbook",
            lambda *_args, **_kwargs: side_effects.append("import") or {"ids": [999]},
        )
        invoke = lambda: service.process_workbook(
            "products.xlsx",
            b"untrusted-workbook",
            {
                "async_mode": True,
                "_billing": {"account_id": "account-b", "remote_token": "token-b"},
            },
            idempotency_key="shared-all-entrypoints",
            workspace_id="shared",
        )
    else:
        original_create = service.create_draft

        def forbidden_create(*args, **kwargs):
            side_effects.append("create_draft")
            return original_create(*args, **kwargs)

        monkeypatch.setattr(service, "create_draft", forbidden_create)
        invoke = lambda: service.process_single(
            {
                "title": "account-b-title",
                "async_mode": True,
                "_billing": {"account_id": "account-b", "remote_token": "token-b"},
            },
            idempotency_key="shared-all-entrypoints",
            workspace_id="shared",
        )

    with pytest.raises(ProductProcessingNotFound) as caught:
        invoke()

    assert "owner-secret" not in str(caught.value)
    assert side_effects == []
    assert service._task_remote_token(task_id) == original_token == "token-a"
    assert len(service.repository.list_drafts(None, 100, 0, workspace_id="shared")[0]) == before_drafts


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


def test_background_remote_billing_failure_uses_stable_non_leaking_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    service._task_remote_tokens[7] = "remote-token-sensitive"
    recorded = threading.Event()
    reasons: list[str] = []
    monkeypatch.setattr(
        service,
        "_execute_task",
        lambda *_args: (_ for _ in ()).throw(
            CustomerAuthUnavailable("remote-token-sensitive api-key payload")
        ),
    )

    def record_failure(_task_id: int, reason: str, _workspace_id: str) -> None:
        reasons.append(reason)
        recorded.set()

    monkeypatch.setattr(service.repository, "fail_task_execution", record_failure)

    assert service._launch_background_execute(7, "local") is True
    assert recorded.wait(timeout=2)
    assert reasons == ["remote billing service is unavailable"]


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


def test_billing_attempt_ordinal_and_state_are_durable_without_token(tmp_path: Path) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)

    first = service.repository.begin_product_billing_attempt(
        task_id=task["id"],
        item_id=item["item_id"],
        workspace_id="local",
        kind="text",
        feature_key="product_processing.text",
        account_id="account-1",
    )
    replay = service.repository.begin_product_billing_attempt(
        task_id=task["id"],
        item_id=item["item_id"],
        workspace_id="local",
        kind="text",
        feature_key="product_processing.text",
        account_id="account-1",
    )
    assert replay["id"] == first["id"]
    assert first["attempt_ordinal"] == 1
    assert first["idempotency_key"].endswith(":attempt:1")

    service.repository.record_product_billing_reservation(
        first["id"], usage_id="use-first", remote_status="reserved"
    )
    service.repository.mark_product_billing_desired_outcome(
        first["id"], desired_outcome="failed", error_message="business failure"
    )
    service.repository.mark_product_billing_settled(first["id"], remote_status="failed")

    restarted = _service(tmp_path, file_database=True)
    second = restarted.repository.begin_product_billing_attempt(
        task_id=task["id"],
        item_id=item["item_id"],
        workspace_id="local",
        kind="text",
        feature_key="product_processing.text",
        account_id="account-1",
    )
    assert second["attempt_ordinal"] == 2
    assert second["idempotency_key"].endswith(":attempt:2")
    database_bytes = (tmp_path / "workbench.sqlite3").read_bytes()
    assert b"remote-token" not in database_bytes
    assert second["account_id"] == "account-1"


def test_begin_product_billing_attempt_is_unique_under_twelve_callers(tmp_path: Path) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)
    barrier = threading.Barrier(12)

    def begin(_index: int) -> dict[str, Any]:
        barrier.wait(timeout=3)
        return service.repository.begin_product_billing_attempt(
            task_id=task["id"],
            item_id=item["item_id"],
            workspace_id="local",
            kind="text",
            feature_key="product_processing.text",
            account_id="account-1",
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        attempts = list(executor.map(begin, range(12)))

    assert {attempt["id"] for attempt in attempts} == {attempts[0]["id"]}
    assert {attempt["idempotency_key"] for attempt in attempts} == {
        attempts[0]["idempotency_key"]
    }
    persisted = service.repository.product_billing_attempts(
        task_id=task["id"], item_id=item["item_id"]
    )
    assert len(persisted) == 1


def test_crash_after_remote_reserve_reuses_durable_attempt_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)
    attempt = service.repository.begin_product_billing_attempt(
        task_id=task["id"],
        item_id=item["item_id"],
        workspace_id="local",
        kind="text",
        feature_key="product_processing.text",
        account_id="account-1",
    )
    restarted = _service(tmp_path, file_database=True)
    remote = RecordingBillingClient()
    _install_remote(monkeypatch, remote)
    restarted._task_remote_tokens[task["id"]] = "remote-token"

    usage = restarted._reserve_product_processing_item_usage(
        task["id"], item["item_id"], task["settings"]
    )

    assert usage == {"text": "use-text"}
    assert remote.reserved[0][1]["idempotency_key"] == attempt["idempotency_key"]
    persisted = restarted.repository.product_billing_attempts(task_id=task["id"])
    assert persisted[0]["usage_id"] == "use-text"
    assert persisted[0]["settlement_state"] == "reserved"


def test_malformed_or_non_reserved_reservation_never_enters_gateway_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    task, item = _task_with_item(service)
    service._task_remote_tokens[task["id"]] = "remote-token"

    class InvalidReserve(RecordingBillingClient):
        def reserve_ai_usage(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
            response = super().reserve_ai_usage(token, payload)
            response["usage"]["status"] = "failed"
            return response

    _install_remote(monkeypatch, InvalidReserve())

    with pytest.raises(CustomerBillingProtocolError) as caught:
        service._reserve_product_processing_item_usage(
            task["id"], item["item_id"], task["settings"]
        )

    assert str(caught.value) == "remote billing service returned an invalid response"
    assert service._reserved_usage_ids(task["id"], item["item_id"]) == {}


@pytest.mark.parametrize("returned_feature", [None, "product_processing.image_grid_2k"])
def test_reservation_requires_the_exact_requested_feature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returned_feature: str | None,
) -> None:
    service = _service(tmp_path)
    task, item = _task_with_item(service)
    service._task_remote_tokens[task["id"]] = "remote-token"

    class InvalidFeatureReserve(RecordingBillingClient):
        def reserve_ai_usage(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
            response = super().reserve_ai_usage(token, payload)
            if returned_feature is None:
                response["usage"].pop("feature_key")
            else:
                response["usage"]["feature_key"] = returned_feature
            return response

    _install_remote(monkeypatch, InvalidFeatureReserve())

    with pytest.raises(CustomerBillingProtocolError):
        service._reserve_product_processing_item_usage(
            task["id"], item["item_id"], task["settings"]
        )

    assert service._reserved_usage_ids(task["id"], item["item_id"]) == {}


def test_remote_reserve_failure_persists_only_stable_ledger_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)
    secret = "remote-token-sensitive"
    service._task_remote_tokens[task["id"]] = secret

    class LeakingUnavailable(RecordingBillingClient):
        def reserve_ai_usage(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
            raise CustomerAuthUnavailable(
                f"upstream failed {token} api-key-sensitive {payload!r}"
            )

    _install_remote(monkeypatch, LeakingUnavailable())

    with pytest.raises(CustomerAuthUnavailable) as caught:
        service._reserve_product_processing_item_usage(
            task["id"], item["item_id"], task["settings"]
        )

    assert str(caught.value) == "remote billing service is unavailable"
    attempts = service.repository.product_billing_attempts(task_id=task["id"])
    assert attempts[0]["last_error"] == "remote billing service is unavailable"
    serialized = json.dumps(attempts)
    assert secret not in serialized
    assert "api-key-sensitive" not in serialized
    assert "feature_key" not in attempts[0]["last_error"]


def test_malformed_settlement_persists_stable_protocol_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)
    service._task_remote_tokens[task["id"]] = "remote-token-sensitive"

    class InvalidSettlement(RecordingBillingClient):
        def settle_ai_usage_failure(
            self, token: str, usage: str, payload: dict[str, Any]
        ) -> dict[str, Any]:
            return {"status": "remote-token-sensitive api-key payload"}

    _install_remote(monkeypatch, InvalidSettlement())
    service._reserve_product_processing_item_usage(
        task["id"], item["item_id"], task["settings"]
    )

    with pytest.raises(CustomerBillingProtocolError) as caught:
        service._settle_product_processing_item_failure_for_item(
            task["id"], item["item_id"], {"reason": "business failure"}
        )

    assert str(caught.value) == "remote billing service returned an invalid response"
    attempts = service.repository.product_billing_attempts(task_id=task["id"])
    assert attempts[0]["last_error"] == "remote billing service returned an invalid response"
    assert "remote-token" not in json.dumps(attempts)


@pytest.mark.parametrize(
    ("entrypoint", "invalid_response"),
    [
        ("success", {"usage": {"status": "succeeded"}}),
        ("failure", {"usage_id": "use-other", "status": "failed"}),
        (
            "reconcile",
            {
                "usage_id": "use-other",
                "status": "failed",
                "usage": {"usage_id": "use-text", "status": "failed"},
            },
        ),
    ],
)
def test_settlement_requires_exact_usage_id_and_preserves_pending_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    invalid_response: dict[str, Any],
) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)
    service._task_remote_tokens[task["id"]] = "remote-token"

    class InvalidUsageSettlement(RecordingBillingClient):
        def settle_ai_usage_success(
            self, token: str, usage: str, payload: dict[str, Any]
        ) -> dict[str, Any]:
            self.succeeded.append((token, usage))
            return invalid_response

        def settle_ai_usage_failure(
            self, token: str, usage: str, payload: dict[str, Any]
        ) -> dict[str, Any]:
            self.failed.append((token, usage, str(payload.get("error_message") or "")))
            return invalid_response

    _install_remote(monkeypatch, InvalidUsageSettlement())
    usage_ids = service._reserve_product_processing_item_usage(
        task["id"], item["item_id"], task["settings"]
    )
    assert usage_ids == {"text": "use-text"}

    with pytest.raises(CustomerBillingProtocolError):
        if entrypoint == "success":
            service._settle_product_processing_item_success(
                task["id"], item["item_id"], task["settings"], {}
            )
        elif entrypoint == "failure":
            service._settle_product_processing_item_failure_for_item(
                task["id"], item["item_id"], {"reason": "business failure"}
            )
        else:
            attempt = service.repository.product_billing_attempts(task_id=task["id"])[0]
            service.repository.mark_product_billing_desired_outcome(
                attempt["id"], desired_outcome="failed", error_message="interrupted"
            )
            service.reconcile_product_billing(task["id"], "fresh-token")

    attempts = service.repository.product_billing_attempts(task_id=task["id"])
    assert attempts[0]["settlement_state"] == "settlement_pending"
    assert attempts[0]["last_error"] == "remote billing service returned an invalid response"
    assert service._reserved_usage_ids(task["id"], item["item_id"]) == {
        "text": "use-text"
    }


def test_restart_reconciles_completed_item_pending_settlement_with_fresh_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)
    attempt = service.repository.begin_product_billing_attempt(
        task_id=task["id"],
        item_id=item["item_id"],
        workspace_id="local",
        kind="text",
        feature_key="product_processing.text",
        account_id="account-1",
    )
    service.repository.record_product_billing_reservation(
        attempt["id"], usage_id="use-text", remote_status="reserved"
    )
    service.repository.mark_product_billing_desired_outcome(
        attempt["id"], desired_outcome="succeeded", error_message=""
    )
    restarted = _service(tmp_path, file_database=True)
    remote = RecordingBillingClient()
    _install_remote(monkeypatch, remote)

    restarted.reconcile_product_billing(task["id"], "remote-token")

    assert remote.succeeded == [("remote-token", "use-text")]
    persisted = restarted.repository.product_billing_attempts(task_id=task["id"])
    assert persisted[0]["settlement_state"] == "settled_succeeded"


@pytest.mark.parametrize("entrypoint", ["resume", "retry"])
def test_terminal_task_entrypoint_reconciles_completed_item_pending_settlement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)
    attempt = service.repository.begin_product_billing_attempt(
        task_id=task["id"],
        item_id=item["item_id"],
        workspace_id="local",
        kind="text",
        feature_key="product_processing.text",
        account_id="account-1",
    )
    service.repository.record_product_billing_reservation(
        attempt["id"], usage_id="use-text", remote_status="reserved"
    )
    service.repository.mark_product_billing_desired_outcome(
        attempt["id"], desired_outcome="succeeded", error_message="settlement network timeout"
    )
    service.repository.set_task_status(task["id"], "completed")
    remote = RecordingBillingClient()
    _install_remote(monkeypatch, remote)

    if entrypoint == "resume":
        service.resume_task(task["id"], remote_token="fresh-token")
    else:
        service.retry_attention(task["id"], remote_token="fresh-token")

    assert remote.succeeded == [("fresh-token", "use-text")]
    persisted = service.repository.product_billing_attempts(task_id=task["id"])
    assert persisted[0]["settlement_state"] == "settled_succeeded"


@pytest.mark.parametrize("returned_feature", [None, "product_processing.image_grid_2k"])
def test_recovery_requires_the_exact_requested_feature(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    returned_feature: str | None,
) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)
    service.repository.begin_product_billing_attempt(
        task_id=task["id"],
        item_id=item["item_id"],
        workspace_id="local",
        kind="text",
        feature_key="product_processing.text",
        account_id="account-1",
    )

    class WrongFeatureReserve(RecordingBillingClient):
        def reserve_ai_usage(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
            response = super().reserve_ai_usage(token, payload)
            if returned_feature is None:
                response["usage"].pop("feature_key")
            else:
                response["usage"]["feature_key"] = returned_feature
            return response

    _install_remote(monkeypatch, WrongFeatureReserve())

    with pytest.raises(CustomerBillingProtocolError) as caught:
        service.reconcile_product_billing(task["id"], "remote-token")

    assert str(caught.value) == "remote billing service returned an invalid response"
    persisted = service.repository.product_billing_attempts(task_id=task["id"])
    assert persisted[0]["usage_id"] == ""
    assert persisted[0]["settlement_state"] == "reserving"
    assert persisted[0]["last_error"] == "remote billing service returned an invalid response"


def test_same_process_reconcile_forgets_exact_old_usage_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)

    class SequencedBillingClient(RecordingBillingClient):
        def __init__(self) -> None:
            super().__init__()
            self.failure_calls = 0

        def reserve_ai_usage(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
            self.reserved.append((token, payload))
            ordinal = len(self.reserved)
            return {
                "usage": {
                    "usage_id": f"use-text-{ordinal}",
                    "status": "reserved",
                    "feature_key": payload["feature_key"],
                }
            }

        def settle_ai_usage_failure(
            self, token: str, usage: str, payload: dict[str, Any]
        ) -> dict[str, Any]:
            self.failure_calls += 1
            self.failed.append((token, usage, str(payload.get("error_message") or "")))
            # 首次结算需连续失败超过客户端重试预算（3 次），
            # 才能让「网络持续不可达」留下 pending 记录供 reconcile 恢复。
            if self.failure_calls <= 3:
                raise CustomerAuthUnavailable("secret upstream failure")
            return {"ok": True, "usage_id": usage, "status": "failed"}

    remote = SequencedBillingClient()
    _install_remote(monkeypatch, remote)
    service._task_remote_tokens[task["id"]] = "first-token"
    first_usage = service._reserve_product_processing_item_usage(
        task["id"], item["item_id"], task["settings"]
    )
    assert first_usage == {"text": "use-text-1"}
    with pytest.raises(CustomerAuthUnavailable):
        service._settle_product_processing_item_failure_for_item(
            task["id"], item["item_id"], {"reason": "business failure"}
        )
    service.repository.update_item_progress(
        task["id"],
        item["item_id"],
        status="failed",
        reason="retryable",
        result={"retryable": True},
    )
    service.repository.set_task_status(task["id"], "failed")
    monkeypatch.setattr(service, "_launch_background_execute", lambda *_args: True)

    service.retry_attention(task["id"], remote_token="fresh-token")
    next_usage = service._reserve_product_processing_item_usage(
        task["id"], item["item_id"], task["settings"]
    )

    assert next_usage == {"text": "use-text-2"}
    attempts = service.repository.product_billing_attempts(
        task_id=task["id"], item_id=item["item_id"]
    )
    assert [row["attempt_ordinal"] for row in attempts] == [1, 2]
    assert attempts[0]["settlement_state"] == "settled_failed"
    assert service._reserved_usage_ids(task["id"], item["item_id"]) == {
        "text": "use-text-2"
    }
    with server_ai_context("fresh-token", next_usage):
        assert usage_id("text") == "use-text-2"


def test_reconcile_does_not_remove_a_newer_in_memory_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)
    attempt = service.repository.begin_product_billing_attempt(
        task_id=task["id"],
        item_id=item["item_id"],
        workspace_id="local",
        kind="text",
        feature_key="product_processing.text",
        account_id="account-1",
    )
    service.repository.record_product_billing_reservation(
        attempt["id"], usage_id="use-old", remote_status="reserved"
    )
    service.repository.mark_product_billing_desired_outcome(
        attempt["id"], desired_outcome="failed", error_message="old attempt"
    )
    service._store_reserved_usage_ids(
        task["id"], item["item_id"], {"text": "use-new"}
    )
    remote = RecordingBillingClient()
    _install_remote(monkeypatch, remote)

    service.reconcile_product_billing(task["id"], "fresh-token")

    assert service._reserved_usage_ids(task["id"], item["item_id"]) == {
        "text": "use-new"
    }


def test_clear_fails_closed_for_durable_pending_settlement_after_restart(tmp_path: Path) -> None:
    service = _service(tmp_path, file_database=True)
    task, item = _task_with_item(service)
    attempt = service.repository.begin_product_billing_attempt(
        task_id=task["id"],
        item_id=item["item_id"],
        workspace_id="local",
        kind="text",
        feature_key="product_processing.text",
        account_id="account-1",
    )
    service.repository.record_product_billing_reservation(
        attempt["id"], usage_id="use-text", remote_status="reserved"
    )
    service.repository.mark_product_billing_desired_outcome(
        attempt["id"], desired_outcome="succeeded", error_message=""
    )
    service.repository.set_task_status(task["id"], "failed")
    restarted = _service(tmp_path, file_database=True)

    with pytest.raises(service_module.ProductProcessingConflict, match="计费结算"):
        restarted.clear_task(task["id"])


def test_recover_background_work_skips_billed_queued_task_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    billed, _item = _task_with_item(service)
    preflight, _preflight_item = _task_with_item(service, preflight_only=True)
    launched: list[int] = []
    monkeypatch.setattr(
        service,
        "_launch_background_execute",
        lambda task_id, _workspace_id: launched.append(task_id) or True,
    )
    monkeypatch.setattr(service.preview_images, "recover_background_work", lambda: {})
    monkeypatch.setattr(service.media_assets, "materialize_until_idle", lambda **_kwargs: {})

    result = service.recover_background_work()

    assert billed["id"] not in launched
    assert preflight["id"] in launched
    assert result["billing_auth_required"] == 1


def test_restart_pauses_billed_queued_task_until_authenticated_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = _service(tmp_path, file_database=True)
    task, _item = _task_with_item(original)
    restarted = _service(tmp_path, file_database=True)
    launched: list[tuple[int, str, str]] = []
    monkeypatch.setattr(
        restarted,
        "_launch_background_execute",
        lambda task_id, workspace_id: launched.append(
            (task_id, workspace_id, restarted._task_remote_token(task_id))
        )
        or True,
    )
    monkeypatch.setattr(restarted.preview_images, "recover_background_work", lambda: {})
    monkeypatch.setattr(restarted.media_assets, "materialize_until_idle", lambda **_kwargs: {})

    recovery = restarted.recover_background_work()

    paused = restarted.repository.get_task(task["id"])
    assert recovery["billing_auth_required"] == 1
    assert paused is not None and paused["status"] == "paused"
    assert launched == []

    resumed = restarted.resume_task(task["id"], remote_token="fresh-token")

    assert resumed["async_mode"] is True
    assert launched == [(task["id"], "local", "fresh-token")]
