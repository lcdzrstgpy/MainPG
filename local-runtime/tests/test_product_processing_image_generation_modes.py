from __future__ import annotations

from types import SimpleNamespace

import pytest

import wh_local.modules.product_processing.service as service_module
from wh_local.modules.product_processing.api.schemas import DraftProcessRequest
from wh_local.modules.product_processing.service import ProductProcessingService


def _raw() -> dict:
    return {
        "source_product_id": "image-mode-offer",
        "category_path": "Home & Kitchen > Drinkware",
        "source_attributes": [],
    }


class _Repository:
    @staticmethod
    def prompts() -> dict[str, str]:
        return {}


class _Processor:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stage="grid_image",
            content=b"generated-image",
            content_type="image/png",
            suffix=".png",
            provider="fake",
            model="fake",
            reference_count=1,
            attempt_count=1,
            provider_status_class="success",
        )

    @staticmethod
    def normalize_standalone_image(media, *, stage: str):
        return SimpleNamespace(stage=stage, content=media.content)

    @staticmethod
    def split_two_grid(media, *, start_index: int):
        return [
            SimpleNamespace(stage=f"grid_image_{start_index}", content=media.content),
            SimpleNamespace(stage=f"grid_image_{start_index + 1}", content=media.content),
        ]

    @staticmethod
    def split_four_grid(_media):
        return [
            SimpleNamespace(stage=f"grid_image_{slot}", content=f"slot-{slot}".encode())
            for slot in range(1, 5)
        ] + [SimpleNamespace(stage="grid_image_summary", content=b"old-summary")]

    @staticmethod
    def compose_grid_summary(parts):
        assert [part.stage for part in parts] == [
            "grid_image_1",
            "grid_image_2",
            "grid_image_3",
            "grid_image_4",
        ]
        return SimpleNamespace(stage="grid_image_summary", content=b"new-summary")


def _service(monkeypatch) -> tuple[ProductProcessingService, _Processor]:
    service = object.__new__(ProductProcessingService)
    service.repository = _Repository()
    processor = _Processor()
    monkeypatch.setattr(service_module, "_ai_enabled", lambda: True)
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))
    monkeypatch.setattr(service, "_media_processor", lambda: processor)
    monkeypatch.setattr(
        service,
        "_persist_media_for_preview",
        lambda parts, _task_id, _draft_id, _workspace_id: [
            f"https://example.com/{part.stage}.jpg" for part in parts
        ],
    )
    return service, processor


@pytest.mark.parametrize("count", [1, 2, 4])
def test_process_request_accepts_only_supported_image_generation_counts(count: int) -> None:
    request = DraftProcessRequest(draft_ids=[1], image_generation_count=count)
    assert request.image_generation_count == count


def test_process_request_rejects_unsupported_image_generation_count() -> None:
    with pytest.raises(ValueError, match="image_generation_count"):
        DraftProcessRequest(draft_ids=[1], image_generation_count=3)


def test_single_image_mode_uses_four_parallel_ready_standalone_calls(monkeypatch) -> None:
    service, processor = _service(monkeypatch)

    output = service._generate_grid_images(
        1,
        2,
        _raw(),
        "Travel Mug",
        "Drinkware",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=1,
    )

    assert len(processor.calls) == 4
    assert all("image_size" not in call for call in processor.calls)
    assert all("SINGLE-IMAGE RUNTIME CONTRACT" in call["prompt"] for call in processor.calls)
    assert output.attempt_count == 4
    assert len(output.carousel_urls) == 4
    assert output.summary_url == ""


def test_two_image_mode_uses_two_landscape_calls_and_splits_four_slots(monkeypatch) -> None:
    service, processor = _service(monkeypatch)

    output = service._generate_grid_images(
        1,
        2,
        _raw(),
        "Travel Mug",
        "Drinkware",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=2,
    )

    assert len(processor.calls) == 2
    assert {call["image_size"] for call in processor.calls} == {"2048x1024"}
    assert all("TWO-IMAGE RUNTIME CONTRACT" in call["prompt"] for call in processor.calls)
    assert output.attempt_count == 2
    assert len(output.carousel_urls) == 4
    assert output.summary_url == ""


