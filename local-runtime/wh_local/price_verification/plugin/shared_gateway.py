"""Price-verification view over the single data-collection plugin transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ...data_collection.plugin_queue import DataCollectionPluginQueue, PluginCommand
from ..contracts import ALLOWED_PLUGIN_COMMAND_TYPES, PriceVerificationActor
from ..repository import PluginCommandRecord, PriceVerificationNotFound


@dataclass(frozen=True)
class SharedPluginSessionSummary:
    session_id: str
    workspace_id: str
    browser: str
    plugin_version: str
    capabilities: Mapping[str, Any]
    status: str
    created_at: str
    last_seen_at: str


class SharedPluginGateway:
    """Queue and read price commands through the already-connected plugin."""

    def __init__(self, queue: DataCollectionPluginQueue) -> None:
        if not isinstance(queue, DataCollectionPluginQueue):
            raise TypeError("queue must be DataCollectionPluginQueue")
        self._queue = queue

    def queue_command(
        self,
        actor: PriceVerificationActor,
        *,
        session_id: str,
        command_type: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> PluginCommandRecord:
        actor = _actor(actor)
        if command_type not in ALLOWED_PLUGIN_COMMAND_TYPES:
            raise ValueError("unsupported plugin command type")
        try:
            command = self._queue.queue_command(
                actor_id=actor.actor_id,
                workspace_id=actor.workspace_id,
                session_id=_session_id(session_id),
                command_type=command_type,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        except PermissionError as error:
            raise PriceVerificationNotFound("resource not found") from error
        return _command_record(actor.workspace_id, session_id, command)

    def get_command(self, actor: PriceVerificationActor, command_id: str) -> PluginCommandRecord:
        actor = _actor(actor)
        try:
            command = self._queue.get_command(
                actor_id=actor.actor_id,
                workspace_id=actor.workspace_id,
                command_id=_command_id(command_id),
            )
        except PermissionError as error:
            raise PriceVerificationNotFound("resource not found") from error
        return _command_record(actor.workspace_id, "", command)

    def list_sessions(self, actor: PriceVerificationActor) -> tuple[SharedPluginSessionSummary, ...]:
        actor = _actor(actor)
        return tuple(
            SharedPluginSessionSummary(
                session_id=str(row["session_id"]),
                workspace_id=str(row["workspace_id"]),
                browser="Edge",
                plugin_version=str(row["capabilities"].get("extension_version", "")),
                capabilities=row["capabilities"],
                status=str(row["status"]),
                created_at=str(row["created_at"]),
                last_seen_at=str(row["last_seen_at"]),
            )
            for row in self._queue.list_sessions(
                actor_id=actor.actor_id,
                workspace_id=actor.workspace_id,
            )
        )

    def list_commands(
        self,
        actor: PriceVerificationActor,
        *,
        command_type: str | None = None,
        limit: int = 20,
    ) -> tuple[PluginCommandRecord, ...]:
        actor = _actor(actor)
        commands = self._queue.list_commands(
            actor_id=actor.actor_id,
            workspace_id=actor.workspace_id,
            command_type=command_type,
            limit=limit,
        )
        return tuple(_command_record(actor.workspace_id, "", command) for command in commands)


def _command_record(
    workspace_id: str, session_id: str, command: PluginCommand
) -> PluginCommandRecord:
    status = "leased" if command.status == "sent" else command.status
    return PluginCommandRecord(
        command_id=str(command.command_id),
        workspace_id=workspace_id,
        session_id=session_id,
        command_type=command.command_type,
        idempotency_key=command.idempotency_key or f"shared-{command.command_id}",
        payload=command.payload,
        result=command.result,
        status=status,
        lease_expires_at=None,
        created_at=command.created_at,
        updated_at=command.updated_at,
    )


def _actor(value: PriceVerificationActor) -> PriceVerificationActor:
    if not isinstance(value, PriceVerificationActor):
        raise TypeError("actor must be PriceVerificationActor")
    return value


def _session_id(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise PriceVerificationNotFound("resource not found") from error
    if parsed < 1:
        raise PriceVerificationNotFound("resource not found")
    return parsed

def _command_id(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise PriceVerificationNotFound("resource not found") from error
    if parsed < 1:
        raise PriceVerificationNotFound("resource not found")
    return parsed
