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


def test_style_prompt_declares_assigned_elements_as_printed_motifs() -> None:
    assignment = assign_style_elements(COWBOY_KEYWORDS, 1, "batch-a")
    prompt = _style_prompt({"design_theme": "牛仔荒野风"}, 1, style_elements=assignment)
    assert (
        f"本款印花主元素：{assignment['hero']}"
        "（Tier 1 主锚，印在产品表面的平面图案，不是独立实物；尺度 1.0）"
    ) in prompt
    assert "印花叙事层 Narrative：" in prompt
    assert "印花点缀层 Rhythm：" in prompt
    assert "禁止生成独立实物、摆件、餐具、食品、家具或场景道具" in prompt
    assert "清单中未分配给本款的元素在本款中禁止出现。" in prompt


def test_style_prompt_accepts_legacy_element_assignment_shape() -> None:
    """历史批次落库的是旧形状 {primary, co, accents}，必须仍可渲染（向后兼容）。"""
    prompt = _style_prompt(
        {"design_theme": "牛仔荒野风"},
        1,
        style_elements={"primary": "猫", "co": "咖啡", "accents": ["樱花"]},
    )
    assert "本款印花主元素：猫（Tier 1 主锚，印在产品表面的平面图案，不是独立实物；尺度 1.0）" in prompt
    assert "印花叙事层 Narrative：咖啡" in prompt
    assert "印花点缀层 Rhythm：樱花" in prompt


def test_style_prompt_instructs_invention_without_assignment() -> None:
    prompt = _style_prompt({"design_theme": "牛仔荒野风"}, 2, style_elements={})
    assert "本款未分配印花元素：必须在 Batch-wide theme & style 的主题内自创一个新图案元素" in prompt


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
    # PART 3 元素是印花，不是实物道具；道具配色仍取本款色板。
    assert "The PART 3 elements are PRINTED motifs" in prompt
    assert "鎏金黄铜色" in prompt
    # 素材图必须保持纯净中性背景（不能因为加了道具而污染）。
    assert "material panel (bottom-right) must stay a clean neutral ecommerce background" in prompt


def test_set_design_applies_when_style_has_no_assigned_elements() -> None:
    prompt = _style_prompt({"color_preferences": "米白、墨绿"}, style_index=2)

    assert "SET DESIGN" in prompt
    assert "The PART 3 elements are PRINTED motifs" in prompt


def test_style_prompt_asks_for_grouped_element_pairing() -> None:
    """POD 是满印：图案要无缝铺满可印表面，不能只在局部放一两个图案。"""

    prompt = _style_prompt(
        {},
        style_index=1,
        style_elements={"primary": "猫", "co": "咖啡", "accents": ["樱花", "蝴蝶"]},
    )

    assert "满印硬要求：这是 all-over print，不是局部印花" in prompt
    assert "无缝、连续地铺满产品所有可印表面" in prompt
    assert "不能只在局部放 1~2 个图案" in prompt


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


def test_operator_hard_rules_are_promoted_to_the_top_of_part_1() -> None:
    """创意编辑（含手写硬性规则）必须是 PART 1 的批级硬条款，不能垫底成 Creative direction。"""

    fields = BusinessFields(product_name="天然橡胶正方形防水桌布", product_category="家居餐垫布艺装饰")
    creative = "硬性规则：\n9. 桌布尺寸为80cm×80cm全覆盖桌面，无下挂。"

    prompt = build_direct_listing_prompt(fields, creative)

    assert "BATCH-SPECIFIC HARD RULES" in prompt
    assert prompt.index("BATCH-SPECIFIC HARD RULES") < prompt.index("Treat the supplied template")
    assert "Creative direction:" not in prompt
    assert "80cm×80cm" in prompt


def test_material_realism_does_not_assume_sewn_fabric() -> None:
    """橡胶/PVC 等光滑材质不能被内置条款强行套成绗缝布料。"""

    prompt = build_direct_listing_prompt(BusinessFields(product_name="天然橡胶桌布"), "")

    assert "MATERIAL REALISM" in prompt
    assert "Do NOT assume this is a sewn fabric product" in prompt
    assert "TEXTILE REALISM (sewn fabric products only" in prompt
    assert "matte rubber sheet stays smooth, flat and matte" in prompt
    assert "Never add fabric weave, quilting, stitching" in prompt


