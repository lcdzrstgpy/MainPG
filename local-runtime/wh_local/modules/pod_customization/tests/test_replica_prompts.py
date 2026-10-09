from __future__ import annotations

from wh_local.modules.pod_customization.contracts import BusinessFields
from wh_local.modules.pod_customization.prompts import (
    build_direct_listing_prompt,
    build_style_listing_prompt,
)
from wh_local.modules.pod_customization.replica_prompts import build_replica_listing_prompt

# 原创花色配方的特征词：复刻提示词绝不能出现这些原创设计语言。
ORIGINAL_RECIPE_TOKENS = (
    "STYLE-SPECIFIC DIVERSITY",
    "palette",
    "density",
    "rendering",
    "invent",
    "visibly different from every other style",
    "accent_colors",
    "自创",
)


def _fields(**overrides) -> BusinessFields:
    defaults = dict(
        product_name="收纳篮",
        product_category="storage basket",
        target_market="US",
    )
    defaults.update(overrides)
    return BusinessFields(**defaults)


def test_replica_prompt_declares_dual_reference_roles_and_target_only() -> None:
    prompt = build_replica_listing_prompt(_fields(), attempt=1)

    assert "Reference image 1 provides ONLY the surface pattern to replicate" in prompt
    assert "Reference image 2 provides ONLY the target product" in prompt
    assert "must contain ONLY the target product" in prompt
    assert "Faithfully replicate the pattern from reference image 1" in prompt


def test_replica_prompt_reuses_four_panel_positions_and_camera_requirements() -> None:
    prompt = build_replica_listing_prompt(_fields(), attempt=1)

    assert "Panel 1 — PRIMARY IMAGE (top-left)" in prompt
    assert "Panel 2 — DETAIL IMAGE A (top-right)" in prompt
    assert "Panel 3 — DETAIL IMAGE B (bottom-left)" in prompt
    assert "Panel 4 — MATERIAL IMAGE (bottom-right)" in prompt
    assert "Panel 1 must be a wide lifestyle scene" in prompt


def test_replica_prompt_declares_same_product_same_pattern_not_recolorways() -> None:
    prompt = build_replica_listing_prompt(_fields(), attempt=1)

    assert "same target product wearing the same replicated pattern" in prompt
    assert "NOT four different products" in prompt
    assert "NOT four different patterns" in prompt


def test_replica_prompt_allows_pattern_text_but_forbids_new_annotations() -> None:
    prompt = build_replica_listing_prompt(_fields(), attempt=1)

    assert "Text that is already part of the pattern itself may stay" in prompt
    assert "Do not add any new text, labels, logos, watermarks" in prompt
    assert "Keep the four-panel divider clean and centered" in prompt


def test_replica_prompt_contains_no_original_recipe() -> None:
    prompt = build_replica_listing_prompt(_fields(), attempt=1)

    for token in ORIGINAL_RECIPE_TOKENS:
        assert token not in prompt


def test_replica_prompt_attempt_two_only_strengthens_consistency() -> None:
    attempt_one = build_replica_listing_prompt(_fields(), attempt=1)
    attempt_two = build_replica_listing_prompt(_fields(), attempt=2)

    assert "RETRY ATTEMPT 2 OF 2:" in attempt_two
    assert "Strengthen only the consistency between the replicated pattern and the target product" in attempt_two
    # attempt 2 只强化一致性，不能加入原创花色配方。
    for token in ORIGINAL_RECIPE_TOKENS:
        assert token not in attempt_two
    # attempt 1 不应带重试段落。
    assert "RETRY ATTEMPT 2 OF 2:" not in attempt_one


def test_replica_prompt_rejects_invalid_attempt() -> None:
    try:
        build_replica_listing_prompt(_fields(), attempt=3)
    except ValueError:
        pass
    else:
        raise AssertionError("attempt 必须为 1 或 2")


def test_full_and_semi_prompts_do_not_leak_replica_content() -> None:
    fields = _fields()
    base = build_direct_listing_prompt(fields, "")
    style = build_style_listing_prompt(base, style_index=1, attempt=1)

    for prompt in (base, style):
        assert "Reference image 1 provides ONLY the surface pattern to replicate" not in prompt
        assert "Faithfully replicate the pattern from reference image 1" not in prompt
        assert "Reference image 2 provides ONLY the target product" not in prompt