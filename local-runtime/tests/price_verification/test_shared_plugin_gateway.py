from __future__ import annotations

from pathlib import Path

from wh_local.data_collection.plugin_queue import DataCollectionPluginQueue
from wh_local.price_verification.contracts import PriceVerificationActor
from wh_local.price_verification.plugin.shared_gateway import SharedPluginGateway


def _actor(workspace_id: str = "workspace-A") -> PriceVerificationActor:
    return PriceVerificationActor(actor_id="user-1", workspace_id=workspace_id)


def _connected_queue(database_path: Path) -> tuple[DataCollectionPluginQueue, dict[str, object]]:
    queue = DataCollectionPluginQueue(database_path)
    session = queue.create_session(
        actor_id="user-1",
        workspace_id="workspace-A",
        capabilities={
            "temu_price_quote_discovery": True,
            "source_browser_image_search": True,
        },
    )
    return queue, session


def test_shared_gateway_queues_quote_command_for_connected_data_session(tmp_path: Path) -> None:
    queue, session = _connected_queue(tmp_path / "runtime.sqlite3")
    gateway = SharedPluginGateway(queue)

    command = gateway.queue_command(
        _actor(),
        session_id=str(session["session_id"]),
        command_type="temu_price_quote_discovery",
        payload={"wait_ms": 500},
        idempotency_key="quote-1",
    )

    polled = queue.poll(str(session["session_token"]))
    assert command.command_id == str(polled[0].command_id)
    assert command.command_type == "temu_price_quote_discovery"
    assert polled[0].payload == {"wait_ms": 500}


def test_shared_gateway_idempotency_is_workspace_scoped(tmp_path: Path) -> None:
    queue, session = _connected_queue(tmp_path / "runtime.sqlite3")
    gateway = SharedPluginGateway(queue)

    first = gateway.queue_command(
        _actor(),
        session_id=str(session["session_id"]),
        command_type="source_browser_image_search",
        payload={"tasks": []},
        idempotency_key="source-1",
    )
    second = gateway.queue_command(
        _actor(),
        session_id=str(session["session_id"]),
        command_type="source_browser_image_search",
        payload={"tasks": [{"quote_key": "changed"}]},
        idempotency_key="source-1",
    )

    assert first.command_id == second.command_id
    assert second.payload == {"tasks": []}
    assert len(gateway.list_commands(_actor(), command_type="source_browser_image_search")) == 1


def test_shared_gateway_reads_completed_result_from_data_queue(tmp_path: Path) -> None:
    queue, session = _connected_queue(tmp_path / "runtime.sqlite3")
    gateway = SharedPluginGateway(queue)
    command = gateway.queue_command(
        _actor(),
        session_id=str(session["session_id"]),
        command_type="temu_price_quote_discovery",
        payload={},
        idempotency_key="quote-result",
    )
    queue.poll(str(session["session_token"]))
    queue.receive_result(
        session_token=str(session["session_token"]),
        command_id=int(command.command_id),
        status="succeeded",
        result={"records": []},
    )

    completed = gateway.get_command(_actor(), command.command_id)

    assert completed.status == "succeeded"
    assert completed.result == {"records": []}
    assert [item.session_id for item in gateway.list_sessions(_actor())] == [str(session["session_id"])]