def test_flat_product_form_and_size_are_locked() -> None:
    """平面软品（桌布/垫子）不得被生成立体方块，写明的尺寸与覆盖方式必须照做。"""

    prompt = build_direct_listing_prompt(BusinessFields(product_name="正方形桌布"), "")

    assert "PRODUCT FORM & SIZE LOCK" in prompt
    assert "never as a box, cube, slab, stack, cushion, tray, molded shell or other 3D block" in prompt
    assert "a square 80x80 tablecloth must stay square" in prompt
    assert "no overhang" in prompt
    assert "no rounded bulge" in prompt


def test_flat_product_forces_top_down_panels() -> None:
    fields = BusinessFields(product_name="天然橡胶正方形防水桌布", product_category="家用防水桌布")

    prompt = build_direct_listing_prompt(
        fields, "材质为天然橡胶，表面光滑。桌布尺寸为80cmx80cm全覆盖桌面，无下挂。"
    )

    assert "Panel 1 — PRIMARY IMAGE (top-left): straight top-down flat lay" in prompt
    assert "FLAT PRODUCT COMPOSITION OVERRIDE" in prompt
    assert "no table edge, no chairs, no drape" in prompt
    # 禁止模型在四格角上写 A/B/C/D 或 1/2/3/4。
    assert "NO QUADRANT LABELS" in prompt
    # 平铺产品不再采用用户构图里的 eye-level 机位。
    assert "user direction" not in prompt


def test_flat_product_uses_flat_lay_set_design() -> None:
    fields = BusinessFields(product_name="天然橡胶正方形防水桌布", product_category="家用防水桌布")
    base = build_direct_listing_prompt(fields, "平铺，无下挂")

    prompt = build_style_listing_prompt(
        base,
        style_index=1,
        attempt=1,
        style_elements={"hero": "雪花纹样"},
    )

    assert "FLAT-LAY SET DESIGN" in prompt
    assert "No room, no table edge, no chairs, no furniture, no hanging cloth" in prompt
    assert "supporting props from the same subject family as the artwork" not in prompt


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


def _structure_facts() -> dict:
    return {
        "part_count": 4,
        "tiers": 4,
        "units_per_tier": 14,
        "height_mm": 31,
        "thickness_mm": 10.2,
        "material": "clear acrylic",
        "transparency": "high",
        "surface_finish": "glossy",
        "confidence": {"tiers": 0.9, "units_per_tier": 0.9, "material": 0.9},
    }


def test_direct_listing_prompt_injects_structure_anchor_blocks() -> None:
    prompt = build_direct_listing_prompt(
        BusinessFields(product_name="Acrylic display stand"),
        "",
        structure_facts=_structure_facts(),
        reference_view_roles=("primary", "side", "top"),
    )
    assert "STRUCTURE FACTS" in prompt
    assert "exactly 4 vertical tiers" in prompt
    assert "TRANSPARENT MATERIAL LOCK" in prompt
    assert "REFERENCE VIEWS CONTRACT" in prompt
    assert "Image 2 = SIDE elevation" in prompt


def test_direct_listing_prompt_without_structure_anchor_stays_unchanged() -> None:
    prompt = build_direct_listing_prompt(BusinessFields(product_name="托特包"), "")
    assert "STRUCTURE FACTS" not in prompt
    assert "PART COUNT & SIZE LOCK" not in prompt
    assert "TRANSPARENT MATERIAL LOCK" not in prompt
    assert "REFERENCE VIEWS CONTRACT" not in prompt


def test_direct_listing_prompt_skips_transparent_lock_for_opaque_material() -> None:
    facts = {**_structure_facts(), "material": "canvas", "transparency": "none"}
    prompt = build_direct_listing_prompt(
        BusinessFields(product_name="帆布托特包"), "", structure_facts=facts
    )
    assert "STRUCTURE FACTS" in prompt
    assert "TRANSPARENT MATERIAL LOCK" not in prompt


def test_style_prompt_renders_batch_scene_mood_when_provided() -> None:
    prompt = _style_prompt({"scene_mood": "暖光木质厨房"}, style_index=1)
    assert "BATCH SCENE MOOD (this batch): 暖光木质厨房." in prompt
    # 款间背景不同是硬约束，不能被基调破坏。
    assert "The scene must change from style to style inside this batch" in prompt


