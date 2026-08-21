from __future__ import annotations

from wh_local.modules.pod_customization.billing_contract import (
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
)


def test_batch_call_plan_matches_remote_pod_freeze_contract() -> None:
    plan = PodCallPlan.for_batch("batch-1", style_count=2)

    assert plan.freeze_payload(encrypted_session_key="rsa-envelope") == {
        "idempotency_key": "pod:batch:batch-1:initial",
        "title_call_count": 6,
        "image_call_count": 4,
        "calls": [
            {"call_id": "batch-1:style:1:image:1", "feature": "pod.image"},
            {"call_id": "batch-1:style:1:image:2", "feature": "pod.image"},
            {"call_id": "batch-1:style:1:title:1", "feature": "pod.title"},
            {"call_id": "batch-1:style:1:title:2", "feature": "pod.title"},
            {"call_id": "batch-1:style:1:title:3", "feature": "pod.title"},
            {"call_id": "batch-1:style:2:image:1", "feature": "pod.image"},
            {"call_id": "batch-1:style:2:image:2", "feature": "pod.image"},
            {"call_id": "batch-1:style:2:title:1", "feature": "pod.title"},
            {"call_id": "batch-1:style:2:title:2", "feature": "pod.title"},
            {"call_id": "batch-1:style:2:title:3", "feature": "pod.title"},
        ],
        "encrypted_session_key": "rsa-envelope",
    }


def test_settlement_payload_has_one_outcome_for_every_frozen_call() -> None:
    plan = PodCallPlan.for_batch("batch-1", style_count=1)
    outcomes = [
        PodCallOutcome(call_id=plan.calls[0].call_id, feature="pod.image", status="success"),
        PodCallOutcome(call_id=plan.calls[1].call_id, feature="pod.image", status="no_return"),
        PodCallOutcome(call_id=plan.calls[2].call_id, feature="pod.title", status="no_return"),
        PodCallOutcome(call_id=plan.calls[3].call_id, feature="pod.title", status="success"),
        PodCallOutcome(call_id=plan.calls[4].call_id, feature="pod.title", status="no_return"),
    ]

    assert plan.settlement_payload("freeze-1", outcomes) == {
        "freeze_id": "freeze-1",
        "items": [
            {"call_id": "batch-1:style:1:image:1", "feature": "pod.image", "status": "success"},
            {"call_id": "batch-1:style:1:image:2", "feature": "pod.image", "status": "no_return"},
            {"call_id": "batch-1:style:1:title:1", "feature": "pod.title", "status": "no_return"},
            {"call_id": "batch-1:style:1:title:2", "feature": "pod.title", "status": "success"},
            {"call_id": "batch-1:style:1:title:3", "feature": "pod.title", "status": "no_return"},
        ],
    }


def test_execution_grant_repr_never_exposes_keys_or_remote_token() -> None:
    grant = PodExecutionGrant(
        freeze_id="freeze-1",
        rule_version=7,
        expires_at="2026-08-21T12:00:00Z",
        provider_keys={"ark": "ARK-SECRET", "wuyin": "WUYIN-SECRET"},
        remote_token="REMOTE-SECRET",
    )

    rendered = repr(grant)
    assert "ARK-SECRET" not in rendered
    assert "WUYIN-SECRET" not in rendered
    assert "REMOTE-SECRET" not in rendered
    assert grant.provider_key("ark") == "ARK-SECRET"
