"""主图背景档位：scene 档必须真的把「主图纯白」改写成真实背景。

四宫格提示词是多层拼装的，模板矩阵、区域规划、运行时契约、场景计划、预设改写、
收尾闸门每一层都写死了「第 1 格纯白」。这些测试锁住场景档下的定点改写，
防止以后改动模板时又把主图退回白底。
"""

from __future__ import annotations

from wh_local.modules.product_processing.domain.prompts import (
    ARK_GRID_IMAGE_PROMPT,
    ARK_GRID_IMAGE_PROMPT_B,
    ARK_GRID_IMAGE_PROMPT_C,
    ARK_GRID_IMAGE_PROMPT_D,
    GRID_FINAL_GATE,
    GRID_RUNTIME_CONTRACT,
)
from wh_local.modules.product_processing.domain.usage_rooms import (
    detect_room_id,
    resolve_usage_place,
)
from wh_local.modules.product_processing.service import (
    PET_PRODUCT_RULE,
    _apply_background_rules,
    _apply_real_usage_rules,
    _apply_scene_main_background,
    _grid_preset_panel_plan,
    _is_pet_product,
    _resolve_scene_style,
    _scene_family_plan,
)

ARK_TEMPLATES = (
    ARK_GRID_IMAGE_PROMPT,
    ARK_GRID_IMAGE_PROMPT_B,
    ARK_GRID_IMAGE_PROMPT_C,
    ARK_GRID_IMAGE_PROMPT_D,
)


def test_ark_templates_lose_white_main_panel_in_scene_mode() -> None:
    for template in ARK_TEMPLATES:
        assert "纯白" in template  # 前提：模板里确实写死了纯白主图
        rewritten = _apply_scene_main_background(template)
        # 只剩「不得出现纯白无缝棚」这类否定约束，正向的主图纯白指令必须消失。
        assert "平台主图（纯白合规）" not in rewritten
        assert "背景：纯白无缝影棚" not in rewritten
        assert "｜纯白无缝｜" not in rewritten
        assert "真实场景背景" in rewritten


def test_runtime_contract_and_final_gate_lose_white_main_panel() -> None:
    rewritten = _apply_scene_main_background(
        GRID_RUNTIME_CONTRACT + "\n\n" + GRID_FINAL_GATE.format(template_check="x")
    )
    assert "pure white" not in rewritten.lower()
    assert "marketplace hero on one real, category-matched backdrop" in rewritten
    assert "2 Panel 1: one real category-matched backdrop" in rewritten


def test_scene_plan_main_panel_matches_background_mode() -> None:
    scene_plan = _scene_family_plan("lifestyle", 7, template="b", main_background="scene")
    assert "pure white" not in scene_plan.lower()
    assert "Panel 1: real category-matched backdrop" in scene_plan
    assert "Panel 1 has no added props" in scene_plan

    white_plan = _scene_family_plan("lifestyle", 7, template="b", main_background="white")
    assert "Panel 1: pure white seamless studio" in white_plan


def test_preset_panel_plan_main_line_is_rewritten() -> None:
    for preset in ("lifestyle_use", "hero_proof", "macro_craft", "knolling_set"):
        block = _grid_preset_panel_plan(preset)
        assert "- Panel 1 (top-left):" in block
        rewritten = _apply_scene_main_background(block)
        assert "pure white" not in rewritten.lower()
        assert rewritten.count("- Panel 1 (top-left):") == 1


def test_rewrite_is_idempotent() -> None:
    once = _apply_scene_main_background(ARK_GRID_IMAGE_PROMPT_B)
    twice = _apply_scene_main_background(once)
    assert once == twice


def test_detect_room_from_bath_mat() -> None:
    assert detect_room_id("浴室用品 / 地垫", "浴室地垫 防滑") == "bathroom"
    assert detect_room_id("Kitchen", "silicone spatula") == "kitchen"
    # 认不出时不要硬猜
    assert detect_room_id("未知类目", "") == ""


def test_resolve_usage_place_prefers_vision_and_falls_back_to_room() -> None:
    vision = "on the bathroom floor beside the shower"
    assert resolve_usage_place(vision, category_text="", title="") == vision
    fallback = resolve_usage_place("", category_text="浴室", title="浴室地垫")
    assert "bathroom" in fallback


