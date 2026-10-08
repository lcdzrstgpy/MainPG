from __future__ import annotations

import pytest

from wh_local.modules.profit_activity.domain.shipping_metrics import (
    DEFAULT_VOLUMETRIC_DIVISOR,
    billable_weight,
    parse_shipping_metrics,
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
