from __future__ import annotations

import pytest

from wh_local.modules.profit_activity.infrastructure.shipping_ocr import (
    _map_to_base,
    _rotate_hint_threshold,
    _rotated_min_score,
    _rotation_angles,
    _upscale_factor,
    dimension_hint_count,
    merge_ocr_variants,
    needs_rotation,
)


def test_upscale_factor_default_and_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WH_PROFIT_OCR_UPSCALE", raising=False)
    assert _upscale_factor() == pytest.approx(1.6)

    monkeypatch.setenv("WH_PROFIT_OCR_UPSCALE", "0.5")
    assert _upscale_factor() == pytest.approx(1.0)  # 关闭多尺度

    monkeypatch.setenv("WH_PROFIT_OCR_UPSCALE", "99")
    assert _upscale_factor() == pytest.approx(3.0)  # 上限保护

    monkeypatch.setenv("WH_PROFIT_OCR_UPSCALE", "abc")
    assert _upscale_factor() == pytest.approx(1.6)  # 非法值回退默认


def test_rotation_angles_default_and_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WH_PROFIT_OCR_ROTATE", raising=False)
    assert _rotation_angles() == (90, 270)

    monkeypatch.setenv("WH_PROFIT_OCR_ROTATE", "")
    assert _rotation_angles() == ()  # 显式关闭多方向

    monkeypatch.setenv("WH_PROFIT_OCR_ROTATE", "45,90")
    assert _rotation_angles() == (90,)  # 非 90 倍数忽略

    monkeypatch.setenv("WH_PROFIT_OCR_ROTATE", "270,90,270")
    assert _rotation_angles() == (270, 90)  # 去重且保序


def test_rotated_min_score_default_and_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WH_PROFIT_OCR_ROTATE_MIN_SCORE", raising=False)
    assert _rotated_min_score() == pytest.approx(0.7)

    monkeypatch.setenv("WH_PROFIT_OCR_ROTATE_MIN_SCORE", "0.2")
    assert _rotated_min_score() == pytest.approx(0.5)  # 下限保护

    monkeypatch.setenv("WH_PROFIT_OCR_ROTATE_MIN_SCORE", "2")
    assert _rotated_min_score() == pytest.approx(0.99)  # 上限保护

    monkeypatch.setenv("WH_PROFIT_OCR_ROTATE_MIN_SCORE", "abc")
    assert _rotated_min_score() == pytest.approx(0.7)  # 非法值回退默认


def test_map_to_base_scales_only() -> None:
    # 未旋转时仅按缩放还原。
    assert _map_to_base(60.0, 100.0, 20.0, 2.0, 0, 0, 0) == (30.0, 50.0, 10.0)


def test_map_to_base_rotation_90() -> None:
    # 基准图 100x50；转 90° 后宽高互换，逆变换应把点还原回基准图。
    assert _map_to_base(30.0, 10.0, 5.0, 1.0, 90, 100, 50) == (90.0, 30.0, 5.0)


def test_map_to_base_rotation_180() -> None:
    assert _map_to_base(70.0, 10.0, 5.0, 1.0, 180, 100, 50) == (30.0, 40.0, 5.0)


def test_map_to_base_rotation_270() -> None:
    # 90° 与 270° 应是互为逆的映射：同一基准点 (90,30) 映射后归一。
    assert _map_to_base(20.0, 90.0, 5.0, 1.0, 270, 100, 50) == (90.0, 30.0, 5.0)


def test_merge_restores_coordinates_to_base_scale() -> None:
    # 放大 2 倍那一遍的中心坐标应除以 2 还原到基准图。
    merged = merge_ocr_variants([(2.0, 0, [(100.0, 60.0, 20.0, "15.9inch", 0.9)])])

    assert merged == [(50.0, 30.0, 10.0, "15.9inch")]


def test_merge_maps_rotated_variant_back_to_base() -> None:
    # 基准图 100x50 中位置 (90,30) 的竖排文字，转 270° 后落在 (x=20, y=90)；
    # 合并时应映射回基准图位置 (90,30)，而非留在旋转空间。
    merged = merge_ocr_variants(
        [(1.0, 270, [(90.0, 20.0, 20.0, "15cm", 0.9)])],
        base_size=(100, 50),
    )

    assert merged == [(30.0, 90.0, 20.0, "15cm")]


