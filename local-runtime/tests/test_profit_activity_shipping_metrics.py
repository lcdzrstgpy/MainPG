from __future__ import annotations

import pytest

from wh_local.modules.profit_activity.domain.shipping_metrics import (
    DEFAULT_VOLUMETRIC_DIVISOR,
    billable_weight,
    parse_shipping_metrics,
    parse_shipping_rows,
    resolve_shipping_metrics,
    to_cm,
    to_kg,
    volumetric_weight,
)


def test_parse_labeled_weight_and_triple_dimensions() -> None:
    metrics = parse_shipping_metrics(["实际重量：1.2kg", "尺寸：30*20*10cm"])

    assert metrics.actual_weight_kg == pytest.approx(1.2)
    assert metrics.length_cm == pytest.approx(30)
    assert metrics.width_cm == pytest.approx(20)
    assert metrics.height_cm == pytest.approx(10)
    # 30*20*10/7000 = 0.857142... < 1.2，计费取实际重量
    assert metrics.volumetric_weight_kg == pytest.approx(0.857142857, rel=1e-6)
    assert metrics.billable_weight_kg == pytest.approx(1.2)


def test_volumetric_weight_wins_when_larger() -> None:
    metrics = parse_shipping_metrics(["重量 0.5kg", "长 50 宽 40 高 30 cm"])

    assert metrics.actual_weight_kg == pytest.approx(0.5)
    # 50*40*30/7000 = 8.571
    assert metrics.volumetric_weight_kg == pytest.approx(8.571428571, rel=1e-6)
    assert metrics.billable_weight_kg == pytest.approx(8.571428571, rel=1e-6)


def test_gram_and_millimeter_units_are_converted() -> None:
    metrics = parse_shipping_metrics(["毛重 500g", "长300 宽200 高100 mm"])

    assert metrics.actual_weight_kg == pytest.approx(0.5)
    assert metrics.length_cm == pytest.approx(30)
    assert metrics.width_cm == pytest.approx(20)
    assert metrics.height_cm == pytest.approx(10)


def test_english_labels_and_inch_units() -> None:
    metrics = parse_shipping_metrics(["Weight: 2 lbs", "L:10 W:8 H:6 inch"])

    assert metrics.actual_weight_kg == pytest.approx(0.90718474)
    assert metrics.length_cm == pytest.approx(25.4)
    assert metrics.width_cm == pytest.approx(20.32)
    assert metrics.height_cm == pytest.approx(15.24)


def test_fullwidth_characters_normalized() -> None:
    metrics = parse_shipping_metrics(["重量：１．２ｋｇ", "尺寸：３０×２０×１０ｃｍ"])

    assert metrics.actual_weight_kg == pytest.approx(1.2)
    assert metrics.length_cm == pytest.approx(30)
    assert metrics.height_cm == pytest.approx(10)


def test_bare_number_with_unit_used_as_fallback() -> None:
    metrics = parse_shipping_metrics(["含包装 0.45kg"])

    assert metrics.actual_weight_kg == pytest.approx(0.45)


def test_missing_dimensions_yield_no_volumetric_weight() -> None:
    metrics = parse_shipping_metrics(["重量 1.5 kg"])

    assert metrics.actual_weight_kg == pytest.approx(1.5)
    assert metrics.volumetric_weight_kg is None
    assert metrics.billable_weight_kg == pytest.approx(1.5)


def test_unparseable_lines_return_empty_metrics() -> None:
    metrics = parse_shipping_metrics(["Temu 物流面单", "发货仓：广州"])

    assert metrics.actual_weight_kg is None
    assert metrics.length_cm is None
    assert metrics.billable_weight_kg is None


def test_use_actual_only_ignores_volumetric_weight() -> None:
    assert billable_weight(0.5, 8.5, use_actual_only=True) == pytest.approx(0.5)
    assert billable_weight(0.5, 8.5, use_actual_only=False) == pytest.approx(8.5)


def test_volumetric_weight_rejects_non_positive_divisor() -> None:
    with pytest.raises(ValueError, match="divisor must be positive"):
        volumetric_weight(30, 20, 10, divisor=0)


def test_unit_conversions() -> None:
    assert to_kg(1, "kg") == pytest.approx(1)
    assert to_kg(1000, "g") == pytest.approx(1)
    assert to_kg(1, None) == pytest.approx(1)
    assert to_cm(1, "m") == pytest.approx(100)
    assert to_cm(10, "mm") == pytest.approx(1)
    assert to_cm(5, None) == pytest.approx(5)


