from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from wh_local.customer.contracts import (
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
    remote = RecordingRemoteBilling(30)

    product_processing_router._attach_billing_context_and_require_points(
        payload,
        _actor(),
        source_ref="test",
        remote_token="remote-session",
        remote_customer_auth=remote,
    )

    assert remote.tokens == ["remote-session"]
    assert payload["_billing"]["remote_token"] == "remote-session"
    assert payload["_billing"]["estimated_points"] == 30


@pytest.mark.parametrize(
    ("remote_token", "remote_customer_auth"),
    [("", RecordingRemoteBilling(30)), ("remote-session", None)],
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
    assert "30" in caught.value.detail
    assert "29" in caught.value.detail
    assert remote.tokens == ["remote-session"]


def test_billing_context_maps_remote_unavailable_to_503() -> None:
    remote = RaisingRemoteBilling(CustomerAuthUnavailable("billing temporarily unavailable"))

    with pytest.raises(HTTPException) as caught:
        product_processing_router._attach_billing_context_and_require_points(
            _text_only_payload(),
            _actor(),
            source_ref="test",
            remote_token="remote-session",
            remote_customer_auth=remote,
        )

    assert caught.value.status_code == 503
    assert caught.value.detail == "billing temporarily unavailable"


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
    assert caught.value.detail == "remote rejected"


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
    remote = RaisingRemoteBilling(PermissionError("remote session rejected"))

    with pytest.raises(HTTPException) as caught:
        product_processing_router._attach_billing_context_and_require_points(
            _text_only_payload(),
            _actor(),
            source_ref="test",
            remote_token="remote-session",
            remote_customer_auth=remote,
        )

    assert caught.value.status_code == 403
    assert caught.value.detail == "remote session rejected"


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