def test_four_grid_repairs_only_failed_slot_with_one_1k_call(monkeypatch) -> None:
    service, processor = _service(monkeypatch)
    monkeypatch.setattr(
        service_module,
        "inspect_visible_text",
        lambda content: {
            "chinese": [],
            "prominent": ["AI COPY"] if content == b"slot-3" else [],
        },
    )

    output = service._generate_grid_images(
        1,
        2,
        _raw(),
        "Travel Mug",
        "Drinkware",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=4,
    )

    assert len(processor.calls) == 2
    assert processor.calls[0]["layout_scaffold"] is True
    assert processor.calls[1]["stage"] == "grid_image_3"
    assert processor.calls[1]["image_size"] == "1024x1024"
    assert processor.calls[1]["model_override"] == "gpt-image-2-1k"
    assert output.attempt_count == 2
    assert output.provider_status_class == "recovered_slot_retry"
    assert len(output.carousel_urls) == 4
    assert output.summary_url.endswith("grid_image_summary.jpg")
    assert [part.content for part in output.carousel_media] == [
        b"slot-1",
        b"slot-2",
        b"generated-image",
        b"slot-4",
    ]


def test_unsplittable_four_grid_never_repeats_the_paid_whole_grid_call(monkeypatch) -> None:
    service, processor = _service(monkeypatch)
    split_attempts = 0
    original_split = processor.split_four_grid

    def split_after_retry(media):
        nonlocal split_attempts
        split_attempts += 1
        if split_attempts == 1:
            raise ValueError("missing trusted grid structure")
        return original_split(media)

    monkeypatch.setattr(processor, "split_four_grid", split_after_retry)
    monkeypatch.setattr(
        service_module,
        "inspect_visible_text",
        lambda _content: {"chinese": [], "prominent": []},
    )

    output = service._generate_grid_images(
        1,
        2,
        _raw(),
        "Travel Mug",
        "Drinkware",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=4,
    )

    assert len(processor.calls) == 1
    assert all(call["stage"] == "grid_image" for call in processor.calls)
    assert all(call["layout_scaffold"] is True for call in processor.calls)
    assert all("model_override" not in call for call in processor.calls)
    assert "image_size" not in processor.calls[0]
    assert output.attempt_count == 1
    assert output.provider_status_class == "failed"
    assert output.carousel_urls == ()


def test_four_grid_source_with_chinese_allows_printed_design_panels(monkeypatch) -> None:
    """麻将/定制印刷类商品：来源图含中文 → 面板中文属产品设计，只拦横幅级、放行入库。"""
    from wh_local.modules.product_processing.infrastructure import media as media_module

    service, processor = _service(monkeypatch)
    monkeypatch.setattr(
        service_module,
        "inspect_visible_text",
        lambda content: {
            "chinese": ["中"] if content == b"slot-2" else [],
            "prominent": [],
        },
    )
    monkeypatch.setattr(
        media_module,
        "_download_reference_image",
        lambda _url: (b"source-with-chinese", "image/jpeg"),
    )
    monkeypatch.setattr(service_module, "detect_chinese_text", lambda _content: ["中"])

    ai_notes: list[str] = []
    output = service._generate_grid_images(
        1,
        2,
        _raw(),
        "Mahjong Tile Set",
        "Toys & Games",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        ai_notes=ai_notes,
        image_generation_count=4,
    )

    assert len(processor.calls) == 1  # 豁免生效：不触发 1K 重绘
    assert output.attempt_count == 1
    assert output.provider_status_class == "success"
    assert len(output.carousel_urls) == 4
    assert any(note.startswith("four_grid:printed_design:") for note in ai_notes)
    assert [part.content for part in output.carousel_media] == [
        b"slot-1",
        b"slot-2",
        b"slot-3",
        b"slot-4",
    ]


def test_four_grid_source_without_chinese_still_repairs_chinese_slots(monkeypatch) -> None:
    """非印刷设计商品：来源图无中文 → 面板中文仍硬拦截并走 1K 槽位重绘。"""
    from wh_local.modules.product_processing.infrastructure import media as media_module

    service, processor = _service(monkeypatch)
    monkeypatch.setattr(
        service_module,
        "inspect_visible_text",
        lambda content: {
            "chinese": ["中"] if content == b"slot-3" else [],
            "prominent": [],
        },
    )
    monkeypatch.setattr(
        media_module,
        "_download_reference_image",
        lambda _url: (b"clean-source", "image/jpeg"),
    )
    monkeypatch.setattr(service_module, "detect_chinese_text", lambda _content: [])

    output = service._generate_grid_images(
        1,
        2,
        _raw(),
        "Travel Mug",
        "Drinkware",
        ["https://example.com/source.jpg"],
        "en",
        "US",
        image_generation_count=4,
    )

    assert len(processor.calls) == 2
    assert processor.calls[1]["stage"] == "grid_image_3"
    assert processor.calls[1]["image_size"] == "1024x1024"
    assert output.provider_status_class == "recovered_slot_retry"
    assert len(output.carousel_urls) == 4