def test_default_divisor_is_seven_thousand() -> None:
    assert DEFAULT_VOLUMETRIC_DIVISOR == 7000.0


# --- 表格型截图（「商品件重尺」，逐格 OCR 合并为行） ---

# 真实截图的合并行：表头「重量（g)」给出重量单位，每行「名称 长 宽 高 体积 重量」。
_TABLE_ROWS = [
    "商品仟重尺",
    "尺寸 长(cm) () ) 高（cm) 体积(cm) 重量（g)",
    "蝴蝶结方形 23 11.50 27 7141.500 314",
    "蝴蝶结椭圆 18.50 12 25 5550 290",
    "珍珠蝴蝶结方形手提篮固定把手 23 11.50 27 7141.500 314",
    "竖编珍珠包活把 12.50 8 19 1900 240",
    "圆形珍珠包活把 20 8 20 3200 260",
    "选择图片 移除",
    "实际重量KG 长cm",
    "如1.2 如30",
    "1688",
    "计费重量 O KG",
]


def test_table_row_uses_volume_cross_check_and_header_unit() -> None:
    rows = parse_shipping_rows(_TABLE_ROWS)

    assert len(rows) == 5
    first = rows[0]
    assert first.label == "蝴蝶结方形"
    assert first.length_cm == pytest.approx(23)
    assert first.width_cm == pytest.approx(11.5)
    assert first.height_cm == pytest.approx(27)
    # 表头「重量（g)」→ 314g = 0.314kg
    assert first.actual_weight_kg == pytest.approx(0.314)
    assert first.volumetric_weight_kg == pytest.approx(23 * 11.5 * 27 / 7000, rel=1e-6)


def test_table_rows_are_listed_as_candidates_in_order() -> None:
    best, candidates = resolve_shipping_metrics(_TABLE_ROWS)

    assert len(candidates) == 5
    assert best.label == "蝴蝶结方形"
    labels = [item.label for item in candidates]
    assert labels == [
        "蝴蝶结方形",
        "蝴蝶结椭圆",
        "珍珠蝴蝶结方形手提篮固定把手",
        "竖编珍珠包活把",
        "圆形珍珠包活把",
    ]
    assert candidates[-1].actual_weight_kg == pytest.approx(0.26)
    assert candidates[-1].length_cm == pytest.approx(20)


def test_table_rows_ignore_ui_noise_rows() -> None:
    # 侧边栏自身 UI 文本（无重量单位、无体积三连）不应产生候选。
    _, candidates = resolve_shipping_metrics(["选择图片 移除", "如1.2 如30", "1688", "计费重量 O KG"])

    assert candidates == []


def test_resolve_falls_back_to_cross_line_aggregate() -> None:
    # 重量与尺寸各占一行时，没有「完整行」，退回跨行聚合。
    best, candidates = resolve_shipping_metrics(["实际重量：1.2kg", "尺寸：30*20*10cm"])

    assert best.actual_weight_kg == pytest.approx(1.2)
    assert best.length_cm == pytest.approx(30)
    assert len(candidates) == 1


# --- 尺寸标注图（无重量，仅带长度单位的边长） ---


def test_dual_unit_annotation_collapses_to_single_length() -> None:
    # 「46cm/18.11in」是同一长度的公制/英制两种写法，应折叠为单一 46cm。
    metrics = parse_shipping_metrics(["尺寸：46cm/18.11in x 20cm/7.87in x 38cm/14.96in"])

    assert metrics.length_cm == pytest.approx(46)
    assert metrics.width_cm == pytest.approx(20)
    assert metrics.height_cm == pytest.approx(38)
    assert metrics.actual_weight_kg is None


# 真实尺寸标注图（手提包 Before/After expansion）的合并行：只有边长，无任何重量信息。
_DIMENSION_ROWS = [
    "Before expansion After expansion",
    "26cm/10.24in 38cm/14.96in",
    "46cm/18.11in 20cm/7.87in 46cm/18.11in 12cm/4.72in 20cm/7.87in",
]


