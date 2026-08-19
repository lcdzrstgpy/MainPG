from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from wh_local.customer.contracts import (
    CustomerBillingProtocolError,
    CustomerBillingPermissionError,
    CustomerAuthRejected,
    CustomerAuthResult,
    CustomerAuthUnavailable,
)
from wh_local.customer.local_session import LocalSessionService
from wh_local.modules.product_processing.api import router as product_processing_router
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService
from wh_local.session import Actor
from wh_local.session import actor_from_authorization


class RecordingRemoteBilling:
    def __init__(self, available: int | str):
        self.available = available
        self.tokens: list[str] = []

    def billing_summary(self, token: str) -> dict[str, Any]:
        self.tokens.append(token)
        return {"wallet": {"available_points": self.available}}


class RaisingRemoteBilling:
    def __init__(self, error: Exception):
        self.error = error

    def billing_summary(self, _token: str) -> dict[str, Any]:
        raise self.error


class PayloadRemoteBilling:
    def __init__(self, payload: object):
        self.payload = payload

    def billing_summary(self, _token: str) -> Any:
        return self.payload


def _actor() -> Actor:
    return Actor(id="user", username="user", role="operator")


def _text_only_payload() -> dict[str, Any]:
    return {
        "draft_ids": [1],
        "title_optimize": True,
        "grid_image": False,
        "image_rewrite": False,
    }


def test_billing_context_uses_remote_summary_and_token() -> None:
    payload = _text_only_payload()
    remote = RecordingRemoteBilling(50)

    product_processing_router._attach_billing_context_and_require_points(
        payload,
        _actor(),
        source_ref="test",
        remote_token="remote-session",
        remote_customer_auth=remote,
    )

    assert remote.tokens == ["remote-session"]
    assert payload["_billing"]["remote_token"] == "remote-session"
    assert payload["_billing"]["estimated_points"] == 50


@pytest.mark.parametrize(
    ("remote_token", "remote_customer_auth"),
    [("", RecordingRemoteBilling(50)), ("remote-session", None)],
)
def test_billing_context_requires_remote_session_and_client(
    remote_token: str,
    remote_customer_auth: RecordingRemoteBilling | None,
) -> None:
    with pytest.raises(HTTPException) as caught:
        product_processing_router._attach_billing_context_and_require_points(
            _text_only_payload(),
            _actor(),
            source_ref="test",
            remote_token=remote_token,
            remote_customer_auth=remote_customer_auth,
        )

    assert caught.value.status_code == 503
    assert caught.value.detail == "server billing session is unavailable"


def test_billing_context_rejects_insufficient_remote_balance() -> None:
    remote = RecordingRemoteBilling(29)

    with pytest.raises(HTTPException) as caught:
        product_processing_router._attach_billing_context_and_require_points(
            _text_only_payload(),
            _actor(),
            source_ref="test",
            remote_token="remote-session",
            remote_customer_auth=remote,
        )

    assert caught.value.status_code == 402
    assert "50" in caught.value.detail
    assert "29" in caught.value.detail
    assert remote.tokens == ["remote-session"]


@pytest.mark.parametrize(
    ("available", "payload"),
    [
        (30, _text_only_payload()),
        (49, _text_only_payload()),
        (
            599,
            {
                "draft_ids": [1],
                "title_optimize": False,
                "description": False,
                "size": False,
                "grid_image": True,
                "image_rewrite": False,
            },
        ),
        (
            649,
            {
                "draft_ids": [1],
                "title_optimize": False,
                "description": False,
                "size": False,
                "grid_image": True,
                "image_rewrite": False,
            },
        ),
        (
            629,
            {**_text_only_payload(), "grid_image": True},
        ),
        (
            699,
            {**_text_only_payload(), "grid_image": True},
        ),
    ],
)
def test_submission_precheck_uses_authoritative_reservation_points(
    available: int,
    payload: dict[str, Any],
) -> None:
    with pytest.raises(HTTPException) as caught:
        product_processing_router._attach_billing_context_and_require_points(
            payload,
            _actor(),
            source_ref="test",
            remote_token="remote-session",
            remote_customer_auth=RecordingRemoteBilling(available),
        )

    assert caught.value.status_code == 402


