from __future__ import annotations

import hashlib
import inspect
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.contracts import (  # noqa: E402
    PluginCommandRequest,
    PriceVerificationActor,
    PriceVerificationContractError,
)
from wh_local.price_verification.plugin.service import (  # noqa: E402
    PluginAuthenticationError,
    PluginBridgeService,
)
from wh_local.price_verification.repository import PriceVerificationRepository  # noqa: E402


@pytest.fixture
def now() -> list[datetime]:
    return [datetime(2026, 8, 4, 9, 0, tzinfo=UTC)]


@pytest.fixture
def repository(tmp_path: Path) -> PriceVerificationRepository:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    yield repository
    repository.close()


@pytest.fixture
def service(repository: PriceVerificationRepository, now: list[datetime]) -> PluginBridgeService:
    return PluginBridgeService(repository=repository, clock=lambda: now[0])


def actor(workspace_id: str) -> PriceVerificationActor:
    return PriceVerificationActor(actor_id=f"user-{workspace_id}", workspace_id=workspace_id)


def connected_command(
    service: PluginBridgeService,
    repository: PriceVerificationRepository,
) -> tuple[object, object]:
    issued = service.issue_pairing_code(actor("A"))
    session = service.connect(
        issued.code,
        browser_name="Edge",
        capabilities={"temu_price_quote_discovery": True},
    )
    command = repository.create_command(
        workspace_id="A",
        session_id=session.session_id,
        request=PluginCommandRequest(
            command_type="temu_price_quote_discovery",
            payload={"query": "wireless lamp"},
            idempotency_key="request-1",
        ),
    )
    return session, command


def test_pairing_code_is_single_use_and_session_token_is_distinct(
    service: PluginBridgeService,
) -> None:
    issued = service.issue_pairing_code(actor("A"))
    session = service.connect(
        issued.code,
        browser_name="Edge",
        capabilities={"temu_price_quote_discovery": True},
    )

    with pytest.raises(PluginAuthenticationError):
        service.connect(issued.code, browser_name="Edge", capabilities={})

    assert session.token != issued.code


def test_pairing_code_expires_after_ten_minutes(
    service: PluginBridgeService, now: list[datetime]
) -> None:
    issued = service.issue_pairing_code(actor("A"))
    now[0] += timedelta(minutes=10, seconds=1)

    with pytest.raises(PluginAuthenticationError, match="expired"):
        service.connect(issued.code, browser_name="Edge", capabilities={})


def test_pairing_and_session_tokens_are_only_persisted_as_sha256(
    service: PluginBridgeService, repository: PriceVerificationRepository
) -> None:
    issued = service.issue_pairing_code(actor("A"))
    session = service.connect(issued.code, browser_name="Edge", capabilities={})

    with sqlite3.connect(repository.database_path) as connection:
        pairing_hash = connection.execute(
            "SELECT code_sha256 FROM price_verification_pairing_codes"
        ).fetchone()[0]
        session_hash = connection.execute(
            "SELECT token_sha256 FROM price_verification_plugin_sessions"
        ).fetchone()[0]

    assert pairing_hash == hashlib.sha256(issued.code.encode("utf-8")).hexdigest()
    assert session_hash == hashlib.sha256(session.token.encode("utf-8")).hexdigest()
    assert issued.code not in pairing_hash
    assert session.token not in session_hash


def test_poll_requires_the_correct_session_token(
    service: PluginBridgeService, repository: PriceVerificationRepository
) -> None:
    _, command = connected_command(service, repository)

    with pytest.raises(PluginAuthenticationError):
        service.poll("wrong-token")

    assert command.command_id


def test_poll_leases_only_allowed_commands_for_120_seconds(
    service: PluginBridgeService, repository: PriceVerificationRepository, now: list[datetime]
) -> None:
    session, command = connected_command(service, repository)

    polled = service.poll(session.token)

    assert [item.command_id for item in polled] == [command.command_id]
    assert polled[0].status == "leased"
    assert polled[0].lease_expires_at == (now[0] + timedelta(seconds=120)).isoformat(timespec="seconds")


def test_stale_lease_can_be_repolled_but_running_result_renews_it(
    service: PluginBridgeService, repository: PriceVerificationRepository, now: list[datetime]
) -> None:
    session, command = connected_command(service, repository)
    service.poll(session.token)
    now[0] += timedelta(seconds=121)

    assert [item.command_id for item in service.poll(session.token)] == [command.command_id]
    running = service.receive_result(session.token, command.command_id, "running", {"progress": 50})

    assert running.status == "running"
    assert running.lease_expires_at == (now[0] + timedelta(seconds=120)).isoformat(timespec="seconds")


def test_rejects_result_larger_than_16_mib(
    service: PluginBridgeService, repository: PriceVerificationRepository
) -> None:
    session, command = connected_command(service, repository)

    with pytest.raises(PriceVerificationContractError, match="16 MiB"):
        service.receive_result(
            session.token,
            command.command_id,
            "succeeded",
            {"payload": "a" * (16 * 1024 * 1024)},
        )


def test_service_rejects_platform_writes_and_recursively_redacts_results(
    service: PluginBridgeService, repository: PriceVerificationRepository
) -> None:
    session, command = connected_command(service, repository)
    service.poll(session.token)

    with pytest.raises(PriceVerificationContractError, match="platform write"):
        service.receive_result(session.token, command.command_id, "succeeded", {"action": "update_price"})

    completed = service.receive_result(
        session.token,
        command.command_id,
        "succeeded",
        {"nested": {"access_token": "never persist"}},
    )

    assert completed.result == {"nested": {"access_token": "[REDACTED]"}}


def test_list_sessions_is_workspace_scoped_and_never_exposes_token_hashes(
    service: PluginBridgeService,
) -> None:
    session_a = service.connect(
        service.issue_pairing_code(actor("A")).code,
        browser_name="Edge",
        capabilities={},
    )
    service.connect(
        service.issue_pairing_code(actor("B")).code,
        browser_name="Chrome",
        capabilities={},
    )

    sessions = service.list_sessions(actor("A"))

    assert [session.session_id for session in sessions] == [session_a.session_id]
    assert not hasattr(sessions[0], "token")
    assert not hasattr(sessions[0], "token_sha256")


def test_repository_exposes_workspace_scoped_plugin_state_operations(
    repository: PriceVerificationRepository,
) -> None:
    session = repository.create_plugin_session(
        workspace_id="A", session_token_hash="a" * 64, browser="Edge"
    )
    command = repository.create_command(
        workspace_id="A",
        session_id=session.session_id,
        request=PluginCommandRequest(
            command_type="temu_price_quote_discovery", payload={}, idempotency_key="state-operation"
        ),
    )
    leased = repository.lease_plugin_commands(
        workspace_id="A",
        session_id=session.session_id,
        command_types=("temu_price_quote_discovery",),
        now="2026-08-04T09:00:00+00:00",
        lease_expires_at="2026-08-04T09:02:00+00:00",
        limit=10,
    )

    completed = repository.record_plugin_result(
        workspace_id="A",
        session_id=session.session_id,
        command_id=command.command_id,
        status="succeeded",
        result={"token": "redacted"},
        now="2026-08-04T09:00:01+00:00",
    )

    assert [item.command_id for item in leased] == [command.command_id]
    assert completed.status == "succeeded"
    assert completed.result == {"token": "[REDACTED]"}
    assert repository.list_plugin_sessions(workspace_id="B") == ()


def test_bridge_does_not_reach_into_repository_private_connection() -> None:
    assert "._connect(" not in inspect.getsource(PluginBridgeService)