def test_dimension_annotation_lists_variants_without_fabricated_weight() -> None:
    best, candidates = resolve_shipping_metrics(_DIMENSION_ROWS)

    # 图中没有重量信息，不能凭空造出实际重量。
    assert best.actual_weight_kg is None
    # 46 与 20 各出现两次（共用边），26/38 是随配置变化的边 → 两组候选，按抛重降序。
    dims = [(item.length_cm, item.width_cm, item.height_cm) for item in candidates]
    assert dims == [(46, 20, 38), (46, 20, 26)]
    assert best.volumetric_weight_kg == pytest.approx(46 * 20 * 38 / 7000, rel=1e-6)


# --- 尺码列表（一行里并列多组尺码，各组都是候选） ---


def test_size_list_row_yields_one_candidate_per_size() -> None:
    best, candidates = resolve_shipping_metrics(
        ["尺码", "HOT", "19.69*13.78*13.78英寸 23.62*15.75*15.75英寸"]
    )

    # 图中没有重量信息，不应凭空造出实际重量。
    assert best.actual_weight_kg is None
    assert all(item.actual_weight_kg is None for item in candidates)
    dims = [(item.length_cm, item.width_cm, item.height_cm) for item in candidates]
    assert dims == [
        pytest.approx((50.0126, 35.0012, 35.0012)),
        pytest.approx((59.9948, 40.005, 40.005)),
    ]
    assert best.length_cm == pytest.approx(50.0126)
    assert best.volumetric_weight_kg == pytest.approx(50.0126 * 35.0012 * 35.0012 / 7000, rel=1e-6)


# --- 尺码表（每行一个属性、每列一个尺码，按列出候选） ---

# 真实尺码表截图：Size 行为尺码，下面每行一个属性，逐列对齐。
_SIZE_TABLE_ROWS = [
    "Size S M L XL",
    "Volume 1cup/250ml 1.5cup/500ml 3cup/1000ml 4.5cup/1500ml",
    "Caliber 5.2in/13cm 6.2in/15.5cm 7.8in/19cm 9in/22cm",
    "Height 1.8in/4.5cm 2.2in/5cm 2.4in/6cm 2.6in/6cm",
    "Bowl diameter 7.2in/18cm 8.8in/22cm 10.4in/26cm 12in/30cm",
]


def test_size_table_yields_one_candidate_per_column() -> None:
    best, candidates = resolve_shipping_metrics(_SIZE_TABLE_ROWS)

    # 图中没有重量信息，不能凭空造出实际重量。
    assert best.actual_weight_kg is None
    assert all(item.actual_weight_kg is None for item in candidates)
    # 体积单位 ml 不应被当成「米」（否则会出现 150000cm 之类的垃圾值）。
    dims = [(item.length_cm, item.width_cm, item.height_cm) for item in candidates]
    assert dims == [
        (13, 4.5, 18),
        (15.5, 5, 22),
        (19, 6, 26),
        (22, 6, 30),
    ]
    assert best.length_cm == pytest.approx(13)
    assert best.volumetric_weight_kg == pytest.approx(13 * 4.5 * 18 / 7000, rel=1e-3)


# --- 双单位标注的一致性校验（公制/英制换算不一致 → 低置信提示） ---


def test_dual_unit_mismatch_flagged_as_warning() -> None:
    # 「15cm/15.9inch」两个单位自相矛盾（15cm 应为 5.9inch），应给出核对提示。
    best, candidates = resolve_shipping_metrics(["18cm/7.08inch 15cm/15.9inch", "24cm/9.4inch"])

    assert best.warnings == ["15cm/15.9inch 两个单位不一致"]
    assert all(item.warnings for item in candidates)
    # 尺寸仍按公制值取用，只是标注为低置信。
    dims = (best.length_cm, best.width_cm, best.height_cm)
    assert dims == (18, 15, 24)


def test_consistent_dual_unit_annotation_has_no_warning() -> None:
    # 正常取整的双单位标注（46cm/18.11in）不应报错。
    _, candidates = resolve_shipping_metrics(["尺寸：46cm/18.11in x 20cm/7.87in x 38cm/14.96in"])

    assert all(item.warnings == [] for item in candidates)


def test_size_table_dual_units_have_no_warning() -> None:
    # 尺码表里「5.2in/13cm」这类四舍五入属正常，不应触发提示。
    _, candidates = resolve_shipping_metrics(_SIZE_TABLE_ROWS)

    assert all(item.warnings == [] for item in candidates)