def test_billing_context_maps_remote_unavailable_to_503() -> None:
    remote = RaisingRemoteBilling(CustomerAuthUnavailable("billing temporarily unavailable remote-secret"))

    with pytest.raises(HTTPException) as caught:
        product_processing_router._attach_billing_context_and_require_points(
            _text_only_payload(),
            _actor(),
            source_ref="test",
            remote_token="remote-session",
            remote_customer_auth=remote,
        )

    assert caught.value.status_code == 503
    assert caught.value.detail == "remote billing service is unavailable"


@pytest.mark.parametrize("remote_status", [401, 402, 403, 404])
def test_billing_context_preserves_remote_customer_rejection(remote_status: int) -> None:
    remote = RaisingRemoteBilling(CustomerAuthRejected(remote_status, "remote rejected"))

    with pytest.raises(HTTPException) as caught:
        product_processing_router._attach_billing_context_and_require_points(
            _text_only_payload(),
            _actor(),
            source_ref="test",
            remote_token="remote-session",
            remote_customer_auth=remote,
        )

    assert caught.value.status_code == remote_status
    assert caught.value.detail == "remote billing request was rejected"


@pytest.mark.parametrize("bad_status", [500, "invalid"])
def test_billing_context_rejects_invalid_customer_rejection_status(bad_status: object) -> None:
    error = CustomerAuthRejected.__new__(CustomerAuthRejected)
    RuntimeError.__init__(error, "invalid remote rejection")
    error.status_code = bad_status  # type: ignore[assignment]
    error.message = "invalid remote rejection"
    remote = RaisingRemoteBilling(error)

    with pytest.raises(HTTPException) as caught:
        product_processing_router._attach_billing_context_and_require_points(
            _text_only_payload(),
            _actor(),
            source_ref="test",
            remote_token="remote-session",
            remote_customer_auth=remote,
        )

    assert caught.value.status_code == 502
    assert caught.value.detail == "remote billing service returned an invalid error status"


def test_billing_context_maps_remote_permission_error_to_403() -> None:
    remote = RaisingRemoteBilling(CustomerBillingPermissionError())

    with pytest.raises(HTTPException) as caught:
        product_processing_router._attach_billing_context_and_require_points(
            _text_only_payload(),
            _actor(),
            source_ref="test",
            remote_token="remote-session",
            remote_customer_auth=remote,
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == "remote billing session was rejected"


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (CustomerBillingProtocolError(), 502, "remote billing service returned an invalid response"),
        (CustomerAuthUnavailable("remote-token-sensitive"), 503, "remote billing service is unavailable"),
        (CustomerAuthRejected(402, "remote-token-sensitive"), 402, "remote billing request was rejected"),
        (CustomerBillingPermissionError(), 403, "remote billing session was rejected"),
    ],
)
def test_call_never_echoes_remote_billing_exception_content(
    error: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    def raise_remote() -> None:
        raise error

    with pytest.raises(HTTPException) as caught:
        product_processing_router._call(raise_remote)

    assert caught.value.status_code == expected_status
    assert caught.value.detail == expected_detail
    assert "remote-token-sensitive" not in str(caught.value.detail)


def test_call_keeps_ordinary_local_value_error_as_400() -> None:
    with pytest.raises(HTTPException) as caught:
        product_processing_router._call(
            lambda: (_ for _ in ()).throw(ValueError("local input is invalid"))
        )

    assert caught.value.status_code == 400
    assert caught.value.detail == "local input is invalid"


def test_call_does_not_misclassify_ordinary_local_permission_error_as_billing() -> None:
    error = PermissionError("ordinary local permission failure")

    with pytest.raises(PermissionError) as caught:
        product_processing_router._call(
            lambda: (_ for _ in ()).throw(error)
        )

    assert caught.value is error


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"wallet": None},
        {"wallet": {}},
        {"wallet": {"available_points": None}},
        {"wallet": {"available_points": ""}},
        {"wallet": {"available_points": "not-a-number"}},
        {"wallet": {"available_points": 1.5}},
        {"wallet": {"available_points": True}},
    ],
)
def test_billing_context_rejects_malformed_remote_summary(payload: object) -> None:
    with pytest.raises(HTTPException) as caught:
        product_processing_router._attach_billing_context_and_require_points(
            _text_only_payload(),
            _actor(),
            source_ref="test",
            remote_token="remote-session",
            remote_customer_auth=PayloadRemoteBilling(payload),
        )

    assert caught.value.status_code == 502
    assert caught.value.detail == "remote billing service returned an invalid wallet summary"


