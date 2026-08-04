from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.contracts import (  # noqa: E402
    MAX_JSON_BYTES,
    MAX_JSON_DEPTH,
    PluginCommandRequest,
    PriceVerificationActor,
    PriceVerificationContractError,
    redact_sensitive,
    safe_json_dumps,
)


def test_actor_requires_stable_actor_and_workspace_ids() -> None:
    actor = PriceVerificationActor(actor_id="user-1", workspace_id="workspace-a")

    assert actor.actor_id == "user-1"
    assert actor.workspace_id == "workspace-a"
    with pytest.raises(PriceVerificationContractError, match="workspace_id"):
        PriceVerificationActor(actor_id="user-1", workspace_id=" ")


def test_rejects_write_command_and_redacts_cookie() -> None:
    with pytest.raises(PriceVerificationContractError, match="command_type"):
        PluginCommandRequest(
            command_type="temu_price_quote_accept", payload={}, idempotency_key="x"
        )

    assert redact_sensitive({"cookie": "x", "safe": "ok"}) == {
        "cookie": "[REDACTED]",
        "safe": "ok",
    }


def test_command_payload_recursively_redacts_credentials_and_platform_writes() -> None:
    request = PluginCommandRequest(
        command_type="temu_price_quote_discovery",
        payload={
            "nested": {"Authorization": "Bearer never-persist"},
            "message": "token=never-persist",
        },
        idempotency_key="request-1",
    )

    assert request.payload["nested"]["Authorization"] == "[REDACTED]"
    assert "never-persist" not in repr(request.payload)
    with pytest.raises(PriceVerificationContractError, match="platform write"):
        PluginCommandRequest(
            command_type="temu_price_quote_discovery",
            payload={"action": "accept_price_quote"},
            idempotency_key="write-request",
        )


@pytest.mark.parametrize(
    "write_action",
    ["save_price_quote", "add_to_cart", "create_order", "change_price"],
)
def test_safe_json_rejects_every_forbidden_platform_write_category(
    write_action: str,
) -> None:
    with pytest.raises(PriceVerificationContractError, match="platform write"):
        safe_json_dumps({"action": write_action})


def test_redaction_covers_nested_conventional_credential_variants() -> None:
    value = redact_sensitive(
        {"provider": {"client_secret": "must-not-persist", "apiToken": "also-secret"}}
    )

    assert value == {
        "provider": {"client_secret": "[REDACTED]", "apiToken": "[REDACTED]"}
    }


def test_safe_json_has_fixed_size_depth_and_binary_boundaries() -> None:
    with pytest.raises(PriceVerificationContractError, match="binary"):
        safe_json_dumps({"image": b"not-json"})
    with pytest.raises(PriceVerificationContractError, match="depth"):
        value: object = "too-deep"
        for _ in range(MAX_JSON_DEPTH):
            value = {"value": value}
        safe_json_dumps(value)
    with pytest.raises(PriceVerificationContractError, match="16 MiB"):
        safe_json_dumps({"result": "x" * MAX_JSON_BYTES})
    assert MAX_JSON_DEPTH == 20
