import pytest

from wh_local.modules.pod_customization.billing_contract import PodCallPlan
from wh_local.modules.pod_customization.service import PodCustomizationService


@pytest.mark.parametrize("count", [1, 10])
def test_fission_billing_uses_independent_profile_and_shared_style_price(count):
    plan = PodCallPlan.for_fission_batch("fission-batch", style_count=count)
    payload = plan.product_batch_freeze_payload()
    assert payload["billing_profile"] == "pod_fission_v1"
    assert payload["link_count"] == count
    assert payload["scope"] == ["title", "four_grid"]
    assert len(plan.calls) == count * 7
    assert PodCustomizationService._pod_billing_profile({"mode": "fission"}) == "pod_fission_v1"


def test_fission_billing_rejects_invalid_count():
    for count in (0, 11, True):
        with pytest.raises(ValueError):
            PodCallPlan.for_fission_batch("fission", style_count=count)


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


def _mark_deliverable(repository, batch_id):
    """Supply persisted commercialization results without calling any provider."""
    with repository._connect() as connection:
        rows = connection.execute(
            "SELECT result_id, style_index, variant_index FROM pod_customization_style_grid_results "
            "WHERE batch_id=? ORDER BY style_index, variant_index", (batch_id,)
        ).fetchall()
        assert len(rows) == 8
        for row in rows:
            connection.execute(
                "UPDATE pod_customization_style_grid_results SET status='completed' WHERE result_id=?",
                (row["result_id"],),
            )
            connection.execute(
                "INSERT INTO pod_customization_style_grid_publications (result_id, role, public_url, updated_at) "
                "VALUES (?, ?, ?, 'now') ON CONFLICT(result_id) DO UPDATE SET public_url=excluded.public_url",
                (row["result_id"], f"panel_{row['variant_index']}", f"https://cdn/{row['result_id']}"),
            )
        connection.execute(
            "UPDATE pod_customization_style_titles SET status='completed' WHERE batch_id=?", (batch_id,)
        )
        for style_index in (1, 2):
            connection.execute(
                "INSERT INTO pod_customization_style_copy "
                "(batch_id, style_index, title, english_title, description, created_at, updated_at) "
                "VALUES (?, ?, '商品标题', 'Bag listing', 'Cotton bag description', 'now', 'now') "
                "ON CONFLICT(batch_id, style_index) DO UPDATE SET title=excluded.title, "
                "english_title=excluded.english_title, description=excluded.description",
                (batch_id, style_index),
            )


def _provider_successes(repository, action_key):
    # Provider audit data must survive the separate commercial-delivery policy.
    with repository._connect() as connection:
        connection.execute(
            "UPDATE pod_customization_billing_outcomes SET status='success' WHERE run_id=("
            "SELECT run_id FROM pod_customization_billing_runs WHERE action_key=?)", (action_key,)
        )
    return repository.prepare_billing_settlement(action_key)


@pytest.mark.parametrize("missing", ["grid", "publication", "title", "copy", "description"])
def test_fission_settlement_requires_complete_delivery_and_freezes_refund(tmp_path, missing):
    from wh_local.modules.pod_customization.billing_contract import PodExecutionGrant
    from wh_local.modules.pod_customization.tests.test_fission_api import request, seed_analysis
    from wh_local.modules.pod_customization.tests.test_replica_worker import (
        BillingCoordinator, CapturingReplicaRuntime, _actor, _service,
    )

    runtime, billing = CapturingReplicaRuntime(), BillingCoordinator()
    service = _service(tmp_path, runtime, billing=billing)
    try:
        seed_analysis(service)
        batch = service.create_fission_batch(_actor(), request(), enqueue=False)
        repository, batch_id = service.repository, batch["id"]
        plan = billing.freezes[0]
        _mark_deliverable(repository, batch_id)
        with repository._connect() as connection:
            if missing == "grid":
                connection.execute("UPDATE pod_customization_style_grid_results SET status='failed' "
                                   "WHERE batch_id=? AND style_index=2 AND variant_index=4", (batch_id,))
            elif missing == "publication":
                connection.execute("DELETE FROM pod_customization_style_grid_publications WHERE result_id IN "
                                   "(SELECT result_id FROM pod_customization_style_grid_results "
                                   "WHERE batch_id=? AND style_index=2 AND variant_index=4)", (batch_id,))
            elif missing == "title":
                connection.execute("UPDATE pod_customization_style_titles SET status='failed' "
                                   "WHERE batch_id=? AND style_index=2", (batch_id,))
            elif missing == "copy":
                connection.execute("DELETE FROM pod_customization_style_copy WHERE batch_id=? AND style_index=2",
                                   (batch_id,))
            else:
                connection.execute("UPDATE pod_customization_style_copy SET description='  ' "
                                   "WHERE batch_id=? AND style_index=2", (batch_id,))
        outcomes = _provider_successes(repository, plan.idempotency_key)
        projected = repository.project_fission_settlement(plan.idempotency_key, outcomes)
        billable = [row for row in projected if row.feature == "pod.image" and row.status == "success"]
        assert len(billable) == 2
        assert all(":style:1:" in row.call_id for row in billable)
        assert all(row.status == "success" for row in projected if row.feature == "pod.title")

        # A later successful retry cannot increase the original action's charge.
        _mark_deliverable(repository, batch_id)
        assert repository.project_fission_settlement(plan.idempotency_key, outcomes) == projected
        with repository._connect() as connection:
            run_id = connection.execute(
                "SELECT run_id FROM pod_customization_billing_runs WHERE action_key=?", (plan.idempotency_key,)
            ).fetchone()[0]
        stored = repository.get_billing_run(run_id, _actor().workspace_id, _actor().id)
        assert stored["action_payload"]["fission_billable_styles"] == [1]
        assert all(row["status"] == "success" for row in stored["outcomes"])

        # Direct style retry IDs omit ':style:N:'; the durable run target identifies the style.
        retry = PodCallPlan.for_style_retry("retry-delivered-style", include_title=True,
                                            billing_profile="pod_fission_v1")
        repository.create_billing_run(
            action_key=retry.idempotency_key, action_type="style_retry", target_id="2", batch_id=batch_id,
            actor_id=_actor().id, workspace_id=_actor().workspace_id, plan=retry,
            grant=PodExecutionGrant("retry-freeze", 1, "", {}),
        )
        retry_outcomes = _provider_successes(repository, retry.idempotency_key)
        retry_projected = repository.project_fission_settlement(retry.idempotency_key, retry_outcomes)
        assert sum(row.status == "success" for row in retry_projected if row.feature == "pod.image") == 2
    finally:
        service.close()
        runtime.close()
