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