def test_scene_plan_uses_real_place_and_drops_prop_budget() -> None:
    place = "on a real bathroom floor beside the shower, with tiled wall and a folded towel nearby"
    plan = _scene_family_plan(
        "ornate", 3, template="b", main_background="scene", usage_place=place
    )
    assert place in plan
    assert "Panel 1:" in plan and place in plan.split("Panel 2:")[0]
    assert "at least four separate props" not in plan
    assert "three different places" not in plan
    assert "same real place" in plan.lower()
    # 装饰贴纸会破坏真实浴室的可信度，真实场景下应关闭
    assert "Decor: one small kraft tag" not in plan


def test_selected_backdrop_wins_over_real_usage_place() -> None:
    place = "on a real bathroom floor beside the shower"
    velvet = "deep charcoal green velvet backdrop"
    plan = _scene_family_plan(
        "ornate",
        3,
        template="b",
        main_background="scene",
        usage_place=place,
        hero_backdrop=velvet,
    )
    panel1 = plan.split("Panel 2:")[0]
    assert velvet in panel1
    assert place not in panel1
    # 2-3 格仍按真实使用位置
    assert place in plan
    assert "no corner badge" in panel1


def test_pet_products_get_the_no_ghost_garment_rule() -> None:
    assert _is_pet_product("宠物用品 / 狗狗服饰", "冬季加厚宠物背心")
    assert not _is_pet_product("厨房电器", "热狗机")
    assert not _is_pet_product("厨房 / 餐具", "陶瓷碗")

    rule = PET_PRODUCT_RULE
    assert "ghost mannequin" in rule
    assert "WHOLE pet" in rule
    assert "Never render only legs" in rule


def test_resolve_scene_style_is_stable() -> None:
    assert _resolve_scene_style("ornate", 0) == "ornate"
    assert _resolve_scene_style("minimal", 5) == "minimal"
    assert _resolve_scene_style("smart", 3) == _resolve_scene_style("smart", 3)
    assert _resolve_scene_style("smart", 3) in {"minimal", "lifestyle", "ornate"}


def test_ornate_style_actually_changes_the_scene() -> None:
    place = "on a real bathroom floor beside the shower"
    ornate = _scene_family_plan(
        "ornate", 3, template="b", main_background="scene", usage_place=place
    )
    assert "ORNATE" in ornate
    assert "dried-flower" in ornate or "dried flowers" in ornate
    assert "1-3 objects" in ornate

    minimal = _scene_family_plan(
        "minimal", 3, template="b", main_background="scene", usage_place=place
    )
    assert "ORNATE" not in minimal
    assert "flourish" not in minimal.lower()
    assert "at most one simple everyday object" in minimal

    contract = _apply_background_rules(
        GRID_RUNTIME_CONTRACT,
        main_background="scene",
        usage_place=place,
        scene_style="ornate",
    )
    assert "1-3 objects" in contract
    assert "only 1-2 large simple everyday objects" not in contract


def test_scene_plan_forces_camera_variety() -> None:
    place = "on a real bathroom floor beside the shower"
    for style in ("ornate", "lifestyle", "minimal"):
        plan = _scene_family_plan(
            style, 3, template="b", main_background="scene", usage_place=place
        )
        assert "CAMERA VARIETY (hard rule)" in plan
        assert "45-degree three-quarter" in plan
        assert "20-30 degrees below eye level" in plan
        assert "90-degree orthographic" in plan


def test_real_usage_rewrites_contract_and_gate() -> None:
    text = _apply_real_usage_rules(GRID_RUNTIME_CONTRACT + "\n\n" + GRID_FINAL_GATE.format(template_check="x"))
    assert "at least four separate props" not in text
    assert "three DIFFERENT places" not in text
    assert "SAME real place of use" in text
    assert "two different framings" in text


def test_background_rules_combine_scene_and_real_usage() -> None:
    text = _apply_background_rules(
        ARK_GRID_IMAGE_PROMPT_B + "\n\n" + GRID_RUNTIME_CONTRACT,
        main_background="scene",
        usage_place="on a real bathroom floor beside the shower",
    )
    assert "平台主图（纯白合规）" not in text
    assert "≥4 件" not in text
    assert "at least four separate props" not in text