@pytest.mark.parametrize("preflight_key", ["preflight_only", "category_preflight_only"])
def test_preflight_skips_remote_billing(preflight_key: str) -> None:
    payload = {**_text_only_payload(), preflight_key: True}
    remote = RecordingRemoteBilling(0)

    product_processing_router._attach_billing_context_and_require_points(
        payload,
        _actor(),
        source_ref="test",
        remote_token="",
        remote_customer_auth=remote,
    )

    assert remote.tokens == []
    assert "_billing" not in payload


def test_remote_token_resolves_platform_token_from_local_bearer_session() -> None:
    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="user",
            remote_token="remote-session",
        )
    )
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", f"Bearer {session.token}".encode())],
        }
    )

    assert product_processing_router._remote_token(request, sessions) == "remote-session"


@pytest.mark.parametrize("authorization", ["", "Basic local-session", "Bearer missing"])
def test_remote_token_returns_empty_for_unavailable_local_session(authorization: str) -> None:
    headers = [(b"authorization", authorization.encode())] if authorization else []
    request = Request({"type": "http", "headers": headers})

    assert product_processing_router._remote_token(request, LocalSessionService()) == ""


def test_create_app_injects_remote_billing_dependencies_into_both_routers(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wh_local.app.main as app_main

    original = app_main.create_product_processing_router
    recorded: list[dict[str, Any]] = []

    def recording_router(*args, **kwargs):
        recorded.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(app_main, "create_product_processing_router", recording_router)

    app_main.create_app(tmp_path / "workbench.sqlite3")

    assert len(recorded) == 2
    assert all(call["customer_sessions"] is recorded[0]["customer_sessions"] for call in recorded)
    assert all(call["remote_customer_auth"] is recorded[0]["remote_customer_auth"] for call in recorded)


def test_all_processing_routes_forward_remote_billing_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'router.sqlite3').as_posix()}")
    service = ProductProcessingService(
        ProductProcessingRepository(database),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    captured: dict[str, dict[str, Any]] = {}

    def record_drafts(payload: dict[str, Any], **_kwargs) -> dict[str, bool]:
        captured["drafts"] = payload
        return {"ok": True}

    def record_workbook(
        _filename: str,
        _content: bytes,
        payload: dict[str, Any],
        **_kwargs,
    ) -> dict[str, bool]:
        captured["workbook"] = payload
        return {"ok": True}

    def record_single(payload: dict[str, Any], **_kwargs) -> dict[str, bool]:
        captured["single"] = payload
        return {"ok": True}

    monkeypatch.setattr(service, "process_drafts", record_drafts)
    monkeypatch.setattr(service, "process_workbook", record_workbook)
    monkeypatch.setattr(service, "process_single", record_single)

    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="user",
            remote_token="remote-session",
        )
    )
    remote = RecordingRemoteBilling(10_000)
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = _actor
    app.include_router(
        product_processing_router.create_product_processing_router(
            service,
            customer_sessions=sessions,
            remote_customer_auth=remote,
        )
    )
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {session.token}"}
    text_only = {
        "title_optimize": True,
        "description": False,
        "size": False,
        "grid_image": False,
        "image_rewrite": False,
    }

    try:
        drafts = client.post(
            "/product-processing/drafts/process",
            json={"draft_ids": [1], **text_only},
            headers=headers,
        )
        workbook = client.post(
            "/product-processing/engine/batch",
            data={
                "max_products": "1",
                **{key: str(value).lower() for key, value in text_only.items()},
            },
            files={"file": ("products.xlsx", b"test workbook", "application/octet-stream")},
            headers=headers,
        )
        single = client.post(
            "/product-processing/engine/single",
            data={key: str(value).lower() for key, value in text_only.items()},
            files={"image_file": ("product.jpg", b"test image", "image/jpeg")},
            headers=headers,
        )

        assert [drafts.status_code, workbook.status_code, single.status_code] == [200, 200, 200]
        assert remote.tokens == ["remote-session", "remote-session", "remote-session"]
        assert {name: payload["_billing"]["remote_token"] for name, payload in captured.items()} == {
            "drafts": "remote-session",
            "workbook": "remote-session",
            "single": "remote-session",
        }
    finally:
        getattr(service, "_dimension_canvas_service").close()
        database.dispose()


