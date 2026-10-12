from __future__ import annotations

from wh_local.modules.pod_customization.contracts import BusinessFields
from wh_local.modules.pod_customization.prompts import (
    assign_style_elements,
    build_direct_listing_prompt,
    build_style_listing_prompt,
    split_element_keywords,
)

# Deliberately theme-neutral base prompt.
BASE = "Create one square 2x2 contact sheet. Product name: laundry basket."
COWBOY_KEYWORDS = ["复古牛仔靴插画", "沙漠仙人掌", "绿松石配饰", "马蹄铁与野花", "牛仔帽"]


def _style_prompt(
    brief: dict,
    style_index: int,
    *,
    attempt: int = 1,
    style_elements: dict | None = None,
) -> str:
    return build_style_listing_prompt(
        BASE,
        style_index=style_index,
        attempt=attempt,
        business_fields=brief,
        style_elements=style_elements,
    )


def _elements_line(prompt: str) -> str:
    line = next(
        line for line in prompt.splitlines() if line.startswith("Style creative signature:")
    )
    return line.split("| elements=", 1)[1].split("|", 1)[0].strip()


def test_split_element_keywords_uses_chinese_punctuation_and_newlines() -> None:
    assert split_element_keywords("复古牛仔靴插画、沙漠仙人掌、绿松石配饰") == [
        "复古牛仔靴插画",
        "沙漠仙人掌",
        "绿松石配饰",
    ]
    assert split_element_keywords("A、B，C;D；E\nF") == ["A", "B", "C", "D", "E", "F"]


def test_split_element_keywords_does_not_split_on_spaces() -> None:
    # 空格不是分隔符：英文复词保持整词。
    assert "Seamless Pattern" in split_element_keywords("牛仔靴、Seamless Pattern、马蹄铁")


def test_split_element_keywords_dedupes_and_keeps_order() -> None:
    assert split_element_keywords("牛仔靴、仙人掌、牛仔靴、野花") == ["牛仔靴", "仙人掌", "野花"]


def test_assignment_is_reproducible_for_same_seed() -> None:
    assert assign_style_elements(COWBOY_KEYWORDS, 3, "batch-a") == assign_style_elements(
        COWBOY_KEYWORDS, 3, "batch-a"
    )


def test_adjacent_styles_never_share_hero() -> None:
    for style_index in range(1, 12):
        current = assign_style_elements(COWBOY_KEYWORDS, style_index, "batch-a")["hero"]
        following = assign_style_elements(COWBOY_KEYWORDS, style_index + 1, "batch-a")["hero"]
        assert current != following


def test_style_assignment_is_tiered_without_internal_duplicates() -> None:
    for style_index in range(1, 40):
        assignment = assign_style_elements(COWBOY_KEYWORDS, style_index, "batch-a")
        tokens = [assignment["hero"], *assignment["narrative"], *assignment["rhythm"]]
        # Tier：1 Hero + ≤2 Narrative + ≤4 Rhythm（受池子大小限制）。
        assert len(assignment["narrative"]) <= 2
        assert len(assignment["rhythm"]) <= 4
        assert len(tokens) == len(set(tokens))


def test_style_assignment_fills_two_narrative_tiers_when_pool_is_large() -> None:
    pool = [f"e{i}" for i in range(10)]
    for style_index in range(1, 9):
        assert len(assign_style_elements(pool, style_index, "batch-a")["narrative"]) == 2


def test_different_seeds_produce_different_distributions() -> None:
    sequence_a = [
        assign_style_elements(COWBOY_KEYWORDS, index, "batch-a")["hero"]
        for index in range(1, 9)
    ]
    sequence_b = [
        assign_style_elements(COWBOY_KEYWORDS, index, "batch-b")["hero"]
        for index in range(1, 9)
    ]
    assert sequence_a != sequence_b


def test_assignment_fallbacks_for_singleton_and_empty() -> None:
    singleton = assign_style_elements(["唯一的元素"], 1, "x")
    assert singleton == {"hero": "唯一的元素", "narrative": [], "rhythm": []}
    empty = assign_style_elements([], 1, "x")
    assert empty == {"hero": "", "narrative": [], "rhythm": []}


def test_direct_listing_prompt_never_renders_element_keywords() -> None:
    fields = BusinessFields(
        product_name="绗缝手提托特包",
        design_theme="美式西南复古牛仔荒野风",
        style_keywords=["复古牛仔靴插画", "沙漠仙人掌"],
    )
    prompt = build_direct_listing_prompt(fields, "")
    assert "Style keywords" not in prompt
    assert "复古牛仔靴插画" not in prompt
    assert "沙漠仙人掌" not in prompt
    assert "Batch-wide theme & style: 美式西南复古牛仔荒野风." in prompt


