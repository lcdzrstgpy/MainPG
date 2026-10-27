"""复刻派生三格提示词：单帧输出、锚点锁、视角来自计划、attempt 2 追加重试。"""

from __future__ import annotations

import pytest

from wh_local.modules.pod_customization.replica_panel_template import (
    DEFAULT_DERIVED_PANEL_DIRECTIONS,
    DERIVED_PANEL_INDEXES,
    build_replica_panel_prompt,
)
from wh_local.modules.pod_customization.replica_specs import ReplicaPlan


def _plan(derived: dict[str, str] | None = None) -> ReplicaPlan:
    return ReplicaPlan(
        main_prompt="Main image prompt body.",
        prompt_version="pod-replica-main-prompt-v1",
        image_model_version="model-x",
        reference_order="B,A",
        composition_template_id="comp-1",
        composition_template_version="v9",
        derived_panels=derived or {},
    )


def test_panels_two_to_four_use_their_own_direction() -> None:
    directions = {
        "panel_2": "TIGHT PATTERN CLOSE-UP direction",
        "panel_3": "THREE-QUARTER structural detail direction",
        "panel_4": "CLEAN NEUTRAL material shot direction",
    }
    plan = _plan(directions)

    for panel_index in DERIVED_PANEL_INDEXES:
        prompt = build_replica_panel_prompt(plan, panel_index)
        assert directions[f"panel_{panel_index}"] in prompt
        # 其余两格的视角不得串入本格。
        for other, text in directions.items():
            if other != f"panel_{panel_index}":
                assert text not in prompt


def test_missing_directions_fall_back_to_defaults_and_stay_distinct() -> None:
    plan = _plan({})

    prompts = {index: build_replica_panel_prompt(plan, index) for index in DERIVED_PANEL_INDEXES}

    for index in DERIVED_PANEL_INDEXES:
        assert DEFAULT_DERIVED_PANEL_DIRECTIONS[f"panel_{index}"] in prompts[index]
    assert len(set(prompts.values())) == 3


def test_prompt_carries_anchor_locks_and_single_frame_output() -> None:
    prompt = build_replica_panel_prompt(_plan(), 2)

    assert "SINGLE ANCHOR BINDING" in prompt
    assert "RIGID ARTWORK LOCK" in prompt
    assert "ANCHOR CONTINUITY" in prompt
    assert "GEOMETRY LOCK" in prompt
    assert "CAMERA MUST DIFFER" in prompt
    assert "SINGLE FRAME OUTPUT" in prompt
    assert "2x2 contact sheet" in prompt  # 明确禁止，而不是要求
    assert "Create one square 2x2" not in prompt


def test_material_panel_switches_only_the_background() -> None:
    prompt = build_replica_panel_prompt(_plan(), 4)

    assert "MATERIAL PANEL" in prompt
    assert "ONLY the background" in prompt


def test_attempt_two_appends_retry_instruction() -> None:
    first = build_replica_panel_prompt(_plan(), 3, attempt=1)
    retry = build_replica_panel_prompt(_plan(), 3, attempt=2)

    assert "RETRY ATTEMPT 2 OF 2" not in first
    assert "RETRY ATTEMPT 2 OF 2" in retry
    assert retry.startswith(first)


@pytest.mark.parametrize("panel_index", [1, 0, 5])
def test_only_derived_panel_indexes_are_accepted(panel_index: int) -> None:
    with pytest.raises(ValueError):
        build_replica_panel_prompt(_plan(), panel_index)