def test_workbook_rechecks_reserve_points_after_real_import_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'workbook-router.sqlite3').as_posix()}")
    service = ProductProcessingService(
        ProductProcessingRepository(database),
        ProductProcessingAssets(tmp_path / "assets"),
    )

    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="user",
            remote_token="remote-session",
        )
    )
    remote = RecordingRemoteBilling(100)
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = _actor
    app.include_router(
        product_processing_router.create_product_processing_router(
            service,
            customer_sessions=sessions,
            remote_customer_auth=remote,
        )
    )
    client = TestClient(app)
    workbook = "商品标题,价格\nProduct A,1\nProduct B,2\nProduct C,3\n".encode()
    idempotency_key = "workbook-no-side-effects"

    def counts() -> tuple[int, bool, set[str]]:
        drafts = len(service.repository.list_drafts(None, 100, 0, workspace_id="local")[0])
        task_exists = service.repository.task_by_idempotency_key(idempotency_key, "local") is not None
        files = {
            str(path.relative_to(service.assets.root))
            for path in service.assets.root.rglob("*")
            if path.is_file()
        }
        return drafts, task_exists, files

    before = counts()

    try:
        responses = [
            client.post(
                "/product-processing/engine/batch",
                data={
                    "title_optimize": "true",
                    "description": "false",
                    "size": "false",
                    "grid_image": "false",
                    "image_rewrite": "false",
                },
                files={"file": ("products.csv", workbook, "text/csv")},
                headers={
                    "Authorization": f"Bearer {session.token}",
                    "Idempotency-Key": idempotency_key,
                },
            )
            for _ in range(2)
        ]

        assert [response.status_code for response in responses] == [402, 402]
        assert all("150" in response.json()["detail"] for response in responses)
        assert counts() == before == (0, False, set())
        assert remote.tokens == ["remote-session"] * 2
    finally:
        getattr(service, "_dimension_canvas_service").close()
        database.dispose()


def test_processing_http_maps_remote_protocol_failure_to_stable_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    monkeypatch.setattr(
        service,
        "process_drafts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CustomerBillingProtocolError()),
    )
    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(customer_id="user", username="user", remote_token="remote-token")
    )
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = _actor
    app.include_router(
        product_processing_router.create_product_processing_router(
            service,
            customer_sessions=sessions,
            remote_customer_auth=RecordingRemoteBilling(10_000),
        )
    )

    response = TestClient(app).post(
        "/product-processing/drafts/process",
        json={"draft_ids": [1], **_text_only_payload()},
        headers={"Authorization": f"Bearer {session.token}"},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "remote billing service returned an invalid response"
    }


