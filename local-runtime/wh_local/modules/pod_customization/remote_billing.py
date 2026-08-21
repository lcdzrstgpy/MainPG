from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...db import connect
from ...session import Actor
from .billing_contract import (
    PodBillingCoordinator,
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
)


class RemotePodBillingCoordinator(PodBillingCoordinator):
    """Adapt the host remote-auth client to POD's in-memory billing contract."""

    def __init__(
        self,
        remote_client: Any,
        remote_token_resolver: Callable[[Actor], str],
    ) -> None:
        self._remote_client = remote_client
        self._remote_token_resolver = remote_token_resolver

    def freeze(self, actor: Actor, plan: PodCallPlan) -> PodExecutionGrant:
        remote_token = self._required_remote_token(actor)
        response = self._remote_client.freeze_pod_points(
            remote_token,
            plan.freeze_payload(encrypted_session_key=""),
        )
        freeze = response.get("freeze") if isinstance(response, Mapping) else None
        if not isinstance(freeze, Mapping):
            raise RuntimeError("POD billing service returned an invalid freeze")
        return self._grant(freeze, remote_token=remote_token)

    def settle(
        self,
        actor: Actor,
        grant: PodExecutionGrant,
        plan: PodCallPlan,
        outcomes: Sequence[PodCallOutcome],
    ) -> None:
        del actor
        self._remote_client.settle_pod_points(
            grant.remote_token,
            grant.freeze_id,
            plan.settlement_payload(grant.freeze_id, outcomes),
        )

    def regrant(self, actor: Actor, freeze_id: str) -> PodExecutionGrant:
        remote_token = self._required_remote_token(actor)
        response = self._remote_client.regrant_pod_keys(remote_token, freeze_id)
        if not isinstance(response, Mapping):
            raise RuntimeError("POD billing service returned an invalid grant")
        return self._grant(response, remote_token=remote_token)

    def _required_remote_token(self, actor: Actor) -> str:
        token = str(self._remote_token_resolver(actor) or "")
        if not token:
            raise RuntimeError("POD billing authentication is required")
        return token

    @staticmethod
    def _grant(payload: Mapping[str, object], *, remote_token: str) -> PodExecutionGrant:
        keys = payload.get("keys")
        if not isinstance(keys, Mapping):
            raise RuntimeError("POD billing service did not grant provider access")
        freeze_id = str(payload.get("freeze_id") or "")
        expires_at = str(payload.get("expires_at") or "")
        if not freeze_id or not expires_at:
            raise RuntimeError("POD billing service returned an invalid grant")
        return PodExecutionGrant(
            freeze_id=freeze_id,
            rule_version=int(payload.get("rule_version") or 0),
            expires_at=expires_at,
            provider_keys={str(key): str(value) for key, value in keys.items()},
            remote_token=remote_token,
        )


def sqlite_remote_token_resolver(database_path: Path) -> Callable[[Actor], str]:
    """Resolve the latest active remote session without exposing it to routes."""

    def resolve(actor: Actor) -> str:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with connect(database_path) as connection:
            row = connection.execute(
                """SELECT sessions.remote_token
                   FROM customer_sessions AS sessions
                   JOIN customer_users AS users ON users.user_id = sessions.user_id
                   WHERE sessions.user_id = ?
                     AND users.workspace_id = ?
                     AND sessions.revoked_at = ''
                     AND sessions.expires_at > ?
                     AND sessions.remote_token <> ''
                   ORDER BY sessions.created_at DESC
                   LIMIT 1""",
                (actor.id, actor.workspace_id, now),
            ).fetchone()
        return str(row["remote_token"] or "") if row is not None else ""

    return resolve


__all__ = ["RemotePodBillingCoordinator", "sqlite_remote_token_resolver"]
