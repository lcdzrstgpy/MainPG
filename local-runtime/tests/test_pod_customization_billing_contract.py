from __future__ import annotations

import pytest

from wh_local.modules.pod_customization.billing_contract import (
    PodCallOutcome,
    PodCallPlan,
    PodExecutionGrant,
)


def test_batch_call_plan_matches_remote_pod_freeze_contract() -> None:
    plan = PodCallPlan.for_batch("batch-1", style_count=2)

    assert plan.freeze_payload(encrypted_session_key="rsa-envelope") == {
        "idempotency_key": "pod:batch:batch-1:initial",
        "title_call_count": 10,
        "image_call_count": 4,
        "calls": [
            {"call_id": "batch-1:style:1:image:1", "feature": "pod.image"},
            {"call_id": "batch-1:style:1:image:2", "feature": "pod.image"},
            {"call_id": "batch-1:style:1:title:1", "feature": "pod.title"},
            {"call_id": "batch-1:style:1:title:2", "feature": "pod.title"},
            {"call_id": "batch-1:style:1:title:3", "feature": "pod.title"},
            {"call_id": "batch-1:style:1:title:4", "feature": "pod.title"},
            {"call_id": "batch-1:style:1:title:5", "feature": "pod.title"},
            {"call_id": "batch-1:style:2:image:1", "feature": "pod.image"},
            {"call_id": "batch-1:style:2:image:2", "feature": "pod.image"},
            {"call_id": "batch-1:style:2:title:1", "feature": "pod.title"},
            {"call_id": "batch-1:style:2:title:2", "feature": "pod.title"},
            {"call_id": "batch-1:style:2:title:3", "feature": "pod.title"},
            {"call_id": "batch-1:style:2:title:4", "feature": "pod.title"},
            {"call_id": "batch-1:style:2:title:5", "feature": "pod.title"},
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
        PodCallOutcome(call_id=plan.calls[5].call_id, feature="pod.title", status="no_return"),
        PodCallOutcome(call_id=plan.calls[6].call_id, feature="pod.title", status="no_return"),
    ]

    assert plan.settlement_payload("freeze-1", outcomes) == {
        "freeze_id": "freeze-1",
        "items": [
            {"call_id": "batch-1:style:1:image:1", "feature": "pod.image", "status": "success"},
            {"call_id": "batch-1:style:1:image:2", "feature": "pod.image", "status": "no_return"},
            {"call_id": "batch-1:style:1:title:1", "feature": "pod.title", "status": "no_return"},
            {"call_id": "batch-1:style:1:title:2", "feature": "pod.title", "status": "success"},
            {"call_id": "batch-1:style:1:title:3", "feature": "pod.title", "status": "no_return"},
            {"call_id": "batch-1:style:1:title:4", "feature": "pod.title", "status": "no_return"},
            {"call_id": "batch-1:style:1:title:5", "feature": "pod.title", "status": "no_return"},
        ],
    }


def test_pod_batch_reuses_product_processing_freeze_contract_per_style() -> None:
    plan = PodCallPlan.for_batch("batch-1", style_count=2)

    assert plan.product_batch_freeze_payload() == {
        "idempotency_key": "pod:batch:batch-1:initial",
        "link_count": 2,
        "scope": ["title", "four_grid"],
        "billing_profile": "pod_random_v1",
    }


def test_semi_freeze_payload_reports_group_and_item_counts() -> None:
    """半定制冻结同时上报组数（link_count）与款数（item_count）。

    远端据此校验 ``item_count == link_count * 4``，兜底「漏除 4、把款数当组数上报」
    的接线错误（那会把冻结积分放大 4 倍）。初始/续跑/整组重试三种计划都必须自洽。
    """
    initial = PodCallPlan.for_semi_batch("semi-1", count=8)
    resume = PodCallPlan.for_semi_batch_resume("semi-1", "resume-1", image_style_indices=(2, 3))
    retry = PodCallPlan.for_semi_style_retry("semi-1:style:1:retry:uuid")

    assert initial.product_batch_freeze_payload() == {
        "idempotency_key": "pod:semi-batch:semi-1:initial",
        "link_count": 2,
        "item_count": 8,
        "scope": ["four_grid"],
        "billing_profile": "pod_semi_v1",
    }
    assert resume.product_batch_freeze_payload() == {
        "idempotency_key": "pod:batch:semi-1:resume:resume-1",
        "link_count": 2,
        "item_count": 8,
        "scope": ["four_grid"],
        "billing_profile": "pod_semi_v1",
    }
    assert retry.product_batch_freeze_payload() == {
        "idempotency_key": "pod:retry:semi-1:style:1:retry:uuid",
        "link_count": 1,
        "item_count": 4,
        "scope": ["four_grid"],
        "billing_profile": "pod_semi_v1",
    }


def test_resume_plan_freezes_only_the_remaining_style_with_original_call_identity() -> None:
    plan = PodCallPlan.for_batch_resume("batch-1", "resume-1", image_style_indices=(2,), title_style_indices=())

    assert plan.product_batch_freeze_payload() == {
        "idempotency_key": "pod:batch:batch-1:resume:resume-1",
        "link_count": 1,
        "scope": ["four_grid"],
        "billing_profile": "pod_random_v1",
    }
    assert [call.call_id for call in plan.calls] == [
        "batch-1:style:2:image:1",
        "batch-1:style:2:image:2",
    ]


def test_pod_attempts_fold_into_product_processing_subitem_results() -> None:
    plan = PodCallPlan.for_batch("batch-1", style_count=2)
    outcomes = [
        PodCallOutcome(call.call_id, call.feature, "success" if index in {1, 3} else "no_return")
        for index, call in enumerate(plan.calls)
    ]

    assert plan.product_batch_settlement_payload(outcomes) == {
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
                    {"feature": "title", "status": "no_return"},
                    {"feature": "four_grid", "status": "no_return"},
                ],
            },
        ]
    }


