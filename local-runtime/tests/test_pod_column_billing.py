from __future__ import annotations

from wh_local.modules.pod_customization.billing_contract import (
    POD_BILLING_PROFILE_RANDOM_GRID,
    PodCallOutcome,
    PodCallPlan,
)


def _plan(style_count: int = 8) -> PodCallPlan:
    return PodCallPlan.for_column_batch("batch", style_count=style_count)


def test_column_plan_has_four_roles_per_group_and_titles_per_style() -> None:
    plan = _plan(8)

    image_calls = [call for call in plan.calls if call.feature == "pod.image"]
    title_calls = [call for call in plan.calls if call.feature == "pod.title"]
    assert len(image_calls) == 2 * 4 * 2  # 2 组 × 4 角色 × 2 次尝试
    assert len(title_calls) == 8 * 5
    assert plan.column_style_count == 8
    assert plan.column_group_count == 2
    assert plan.billing_profile == POD_BILLING_PROFILE_RANDOM_GRID
    assert "batch:group:2:role:hero:image:2" in {call.call_id for call in image_calls}
    assert "batch:group:2:style:8:title:5" in {call.call_id for call in title_calls}


def test_column_freeze_payload_reports_groups_as_links_and_styles_as_items() -> None:
    payload = _plan(20).product_batch_freeze_payload()

    assert payload["link_count"] == 5
    assert payload["item_count"] == 20
    assert payload["scope"] == ["title", "four_grid"]
    assert payload["billing_profile"] == POD_BILLING_PROFILE_RANDOM_GRID


def test_column_settlement_only_requires_primary_images_for_four_grid() -> None:
    plan = _plan(4)
    outcomes = [PodCallOutcome(call.call_id, call.feature, "no_return") for call in plan.calls]
    # 主图第 2 次尝试成功；细节 A / B / 素材全部失败。
    outcomes = [
        PodCallOutcome(call.call_id, call.feature, "success")
        if call.call_id == "batch:group:1:role:lifestyle:image:2"
        else outcome
        for outcome, call in zip(outcomes, plan.calls, strict=True)
    ]
    # 标题：4 个款各成功一次。
    outcomes = [
        PodCallOutcome(call.call_id, call.feature, "success")
        if call.feature == "pod.title" and call.call_id.endswith(":title:1")
        else outcome
        for outcome, call in zip(outcomes, plan.calls, strict=True)
    ]

    payload = plan.product_batch_settlement_payload(outcomes)

    assert payload["items"] == [
        {
            "link_idx": 1,
            "subitems": [
                {"feature": "title", "status": "success"},
                {"feature": "four_grid", "status": "success"},
            ],
        }
    ]


def test_column_settlement_fails_the_group_when_primary_never_returned() -> None:
    plan = _plan(4)
    outcomes = [
        PodCallOutcome(
            call.call_id,
            call.feature,
            "success" if call.feature == "pod.title" else "no_return",
        )
        for call in plan.calls
    ]

    payload = plan.product_batch_settlement_payload(outcomes)

    subitems = payload["items"][0]["subitems"]
    assert {"feature": "four_grid", "status": "no_return"} in subitems
    assert {"feature": "title", "status": "success"} in subitems


def test_column_resume_plan_keeps_original_call_identity() -> None:
    plan = PodCallPlan.for_column_batch_resume(
        "batch",
        "resume-1",
        group_roles={1: ["detail_b", "hero"], 2: ["lifestyle", "detail_a", "detail_b", "hero"]},
        title_style_indices=(3,),
    )

    call_ids = {call.call_id for call in plan.calls}
    assert "batch:group:1:role:detail_b:image:1" in call_ids
    assert "batch:group:1:role:lifestyle:image:1" not in call_ids
    assert "batch:group:2:role:lifestyle:image:2" in call_ids
    assert "batch:group:1:style:3:title:1" in call_ids
    assert plan.column_group_count == 2
    assert plan.column_style_count == 8
    assert "batch:group:1:role:hero:image:1" in call_ids

    payload = plan.product_batch_freeze_payload()
    assert payload["link_count"] == 2
    assert payload["item_count"] == 8


def test_column_plan_survives_billing_plan_roundtrip() -> None:
    """plan_json 落库→读回必须保留 column_style_count，否则重启后会按「款」结算。"""
    from wh_local.modules.pod_customization.service import PodCustomizationService

    plan = _plan(8)
    stored = {
        "idempotency_key": plan.idempotency_key,
        "calls": [call.payload() for call in plan.calls],
        "column_style_count": plan.column_style_count,
        "billing_profile": plan.billing_profile,
    }

    rebuilt = PodCustomizationService._billing_plan(stored)

    assert rebuilt.column_style_count == 8
    assert rebuilt.product_batch_freeze_payload()["link_count"] == 2
    assert rebuilt.product_batch_freeze_payload()["item_count"] == 8