def test_retry_attention_reacquires_remote_token_and_prechecks_balance_over_http(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'retry-router.sqlite3').as_posix()}")
    service = ProductProcessingService(
        ProductProcessingRepository(database),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    captured: dict[str, Any] = {}
    settings = {
        "processing_scope": ["title"],
        "title_optimize": True,
        "description": False,
        "size": False,
        "grid_image": False,
        "image_rewrite": False,
    }
    monkeypatch.setattr(
        service,
        "task_outputs",
        lambda *_args, **_kwargs: {
            "task": {"metadata": {"settings": settings, "preflight_only": False}},
            "items": [
                {"product_draft_id": 41, "status": "completed"},
                {"product_draft_id": 42, "status": "failed"},
                {"product_draft_id": 43, "status": "attention_required"},
            ],
        },
    )

    def record_retry(
        task_id: int,
        workspace_id: str,
        *,
        draft_ids: list[int] | None,
        remote_token: str,
    ) -> dict[str, Any]:
        captured.update(
            task_id=task_id,
            workspace_id=workspace_id,
            draft_ids=draft_ids,
            remote_token=remote_token,
        )
        return {"ok": True}

    monkeypatch.setattr(service, "retry_attention", record_retry)
    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="user",
            remote_token="remote-retry-session",
        )
    )
    remote = RecordingRemoteBilling(50)
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = _actor
    app.include_router(
        product_processing_router.create_product_processing_router(
            service,
            customer_sessions=sessions,
            remote_customer_auth=remote,
        )
    )

    response = TestClient(app).post(
        "/product-processing/tasks/9/retry-attention",
        json={"draft_ids": [42]},
        headers={"Authorization": f"Bearer {session.token}"},
    )

    assert response.status_code == 200
    assert remote.tokens == ["remote-retry-session"]
    assert captured == {
        "task_id": 9,
        "workspace_id": "local",
        "draft_ids": [42],
        "remote_token": "remote-retry-session",
    }
    getattr(service, "_dimension_canvas_service").close()
    database.dispose()


def test_retry_attention_rejects_insufficient_remote_balance_before_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = create_database(f"sqlite:///{(tmp_path / 'retry-insufficient.sqlite3').as_posix()}")
    service = ProductProcessingService(
        ProductProcessingRepository(database),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    monkeypatch.setattr(
        service,
        "task_outputs",
        lambda *_args, **_kwargs: {
            "task": {
                "metadata": {
                    "settings": {
                        "processing_scope": ["title"],
                        "title_optimize": True,
                        "description": False,
                        "size": False,
                        "grid_image": False,
                        "image_rewrite": False,
                    },
                    "preflight_only": False,
                }
            },
            "items": [{"product_draft_id": 42, "status": "failed"}],
        },
    )
    monkeypatch.setattr(
        service,
        "retry_attention",
        lambda *_args, **_kwargs: pytest.fail("retry must not run without enough points"),
    )
    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(
            customer_id="customer-1",
            username="user",
            remote_token="remote-retry-session",
        )
    )
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = _actor
    app.include_router(
        product_processing_router.create_product_processing_router(
            service,
            customer_sessions=sessions,
            remote_customer_auth=RecordingRemoteBilling(29),
        )
    )

    response = TestClient(app).post(
        "/product-processing/tasks/9/retry-attention",
        json={"draft_ids": [42]},
        headers={"Authorization": f"Bearer {session.token}"},
    )

    assert response.status_code == 402
    getattr(service, "_dimension_canvas_service").close()
    database.dispose()


