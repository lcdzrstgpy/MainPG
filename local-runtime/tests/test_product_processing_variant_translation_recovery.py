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
    values = [f"规格{i}" for i in range(45)]
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
