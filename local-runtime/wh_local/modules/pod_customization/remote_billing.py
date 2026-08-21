from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ...customer.contracts import CustomerBillingPermissionError
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

    def settlement_grant(
        self,
        actor: Actor,
        freeze_id: str,
        *,
        rule_version: int,
        expires_at: str,
    ) -> PodExecutionGrant:
        """Authenticate settlement without requiring an active provider-key grant."""
        remote_token = self._required_remote_token(actor)
        response = self._remote_client.pod_freeze_status(remote_token, freeze_id)
        freeze = response.get("freeze") if isinstance(response, Mapping) else None
        status = freeze if isinstance(freeze, Mapping) else response
        if not isinstance(status, Mapping) or str(status.get("freeze_id") or "") != freeze_id:
            raise RuntimeError("POD billing service returned an invalid freeze status")
        return PodExecutionGrant(
            freeze_id=freeze_id,
            rule_version=int(status.get("rule_version") or rule_version),
            expires_at=str(status.get("expires_at") or expires_at),
            provider_keys={},
            remote_token=remote_token,
        )

    def _required_remote_token(self, actor: Actor) -> str:
        token = str(self._remote_token_resolver(actor) or "")
        if not token:
            raise CustomerBillingPermissionError()
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


def session_remote_token_resolver(sessions: Any) -> Callable[[Actor], str]:
    """Resolve a remote token from the live session service only."""

    def resolve(actor: Actor) -> str:
        resolver = getattr(sessions, "remote_token_for_actor", None)
        if not callable(resolver):
            return ""
        return str(resolver(actor.id, actor.workspace_id) or "")

    return resolve


__all__ = ["RemotePodBillingCoordinator", "session_remote_token_resolver"]