def test_direct_listing_prompt_keeps_interior_unprinted_rule() -> None:
    fields = BusinessFields(product_name="收纳筐", design_theme="美式乡村")
    prompt = build_direct_listing_prompt(fields, "")
    assert "keep the interior surface unprinted" in prompt


def test_style_prompt_declares_assigned_elements_as_subject() -> None:
    assignment = assign_style_elements(COWBOY_KEYWORDS, 1, "batch-a")
    prompt = _style_prompt({"design_theme": "牛仔荒野风"}, 1, style_elements=assignment)
    assert f"本款素材主语：{assignment['hero']}（Tier 1 Hero 主锚，视觉主角，尺度 1.0）" in prompt
    assert "叙事层 Narrative：" in prompt
    assert "节奏层 Rhythm：" in prompt
    assert "清单中未分配给本款的元素在本款中禁止出现。" in prompt


def test_style_prompt_accepts_legacy_element_assignment_shape() -> None:
    """历史批次落库的是旧形状 {primary, co, accents}，必须仍可渲染（向后兼容）。"""
    prompt = _style_prompt(
        {"design_theme": "牛仔荒野风"},
        1,
        style_elements={"primary": "猫", "co": "咖啡", "accents": ["樱花"]},
    )
    assert "本款素材主语：猫（Tier 1 Hero 主锚，视觉主角，尺度 1.0）" in prompt
    assert "叙事层 Narrative：咖啡" in prompt
    assert "节奏层 Rhythm：樱花" in prompt


def test_style_prompt_instructs_invention_without_assignment() -> None:
    prompt = _style_prompt({"design_theme": "牛仔荒野风"}, 2, style_elements={})
    assert "本款未分配素材：必须在 Batch-wide theme & style 的主题内自创一个新元素" in prompt


def test_style_prompt_keeps_theme_and_rendering_contracts() -> None:
    prompt = _style_prompt(
        {"design_theme": "牛仔荒野风", "color_preferences": ["暖陶土红", "绿松石蓝", "暖金"]},
        3,
        style_elements=assign_style_elements(COWBOY_KEYWORDS, 3, "batch-a"),
    )
    assert "主题整批统一风格必须保持，优先级高于本款配方建议" in prompt
    assert "整幅图案（含所有元素与底色）必须采用本款渲染工艺" in prompt
    assert "color_harmony=" in prompt
    assert "本款配色 60-30-10" in prompt
    assert "禁止输出“把所有元素等权复制粘贴”" in prompt


def test_style_prompt_has_no_color_tiers_without_user_colors() -> None:
    prompt = _style_prompt(
        {"design_theme": "牛仔荒野风"},
        3,
        style_elements=assign_style_elements(COWBOY_KEYWORDS, 3, "batch-a"),
    )
    assert "color_harmony=" not in prompt
    assert "本款配色 60-30-10" not in prompt


def test_style_prompt_attempt_two_adds_retry_direction() -> None:
    prompt = _style_prompt(
        {"design_theme": "牛仔荒野风"},
        1,
        attempt=2,
        style_elements=assign_style_elements(COWBOY_KEYWORDS, 1, "batch-a"),
    )
    assert "RETRY ATTEMPT 2 OF 2:" in prompt


def test_style_prompt_is_deterministic() -> None:
    assignment = assign_style_elements(COWBOY_KEYWORDS, 7, "batch-a")
    first = _style_prompt({"design_theme": "牛仔荒野风"}, 7, style_elements=assignment)
    second = _style_prompt({"design_theme": "牛仔荒野风"}, 7, style_elements=assignment)
    assert first == second


def test_style_prompt_includes_parameterized_set_design() -> None:
    """背景不能是空白棚拍：必须带上按本款元素/配色参数化的场景美术指导。"""

    prompt = _style_prompt(
        {"color_preferences": "暗夜靛蓝、鎏金黄铜色"},
        style_index=1,
        style_elements={"primary": "猫", "co": "咖啡", "accents": ["樱花"]},
    )

    assert "SET DESIGN" in prompt
    # 参数化：场景美感必须显式绑定「本款元素」，道具配色取本款色板。
    assert "this style's PART 3 elements (猫, 咖啡)" in prompt
    assert "鎏金黄铜色" in prompt
    # 素材图必须保持纯净中性背景（不能因为加了道具而污染）。
    assert "material panel (bottom-right) must stay a clean neutral ecommerce background" in prompt


def test_set_design_applies_when_style_has_no_assigned_elements() -> None:
    prompt = _style_prompt({"color_preferences": "米白、墨绿"}, style_index=2)

    assert "SET DESIGN" in prompt
    assert "this style's own subject" in prompt


