from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from wh_local.customer.contracts import CustomerAuthResult
from wh_local.customer.local_session import LocalSessionService
from wh_local.modules.product_processing.api import router as product_processing_router
from wh_local.session import Actor


class RecordingRemoteBilling:
    def __init__(self, available: int):
        self.available = available
        self.tokens: list[str] = []

    def billing_summary(self, token: str) -> dict[str, Any]:
        self.tokens.append(token)
        return {"wallet": {"available_points": self.available}}


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
