import pytest

from wh_local.customer.contracts import CustomerBillingPermissionError
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

    def freeze_batch_points(self, token: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("freeze", token, payload))
        return {
            "freeze": {
                "freeze_id": str(payload.get("idempotency_key") or "product-freeze-1"),
                "rule_version": 7,
                "expires_at": "2099-01-08T00:00:00Z",
                "keys": [
                    {"provider": "ark", "api_key": "short-ark", "expires_at": "2099-01-01T00:00:00Z"},
                    {"provider": "wuyin", "api_key": "short-wuyin", "expires_at": "2099-01-01T00:00:00Z"},
                ],
            }
        }

    def settle_batch_points(self, token: str, freeze_id: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("settle", token, {**payload, "freeze_id": freeze_id}))
        return {"status": "settled"}

    def batch_freeze_status(self, token: str, freeze_id: str) -> dict[str, object]:
        self.calls.append(("status", token, freeze_id))
        return {
            "freeze": {
                "freeze_id": freeze_id,
                "status": "settled",
                "link_count": 1,
                "expires_at": "2000-01-01T00:00:00Z",
            }
        }


class RealClientShapeRemoteBilling(FakeRemoteBilling):
    def freeze_batch_points(self, token: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("freeze", token, payload))
        return {
            "freeze": {
                "freeze_id": "product-freeze-real-shape",
                "rule_version": 9,
                "expires_at": "2099-01-08T00:00:00Z",
                "keys": [
                    {"provider": "ark", "api_key": "short-ark", "expires_at": "2099-01-01T06:00:00Z"},
                    {"provider": "wuyin", "api_key": "short-wuyin", "expires_at": "2099-01-01T06:00:00Z"},
                ],
            }
        }

class FakeSemiRemoteBilling(FakeRemoteBilling):
    """Status reports a semi profile so regrant re-freezes through the pod-semi route."""

    def freeze_pod_semi_points(self, token: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("freeze-semi", token, payload))
        return {
            "freeze": {
                "freeze_id": str(payload.get("idempotency_key") or "semi-freeze-1"),
                "rule_version": 7,
                "expires_at": "2099-01-08T00:00:00Z",
                "keys": [
                    {"provider": "ark", "api_key": "short-ark", "expires_at": "2099-01-01T00:00:00Z"},
                ],
            }
        }

    def batch_freeze_status(self, token: str, freeze_id: str) -> dict[str, object]:
        self.calls.append(("status", token, freeze_id))
        return {
            "freeze": {
                "freeze_id": freeze_id,
                "status": "frozen",
                "link_count": 2,
                "scope": ["four_grid"],
                "billing_profile": "pod_semi_v1",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        }


def test_semi_regrant_reports_item_count_for_crash_recovery() -> None:
    remote = FakeSemiRemoteBilling()
    coordinator = RemotePodBillingCoordinator(remote, lambda _actor: "remote-session")
    actor = Actor(id="user-1", username="user", role="operator", workspace_id="workspace-1")

    coordinator.regrant(actor, "semi-freeze-1")

    assert remote.calls[-1] == (
        "freeze-semi",
        "remote-session",
        {
            "idempotency_key": "semi-freeze-1",
            "link_count": 2,
            "scope": ["four_grid"],
            "billing_profile": "pod_semi_v1",
            "item_count": 8,
        },
    )


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

    assert remote.calls[0] == (
        "freeze",
        "remote-session",
        {
            "idempotency_key": "pod:retry:title-action",
            "link_count": 1,
            "scope": ["title"],
            "billing_profile": "pod_random_v1",
        },
    )
    assert remote.calls[1] == (
        "settle",
        "remote-session",
        {
            "freeze_id": "pod:retry:title-action",
            "items": [
                {
                    "link_idx": 1,
                    "subitems": [{"feature": "title", "status": "success"}],
                }
            ],
        },
    )
    assert remote.calls[2] == ("status", "remote-session", "pod:retry:title-action")
    assert remote.calls[3] == (
        "freeze",
        "remote-session",
        {
            "idempotency_key": "pod:retry:title-action",
            "link_count": 1,
            "scope": [],
            "billing_profile": "pod_random_v1",
        },
    )
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

    assert grant.freeze_id == "product-freeze-real-shape"
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
        "product-freeze-1",
        rule_version=7,
        expires_at="2000-01-01T00:00:00Z",
    )

    assert grant.provider_keys == {}
    assert remote.calls == [("status", "remote-session", "product-freeze-1")]
class FissionProfileRemoteBilling(FakeRemoteBilling):
    """回显裂变画像；裂变已与全定制共用同一个 batch 入口，不再有独立通道。"""

    def batch_freeze_status(self, token: str, freeze_id: str) -> dict[str, object]:
        self.calls.append(("status", token, freeze_id))
        return {
            "freeze": {
                "freeze_id": freeze_id,
                "status": "frozen",
                "link_count": 2,
                "scope": ["title", "four_grid"],
                "billing_profile": "pod_fission_v1",
                "expires_at": "2099-01-01T00:00:00Z",
            }
        }


def test_fission_batch_uses_the_unified_batch_channel_end_to_end() -> None:
    """裂变与全定制共用同一个入口与画像：不得再落回任何独立通道。"""
    remote = FissionProfileRemoteBilling()
    coordinator = RemotePodBillingCoordinator(remote, lambda _actor: "remote-session")
    actor = Actor(id="user-1", username="user", role="operator", workspace_id="workspace-1")
    plan = PodCallPlan.for_batch(
        "fission-1", style_count=2, billing_profile="pod_fission_v1"
    )

    grant = coordinator.freeze(actor, plan)
    outcomes = tuple(
        PodCallOutcome(call.call_id, call.feature, "success") for call in plan.calls
    )
    coordinator.settle(actor, grant, plan, outcomes)
    renewed = coordinator.regrant(actor, grant.freeze_id)

    # 冻结：一款一条 link，scope 为 title + four_grid。
    assert remote.calls[0] == (
        "freeze",
        "remote-session",
        {
            "idempotency_key": "pod:batch:fission-1:initial",
            "link_count": 2,
            "scope": ["title", "four_grid"],
            "billing_profile": "pod_fission_v1",
        },
    )
    # 结算：每条 link 覆盖冻结 scope 的两个子项。
    assert remote.calls[1] == (
        "settle",
        "remote-session",
        {
            "freeze_id": "pod:batch:fission-1:initial",
            "items": [
                {
                    "link_idx": 1,
                    "subitems": [
                        {"feature": "title", "status": "success"},
                        {"feature": "four_grid", "status": "success"},
                    ],
                },
                {
                    "link_idx": 2,
                    "subitems": [
                        {"feature": "title", "status": "success"},
                        {"feature": "four_grid", "status": "success"},
                    ],
                },
            ],
        },
    )
    # 重冻结按回显画像走同一个 batch 接口（不带半定制的 item_count）。
    assert remote.calls[2] == ("status", "remote-session", "pod:batch:fission-1:initial")
    assert remote.calls[3] == (
        "freeze",
        "remote-session",
        {
            "idempotency_key": "pod:batch:fission-1:initial",
            "link_count": 2,
            "scope": ["title", "four_grid"],
            "billing_profile": "pod_fission_v1",
        },
    )
    assert renewed.provider_key("ark") == "short-ark"
    assert {route for route, _token, _payload in remote.calls if route.startswith(("freeze", "settle"))} == {
        "freeze",
        "settle",
    }
