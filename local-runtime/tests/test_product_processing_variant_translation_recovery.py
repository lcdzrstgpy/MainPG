from __future__ import annotations

import json
import re

from wh_local.modules.product_processing.doubao_text import (
    DoubaoTextError,
    DoubaoTextResult,
)
from wh_local.modules.product_processing.service import ProductProcessingService


def _requested_values(prompt: str) -> list[str]:
    match = re.search(r"Requested raw values: (\[.*\])\Z", prompt)
    assert match is not None
    value = json.loads(match.group(1))
    assert isinstance(value, list)
    return [str(item) for item in value]


def test_missing_variant_translations_are_retried_in_batches(monkeypatch) -> None:
    service = object.__new__(ProductProcessingService)
    calls: list[list[str]] = []

    class _PartialClient:
        last_attempt_count = 1

        def generate_listing_text(self, prompt: str) -> DoubaoTextResult:
            requested = _requested_values(prompt)
            calls.append(requested)
            returned = requested[:-1] if len(requested) > 1 else requested
            return DoubaoTextResult(
                optimized_title="",
                description="",
                variant_translations=tuple(
                    (value, f"Translated {value}") for value in returned
                ),
                product_dimensions={},
            )

    monkeypatch.setattr(service, "_doubao_text_client", _PartialClient)
    # 用纯 ASCII 值：export_value 需为目标语言（不含中文），否则会被语言校验
    # 视为“漏译”剔除。真实场景里 AI 不会返回含中文的 export_value。
    values = [f"opt-{i}" for i in range(45)]
    initial = {value: f"Initial {value}" for value in values[:5]}

    translations, review, attempts, sources = service._complete_variant_translations(
        values,
        initial,
        "en",
    )

    assert [len(request) for request in calls] == [20, 1, 20, 1]
    assert all(value not in calls[0] for value in values[:5])
    assert translations[values[0]] == f"Initial {values[0]}"
    assert translations[values[-1]] == f"Translated {values[-1]}"
    assert review == []
    assert attempts == 4
    assert sources == {"ai": 45, "builtin": 0, "original": 0}


def test_failed_ai_repair_uses_builtin_then_marks_unknown_for_review(monkeypatch) -> None:
    service = object.__new__(ProductProcessingService)

    class _FailedClient:
        last_attempt_count = 3

        def generate_listing_text(self, _prompt: str) -> DoubaoTextResult:
            raise DoubaoTextError(
                "invalid response",
                error_kind="invalid_response",
                retryable=True,
                attempt_count=3,
            )

    monkeypatch.setattr(service, "_doubao_text_client", _FailedClient)
    values = ["黑色", "175cm以上", "YWM-01", "糖果色针织围巾（米白）"]

    translations, review, attempts, sources = service._complete_variant_translations(
        values,
        {},
        "en",
    )

    assert translations == {
        "黑色": "Black",
        "175cm以上": "175cm and above",
        "YWM-01": "YWM-01",
        "糖果色针织围巾（米白）": "糖果色针织围巾（米白）",
    }
    assert review == ["糖果色针织围巾（米白）"]
    assert attempts == 3
    assert sources == {"ai": 0, "builtin": 3, "original": 1}


def test_variant_translation_matching_only_accepts_original_required_values() -> None:
    payload = {
        "variant_translations": [
            {"raw_value": " 黑色 ", "export_value": "Black"},
            {"raw_value": "黑色", "export_value": "Duplicate"},
            {"raw_value": 73, "export_value": 73},
            {"raw_value": 0, "export_value": 0},
            {"raw_value": "模型擅自添加", "export_value": "Extra"},
            {"raw_value": "白色", "export_value": ""},
        ]
    }

    translations = ProductProcessingService._combined_variant_translations(
        payload,
        ["黑色", "73", "0", "白色"],
    )

    assert translations == {"黑色": "Black", "73": "73", "0": "0"}


def test_variant_translation_matching_tolerates_fullwidth_and_space_drift() -> None:
    # AI 回显时把同一选项写成全角数字/符号、内部空格或大小写不一致，
    # 应仍能对回原始选项，而不是误判缺失。
    payload = {
        "variant_translations": [
            {"raw_value": "１７５cm＋", "export_value": "175cm+"},
            {"raw_value": "红 色", "export_value": "Red"},
            {"raw_value": "LightBlue", "export_value": "Light Blue"},
        ]
    }

    translations = ProductProcessingService._combined_variant_translations(
        payload,
        ["175cm+", "红色", "Light Blue"],
    )

    assert translations == {
        "175cm+": "175cm+",
        "红色": "Red",
        "Light Blue": "Light Blue",
    }


def test_variant_translation_matching_never_cross_pairs_ambiguous_options() -> None:
    # "AB" 与 "A B" 归一化后都变成 "ab"，撞 key。此时即便 AI 回显全角 "ＡＢ"
    # （精确 casefold 不命中），归一化也必须放弃，绝不能错配到任一原始值。
    payload = {
        "variant_translations": [
            {"raw_value": "ＡＢ", "export_value": "ambiguous"},
        ]
    }

    translations = ProductProcessingService._combined_variant_translations(
        payload,
        ["AB", "A B"],
    )

    assert translations == {}


def test_ai_chinese_export_value_falls_back_to_builtin_dictionary() -> None:
    # AI 偷懒把中文原样返回（"红色"→"红色"），语言校验应剔除它，
    # 再走词库兜底得到正确英文，而不是当作“已翻译”导出。
    service = object.__new__(ProductProcessingService)

    translations, review, attempts, sources = service._complete_variant_translations(
        ["红色"],
        {"红色": "红色"},
        "en",
    )

    assert translations == {"红色": "Red"}
    assert review == []
    assert sources == {"ai": 0, "builtin": 1, "original": 0}


def test_ai_chinese_export_value_without_builtin_marks_review() -> None:
    # AI 返回中文且词库无对应词时，语言校验剔除后应原样保留并标红待人工。
    service = object.__new__(ProductProcessingService)

    translations, review, attempts, sources = service._complete_variant_translations(
        ["糖果色针织围巾（米白）"],
        {"糖果色针织围巾（米白）": "糖果色针织围巾（米白）"},
        "en",
    )

    assert translations == {"糖果色针织围巾（米白）": "糖果色针织围巾（米白）"}
    assert review == ["糖果色针织围巾（米白）"]
    assert sources == {"ai": 0, "builtin": 0, "original": 1}


def test_numeric_source_variant_values_become_strings_including_zero() -> None:
    raw = {
        "source_variant_records": [
            {"attributes": {"Number": 0, "Size": 73, "Length": 160.22}},
        ]
    }

    assert ProductProcessingService._unique_variant_values(raw) == [
        "0",
        "73",
        "160.22",
    ]


def test_preview_exposes_untranslated_values_for_red_warning() -> None:
    service = object.__new__(ProductProcessingService)
    preview = service._preview_item(
        {"id": 8, "status": "completed"},
        {
            "optimized_title": "Title",
            "description": "Description",
            "variant_translation_review_values": ["糖果色", "  未知规格  ", ""],
            "variant_translation_sources": {"ai": 2, "builtin": 1, "original": 2},
        },
        {},
    )

    assert preview["variant_translation_review_values"] == ["糖果色", "未知规格"]
    assert preview["variant_translation_sources"] == {
        "ai": 2,
        "builtin": 1,
        "original": 2,
    }