def test_resume_reacquires_remote_token_prechecks_and_passes_only_memory_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    settings = {
        **_text_only_payload(),
        "_billing": {"account_id": "user"},
    }
    monkeypatch.setattr(
        service,
        "task_outputs",
        lambda *_args, **_kwargs: {
            "task": {"metadata": {"settings": settings, "preflight_only": False}},
            "items": [{"product_draft_id": 42, "status": "pending"}],
        },
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        service,
        "resume_task",
        lambda task_id, workspace_id, *, remote_token: captured.update(
            task_id=task_id,
            workspace_id=workspace_id,
            remote_token=remote_token,
        )
        or {"ok": True},
    )
    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(customer_id="user", username="user", remote_token="resume-token")
    )
    remote = RecordingRemoteBilling(50)
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = _actor
    app.include_router(
        product_processing_router.create_product_processing_router(
            service,
            customer_sessions=sessions,
            remote_customer_auth=remote,
        )
    )

    response = TestClient(app).post(
        "/product-processing/tasks/9/resume",
        headers={"Authorization": f"Bearer {session.token}"},
    )

    assert response.status_code == 200
    assert remote.tokens == ["resume-token"]
    assert captured["remote_token"] == "resume-token"


def test_resume_terminal_settlement_recovery_skips_new_work_balance_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    monkeypatch.setattr(
        service,
        "task_outputs",
        lambda *_args, **_kwargs: {
            "task": {
                "metadata": {
                    "settings": {**_text_only_payload(), "_billing": {"account_id": "user"}},
                    "preflight_only": False,
                }
            },
            "items": [{"product_draft_id": 42, "status": "completed"}],
        },
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        service,
        "resume_task",
        lambda task_id, workspace_id, *, remote_token: captured.update(
            task_id=task_id, workspace_id=workspace_id, remote_token=remote_token
        )
        or {"ok": True},
    )
    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(customer_id="user", username="user", remote_token="fresh-token")
    )
    remote = RecordingRemoteBilling(0)
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = _actor
    app.include_router(
        product_processing_router.create_product_processing_router(
            service,
            customer_sessions=sessions,
            remote_customer_auth=remote,
        )
    )

    response = TestClient(app).post(
        "/product-processing/tasks/9/resume",
        headers={"Authorization": f"Bearer {session.token}"},
    )

    assert response.status_code == 200
    assert captured["remote_token"] == "fresh-token"
    assert remote.tokens == ["fresh-token"]


@pytest.mark.parametrize("endpoint", ["resume", "retry-attention"])
def test_authenticated_recovery_reconciles_locked_usage_before_new_work_precheck(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    item_status = "pending" if endpoint == "resume" else "failed"
    monkeypatch.setattr(
        service,
        "task_outputs",
        lambda *_args, **_kwargs: {
            "task": {
                "metadata": {
                    "settings": {**_text_only_payload(), "_billing": {"account_id": "user"}},
                    "preflight_only": False,
                }
            },
            "items": [{"product_draft_id": 42, "status": item_status}],
        },
    )
    monkeypatch.setattr(
        service.repository,
        "product_billing_attempts",
        lambda **_kwargs: [{"id": 1}],
    )
    events: list[str] = []

    class LockedBalanceRemote:
        released = False

        def billing_summary(self, token: str) -> dict[str, Any]:
            assert token == "fresh-token"
            events.append("summary-after" if self.released else "summary-before")
            return {"wallet": {"available_points": 50 if self.released else 0}}

    remote = LockedBalanceRemote()

    def reconcile(task_id: int, token: str) -> dict[str, int]:
        assert task_id == 9 and token == "fresh-token"
        events.append("reconcile")
        remote.released = True
        return {"reconciled": 1, "pending": 0}

    monkeypatch.setattr(service, "reconcile_product_billing", reconcile)
    if endpoint == "resume":
        monkeypatch.setattr(
            service,
            "resume_task",
            lambda *_args, **_kwargs: events.append("mutate") or {"ok": True},
        )
    else:
        monkeypatch.setattr(
            service,
            "retry_attention",
            lambda *_args, **_kwargs: events.append("mutate") or {"ok": True},
        )
    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(customer_id="user", username="user", remote_token="fresh-token")
    )
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = _actor
    app.include_router(
        product_processing_router.create_product_processing_router(
            service,
            customer_sessions=sessions,
            remote_customer_auth=remote,
        )
    )

    response = TestClient(app).post(
        f"/product-processing/tasks/9/{endpoint}",
        headers={"Authorization": f"Bearer {session.token}"},
    )

    assert response.status_code == 200
    assert events == ["summary-before", "reconcile", "summary-after", "mutate"]


