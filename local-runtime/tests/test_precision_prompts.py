from __future__ import annotations

import io

from PIL import Image

from wh_local.modules.pod_customization.column_prompts import (
    build_column_base_prompt,
    build_column_round_prompt,
    build_column_style_recipes,
)
from wh_local.modules.pod_customization.contracts import BusinessFields
from wh_local.modules.product_processing.domain.precision_prompts import (
    PRECISION_DETAIL,
    PRECISION_MIN_CELL_PIXELS,
    PRECISION_STANDARD,
    PRECISION_TEXT,
    SQUARE_CANVAS,
    WIDE_CANVAS,
    assess_precision_budget,
    assert_grid_cell_budget,
    build_precision_panel_blocks,
    build_precision_single_prompt,
    canvas_size_for_precision,
    classify_precision,
    normalize_precision_class,
    precision_class_for_subject,
    resolve_precision_class,
)


def _png(size: tuple[int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, "white").save(output, "PNG")
    return output.getvalue()


def _fields(category: str = "键盘") -> BusinessFields:
    return BusinessFields.model_validate(
        {
            "product_name": "机械键盘",
            "product_category": category,
            "design_theme": "minimal line art",
            "color_preferences": ["奶油白", "雾灰"],
            "style_keywords": ["细线", "几何", "留白"],
        }
    )


def _recipes():
    fields = _fields()
    return build_column_style_recipes(
        fields,
        [{"style_index": index, "elements": {"hero": "细线几何"}} for index in range(1, 5)],
    )


def test_classify_precision_splits_text_and_structure_items() -> None:
    assert classify_precision("麻将牌") == PRECISION_TEXT
    assert classify_precision("机械键盘") == PRECISION_TEXT
    assert classify_precision("拼图") == PRECISION_DETAIL
    assert classify_precision("抱枕") == PRECISION_STANDARD
    assert classify_precision("抱枕", artwork_text="SALE") == PRECISION_STANDARD


def test_normalize_and_resolve_precision_prefer_explicit_fields() -> None:
    assert normalize_precision_class("fine_detail") == PRECISION_DETAIL
    assert normalize_precision_class("text") == PRECISION_TEXT
    assert normalize_precision_class("unknown") == PRECISION_STANDARD

    explicit = {"precision_class": "precision", "business_fields": {"product_category": "抱枕"}}
    assert resolve_precision_class(explicit) == PRECISION_DETAIL

    meta = {"generation_meta": {"precision_class": "precision_text"}}
    assert resolve_precision_class(meta) == PRECISION_TEXT

    fallback = {"business_fields": {"product_category": "麻将牌"}}
    assert resolve_precision_class(fallback) == PRECISION_TEXT


def test_canvas_mapping_only_applies_to_precision_classes() -> None:
    assert canvas_size_for_precision(PRECISION_STANDARD, product_category="机械键盘") == ""
    assert canvas_size_for_precision(PRECISION_TEXT, product_category="机械键盘") == WIDE_CANVAS
    assert canvas_size_for_precision(PRECISION_TEXT, product_category="麻将牌") == SQUARE_CANVAS
    assert canvas_size_for_precision(PRECISION_DETAIL, product_category="拼图") == SQUARE_CANVAS
    # 主体识别给出的外形比例优先于品类关键词。
    assert canvas_size_for_precision(PRECISION_TEXT, product_category="抱枕", shape="wide") == WIDE_CANVAS
    assert canvas_size_for_precision(PRECISION_TEXT, product_category="机械键盘", shape="square") == SQUARE_CANVAS


def test_precision_class_for_subject_trusts_explicit_model_signals() -> None:
    assert (
        precision_class_for_subject(
            {"is_small_or_intricate": True, "has_legible_text": True},
            product_category="抱枕",
        )
        == PRECISION_TEXT
    )
    assert (
        precision_class_for_subject(
            {"is_small_or_intricate": True, "has_legible_text": False},
            product_category="抱枕",
        )
        == PRECISION_DETAIL
    )
    # 模型明确说普通品时，不再拿品类关键词硬触发。
    assert (
        precision_class_for_subject(
            {"is_small_or_intricate": False, "has_legible_text": False},
            product_category="机械键盘",
        )
        == PRECISION_STANDARD
    )
    # 旧回执没有识别信号时，才用品类兜底。
    assert precision_class_for_subject({}, product_category="麻将牌") == PRECISION_TEXT


def test_precision_panel_blocks_keep_panel4_as_orthographic_view() -> None:
    blocks = build_precision_panel_blocks(over_budget=True, flat_product=False)

    assert len(blocks) == 4
    assert "PANEL 2" in blocks[1] and "90%-100%" in blocks[1]
    assert "PANEL 3" in blocks[2] and "90%-100%" in blocks[2]
    assert "PANEL 4" in blocks[3]
    assert "orthographic" in blocks[3]
    assert "12%-18%" in blocks[3]


def test_precision_budget_levels_and_recommendations() -> None:
    over = assess_precision_budget(
        PRECISION_TEXT,
        part_count=120,
        canvas_size=WIDE_CANVAS,
        has_text=True,
        products_per_call=1,
    )
    assert over.level == "over_budget"
    assert over.detail_units == 120
    assert over.recommendation == "one_hero_three_details"

    multi = assess_precision_budget(
        PRECISION_TEXT, part_count=120, canvas_size=WIDE_CANVAS, has_text=True, products_per_call=4
    )
    assert multi.recommendation == "same_pattern_recolor"

    text_only = assess_precision_budget(
        PRECISION_TEXT, part_count=1, canvas_size=SQUARE_CANVAS, has_text=True
    )
    assert text_only.level == "warn"
    assert text_only.recommendation == "local_text_overlay"

    ordinary = assess_precision_budget(
        PRECISION_DETAIL, part_count=4, canvas_size=SQUARE_CANVAS
    )
    assert ordinary.level == "ok"
    assert ordinary.recommendation == "precision_template_only"


def test_single_prompt_keeps_precision_locks() -> None:
    prompt = build_precision_single_prompt(
        facts=["Product: mechanical keyboard."],
        panel_block="PANEL 2 — macro detail of the keycap grid.",
        precision_class=PRECISION_TEXT,
        flat_product=False,
        detail_units=120,
    )

    assert "PRECISION OVERRIDE" in prompt
    assert "CAMERA LOCK" in prompt
    assert "about 120 distinct repeated units" in prompt
    assert "PRECISION TEXT POLICY" in prompt
    assert "never a grid, strip or collage" in prompt


def test_cell_budget_gate_accepts_2k_and_rejects_small_sources() -> None:
    assert assert_grid_cell_budget(_png((2048, 2048)), min_cell_pixels=PRECISION_MIN_CELL_PIXELS) == (
        1024,
        1024,
    )
    assert assert_grid_cell_budget(_png((2880, 1440)), min_cell_pixels=PRECISION_MIN_CELL_PIXELS) == (
        1440,
        720,
    )

    try:
        assert_grid_cell_budget(_png((1254, 1254)), min_cell_pixels=PRECISION_MIN_CELL_PIXELS)
    except ValueError as exc:
        assert "precision grid cell budget too low" in str(exc)
    else:  # pragma: no cover - 失败路径必须抛错
        raise AssertionError("small grid source must fail the precision cell budget gate")


def test_precision_primary_round_replaces_scene_with_camera_and_text_locks() -> None:
    prompt = build_column_round_prompt(
        build_column_base_prompt(_fields(), ""),
        role="lifestyle",
        recipes=_recipes(),
        precision_class=PRECISION_TEXT,
    )

    assert "PRECISION OVERRIDE" in prompt
    assert "PRECISION BLOCK C · CAMERA LOCK" in prompt
    assert "PRECISION BLOCK F · TEXT POLICY" in prompt
    assert "Orthographic front view" in prompt
    assert "Deep depth of field" in prompt
    assert "EQUAL visual weight" in prompt
    assert "at least 10% of the panel edge" in prompt
    assert "divider band" in prompt
    # 通用模板的场景/浅景深段落在精小品模板里不得出现。
    assert "=== PART 4 · ART DIRECTION" not in prompt
    assert "shallow depth of field with a soft background" not in prompt
    assert "shallow focus" not in prompt
    # 四款配方仍然逐格保留。
    for position in ("TOP-LEFT", "TOP-RIGHT", "BOTTOM-LEFT", "BOTTOM-RIGHT"):
        assert f"QUADRANT {position}" in prompt


def test_precision_derived_round_keeps_anchor_scene_but_locks_the_lens() -> None:
    prompt = build_column_round_prompt(
        build_column_base_prompt(_fields(), ""),
        role="detail_a",
        recipes=_recipes(),
        precision_class=PRECISION_TEXT,
    )

    assert "PRECISION LENS LOCK" in prompt
    assert "expected to have shallow focus" not in prompt
    assert "Depth of field must stay deep" in prompt
    assert "focus must stay deep" in prompt
    # 微距轮允许裁进产品，不得再要求"完整产品占 70%-85%"。
    assert "The complete product fills 70%-85%" not in prompt
    assert "PRECISION BLOCK C · CAMERA LOCK" not in prompt


def test_standard_round_prompt_is_unchanged_by_precision_defaults() -> None:
    prompt = build_column_round_prompt(
        build_column_base_prompt(_fields("抱枕"), ""),
        role="lifestyle",
        recipes=_recipes(),
    )

    assert "=== PART 4 · ART DIRECTION" in prompt
    assert "PRECISION OVERRIDE" not in prompt
