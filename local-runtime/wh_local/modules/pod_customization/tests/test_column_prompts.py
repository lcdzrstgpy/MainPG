from __future__ import annotations

from wh_local.modules.pod_customization.column_prompts import (
    build_column_base_prompt,
    build_column_round_prompt,
    build_column_style_recipes,
)
from wh_local.modules.pod_customization.contracts import ArtDirection, BusinessFields


def _fields(**overrides: object) -> BusinessFields:
    values = {
        "product_name": "Botanical tote bag",
        "product_category": "女士手提包",
        "design_theme": "modern botanical",
        "color_preferences": ["鼠尾草绿", "奶油白", "黄铜金", "暖胡桃木棕"],
        "style_keywords": [
            "尤加利叶纹样",
            "蕨类叶脉",
            "橄榄枝图形",
            "浆果串纹样",
            "藤蔓曲线",
            "橡果图形",
            "细叶条纹",
            "花苞图形",
        ],
        "fabric_craft": "绗缝",
    }
    values.update(overrides)
    return BusinessFields.model_validate(values)


def _recipes(*, elements: dict[int, dict[str, object]] | None = None):
    fields = _fields()
    payload = elements or {}
    return build_column_style_recipes(
        fields,
        [
            {"style_index": index, "elements": payload.get(index, {})}
            for index in range(1, 5)
        ],
        seed="batch-seed",
    )


def _composition() -> dict[str, dict[str, str]]:
    return {
        "panel_1": {"zh": "主图", "en": "low 3/4 angle, wide lifestyle framing"},
        "panel_2": {"zh": "细节 A", "en": "tighter macro crop from the same side"},
        "panel_3": {"zh": "细节 B", "en": "another angle from the opposite side"},
        "panel_4": {"zh": "素材", "en": "straight-on front view, clean background"},
    }


def test_round_one_renders_a1_b1_c1_d1() -> None:
    prompt = build_column_round_prompt(
        build_column_base_prompt(_fields(), ""),
        role="lifestyle",
        recipes=_recipes(elements={1: {"hero": "尤加利叶纹样", "narrative": ["蕨类叶脉"], "rhythm": ["橡果图形"]}}),
        composition=_composition(),
        group_index=1,
    )

    assert "=== PART 1 · CORE CONTRACT" in prompt
    assert "=== PART 2 · SCENE & COMPOSITION" in prompt
    assert "=== PART 3 · ELEMENT ROTATION" in prompt
    assert "=== PART 4 · ART DIRECTION" in prompt
    assert "QUADRANT ISOLATION LOCK" in prompt
    assert "SAME CAMERA, DIFFERENT ARTWORK" in prompt
    # 旧的四格共用同一张布局的锁不能出现在一轮四款的提示词里。
    assert "ARTWORK LAYOUT LOCK" not in prompt
    # 构图模板 panel_1 必须原样进入 B1。
    assert "low 3/4 angle, wide lifestyle framing" in prompt
    # 四个象限都要有独立配方，且每款只能用自己的元素。
    for position in ("TOP-LEFT", "TOP-RIGHT", "BOTTOM-LEFT", "BOTTOM-RIGHT"):
        assert f"QUADRANT {position}" in prompt
    assert "尤加利叶纹样" in prompt
    assert "本款配方只作用于本象限" in prompt


def test_style_blocks_carry_area_and_relative_size_contract() -> None:
    """v23：每款配方必须带面积份额与相对尺寸，否则四格只剩零散图案。"""
    prompt = build_column_round_prompt(
        build_column_base_prompt(_fields(), ""),
        role="lifestyle",
        recipes=_recipes(elements={1: {"hero": "尤加利叶纹样", "narrative": ["蕨类叶脉"], "rhythm": ["橡果图形"]}}),
        composition=_composition(),
        group_index=1,
    )
    assert "视觉权重（本款硬要求）" in prompt
    assert "主元素约占可印表面" in prompt
    assert "线性尺寸至少是 Narrative 的 1.8 倍" in prompt
    assert "Rhythm 的 3.6 倍" in prompt
    assert "满印密度（本款硬要求）" in prompt
    assert "可印表面覆盖率 ≥ 90%" in prompt
    assert "weight=" in prompt