def test_restarted_billed_queue_is_resumed_only_by_authenticated_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = f"sqlite:///{tmp_path / 'restart-resume.sqlite3'}"
    original = ProductProcessingService(
        ProductProcessingRepository(create_database(database_url)),
        ProductProcessingAssets(tmp_path / "original-assets"),
    )
    draft, _created = original.create_draft(
        {
            "source_type": "manual",
            "title": "restart resume",
            "image_url": "https://images.example.test/restart.jpg",
        }
    )
    task = original.repository.create_task(
        title="restart resume",
        preflight_only=False,
        settings={
            **_text_only_payload(),
            "async_mode": True,
            "_billing": {"account_id": "user"},
        },
        drafts=[draft],
        idempotency_key=None,
    )
    restarted = ProductProcessingService(
        ProductProcessingRepository(create_database(database_url)),
        ProductProcessingAssets(tmp_path / "restarted-assets"),
    )
    monkeypatch.setattr(restarted.preview_images, "recover_background_work", lambda: {})
    monkeypatch.setattr(restarted.media_assets, "materialize_until_idle", lambda **_kwargs: {})
    launched: list[tuple[int, str]] = []
    monkeypatch.setattr(
        restarted,
        "_launch_background_execute",
        lambda task_id, _workspace: launched.append(
            (task_id, restarted._task_remote_token(task_id))
        )
        or True,
    )
    assert restarted.recover_background_work()["billing_auth_required"] == 1
    assert restarted.repository.get_task(task["id"])["status"] == "paused"
    sessions = LocalSessionService()
    session = sessions.login_customer(
        CustomerAuthResult(customer_id="user", username="user", remote_token="fresh-token")
    )
    remote = RecordingRemoteBilling(50)
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = _actor
    app.include_router(
        product_processing_router.create_product_processing_router(
            restarted,
            customer_sessions=sessions,
            remote_customer_auth=remote,
        )
    )

    response = TestClient(app).post(
        f"/product-processing/tasks/{task['id']}/resume",
        headers={"Authorization": f"Bearer {session.token}"},
    )

    assert response.status_code == 200
    assert launched == [(task["id"], "fresh-token")]
    assert remote.tokens == ["fresh-token"]
    assert restarted.repository.get_task(task["id"])["status"] == "queued"


@pytest.mark.parametrize("endpoint", ["retry-attention", "clear", "resume"])
def test_task_billing_owner_mismatch_fails_before_remote_or_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    service = ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )
    monkeypatch.setattr(
        service,
        "task_outputs",
        lambda *_args, **_kwargs: {
            "task": {
                "metadata": {
                    "settings": {
                        **_text_only_payload(),
                        "_billing": {"account_id": "different-account"},
                    },
                    "preflight_only": False,
                }
            },
            "items": [{"product_draft_id": 42, "status": "failed"}],
        },
    )
    monkeypatch.setattr(service, "retry_attention", lambda *_a, **_k: pytest.fail("must not mutate"))
    monkeypatch.setattr(service, "resume_task", lambda *_a, **_k: pytest.fail("must not mutate"))
    monkeypatch.setattr(service, "clear_task", lambda *_a, **_k: pytest.fail("must not mutate"))
    remote = RecordingRemoteBilling(10_000)
    app = FastAPI()
    app.dependency_overrides[actor_from_authorization] = _actor
    app.include_router(
        product_processing_router.create_product_processing_router(
            service,
            customer_sessions=LocalSessionService(),
            remote_customer_auth=remote,
        )
    )

    response = TestClient(app).post(f"/product-processing/tasks/9/{endpoint}")

    assert response.status_code == 404
    assert remote.tokens == []