def test_merge_dedupes_same_text_at_same_position() -> None:
    # 同一文本、位置几乎重合的多来源重复检测只保留一条（取置信度更高的定位）。
    merged = merge_ocr_variants(
        [
            (1.0, 0, [(100.0, 50.0, 20.0, "18cm/7.08inch", 0.6)]),
            (2.0, 0, [(202.0, 102.0, 42.0, "18cm/7.08inch", 0.95)]),
        ]
    )

    assert merged == [(101.0, 51.0, 21.0, "18cm/7.08inch")]


def test_merge_keeps_repeated_text_at_different_positions() -> None:
    # 尺寸图里重复出现的共用边（同文本、不同位置）必须各自保留。
    merged = merge_ocr_variants(
        [(1.0, 0, [(100.0, 40.0, 20.0, "46cm", 0.9), (100.0, 400.0, 20.0, "46cm", 0.9)])]
    )

    assert merged == [(100.0, 40.0, 20.0, "46cm"), (100.0, 400.0, 20.0, "46cm")]


def test_merge_recovers_text_seen_by_only_one_scale() -> None:
    # 小字只在放大那一遍被识别出来，也应保留（这正是多尺度的目的）。
    merged = merge_ocr_variants(
        [
            (1.0, 0, [(100.0, 50.0, 20.0, "24cm", 0.9)]),
            (1.6, 0, [(160.0, 400.0, 32.0, "15.9inch", 0.8)]),
        ]
    )

    assert merged == [(100.0, 50.0, 20.0, "24cm"), (100.0, 250.0, 20.0, "15.9inch")]


def test_merge_sorts_by_reading_order_and_drops_blank() -> None:
    merged = merge_ocr_variants(
        [
            (
                1.0,
                0,
                [
                    (200.0, 80.0, 20.0, "第二行", 0.9),
                    (100.0, 90.0, 20.0, "第一行", 0.9),
                    (100.0, 10.0, 20.0, "   ", 0.9),
                ],
            )
        ]
    )

    assert merged == [(100.0, 90.0, 20.0, "第一行"), (200.0, 80.0, 20.0, "第二行")]


def test_rotate_hint_threshold_default_and_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WH_PROFIT_OCR_ROTATE_HINT", raising=False)
    assert _rotate_hint_threshold() == 2

    monkeypatch.setenv("WH_PROFIT_OCR_ROTATE_HINT", "-1")
    assert _rotate_hint_threshold() == 0  # 下限保护

    monkeypatch.setenv("WH_PROFIT_OCR_ROTATE_HINT", "abc")
    assert _rotate_hint_threshold() == 2  # 非法值回退默认


def test_dimension_hint_count_matches_unit_annotated_numbers() -> None:
    variants = [
        (1.0, 0, [(10.0, 10.0, 20.0, "18cm/7.08inch", 0.9)]),
        (1.6, 0, [(20.0, 20.0, 20.0, "2.5kg", 0.9), (30.0, 30.0, 20.0, "商品件重尺", 0.9)]),
    ]

    # 只有「数字+单位」计数：「商品件重尺」不含数字，不计入。
    assert dimension_hint_count(variants) == 2


def test_needs_rotation_depends_on_hint_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    sparse = [(1.0, 0, [(10.0, 10.0, 20.0, "alanacc", 0.9)])]
    rich = [(1.0, 0, [(10.0, 10.0, 20.0, "18cm", 0.9), (20.0, 20.0, 20.0, "9inch", 0.9)])]

    monkeypatch.delenv("WH_PROFIT_OCR_ROTATE_HINT", raising=False)
    assert needs_rotation(sparse) is True   # 没有尺寸信息 → 值得试旋转变体
    assert needs_rotation(rich) is False    # 已有两条尺寸 → 跳过，控制耗时

    monkeypatch.setenv("WH_PROFIT_OCR_ROTATE_HINT", "0")
    assert needs_rotation(rich) is False    # 阈值 0 → 永不触发额外旋转