def test_style_prompt_asks_for_grouped_element_pairing() -> None:
    """元素不能均匀散点平铺，必须成组、有主次（用户反馈"搭配不好看"）。"""

    prompt = _style_prompt(
        {},
        style_index=1,
        style_elements={"primary": "猫", "co": "咖啡", "accents": ["樱花", "蝴蝶"]},
    )

    assert "元素搭配要有主次与联系" in prompt
    assert "不要均匀散点平铺" in prompt


def test_prompt_is_organized_into_four_parts() -> None:
    """提示词按产品侧指定的四段式组织，且顺序固定。"""

    base = build_direct_listing_prompt(BusinessFields(product_name="Tote bag"), "")
    prompt = build_style_listing_prompt(
        base,
        style_index=1,
        attempt=1,
        business_fields={"design_theme": "牛仔荒野风", "color_preferences": "米白、墨绿"},
        style_elements={"primary": "马蹄铁", "co": "野花", "accents": ["仙人掌"]},
    )

    for header in (
        "PART 1 · CORE CONTRACT",
        "PART 2 · PANEL ROLES",
        "PART 3 · ELEMENT ROTATION",
        "PART 4 · ART DIRECTION",
    ):
        assert header in prompt
    assert (
        prompt.index("PART 1 ·")
        < prompt.index("PART 2 ·")
        < prompt.index("PART 3 ·")
        < prompt.index("PART 4 ·")
    )


def test_set_design_scene_rotates_across_styles() -> None:
    """同批次各款必须换场景，否则整批背景雷同（实测踩过）。"""

    p1 = _style_prompt({"color_preferences": "圣诞正红、米白暖调"}, style_index=1)
    p2 = _style_prompt({"color_preferences": "圣诞正红、米白暖调"}, style_index=2)

    def scene(prompt: str) -> str:
        return next(line for line in prompt.splitlines() if line.startswith("- Stage this ONE lived-in scene"))

    assert scene(p1) != scene(p2)


def test_style_assignment_can_use_up_to_four_rhythm() -> None:
    """池子足够大时，Rhythm 组最多 4 个（1 Hero + 2 Narrative + 4 Rhythm = 7 个元素/款）。"""

    pool = [f"e{i}" for i in range(10)]
    sizes = {
        len(assign_style_elements(pool, index, "batch-a")["rhythm"])
        for index in range(1, 9)
    }
    assert max(sizes) == 4


def test_direct_listing_prompt_renders_fabric_craft_when_provided() -> None:
    prompt = build_direct_listing_prompt(
        BusinessFields(product_name="绗缝托特包", fabric_craft="绗缝"), ""
    )
    assert "FABRIC CRAFT (this batch): 绗缝." in prompt
    # 工艺词只「优先使用」，CARVE-OUT（模板真实工艺）仍最高优先。
    assert "Prefer these craft terms" in prompt
    assert "CARVE-OUT above" in prompt


def test_direct_listing_prompt_omits_fabric_craft_when_blank() -> None:
    prompt = build_direct_listing_prompt(BusinessFields(product_name="托特包"), "")
    assert "FABRIC CRAFT (this batch)" not in prompt


def test_direct_listing_prompt_carries_the_static_60_30_10_rule() -> None:
    prompt = build_direct_listing_prompt(BusinessFields(product_name="托特包"), "")
    assert "60-30-10 COLOR HARMONY" in prompt
    assert "DOMINANT BASE" in prompt and "STRUCTURAL ACCENT" in prompt and "POP HIGHLIGHT" in prompt


def test_style_prompt_renders_batch_scene_mood_when_provided() -> None:
    prompt = _style_prompt({"scene_mood": "暖光木质厨房"}, style_index=1)
    assert "BATCH SCENE MOOD (this batch): 暖光木质厨房." in prompt
    # 款间背景不同是硬约束，不能被基调破坏。
    assert "The scene must change from style to style inside this batch" in prompt


def test_style_prompt_omits_scene_mood_when_blank() -> None:
    prompt = _style_prompt({"design_theme": "牛仔荒野风"}, style_index=1)
    assert "BATCH SCENE MOOD" not in prompt


def test_style_prompt_downgrades_lighting_and_keeps_glow() -> None:
    """光影归构图模版：大提示词不再写主导性光线，只保留 GLOW 作氛围。"""
    prompt = _style_prompt({"design_theme": "牛仔荒野风"}, style_index=1)
    assert "warm directional light" not in prompt
    assert "Lighting: warm" not in prompt
    assert "GLOW" in prompt


def test_style_prompt_renders_color_harmony_tiers_with_user_colors() -> None:
    prompt = _style_prompt(
        {"color_preferences": ["暖陶土红", "绿松石蓝", "暖金", "雾灰"]},
        style_index=1,
        style_elements=assign_style_elements(COWBOY_KEYWORDS, 1, "batch-a"),
    )
    assert "color_harmony=" in prompt
    assert "base≈" in prompt and "(60%)" in prompt and "(30%)" in prompt and "(10%)" in prompt
