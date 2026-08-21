from wh_local.modules.pod_customization.billing_contract import (
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.remote_billing import RemotePodBillingCoordinator
from wh_local.session import Actor


class FakeRemoteBilling:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def freeze_pod_points(self, token: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("freeze", token, payload))
        return {
            "freeze": {
                "freeze_id": "pod-freeze-1",
                "rule_version": 7,
                "expires_at": "2099-01-01T00:00:00Z",
                "keys": {"ark": "short-ark", "wuyin": "short-wuyin"},
            }
        }

    def settle_pod_points(self, token: str, freeze_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("settle", token, {**payload, "freeze_id": freeze_id}))
        return {"status": "settled"}

    def regrant_pod_keys(self, token: str, freeze_id: str) -> dict[str, object]:
        self.calls.append(("regrant", token, freeze_id))
        return {
            "freeze_id": freeze_id,
            "rule_version": 7,
            "expires_at": "2099-01-01T00:00:00Z",
            "keys": {"ark": "short-ark", "wuyin": "short-wuyin"},
        }

    def pod_freeze_status(self, token: str, freeze_id: str) -> dict[str, object]:
        self.calls.append(("status", token, freeze_id))
        return {
            "freeze": {
                "freeze_id": freeze_id,
                "status": "settled",
                "rule_version": 7,
                "expires_at": "2000-01-01T00:00:00Z",
            }
        }


class RealClientShapeRemoteBilling(FakeRemoteBilling):
    def freeze_pod_points(self, token: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("freeze", token, payload))
        return {
            "freeze": {
                "freeze_id": "pod-freeze-real-shape",
                "rule_version": 9,
                "expires_at": "2099-01-01T06:00:00Z",
                "freeze_expires_at": "2099-01-08T00:00:00Z",
                "keys": {"ark": "short-ark", "wuyin": "short-wuyin"},
            }
        }

def test_remote_coordinator_adapts_exact_plan_and_keeps_token_out_of_repr() -> None:
    remote = FakeRemoteBilling()
    coordinator = RemotePodBillingCoordinator(remote, lambda _actor: "remote-session")
    actor = Actor(id="user-1", username="user", role="operator", workspace_id="workspace-1")
    plan = PodCallPlan.for_retry("title-action", feature="pod.title", max_attempts=3)

    grant = coordinator.freeze(actor, plan)
    outcomes = tuple(
        PodCallOutcome(call.call_id, call.feature, "success" if index == 0 else "no_return")
        for index, call in enumerate(plan.calls)
    )
    coordinator.settle(actor, grant, plan, outcomes)
    renewed = coordinator.regrant(actor, grant.freeze_id)

    freeze_payload = remote.calls[0][2]
    assert isinstance(freeze_payload, dict)
    assert freeze_payload["title_call_count"] == 3
    assert freeze_payload["image_call_count"] == 0
    assert freeze_payload["calls"] == [call.payload() for call in plan.calls]
    assert grant.provider_key("ark") == "short-ark"
    assert renewed.provider_key("wuyin") == "short-wuyin"
    assert "remote-session" not in repr(grant)
    assert "short-ark" not in repr(grant)


def test_expired_grant_refuses_provider_keys_in_normal_client_execution() -> None:
    grant = PodExecutionGrant(
        "pod-freeze-expired",
        1,
        "2000-01-01T00:00:00Z",
        {"ark": "must-not-be-used", "wuyin": "must-not-be-used"},
    )

    assert grant.provider_key("ark") == ""
    assert grant.provider_key("wuyin") == ""


def test_coordinator_accepts_normalized_real_client_grant_shape() -> None:
    remote = RealClientShapeRemoteBilling()
    coordinator = RemotePodBillingCoordinator(remote, lambda _actor: "remote-session")
    actor = Actor(id="user-1", username="user", role="operator", workspace_id="workspace-1")

    grant = coordinator.freeze(
        actor,
        PodCallPlan.for_retry("real-client-shape", feature="pod.title"),
    )

    assert grant.freeze_id == "pod-freeze-real-shape"
    assert grant.rule_version == 9
    assert grant.expires_at == "2099-01-01T06:00:00Z"
    assert grant.provider_key("ark") == "short-ark"


def test_missing_live_remote_token_is_an_authentication_error() -> None:
    coordinator = RemotePodBillingCoordinator(FakeRemoteBilling(), lambda _actor: "")
    actor = Actor(id="user-1", username="user", role="operator", workspace_id="workspace-1")

    with pytest.raises(CustomerBillingPermissionError):
        coordinator.freeze(
            actor,
            PodCallPlan.for_retry("missing-live-token", feature="pod.image"),
        )


def test_settlement_auth_checks_status_without_requesting_provider_keys() -> None:
    remote = FakeRemoteBilling()
    coordinator = RemotePodBillingCoordinator(remote, lambda _actor: "remote-session")
    actor = Actor(id="user-1", username="user", role="operator", workspace_id="workspace-1")

    grant = coordinator.settlement_grant(
        actor,
        "pod-freeze-1",
        rule_version=7,
        expires_at="2000-01-01T00:00:00Z",
    )

    assert grant.provider_keys == {}
    assert remote.calls == [("status", "remote-session", "pod-freeze-1")]
import pytest

from wh_local.customer.contracts import CustomerBillingPermissionError
