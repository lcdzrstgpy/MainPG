from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Literal, Mapping, Protocol, Sequence

from ...session import Actor


PodFeature = Literal["pod.title", "pod.image"]
PodCallStatus = Literal["success", "no_return"]

_PRODUCT_BATCH_FEATURES: tuple[tuple[PodFeature, str], ...] = (
    ("pod.title", "title"),
    ("pod.image", "four_grid"),
)

# 每个款式预留的标题调用次数。与 title_runtime.MAX_ATTEMPTS 保持一致；标题重生不额外计费。
TITLE_ATTEMPTS = 5


class PodBillingAuthorizationRequired(RuntimeError):
    """The durable billing action must pause until a fresh grant is issued."""


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
        if isinstance(style_count, bool) or not isinstance(style_count, int) or not 1 <= style_count <= 200:
            raise ValueError("style_count must be between 1 and 200")
        calls = tuple(
            call
            for style_index in range(1, style_count + 1)
            for call in (
                PodPlannedCall(f"{batch_id}:style:{style_index}:image:1", "pod.image"),
                PodPlannedCall(f"{batch_id}:style:{style_index}:image:2", "pod.image"),
                *(
                    PodPlannedCall(f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title")
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                ),
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
                for attempt in range(1, TITLE_ATTEMPTS + 1)
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
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(idempotency_key=f"pod:retry:{action_id}", calls=tuple(calls))

    @classmethod
    def for_batch_retry(
        cls,
        action_id: str,
        *,
        image_style_indices: Sequence[int],
        title_style_indices: Sequence[int],
        include_title: bool,
    ) -> "PodCallPlan":
        """Build one durable plan for selected failed POD styles.

        Image retries keep the existing two-attempt grid allowance and then
        regenerate their listing title.  Title-only retries reserve title calls
        without re-running image generation.
        """
        image_indices = tuple(image_style_indices)
        title_indices = tuple(title_style_indices)
        if not image_indices and not title_indices:
            raise ValueError("at least one failed style is required")
        calls: list[PodPlannedCall] = []
        for style_index in image_indices:
            calls.extend(
                (
                    PodPlannedCall(f"{action_id}:style:{style_index}:image:1", "pod.image"),
                    PodPlannedCall(f"{action_id}:style:{style_index}:image:2", "pod.image"),
                )
            )
            if include_title:
                calls.extend(
                    PodPlannedCall(f"{action_id}:style:{style_index}:title:{attempt}", "pod.title")
                    for attempt in range(1, TITLE_ATTEMPTS + 1)
                )
        for style_index in title_indices:
            calls.extend(
                PodPlannedCall(f"{action_id}:style:{style_index}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(idempotency_key=f"pod:retry:{action_id}", calls=tuple(calls))

    @classmethod
    def for_batch_resume(
        cls,
        batch_id: str,
        resume_id: str,
        *,
        image_style_indices: Sequence[int],
        title_style_indices: Sequence[int],
    ) -> "PodCallPlan":
        """Freeze only work that remains after a previously settled pause.

        Call identifiers intentionally remain tied to the original batch and
        style so the worker can resume its persisted generation state, while
        the freeze idempotency key identifies this distinct resume action.
        """
        image_indices = tuple(image_style_indices)
        title_indices = tuple(title_style_indices)
        if not image_indices and not title_indices:
            raise ValueError("at least one remaining POD style is required")
        if set(image_indices) & set(title_indices):
            raise ValueError("a resumed style cannot be both image and title only")
        calls: list[PodPlannedCall] = []
        for style_index in image_indices:
            calls.extend(
                PodPlannedCall(f"{batch_id}:style:{style_index}:image:{attempt}", "pod.image")
                for attempt in (1, 2)
            )
        for style_index in title_indices:
            calls.extend(
                PodPlannedCall(f"{batch_id}:style:{style_index}:title:{attempt}", "pod.title")
                for attempt in range(1, TITLE_ATTEMPTS + 1)
            )
        return cls(
            idempotency_key=f"pod:batch:{batch_id}:resume:{resume_id}",
            calls=tuple(calls),
        )

    def freeze_payload(self, *, encrypted_session_key: str) -> dict[str, object]:
        return {
            "idempotency_key": self.idempotency_key,
            "title_call_count": sum(call.feature == "pod.title" for call in self.calls),
            "image_call_count": sum(call.feature == "pod.image" for call in self.calls),
            "calls": [call.payload() for call in self.calls],
            "encrypted_session_key": str(encrypted_session_key),
        }

    def product_batch_freeze_payload(self) -> dict[str, object]:
        """Project POD work onto the existing product-processing batch ledger.

        One POD style is one billed product link. Provider retries remain local
        attempts and do not create additional billable subitems.
        """
        groups = self._product_batch_groups()
        present = {call.feature for group in groups for call in group}
        return {
            "idempotency_key": self.idempotency_key,
            "link_count": len(groups),
            "scope": [remote for pod, remote in _PRODUCT_BATCH_FEATURES if pod in present],
            "billing_profile": "pod_random_v1",
        }

    def product_batch_settlement_payload(
        self,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[str, object]:
        """Fold provider attempts into product-processing subitem outcomes."""
        outcome_by_call = self._validated_outcomes(outcomes)
        items: list[dict[str, object]] = []
        for link_idx, group in enumerate(self._product_batch_groups(), start=1):
            subitems: list[dict[str, str]] = []
            for pod_feature, product_feature in _PRODUCT_BATCH_FEATURES:
                matching = [call for call in group if call.feature == pod_feature]
                if not matching:
                    continue
                succeeded = any(
                    outcome_by_call[(call.call_id, call.feature)] == "success"
                    for call in matching
                )
                subitems.append(
                    {
                        "feature": product_feature,
                        "status": "success" if succeeded else "no_return",
                    }
                )
            items.append({"link_idx": link_idx, "subitems": subitems})
        return {"items": items}

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

    def _product_batch_groups(self) -> tuple[tuple[PodPlannedCall, ...], ...]:
        groups: dict[int, list[PodPlannedCall]] = {}
        for call in self.calls:
            group_id = 1
            marker = ":style:"
            if marker in call.call_id:
                style_suffix = call.call_id.rsplit(marker, 1)[1]
                raw_style_id = style_suffix.split(":", 1)[0]
                if raw_style_id.isdigit():
                    group_id = int(raw_style_id)
            groups.setdefault(group_id, []).append(call)
        return tuple(tuple(groups[group_id]) for group_id in sorted(groups))

    def _validated_outcomes(
        self,
        outcomes: Sequence[PodCallOutcome],
    ) -> dict[tuple[str, PodFeature], PodCallStatus]:
        expected = {(call.call_id, call.feature) for call in self.calls}
        actual = [(outcome.call_id, outcome.feature) for outcome in outcomes]
        if len(actual) != len(expected) or len(set(actual)) != len(actual) or set(actual) != expected:
            raise ValueError("settlement outcomes must match the frozen POD call plan exactly")
        return {
            (outcome.call_id, outcome.feature): outcome.status
            for outcome in outcomes
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
