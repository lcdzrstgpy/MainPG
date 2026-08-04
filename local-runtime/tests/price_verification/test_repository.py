from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.contracts import PluginCommandRequest  # noqa: E402
from wh_local.price_verification.repository import (  # noqa: E402
    PriceVerificationNotFound,
    PriceVerificationRepository,
)


def test_migration_creates_all_workspace_owned_tables_and_indexes(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")

    with sqlite3.connect(repository.database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

    assert {
        "price_verification_pairing_codes",
        "price_verification_plugin_sessions",
        "price_verification_plugin_commands",
        "price_verification_provider_budgets",
        "price_verification_quote_runs",
        "price_verification_quote_items",
        "price_verification_sourcing_runs",
        "price_verification_source_candidates",
    } <= tables
    assert {
        "idx_price_verification_pairing_codes_workspace_expires",
        "idx_price_verification_plugin_sessions_workspace_seen",
        "idx_price_verification_plugin_commands_workspace_status",
        "idx_price_verification_provider_budgets_workspace_date",
        "idx_price_verification_quote_runs_workspace_created",
        "idx_price_verification_quote_items_workspace_run",
        "idx_price_verification_sourcing_runs_workspace_created",
        "idx_price_verification_source_candidates_workspace_run",
    } <= indexes


def test_repository_stores_only_redacted_command_snapshot(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    session = repository.create_plugin_session(
        workspace_id="A", session_token_hash="a" * 64, browser="Edge"
    )
    command = repository.create_command(
        workspace_id="A",
        session_id=session.session_id,
        request=PluginCommandRequest(
            command_type="temu_price_quote_discovery",
            payload={"cookie": "not-stored", "nested": {"token": "not-stored"}},
            idempotency_key="cmd-1",
        ),
    )

    assert command.payload == {
        "cookie": "[REDACTED]",
        "nested": {"token": "[REDACTED]"},
    }
    with sqlite3.connect(repository.database_path) as connection:
        serialized = connection.execute(
            "SELECT payload_json FROM price_verification_plugin_commands"
        ).fetchone()[0]
    assert "not-stored" not in serialized


def test_workspace_cannot_read_another_workspace_quote_run(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    run = repository.create_quote_run(workspace_id="A", command_id="cmd-1", items=[])

    with pytest.raises(PriceVerificationNotFound):
        repository.get_quote_run(workspace_id="B", run_id=run.run_id)


def test_workspace_scopes_sessions_commands_and_sourcing_runs(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    session = repository.create_plugin_session(
        workspace_id="A", session_token_hash="a" * 64, browser="Edge"
    )
    command = repository.create_command(
        workspace_id="A",
        session_id=session.session_id,
        request=PluginCommandRequest(
            command_type="source_browser_image_search", payload={}, idempotency_key="cmd-1"
        ),
    )
    quote_run = repository.create_quote_run(
        workspace_id="A", command_id=command.command_id, items=[]
    )
    sourcing_run = repository.create_sourcing_run(
        workspace_id="A", quote_run_id=quote_run.run_id, candidates=[]
    )

    for read in (
        lambda: repository.get_plugin_session(workspace_id="B", session_id=session.session_id),
        lambda: repository.get_command(workspace_id="B", command_id=command.command_id),
        lambda: repository.get_sourcing_run(workspace_id="B", run_id=sourcing_run.run_id),
    ):
        with pytest.raises(PriceVerificationNotFound):
            read()


def test_command_idempotency_is_limited_to_workspace_and_command_type(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    session = repository.create_plugin_session(
        workspace_id="A", session_token_hash="a" * 64, browser="Edge"
    )
    request = PluginCommandRequest(
        command_type="temu_price_quote_discovery", payload={}, idempotency_key="same"
    )
    first = repository.create_command(workspace_id="A", session_id=session.session_id, request=request)
    second = repository.create_command(workspace_id="A", session_id=session.session_id, request=request)

    assert second == first
