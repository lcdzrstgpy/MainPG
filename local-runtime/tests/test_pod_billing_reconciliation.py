from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wh_local.modules.pod_customization.billing_contract import (
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.reconciliation import PodBillingReconciler
from wh_local.modules.pod_customization.remote_billing import PodBillingRemoteError
from wh_local.modules.pod_customization.repository import PodCustomizationRepository
from wh_local.session import Actor


ACTOR = Actor(id="owner", username="", role="", workspace_id="workspace-a")
DEVICE = "device-a"


def _repository(tmp_path: Path) -> PodCustomizationRepository:
    return PodCustomizationRepository(tmp_path / "ledger.sqlite3")


def _intent(
    repository: PodCustomizationRepository,
    action_key: str,
    *,
    style_count: int = 2,
    device_id: str = DEVICE,
) -> PodCallPlan:
    plan = PodCallPlan.for_batch(action_key, style_count=style_count)
    repository.create_billing_intent(
        action_key=plan.idempotency_key,
        action_type="batch_initial",
        target_id=action_key,
        batch_id=action_key,
        actor_id=ACTOR.id,
        workspace_id=ACTOR.workspace_id,
        plan=plan,
        module=plan.module,
        unit_kind=plan.unit_kind,
        link_count=len(plan.unit_ids),
        device_id=device_id,
    )
    return plan


def _key(name: str) -> str:
    """计划幂等键（= freeze_id = action_key）。"""
    return f"pod:batch:{name}:initial"


def _authorized(
    repository: PodCustomizationRepository,
    action_key: str,
    *,
    settle_deadline: str = "",
    device_id: str = DEVICE,
) -> PodCallPlan:
    plan = _intent(repository, action_key, device_id=device_id)
    repository.attach_billing_freeze(
        plan.idempotency_key,
        freeze_id=plan.idempotency_key,
        rule_version=7,
        grant_expires_at="2099-01-01T00:00:00Z",
        frozen_points=90,
        link_prices=[45, 45],
        unit_ids=list(plan.unit_ids),
        settle_deadline=settle_deadline,
        module=plan.module,
        unit_kind=plan.unit_kind,
        billing_profile=plan.billing_profile,
        link_count=len(plan.unit_ids),
        device_id=device_id,
    )
    return plan


class FakeReconCoordinator:
    def __init__(self) -> None:
        self.probes: dict[str, object] = {}
        self.freezes: list[PodCallPlan] = []
        self.settlements: list[tuple[PodCallPlan, tuple]] = []
        self.open_freezes_payload: dict[str, object] = {"freezes": [], "unavailable": False}
        self.daily: dict[str, object] = {}
        self.daily_detail: dict[str, object] = {"items": []}

    def freeze(self, _actor, plan):
        self.freezes.append(plan)
        return PodExecutionGrant(
            plan.idempotency_key,
            7,
            "2099-01-01T00:00:00Z",
            {"wuyin": "k", "ark": "a"},
            snapshot={
                "frozen_points": 90,
                "link_prices": [45, 45],
                "unit_ids": list(plan.unit_ids),
                "settle_deadline": "2099-01-01T00:10:00Z",
                "raw": {"freeze_id": plan.idempotency_key},
            },
        )

    def freeze_status(self, _actor, freeze_id):
        value = self.probes.get(freeze_id, {"status": "frozen"})
        if isinstance(value, Exception):
            raise value
        return value

    def settle(self, _actor, _grant, plan, outcomes):
        self.settlements.append((plan, tuple(outcomes)))
        return {"charged_points": 45, "refunded_points": 0, "links": []}

    def open_freezes(self, _actor, *, module=""):
        return self.open_freezes_payload

    def daily_report(self, _actor, *, date, tz="Asia/Shanghai"):
        return self.daily

    def daily_report_detail(self, _actor, *, date, tz="Asia/Shanghai", freeze_id=""):
        return self.daily_detail


def _reconciler(
    repository: PodCustomizationRepository,
    coordinator: FakeReconCoordinator,
) -> PodBillingReconciler:
    def settle_run(stored):
        repository.prepare_billing_settlement(stored["action_key"])
        repository.mark_billing_settled(stored["action_key"])

    return PodBillingReconciler(
        repository,
        coordinator,
        settle_run=settle_run,
        plan_builder=_plan_builder,
        device_id=lambda: DEVICE,
    )


def _plan_builder(payload: dict) -> PodCallPlan:
    from wh_local.modules.pod_customization.service import PodCustomizationService

    return PodCustomizationService._billing_plan(payload)


def _past() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()


def test_startup_reconciliation_handles_every_remote_state(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    coordinator = FakeReconCoordinator()
    _authorized(repository, "freeze-frozen", settle_deadline=_past())
    _authorized(repository, "freeze-settled")
    _authorized(repository, "freeze-released")
    _authorized(repository, "freeze-missing")
    coordinator.probes = {
        _key("freeze-frozen"): {"status": "frozen"},
        _key("freeze-settled"): {"status": "settled"},
        _key("freeze-released"): {
            "status": "released",
            "released_points": 45,
            "retained_points": 45,
            "release_reason": "ttl",
        },
        _key("freeze-missing"): PodBillingRemoteError(
            "freeze not found", status_code=404, code="freeze_not_found"
        ),
    }

    result = _reconciler(repository, coordinator).reconcile_actor(ACTOR, scope="startup")

    assert result["checked"] == 4
    # 404 是「损失」终态，其余三个分支是修复。
    assert result["repaired"] == 3
    assert result["loss"] == 1
    statuses = {
        key: repository.find_billing_run_by_action_key(key)["status"]
        for key in coordinator.probes
    }
    assert statuses == {
        _key("freeze-frozen"): "settled",
        _key("freeze-settled"): "settled",
        _key("freeze-released"): "expired_released",
        _key("freeze-missing"): "orphaned",
    }
    # 释放金额取服务端实值，不推算。
    with repository._connect() as connection:
        row = connection.execute(
            "SELECT source, released_points, retained_points FROM "
            "pod_customization_billing_settlements WHERE freeze_id = ?",
            (_key("freeze-released"),),
        ).fetchone()
    assert row["source"] == "ttl"
    assert row["released_points"] == 45
    assert row["retained_points"] == 45


def test_intent_is_replayed_with_the_same_action_key(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    coordinator = FakeReconCoordinator()
    plan = _intent(repository, "intent-replay")

    _reconciler(repository, coordinator).reconcile_actor(ACTOR)

    assert [item.idempotency_key for item in coordinator.freezes] == [plan.idempotency_key]
    stored = repository.find_billing_run_by_action_key(_key("intent-replay"))
    assert stored["status"] == "authorized"
    assert stored["freeze_id"] == _key("intent-replay")
    assert stored["frozen_points"] == 90
    assert stored["unit_ids_json"] == '["style-0","style-1"]'


def test_intent_replay_terminal_conflict_is_probed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    class _TerminalReplay(FakeReconCoordinator):
        def freeze(self, _actor, plan):
            raise PodBillingRemoteError(
                "freeze not active", status_code=409, code="freeze_not_active"
            )

    coordinator = _TerminalReplay()
    coordinator.probes = {
        _key("intent-terminal"): {"status": "released", "released_points": 0}
    }
    _intent(repository, "intent-terminal")

    _reconciler(repository, coordinator).reconcile_actor(ACTOR)

    assert (
        repository.find_billing_run_by_action_key(_key("intent-terminal"))["status"]
        == "expired_released"
    )


def test_multi_device_freezes_are_listed_as_unclaimed_not_auto_settled(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    coordinator = FakeReconCoordinator()
    _authorized(repository, "local-freeze", settle_deadline="2099-01-01T00:00:00Z")
    # 本机有记录，但服务端标记为另一台设备 → 待认领，不自动结算。
    _authorized(repository, "other-device-freeze")
    coordinator.open_freezes_payload = {
        "unavailable": False,
        "freezes": [
            {"freeze_id": _key("local-freeze"), "device_id": DEVICE},
            {"freeze_id": _key("other-device-freeze"), "device_id": "device-b"},
            {"freeze_id": "missing-local-freeze", "device_id": "device-b"},
        ],
    }

    result = _reconciler(repository, coordinator).reconcile_actor(ACTOR)

    reasons = {item["freeze_id"]: item["reason"] for item in result["unclaimed"]}
    assert reasons == {
        _key("other-device-freeze"): "other_device",
        "missing-local-freeze": "local_record_missing",
    }
    # 他机冻结绝不能被自动结算。
    assert coordinator.settlements == []


def test_daily_reconciliation_reports_differences_and_drills_down(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    coordinator = FakeReconCoordinator()
    _authorized(repository, "daily-freeze", settle_deadline=_past())
    repository.prepare_billing_settlement(_key("daily-freeze"))
    repository.insert_settlement(
        freeze_id=_key("daily-freeze"),
        source="settle",
        expected_points=45,
        charged_points=45,
        refunded_points=45,
        rule_version="v7",
        items=[],
    )
    repository.mark_billing_settled(_key("daily-freeze"))
    coordinator.daily = {
        "freeze_count": 2,
        "freeze_points": 90,
        "settle_count": 1,
        "charged_points": 45,
        "refunded_points": 45,
        "close_count": 0,
        "release_count": 0,
        "released_points": 0,
        "retained_points": 0,
        "zero_count": 0,
        "open_freezes": 0,
    }

    result = _reconciler(repository, coordinator).reconcile_daily(ACTOR)

    assert result["local"]["freeze_count"] == 1
    assert result["local"]["settle_count"] == 1
    assert result["differences"]["freeze_count"] == {"local": 1.0, "server": 2.0}
    assert result["detail"] == {"items": []}
    assert result["record"]["scope"] == "daily"
    assert result["record"]["daily_difference"] > 0


def test_transient_probe_errors_never_auto_fix_the_ledger(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    coordinator = FakeReconCoordinator()
    _authorized(repository, "freeze-error")
    coordinator.probes = {
            _key("freeze-error"): PodBillingRemoteError(
                "service unavailable", status_code=503, code="unavailable", retryable=True
            )
        }

    result = _reconciler(repository, coordinator).reconcile_actor(ACTOR)

    assert result["repaired"] == 0
    assert result["notes"]
    stored = repository.find_billing_run_by_action_key(_key("freeze-error"))
    assert stored["status"] == "authorized"
    assert coordinator.settlements == []


def test_deadline_not_reached_keeps_frozen_run_untouched(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    coordinator = FakeReconCoordinator()
    future = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    _authorized(repository, "freeze-future", settle_deadline=future)
    coordinator.probes = {_key("freeze-future"): {"status": "frozen"}}

    result = _reconciler(repository, coordinator).reconcile_actor(ACTOR)

    assert result["repaired"] == 0
    assert (
        repository.find_billing_run_by_action_key(_key("freeze-future"))["status"]
        == "authorized"
    )


def test_recon_runs_are_persisted_for_read_only_lookup(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    coordinator = FakeReconCoordinator()
    _authorized(repository, "freeze-runs", settle_deadline=_past())
    coordinator.probes = {_key("freeze-runs"): {"status": "settled"}}

    _reconciler(repository, coordinator).reconcile_actor(ACTOR, scope="startup")

    rows = repository.list_recon_runs(ACTOR.workspace_id, ACTOR.id)
    assert len(rows) == 1
    assert rows[0]["scope"] == "startup"
    assert rows[0]["checked_count"] == 1
    assert rows[0]["repaired_count"] == 1
