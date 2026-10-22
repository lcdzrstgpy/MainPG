from __future__ import annotations

import pytest

from wh_local.modules.pod_customization.column_generation import (
    COLUMN_ROLES,
    DERIVED_ROLES,
    PRIMARY_ROLE,
    SHEET_STATUS_COMPLETED,
    SHEET_STATUS_FAILED,
    SHEET_STATUS_QUEUED,
    column_group_from_call_id,
    column_image_call_id,
    column_role_from_call_id,
    column_style_from_call_id,
    column_title_call_id,
    group_count_for,
    group_roles_to_freeze,
    group_roles_to_generate,
    group_style_indices,
    plan_style_groups,
    style_group_index,
    style_quadrant_offset,
    validate_style_count,
)


def test_style_count_must_be_a_positive_multiple_of_four() -> None:
    assert validate_style_count(4) == 4
    assert validate_style_count(200) == 200
    for invalid in (0, 2, 5, 201, -4):
        with pytest.raises(ValueError):
            validate_style_count(invalid)


def test_groups_cover_styles_in_quadrant_order() -> None:
    assert group_count_for(8) == 2
    assert group_style_indices(1) == (1, 2, 3, 4)
    assert group_style_indices(2) == (5, 6, 7, 8)
    assert [group.style_indices for group in plan_style_groups(8)] == [
        (1, 2, 3, 4),
        (5, 6, 7, 8),
    ]
    assert style_group_index(1) == 1
    assert style_group_index(4) == 1
    assert style_group_index(5) == 2
    assert [style_quadrant_offset(index) for index in range(1, 6)] == [0, 1, 2, 3, 0]


def test_call_ids_are_positional_and_round_trip() -> None:
    image_id = column_image_call_id("batch", 2, "detail_a", 1)
    assert image_id == "batch:group:2:role:detail_a:image:1"
    assert column_group_from_call_id(image_id) == 2
    assert column_role_from_call_id(image_id) == "detail_a"
    assert column_style_from_call_id(image_id) is None

    title_id = column_title_call_id("batch", 2, 6, 3)
    assert title_id == "batch:group:2:style:6:title:3"
    assert column_group_from_call_id(title_id) == 2
    assert column_style_from_call_id(title_id) == 6
    assert column_role_from_call_id(title_id) == ""

    # 历史 legacy 调用 id 不带 :group:，必须解析为 None，不能误判。
    assert column_group_from_call_id("batch:style:3:image:1") is None


def test_roles_to_generate_follow_the_primary_barrier() -> None:
    assert group_roles_to_generate({}) == (PRIMARY_ROLE,)
    primary_done = {
        PRIMARY_ROLE: SHEET_STATUS_COMPLETED,
        "detail_a": SHEET_STATUS_QUEUED,
        "detail_b": SHEET_STATUS_QUEUED,
        "hero": SHEET_STATUS_QUEUED,
    }
    assert group_roles_to_generate(primary_done) == DERIVED_ROLES
    all_done = {role: SHEET_STATUS_COMPLETED for role in COLUMN_ROLES}
    assert group_roles_to_generate(all_done) == ()
    failed_primary = {role: SHEET_STATUS_FAILED for role in COLUMN_ROLES}
    assert group_roles_to_generate(failed_primary) == ()


def test_roles_to_freeze_include_derived_when_primary_is_pending() -> None:
    # 主图未完成时必须把派生轮一起冻结，否则主图跑完后派生调用不在计划里。
    assert group_roles_to_freeze({}) == COLUMN_ROLES
    assert (
        group_roles_to_freeze(
            {
                PRIMARY_ROLE: SHEET_STATUS_COMPLETED,
                "detail_a": SHEET_STATUS_COMPLETED,
                "detail_b": SHEET_STATUS_QUEUED,
                "hero": SHEET_STATUS_QUEUED,
            }
        )
        == ("detail_b", "hero")
    )
    assert group_roles_to_freeze({role: SHEET_STATUS_FAILED for role in COLUMN_ROLES}) == ()
