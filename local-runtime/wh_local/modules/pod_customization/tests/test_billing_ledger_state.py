from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from wh_local.modules.pod_customization.billing_contract import PodCallPlan
from wh_local.modules.pod_customization.repository import (
    PodCustomizationRepository,
    PodRepositoryError,
)


def _repository(tmp_path: Path) -> PodCustomizationRepository:
    return PodCustomizationRepository(tmp_path / "ledger.sqlite3")


def _intent(repository: PodCustomizationRepository, action_key: str = "pod:batch:b1:initial"):
    plan = PodCallPlan.for_batch("b1", style_count=2)
    return repository.create_billing_intent(
        action_key=action_key,
        action_type="batch_initial",
        target_id="b1",
        batch_id="b1",
        actor_id="owner",
        workspace_id="workspace-a",
        plan=plan,
        module="pod_custom",
        unit_kind="style",
        link_count=2,
        device_id="device-a",
    )


def test_create_intent_writes_planned_outcomes_without_freeze(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    stored = _intent(repository)

    assert stored["status"] == "intent"
    assert stored["freeze_id"] is None
    assert stored["module"] == "pod_custom"
    assert stored["unit_kind"] == "style"
    assert stored["link_count"] == 2
    assert stored["device_id"] == "device-a"
    assert stored["billing_profile"] == stored["plan"]["billing_profile"]
    assert stored["outcomes"]
    assert {outcome["status"] for outcome in stored["outcomes"]} == {"planned"}


def test_attach_freeze_is_a_single_shot_cas(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _intent(repository)

    attached = repository.attach_billing_freeze(
        "pod:batch:b1:initial",
        freeze_id="freeze-1",
        rule_version=7,
        grant_expires_at="2099-01-01T00:00:00Z",
        frozen_points=90.0,
        link_prices=[45.0, 45.0],
        unit_ids=["style-0", "style-1"],
        multiplier_percent=100,
        settle_deadline="2099-01-01T00:10:00Z",
        freeze_raw={"freeze_id": "freeze-1"},
    )
    assert attached is True

    stored = repository.get_billing_run(
        repository.list_pending_billing_runs("workspace-a", "owner")[0]["run_id"],
        "workspace-a",
        "owner",
    )
    assert stored["status"] == "authorized"
    assert stored["freeze_id"] == "freeze-1"
    assert stored["frozen_points"] == 90.0
    assert stored["settle_deadline"] == "2099-01-01T00:10:00Z"
    assert stored["zero_billed"] == 0
    assert stored["plan"]["billing_profile"]

    # 第二次回填必须被 CAS 拒绝，不能覆盖已有快照。
    assert (
        repository.attach_billing_freeze(
            "pod:batch:b1:initial",
            freeze_id="freeze-2",
            frozen_points=1.0,
        )
        is False
    )
    assert (
        repository.get_billing_run(stored["run_id"], "workspace-a", "owner")["freeze_id"]
        == "freeze-1"
    )


def test_zero_points_marks_zero_billed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _intent(repository, action_key="pod:brief:b1")

    repository.attach_billing_freeze(
        "pod:brief:b1", freeze_id="freeze-zero", frozen_points=0
    )

    run_id = repository.list_pending_billing_runs("workspace-a", "owner")[0]["run_id"]
    assert repository.get_billing_run(run_id, "workspace-a", "owner")["zero_billed"] == 1


def test_mark_billing_settled_only_accepts_settlement_states(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _intent(repository)
    assert (
        repository.attach_billing_freeze(
            "pod:batch:b1:initial", freeze_id="freeze-settle", frozen_points=90
        )
        is True
    )

    # authorized 不是可结算前置状态：必须先 prepare 进入 settling。
    assert repository.mark_billing_settled("pod:batch:b1:initial") is False
    repository.prepare_billing_settlement("pod:batch:b1:initial")
    assert repository.mark_billing_settled("pod:batch:b1:initial") is True
    # 并发/重复结算只有第一条生效。
    assert repository.mark_billing_settled("pod:batch:b1:initial") is False


def test_terminate_billing_run_rejects_unknown_state_and_is_idempotent(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _intent(repository)

    with pytest.raises(ValueError):
        repository.terminate_billing_run("pod:batch:b1:initial", "settled")

    assert repository.terminate_billing_run("pod:batch:b1:initial", "orphaned", "404") is True
    assert repository.terminate_billing_run("pod:batch:b1:initial", "orphaned", "404") is False


def test_insert_settlement_is_unique_per_freeze(tmp_path: Path) -> None:
    repository = _repository(tmp_path)

    first = repository.insert_settlement(
        freeze_id="freeze-1",
        source="settle",
        expected_points=45.0,
        charged_points=45.0,
        refunded_points=0.0,
        rule_version="v7",
        items=[
            {
                "link_idx": 0,
                "unit_id": "style-0",
                "feature_status": {"image": "success", "title": "no_return"},
                "charged_points": 45.0,
                "refunded_points": 0.0,
            }
        ],
    )
    second = repository.insert_settlement(
        freeze_id="freeze-1", source="settle", charged_points=999.0
    )

    assert second["settlement_id"] == first["settlement_id"]
    assert second["charged_points"] == 45.0
    with sqlite3.connect(tmp_path / "ledger.sqlite3") as connection:
        items = connection.execute(
            "SELECT link_idx, unit_id FROM pod_customization_billing_settlement_items"
        ).fetchall()
    assert items == [(0, "style-0")]


def test_expire_stale_intents_only_touches_old_rows(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _intent(repository, action_key="pod:batch:old:initial")
    _intent(repository, action_key="pod:batch:new:initial")
    with repository._connect() as connection:
        connection.execute(
            """UPDATE pod_customization_billing_runs
               SET created_at = '2000-01-01T00:00:00+00:00'
               WHERE action_key = 'pod:batch:old:initial'"""
        )

    assert repository.expire_stale_intents(older_than_hours=24) == 1

    runs = {run["action_key"]: run for run in repository.list_pending_billing_runs("workspace-a", "owner")}
    assert set(runs) == {"pod:batch:new:initial"}


def _insert_terminal_batch(database: Path, batch_id: str) -> None:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        """INSERT INTO pod_customization_batches (
               batch_id, workspace_id, owner_user_id, title, status, template_id,
               template_snapshot_id, template_name, requested_count, initial_call_count,
               max_refill_calls, prompt_version, prompt_snapshot, business_fields_json,
               created_at, updated_at
           ) VALUES (?, 'workspace-a', 'owner', 't', 'completed', 'template',
                     'snapshot', 't', 4, 1, 1, 'v1', '{}', '{}', 'now', 'now')""",
        (batch_id,),
    )
    connection.commit()
    connection.close()


def test_delete_batch_rejects_active_run_and_preserves_terminal_ledger(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    database = tmp_path / "ledger.sqlite3"
    _insert_terminal_batch(database, "b1")
    _intent(repository)

    with pytest.raises(PodRepositoryError) as error:
        repository.delete_batch("b1", "workspace-a", "owner")
    assert error.value.status_code == 409
    assert "结算中" in str(error.value)

    repository.terminate_billing_run("pod:batch:b1:initial", "orphaned", "manual")
    assert repository.delete_batch("b1", "workspace-a", "owner") == []

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """SELECT status, batch_deleted_at FROM pod_customization_billing_runs
               WHERE action_key = 'pod:batch:b1:initial'"""
        ).fetchone()
        batch = connection.execute(
            "SELECT 1 FROM pod_customization_batches WHERE batch_id = 'b1'"
        ).fetchone()
    assert row is not None and row[0] == "orphaned" and row[1]
    assert batch is None