def test_replica_grid_strategy_matches_the_shared_batch_shape_with_replica_profile() -> None:
    replica = PodCallPlan.for_replica_batch("rb-1", style_count=2, generation_strategy="grid")
    shared = PodCallPlan.for_batch("rb-1", style_count=2)
    assert [call.call_id for call in replica.calls] == [call.call_id for call in shared.calls]
    assert [call.feature for call in replica.calls] == [call.feature for call in shared.calls]
    assert replica.billing_profile == "pod_replica_v1_a"


def test_replica_independent_panels_reserve_four_panels_per_style() -> None:
    plan = PodCallPlan.for_replica_batch(
        "rb-1", style_count=1, generation_strategy="independent_panels"
    )
    image_ids = [call.call_id for call in plan.calls if call.feature == "pod.image"]
    assert image_ids == [
        "rb-1:style:1:image:p1:1",
        "rb-1:style:1:image:p1:2",
        "rb-1:style:1:image:p2:1",
        "rb-1:style:1:image:p2:2",
        "rb-1:style:1:image:p3:1",
        "rb-1:style:1:image:p3:2",
        "rb-1:style:1:image:p4:1",
        "rb-1:style:1:image:p4:2",
    ]
    assert plan.billing_profile == "pod_replica_v1_b"
    # 一款仍是一个 link：4 个面板 × 2 次尝试全部折叠进同一条 subitem。
    assert plan.product_batch_freeze_payload()["link_count"] == 1
    outcomes = tuple(
        PodCallOutcome(call.call_id, call.feature, "success") for call in plan.calls
    )
    payload = plan.product_batch_settlement_payload(outcomes)
    assert payload == {
        "items": [{
            "link_idx": 1,
            "subitems": [
                {"feature": "title", "status": "success"},
                {"feature": "four_grid", "status": "success"},
            ],
        }]
    }


def test_replica_independent_panels_keep_two_styles_as_two_links() -> None:
    plan = PodCallPlan.for_replica_batch(
        "rb-2", style_count=2, generation_strategy="independent_panels"
    )
    assert sum(call.feature == "pod.image" for call in plan.calls) == 16
    assert plan.product_batch_freeze_payload()["link_count"] == 2

    with pytest.raises(ValueError):
        PodCallPlan.for_replica_batch("rb-3", style_count=1, generation_strategy="nope")


def test_replica_panel_retry_and_resume_plans_reuse_the_panel_call_shape() -> None:
    from wh_local.modules.pod_customization.billing_contract import REPLICA_PANEL_COUNT

    style_retry = PodCallPlan.for_replica_style_retry("rb:style:1:retry:x", include_title=False)
    image_ids = [call.call_id for call in style_retry.calls if call.feature == "pod.image"]
    assert len(image_ids) == REPLICA_PANEL_COUNT * 2
    assert all(":style:1:" in call_id and ":image:p" in call_id for call_id in image_ids)

    batch_retry = PodCallPlan.for_replica_batch_retry(
        "rb:batch-retry:y",
        image_style_indices=(1,),
        title_style_indices=(),
        include_title=False,
    )
    assert all(call.feature == "pod.image" for call in batch_retry.calls)
    assert len(batch_retry.calls) == REPLICA_PANEL_COUNT * 2

    resume = PodCallPlan.for_replica_batch_resume(
        "rb", "resume-1", image_style_indices=(2,), title_style_indices=(), include_title=False
    )
    assert all(":style:2:image:p" in call.call_id for call in resume.calls)
    # 款级分组仍把这一款认成一条 link。
    assert resume.product_batch_freeze_payload()["link_count"] == 1
    assert resume.billing_profile == "pod_replica_v1_b"


def test_batch_plan_accepts_200_styles_as_exactly_1400_calls_and_rejects_more() -> None:
    plan = PodCallPlan.for_batch("batch-max-200", style_count=200)

    assert len(plan.calls) == 1400
    assert sum(call.feature == "pod.image" for call in plan.calls) == 400
    assert sum(call.feature == "pod.title" for call in plan.calls) == 1000

    with pytest.raises(ValueError, match="1 and 200"):
        PodCallPlan.for_batch("batch-too-large", style_count=201)


def test_execution_grant_repr_never_exposes_keys_or_remote_token() -> None:
    grant = PodExecutionGrant(
        freeze_id="freeze-1",
        rule_version=7,
        expires_at="2026-12-31T12:00:00Z",
        provider_keys={"ark": "ARK-SECRET", "wuyin": "WUYIN-SECRET"},
        remote_token="REMOTE-SECRET",
    )

    rendered = repr(grant)
    assert "ARK-SECRET" not in rendered
    assert "WUYIN-SECRET" not in rendered
    assert "REMOTE-SECRET" not in rendered
    assert grant.provider_key("ark") == "ARK-SECRET"
