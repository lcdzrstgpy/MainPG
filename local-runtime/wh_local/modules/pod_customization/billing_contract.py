from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal, Mapping, Protocol, Sequence

from ...session import Actor


PodFeature = Literal["pod.title", "pod.image"]
PodCallStatus = Literal["success", "no_return"]


@dataclass(frozen=True)
class PodPlannedCall:
    call_id: str
    feature: PodFeature

    def payload(self) -> dict[str, str]:
        return {"call_id": self.call_id, "feature": self.feature}


@dataclass(frozen=True)
class PodCallOutcome:
    call_id: str
    feature: PodFeature
    status: PodCallStatus

    def payload(self) -> dict[str, str]:
        return {
            "call_id": self.call_id,
            "feature": self.feature,
            "status": self.status,
        }


@dataclass(frozen=True)
class PodCallPlan:
    idempotency_key: str
    calls: tuple[PodPlannedCall, ...]

    @classmethod
    def for_batch(cls, batch_id: str, *, style_count: int) -> "PodCallPlan":
        if style_count < 1:
            raise ValueError("style_count must be positive")
        calls = tuple(
            call
            for style_index in range(1, style_count + 1)
            for call in (
                PodPlannedCall(f"{batch_id}:style:{style_index}:image:1", "pod.image"),
                PodPlannedCall(f"{batch_id}:style:{style_index}:image:2", "pod.image"),
                PodPlannedCall(f"{batch_id}:style:{style_index}:title:1", "pod.title"),
                PodPlannedCall(f"{batch_id}:style:{style_index}:title:2", "pod.title"),
                PodPlannedCall(f"{batch_id}:style:{style_index}:title:3", "pod.title"),
            )
        )
        return cls(idempotency_key=f"pod:batch:{batch_id}:initial", calls=calls)

    @classmethod
    def for_retry(
        cls, action_id: str, *, feature: PodFeature, max_attempts: int = 1
    ) -> "PodCallPlan":
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        return cls(
            idempotency_key=f"pod:retry:{action_id}",
            calls=tuple(
                PodPlannedCall(f"{action_id}:attempt:{attempt}", feature)
                for attempt in range(1, max_attempts + 1)
            ),
        )

    @classmethod
    def for_trial(cls, trial_id: str, *, include_title: bool) -> "PodCallPlan":
        calls = [
            PodPlannedCall(f"{trial_id}:image:1", "pod.image"),
            PodPlannedCall(f"{trial_id}:image:2", "pod.image"),
        ]
        if include_title:
            calls.extend(
                PodPlannedCall(f"{trial_id}:title:{attempt}", "pod.title")
                for attempt in range(1, 4)
            )
        return cls(idempotency_key=f"pod:trial:{trial_id}", calls=tuple(calls))

    @classmethod
    def for_style_retry(cls, action_id: str, *, include_title: bool) -> "PodCallPlan":
        calls = [
            PodPlannedCall(f"{action_id}:image:1", "pod.image"),
            PodPlannedCall(f"{action_id}:image:2", "pod.image"),
        ]
        if include_title:
            calls.extend(
                PodPlannedCall(f"{action_id}:title:{attempt}", "pod.title")
                for attempt in range(1, 4)
            )
        return cls(idempotency_key=f"pod:retry:{action_id}", calls=tuple(calls))

    def freeze_payload(self, *, encrypted_session_key: str) -> dict[str, object]:
        return {
            "idempotency_key": self.idempotency_key,
            "title_call_count": sum(call.feature == "pod.title" for call in self.calls),
            "image_call_count": sum(call.feature == "pod.image" for call in self.calls),
            "calls": [call.payload() for call in self.calls],
            "encrypted_session_key": str(encrypted_session_key),
        }

    def settlement_payload(
        self,
        freeze_id: str,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[str, object]:
        expected = {(call.call_id, call.feature) for call in self.calls}
        actual = [(outcome.call_id, outcome.feature) for outcome in outcomes]
        if len(actual) != len(expected) or len(set(actual)) != len(actual) or set(actual) != expected:
            raise ValueError("settlement outcomes must match the frozen POD call plan exactly")
        return {
            "freeze_id": str(freeze_id),
            "items": [outcome.payload() for outcome in outcomes],
        }


@dataclass(frozen=True)
class PodExecutionGrant:
    freeze_id: str
    rule_version: int
    expires_at: str
    provider_keys: Mapping[str, str] = field(repr=False)
    remote_token: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "provider_keys",
            MappingProxyType(
                {
                    str(provider): str(value)
                    for provider, value in self.provider_keys.items()
                    if str(value)
                }
            ),
        )

    def provider_key(self, provider: str) -> str:
        try:
            expires_at = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return ""
        if expires_at <= datetime.now(timezone.utc):
            return ""
        return str(self.provider_keys.get(str(provider)) or "")


class PodBillingCoordinator(Protocol):
    """Injected adapter over the remote POD freeze/settle/regrant contract.

    The adapter owns encrypted-session-key creation and grant-envelope
    decryption.  This module receives plaintext grants only in memory.
    """

    def freeze(self, actor: Actor, plan: PodCallPlan) -> PodExecutionGrant: ...

    def settle(
        self,
        actor: Actor,
        grant: PodExecutionGrant,
        plan: PodCallPlan,
        outcomes: Sequence[PodCallOutcome],
    ) -> None: ...

    def regrant(self, actor: Actor, freeze_id: str) -> PodExecutionGrant: ...