def test_style_prompt_omits_scene_mood_when_blank() -> None:
    prompt = _style_prompt({"design_theme": "牛仔荒野风"}, style_index=1)
    assert "BATCH SCENE MOOD" not in prompt


def test_style_prompt_keeps_art_direction_lighting_and_glow() -> None:
    """美术指导必须带主导性光线，否则成图又平又素（构图模板链路不进生图提示词）。"""
    prompt = _style_prompt({"design_theme": "牛仔荒野风"}, style_index=1)
    assert "LIGHTING — one clear warm directional key light" in prompt
    assert "soft contact shadow" in prompt
    assert "GLOW" in prompt
    # 细节格沿用同一场景与光向，不能掉回灰底棚拍。
    assert "Panels 1-3 are ONE shoot" in prompt
    # 场景要有 3-5 个同题材道具，但不能把印花纹样做成 3D 实物。
    assert "3-5 supporting props" in prompt
    assert "never a 3D copy of a printed motif" in prompt


def test_style_prompt_makes_density_a_hard_rule() -> None:
    """density 不能只写在 signature 里：必须成为覆盖率 + 重复次数的硬指令。"""
    prompt = _style_prompt(
        {"design_theme": "牛仔荒野风"},
        1,
        style_elements=assign_style_elements(COWBOY_KEYWORDS, 1, "batch-a"),
    )
    assert "图案密度（本款硬要求" in prompt
    assert "可印表面覆盖率 100%" in prompt
    assert "满印填充纹样" in prompt
    # PART 1 的批级满印锁（在 base prompt 里），避免四格或不同款退回局部印花。
    assert "ALL-OVER PRINT LOCK" in build_direct_listing_prompt(
        BusinessFields(product_name="绗缝化妆包"), ""
    )
    # 密度池里不再出现 sparse / oversized 这类会把画面做空的档位。
    for index in range(1, 11):
        style_prompt = _style_prompt(
            {"design_theme": "牛仔荒野风"},
            index,
            style_elements=assign_style_elements(COWBOY_KEYWORDS, index, "batch-a"),
        )
        assert "density=very sparse" not in style_prompt
        assert "density=sparse" not in style_prompt
        assert "one oversized motif" not in style_prompt


def test_style_prompt_renders_color_harmony_tiers_with_user_colors() -> None:
    prompt = _style_prompt(
        {"color_preferences": ["暖陶土红", "绿松石蓝", "暖金", "雾灰"]},
        style_index=1,
        style_elements=assign_style_elements(COWBOY_KEYWORDS, 1, "batch-a"),
    )
    assert "color_harmony=" in prompt
    assert "base≈" in prompt and "(60%)" in prompt and "(30%)" in prompt and "(10%)" in prompt


def test_prompt_locks_artwork_layout_across_the_four_panels() -> None:
    """四格共用主图确定的图案布局：只换机位/景别，不得把主元素挪位或改相对尺度。"""

    base = build_direct_listing_prompt(BusinessFields(product_name="托特包"), "")
    prompt = build_style_listing_prompt(
        base, style_index=1, attempt=1, style_elements={"hero": "树"}
    )

    assert "ARTWORK LAYOUT LOCK" in base
    assert "Panel 1 (top-left) defines the printed layout ONCE" in base
    assert "near a side edge, at the top, in a corner" in base
    # 默认面板描述：细节格是同一布局的放大；换视角时图案随商品刚性旋转。
    assert "literal zoom into Panel 1's layout" in base
    assert "rotates rigidly with the product" in base
    # PART 3 再钉一次，避免长提示词里被稀释。
    assert "四格图案布局锁定" in prompt
    assert "禁止把主元素挪到侧边、顶部" in prompt


def test_flat_product_panels_keep_the_same_artwork_layout() -> None:
    """平铺产品的四格也必须共用同一张图案布局，只允许俯拍裁切。"""

    base = build_direct_listing_prompt(BusinessFields(product_name="正方形桌布"), "平铺，无下挂")

    assert "same artwork layout shown in" in base
    assert "never a re-arranged or re-scaled version of the artwork" in base
    assert "using the same layout as Panel 1" in base