def test_derived_rounds_only_render_a2_b2() -> None:
    for role in ("detail_a", "detail_b", "hero"):
        prompt = build_column_round_prompt(
            build_column_base_prompt(_fields(), ""),
            role=role,
            recipes=_recipes(),
            composition=_composition(),
            group_index=1,
        )

        assert "OUTPUT FORMAT LOCK" in prompt
        assert "ANCHOR BINDING" in prompt
        assert "RIGID ARTWORK LOCK" in prompt
        assert "reference image 4 = BOTTOM-RIGHT" in prompt
        assert "PART 3 · ELEMENT ROTATION" not in prompt
        assert "PART 4 · ART DIRECTION" not in prompt
        assert "COLOR IMPACT" not in prompt
        assert "ROUND SHOOTING DIRECTION" in prompt
        # B2 只控制视角：必须显式声明背景与光线跟随锚点。
        assert "must stay identical to the four reference images" in prompt
        # 机位必须与锚点不同，且景深允许/要求按本轮方向变化。
        assert "CAMERA MUST DIFFER" in prompt
        assert "CAMERA REMINDER (last check)" in prompt
        assert "depth of field (a macro round is expected to have shallow focus)" in prompt

    assert "tighter macro crop from the same side" in build_column_round_prompt(
        build_column_base_prompt(_fields(), ""),
        role="detail_a",
        recipes=_recipes(),
        composition=_composition(),
    )
    detail_a_prompt = build_column_round_prompt(
        build_column_base_prompt(_fields(), ""),
        role="detail_a",
        recipes=_recipes(),
        composition=_composition(),
    )
    # 机位只由构图模板决定：不能在模板之外写死景别。
    assert "tighter macro crop from the same side" in detail_a_prompt
    assert "SHOT SCALE" not in detail_a_prompt

    detail_b_prompt = build_column_round_prompt(
        build_column_base_prompt(_fields(), ""),
        role="detail_b",
        recipes=_recipes(),
        composition=_composition(),
    )
    assert "another angle from the opposite side" in detail_b_prompt
    assert "EXTREME MACRO" not in detail_b_prompt

    hero_prompt = build_column_round_prompt(
        build_column_base_prompt(_fields(), ""),
        role="hero",
        recipes=_recipes(),
        composition=_composition(),
    )
    assert "MATERIAL PANEL" in hero_prompt


def test_art_direction_is_injected_into_round_one_only() -> None:
    art_direction = ArtDirection(
        scene="清晨的木质厨房岛台",
        lighting="右后方暖金色侧逆光，3400K，投影朝左前方",
        color_temperature="主体 3400K，窗外 5600K 冷蓝",
        mood="刚起床的安静厨房",
        palette=["暖胡桃木棕", "奶油白", "鼠尾草绿"],
        props=[
            "近景：釉面陶瓷杯，杯口淡金描边",
            "中景：厚切木质砧板，边缘有刀痕",
            "远景：黄铜壶里一束尤加利叶",
        ],
        background="背景压暗，f/2.0 浅景深",
        composition_hint="产品放在右下三分点，左上留出窗光",
        avoid=["冷白棚拍", "纯灰背景"],
    )
    round_one = build_column_round_prompt(
        build_column_base_prompt(_fields(), ""),
        role="lifestyle",
        recipes=_recipes(),
        composition=_composition(),
        art_direction=art_direction,
    )
    assert "ART DIRECTION — batch-wide set design" in round_one
    assert "右后方暖金色侧逆光" in round_one
    assert "近景：釉面陶瓷杯" in round_one
    assert "AVOID: 冷白棚拍, 纯灰背景" in round_one

    round_two = build_column_round_prompt(
        build_column_base_prompt(_fields(), ""),
        role="detail_a",
        recipes=_recipes(),
        composition=_composition(),
        art_direction=art_direction,
    )
    assert "右后方暖金色侧逆光" not in round_two


def test_prompts_are_deterministic_and_attempt_two_adds_retry_note() -> None:
    args = dict(role="lifestyle", recipes=_recipes(), composition=_composition())
    first = build_column_round_prompt(build_column_base_prompt(_fields(), ""), **args)
    second = build_column_round_prompt(build_column_base_prompt(_fields(), ""), **args)
    assert first == second

    retry = build_column_round_prompt(
        build_column_base_prompt(_fields(), ""), attempt=2, **args
    )
    assert "RETRY ATTEMPT 2 OF 2" in retry


def test_flat_products_force_top_down_rounds() -> None:
    fields = _fields(product_name="桌布", product_category="桌布")
    recipes = build_column_style_recipes(
        fields,
        [{"style_index": index, "elements": {}} for index in range(1, 5)],
        seed="seed",
    )
    round_one = build_column_round_prompt(
        build_column_base_prompt(fields, "80cm×80cm 无下挂"),
        role="lifestyle",
        recipes=recipes,
        composition=_composition(),
        flat_product=True,
    )
    assert "FLAT PRODUCT COMPOSITION OVERRIDE" in round_one
    assert "TOP-DOWN flat lays" in round_one

    round_two = build_column_round_prompt(
        build_column_base_prompt(fields, "80cm×80cm 无下挂"),
        role="hero",
        recipes=recipes,
        composition=_composition(),
        flat_product=True,
    )
    assert "TOP-DOWN flat lays" in round_two


def test_base_prompt_carries_structure_locks_only_when_available() -> None:
    plain = build_column_base_prompt(_fields(), "")
    assert "STRUCTURE FACTS" not in plain
    assert "GEOMETRY LOCK" in plain
    assert "ALL-OVER PRINT LOCK" in plain

    structured = build_column_base_prompt(
        _fields(),
        "",
        structure_facts={
            "part_count": 2,
            "layer_count": 1,
        },
        reference_view_roles=("primary", "side"),
    )
    assert "STRUCTURE" in structured
    assert "REFERENCE" in structured


def test_prompt_length_stays_within_budget() -> None:
    """第 1 轮要同时写 4 套配方，必须给长度预算，避免契约被稀释。"""
    base = build_column_base_prompt(_fields(), "")
    round_one = build_column_round_prompt(base, role="lifestyle", recipes=_recipes())
    round_two = build_column_round_prompt(base, role="detail_b", recipes=_recipes())

    assert len(round_one) < 16_000
    assert len(round_two) < 6_500
    # 派生轮不重复底座：它靠锚点与 A2 硬约束，不应再把 4 套配方复述一遍。
    assert round_two.count("STYLE-0") == 0
