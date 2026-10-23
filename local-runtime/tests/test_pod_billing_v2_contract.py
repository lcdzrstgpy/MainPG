from __future__ import annotations

import pytest

from wh_local.customer.contracts import CustomerBillingRemoteError
from wh_local.customer.remote_client import CustomerAuthClient
from wh_local.modules.pod_customization.billing_contract import (
    POD_BILLING_PROFILE_FISSION,
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.remote_billing import (
    RemotePodBillingCoordinator,
    build_v2_freeze_payload,
    build_v2_settlement_payload,
    expected_points_for,
    parse_v2_settlement_receipt,
)


def _outcomes(plan: PodCallPlan, *, success_feature: str = "pod.image") -> tuple[PodCallOutcome, ...]:
    return tuple(
        PodCallOutcome(
            call.call_id,
            call.feature,
            "success" if call.feature == success_feature else "no_return",
        )
        for call in plan.calls
    )


def test_v2_full_custom_freeze_payload_is_a_golden_snapshot() -> None:
    payload = build_v2_freeze_payload(PodCallPlan.for_batch("b1", style_count=2))

    assert payload == {
        "idempotency_key": "pod:batch:b1:initial",
        "module": "pod_custom",
        "billing_profile": "pod_random_v1",
        "unit_kind": "style",
        "link_count": 2,
        "scope": {"image": True, "title": True},
        "unit_ids": ["style-0", "style-1"],
    }


def test_v2_column_freeze_payload_reports_groups_and_items() -> None:
    payload = build_v2_freeze_payload(PodCallPlan.for_column_batch("c1", style_count=8))

    assert payload["billing_profile"] == "pod_random_grid_v1"
    assert payload["unit_kind"] == "group"
    assert payload["link_count"] == 2
    assert payload["item_count"] == 8
    assert payload["unit_ids"] == ["group-0", "group-1"]
    assert payload["module"] == "pod_custom"


def test_v2_semi_freeze_payload_keeps_group_units() -> None:
    payload = build_v2_freeze_payload(PodCallPlan.for_semi_batch("s1", count=8))

    assert payload["module"] == "pod_semi"
    assert payload["unit_kind"] == "group"
    assert payload["link_count"] == 2
    assert payload["item_count"] == 8
    assert payload["unit_ids"] == ["group-0", "group-1"]
    assert payload["scope"] == {"image": True, "title": False}


def test_v2_fission_plan_is_a_normal_profile() -> None:
    plan = PodCallPlan.for_batch(
        "f1", style_count=3, billing_profile=POD_BILLING_PROFILE_FISSION
    )
    payload = build_v2_freeze_payload(plan)

    assert payload["module"] == "pod_fission"
    assert payload["billing_profile"] == "pod_fission_v1"
    assert payload["unit_kind"] == "style"
    assert payload["unit_ids"] == ["style-0", "style-1", "style-2"]


def test_v2_zero_billing_plan_is_tagged_pod_zero() -> None:
    payload = build_v2_freeze_payload(PodCallPlan.for_brief("brief-1"))

    assert payload["module"] == "pod_zero"
    assert payload["scope"] == {"image": False, "title": True}
    assert payload["link_count"] == 1
    assert payload["unit_ids"] == ["style-0"]


def test_v2_settlement_payload_uses_zero_based_links_and_image_feature() -> None:
    plan = PodCallPlan.for_batch("b1", style_count=2)
    payload = build_v2_settlement_payload(plan, _outcomes(plan))

    assert [link["link_idx"] for link in payload["links"]] == [0, 1]
    assert [link["unit_id"] for link in payload["links"]] == ["style-0", "style-1"]
    assert payload["links"][0]["subitems"] == [
        {"feature": "title", "status": "no_return"},
        {"feature": "image", "status": "success"},
    ]
    # v1 的 1 基索引与 four_grid 名称不得出现在 v2 载荷里。
    assert "four_grid" not in str(payload)
    assert payload["links"][0]["link_idx"] == 0


def test_v2_receipt_uses_server_amounts_only() -> None:
    receipt = parse_v2_settlement_receipt(
        {
            "settlement_id": "settle-1",
            "charged_points": 45,
            "refunded_points": 45,
            "release_reason": "normal",
            "rule_version": "v7",
            "links": [
                {
                    "link_idx": 0,
                    "unit_id": "style-0",
                    "status": "success",
                    "charged_points": 45,
                    "refunded_points": 0,
                },
                {
                    "link_idx": 1,
                    "unit_id": "style-1",
                    "status": "no_return",
                    "charged_points": 0,
                    "refunded_points": 45,
                },
            ],
        }
    )

    assert receipt["charged_points"] == 45
    assert receipt["refunded_points"] == 45
    assert receipt["rule_version"] == "v7"
    assert [link["link_idx"] for link in receipt["links"]] == [0, 1]


def test_expected_points_uses_frozen_link_prices() -> None:
    plan = PodCallPlan.for_batch("b1", style_count=2)
    outcomes = list(_outcomes(plan))
    # 第二款图片也成功。
    outcomes = [
        PodCallOutcome(call.call_id, call.feature, "success")
        if call.call_id.startswith("b1:style:2:image:")
        else outcome
        for outcome, call in zip(outcomes, plan.calls, strict=True)
    ]

    assert expected_points_for(plan, outcomes, [45.0, 45.0]) == 90.0


class _FakeRemote:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []

    def freeze_batch_points(self, token: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append(("freeze-v1", token, payload))
        return {
            "freeze": {
                "freeze_id": str(payload["idempotency_key"]),
                "rule_version": 7,
                "expires_at": "2099-01-08T00:00:00Z",
                "keys": {"ark": ["", "ark-key"], "wuyin": ["wuyin-key"]},
            }
        }

    def pod_v2_freeze(self, token: str, payload: dict[str, object], *, idempotency_key: str):
        self.calls.append(("freeze-v2", token, {"key": idempotency_key, **payload}))
        return {
            "freeze": {
                "freeze_id": idempotency_key,
                "status": "frozen",
                "frozen_points": 90,
                "link_prices": [45, 45],
                "unit_ids": ["style-0", "style-1"],
                "settle_deadline": "2099-01-01T00:10:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "rule_version": "v7",
                "keys": {"ark": ["", "ark-key"], "wuyin": ["wuyin-key"]},
            }
        }

    def pod_v2_settle(self, token, freeze_id, payload, *, idempotency_key=""):
        self.calls.append(("settle-v2", token, payload))
        return {"charged_points": 45, "refunded_points": 45, "links": []}


def _actor():
    from wh_local.session import Actor

    return Actor(id="user-1", username="user", role="operator", workspace_id="workspace-1")


def test_v1_grant_accepts_provider_arrays_and_first_non_empty_key() -> None:
    remote = _FakeRemote()
    coordinator = RemotePodBillingCoordinator(remote, lambda _actor: "remote-session")

    grant = coordinator.freeze(_actor(), PodCallPlan.for_batch("b1", style_count=1))

    assert grant.provider_keys == {"ark": "ark-key", "wuyin": "wuyin-key"}
    assert grant.keys_missing is False


def test_empty_keys_do_not_fail_the_freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoKeysRemote(_FakeRemote):
        def freeze_batch_points(self, token, payload):
            return {
                "freeze": {
                    "freeze_id": str(payload["idempotency_key"]),
                    "rule_version": 1,
                    "expires_at": "2099-01-08T00:00:00Z",
                    "keys": {},
                }
            }

    coordinator = RemotePodBillingCoordinator(_NoKeysRemote(), lambda _actor: "remote-session")
    grant = coordinator.freeze(_actor(), PodCallPlan.for_batch("b1", style_count=1))

    assert grant.keys_missing is True
    assert grant.provider_keys == {}


def test_v2_freeze_switch_returns_a_snapshot_and_structured_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WH_LOCAL_BILLING_CONTRACT_VERSION", "v2")
    remote = _FakeRemote()
    coordinator = RemotePodBillingCoordinator(remote, lambda _actor: "remote-session")
    plan = PodCallPlan.for_batch("b1", style_count=2)

    grant = coordinator.freeze(_actor(), plan)

    assert remote.calls[0][0] == "freeze-v2"
    assert grant.freeze_id == "pod:batch:b1:initial"
    assert grant.snapshot["frozen_points"] == 90
    assert grant.snapshot["link_prices"] == [45.0, 45.0]
    assert grant.snapshot["unit_ids"] == ["style-0", "style-1"]
    assert grant.snapshot["settle_deadline"] == "2099-01-01T00:10:00Z"
    assert grant.snapshot["rule_version"] == "v7"

    receipt = coordinator.settle(_actor(), grant, plan, _outcomes(plan))
    assert receipt is not None
    assert receipt["charged_points"] == 45
    assert remote.calls[1][0] == "settle-v2"


def test_v2_freeze_rejects_non_frozen_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SettledReplay(_FakeRemote):
        def pod_v2_freeze(self, token, payload, *, idempotency_key):
            return {"freeze": {"freeze_id": idempotency_key, "status": "settled"}}

    monkeypatch.setenv("WH_LOCAL_BILLING_CONTRACT_VERSION", "v2")
    coordinator = RemotePodBillingCoordinator(_SettledReplay(), lambda _actor: "token")

    with pytest.raises(Exception) as error:
        coordinator.freeze(_actor(), PodCallPlan.for_batch("b1", style_count=1))
    assert "freeze_not_active" == getattr(error.value, "code", "")


class _FakeResponse:
    def __init__(self, status: int, text: str) -> None:
        self.status_code = status
        self.text = text


class _FakeSession:
    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self.text = text

    def request(self, method, url, data=None, headers=None, timeout=None):
        return _FakeResponse(self.status, self.text)


def test_v2_error_body_is_parsed_into_structured_exception() -> None:
    client = CustomerAuthClient("https://example.test")
    client._session = _FakeSession(  # noqa: SLF001 - test doubles the transport
        409,
        '{"error": {"code": "idempotency_key_reused", "retryable": false}}',
    )

    with pytest.raises(CustomerBillingRemoteError) as error:
        client.pod_v2_settle("token", "freeze-1", {"links": []})

    assert error.value.code == "idempotency_key_reused"
    assert error.value.retryable is False
    assert error.value.status_code == 409


def test_v2_write_methods_send_idempotency_key_header(monkeypatch: pytest.MonkeyPatch) -> None:
    client = CustomerAuthClient("https://example.test")
    captured: list[tuple[str, dict[str, str]]] = []

    def _fake_post(path, payload, headers=None):
        captured.append((path, dict(headers or {})))
        return {"ok": True}

    monkeypatch.setattr(client, "_post", _fake_post)

    client.pod_v2_freeze("token", {"idempotency_key": "freeze-1"}, idempotency_key="freeze-1")
    client.pod_v2_close("token", "freeze-1", {"links": []})

    assert captured[0][0] == "/api/customer/billing/v2/pod/freezes"
    assert captured[0][1]["Idempotency-Key"] == "freeze-1"
    assert captured[1][0] == "/api/customer/billing/v2/pod/freezes/freeze-1/close"
    assert captured[1][1]["Idempotency-Key"] == "freeze-1:close"


def test_grant_repr_hides_keys_and_snapshot() -> None:
    grant = PodExecutionGrant(
        "freeze-1",
        1,
        "2099-01-01T00:00:00Z",
        {"ark": "secret-key"},
        snapshot={"raw": {"keys": {"ark": ["secret-key"]}}},
    )

    assert "secret-key" not in repr(grant)
