import sqlite3
from pathlib import Path

import pytest

from wh_local.modules.pod_customization.billing_contract import (
    POD_BILLING_PROFILE_FISSION,
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
)
from wh_local.modules.pod_customization.repository import PodCustomizationRepository
from wh_local.modules.pod_customization.service import PodCustomizationService


@pytest.mark.parametrize("count", [1, 5])
def test_fission_billing_freezes_four_panels_per_style(count):
    plan = PodCallPlan.for_fission_batch("fission-batch", style_count=count)
    payload = plan.product_batch_freeze_payload()
    assert payload["billing_profile"] == "pod_fission_v1"
    assert payload["link_count"] == count
    assert payload["scope"] == ["title", "four_grid"]
    assert len(plan.calls) == count * 13
    image_calls = [call for call in plan.calls if call.feature == "pod.image"]
    assert len(image_calls) == count * 8
    assert all(":image:p" in call.call_id for call in image_calls)
    assert plan.module == "pod_fission"
    assert plan.unit_kind == "style"
    assert list(plan.unit_ids) == [f"style-{index}" for index in range(count)]
    assert PodCustomizationService._pod_billing_profile({"mode": "fission"}) == "pod_fission_v1"


def test_fission_billing_rejects_invalid_count():
    for count in (0, True):
        with pytest.raises(ValueError):
            PodCallPlan.for_fission_batch("fission", style_count=count)


def test_fission_retry_and_resume_keep_panel_shape():
    retry = PodCallPlan.for_fission_style_retry("batch:style:1:retry:x", include_title=True)
    assert sum(call.feature == "pod.image" for call in retry.calls) == 8
    assert retry.billing_profile == POD_BILLING_PROFILE_FISSION
    resume = PodCallPlan.for_fission_batch_resume(
        "batch", "resume-1", image_style_indices=(2,), title_style_indices=(1,), include_title=True
    )
    assert sum(call.feature == "pod.image" for call in resume.calls) == 8
    assert sum(call.feature == "pod.title" for call in resume.calls) == 10
    assert all(":style:2:" in call.call_id for call in resume.calls if call.feature == "pod.image")


def test_local_fission_price_matches_full_customization(tmp_path, monkeypatch):
    from wh_local import billing
    from wh_local.db import init_db, transaction
    from wh_local.session import Actor

    database = tmp_path / "billing.sqlite3"
    init_db(database)
    actor = Actor(id="local-demo-admin", username="admin", role="admin", workspace_id="default")
    with transaction(database) as connection:
        billing._ensure_billing_account(connection, actor)
        billing.grant_points_to_wallet(connection, account_id=actor.id, workspace_id=actor.workspace_id,
            points=1000, source_type="test", source_id="test", idempotency_key="test")
    monkeypatch.setattr(billing.secrets, "randbelow", lambda _: 3)
    full = billing.freeze_batch_points(database, actor, link_count=1, scope=["title", "four_grid"],
        billing_profile="pod_random_v1", idempotency_key="full")
    fission = billing.freeze_batch_points(database, actor, link_count=1, scope=["title", "four_grid"],
        billing_profile="pod_fission_v1", idempotency_key="fission")
    assert fission["frozen_points"] == full["frozen_points"]


def test_fission_settlement_bills_image_success_even_when_title_failed():
    """统一口径：image = success 即扣该款，标题失败不影响扣费。"""
    plan = PodCallPlan.for_fission_batch("fission-batch", style_count=2)
    outcomes = tuple(
        PodCallOutcome(
            call.call_id,
            call.feature,
            "success" if call.feature == "pod.image" else "no_return",
        )
        for call in plan.calls
    )

    payload = plan.product_batch_settlement_payload(outcomes)

    assert payload["items"] == [
        {
            "link_idx": 1,
            "subitems": [
                {"feature": "title", "status": "no_return"},
                {"feature": "four_grid", "status": "success"},
            ],
        },
        {
            "link_idx": 2,
            "subitems": [
                {"feature": "title", "status": "no_return"},
                {"feature": "four_grid", "status": "success"},
            ],
        },
    ]


def test_fission_settlement_refunds_style_when_image_failed():
    plan = PodCallPlan.for_fission_batch("fission-batch", style_count=2)
    outcomes = tuple(
        PodCallOutcome(call.call_id, call.feature, "no_return")
        for call in plan.calls
    )

    payload = plan.product_batch_settlement_payload(outcomes)

    assert [item["link_idx"] for item in payload["items"]] == [1, 2]
    assert payload["items"][0]["subitems"][1] == {"feature": "four_grid", "status": "no_return"}
    assert payload["items"][1]["subitems"][1] == {"feature": "four_grid", "status": "no_return"}


def test_fission_title_completion_never_creates_a_second_freeze(tmp_path: Path):
    """补标题免费：标题结果只是 outcome 变化，不产生新的冻结与新的账本行。"""
    repository = PodCustomizationRepository(tmp_path / "ledger.sqlite3")
    plan = PodCallPlan.for_fission_batch("fission-batch", style_count=2)
    repository.create_billing_run(
        action_key=plan.idempotency_key,
        action_type="batch_initial",
        target_id="fission-batch",
        batch_id="fission-batch",
        actor_id="owner",
        workspace_id="workspace-a",
        plan=plan,
        grant=PodExecutionGrant(plan.idempotency_key, 1, "2099-01-01T00:00:00Z", {"ark": "k"}),
    )
    title_calls = [call for call in plan.calls if call.feature == "pod.title"][:2]
    for call in title_calls:
        repository.record_billing_outcome(
            plan.idempotency_key, PodCallOutcome(call.call_id, call.feature, "success")
        )

    with repository._connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM pod_customization_billing_runs WHERE batch_id = 'fission-batch'"
        ).fetchone()[0]
    stored = repository.find_billing_run_by_action_key(plan.idempotency_key)
    assert count == 1
    assert "fission_billable_styles" not in stored["action_payload"]


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
                     'snapshot', 't', 2, 1, 1, 'v1', '{}', '{}', 'now', 'now')""",
        (batch_id,),
    )
    connection.commit()
    connection.close()


def test_delete_batch_keeps_the_fission_ledger(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite3"
    repository = PodCustomizationRepository(database)
    _insert_terminal_batch(database, "fission-del")
    plan = PodCallPlan.for_fission_batch("fission-del", style_count=2)
    repository.create_billing_run(
        action_key=plan.idempotency_key,
        action_type="batch_initial",
        target_id="fission-del",
        batch_id="fission-del",
        actor_id="owner",
        workspace_id="workspace-a",
        plan=plan,
        grant=PodExecutionGrant(plan.idempotency_key, 1, "2099-01-01T00:00:00Z", {"ark": "k"}),
    )
    repository.terminate_billing_run(plan.idempotency_key, "abandoned", "test")

    assert repository.delete_batch("fission-del", "workspace-a", "owner") == []

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """SELECT status, batch_deleted_at FROM pod_customization_billing_runs
               WHERE action_key = ?""",
            (plan.idempotency_key,),
        ).fetchone()
    assert row is not None
    assert row[0] == "abandoned"
    assert row[1]
