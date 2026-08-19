from __future__ import annotations

import hashlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from types import SimpleNamespace

import pytest
import requests
from PIL import Image, JpegImagePlugin

from wh_local.modules.product_processing import service as service_module
from wh_local.modules.product_processing import provider_config as provider_config_module
from wh_local.modules.product_processing.domain.prompts import (
    GRID_IMAGE_PROMPT,
    GRID_IMAGE_PROMPT_B,
    GRID_IMAGE_REPAIR_PROMPT,
    GRID_RUNTIME_CONTRACT,
    PREMIUM_IMAGE_PROMPT,
)
from wh_local.modules.product_processing.domain.workbooks import _http_urls
from wh_local.modules.product_processing.infrastructure import ocr_gate
from wh_local.modules.product_processing.infrastructure import media as media_module
from wh_local.modules.product_processing.infrastructure.media import (
    GeneratedMedia,
    MediaProcessingError,
    ProductImageProcessor,
)


def _grid_bytes(size: int = 2048) -> bytes:
    image = Image.new("RGB", (size, size), "white")
    half = size // 2
    image.paste((210, 30, 30), (0, 0, half - 8, half - 8))
    image.paste((30, 180, 50), (half + 8, 0, size, half - 8))
    image.paste((30, 70, 210), (0, half + 8, half - 8, size))
    image.paste((220, 180, 30), (half + 8, half + 8, size, size))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _two_grid_bytes() -> bytes:
    image = Image.new("RGB", (2048, 1024), "white")
    image.paste((210, 30, 30), (0, 0, 1016, 1024))
    image.paste((30, 70, 210), (1032, 0, 2048, 1024))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _media(content: bytes) -> GeneratedMedia:
    return GeneratedMedia(
        stage="grid_image",
        content=content,
        content_type="image/png",
        suffix=".png",
        provider="fake",
        model="fake",
        reference_count=1,
    )


def test_wuyin_result_download_retries_transient_ssl_failure(monkeypatch) -> None:
    class _SubmitResponse:
        ok = True

        def json(self) -> dict:
            return {"code": 200, "data": {"id": "task-1"}}

        def close(self) -> None:
            return

    class _ImageResponse:
        content = b"generated-image"
        headers = {"Content-Type": "image/png"}

        def raise_for_status(self) -> None:
            return

        def close(self) -> None:
            return

    download_attempts = 0

    def _get(url: str, **kwargs):  # noqa: ANN003
        nonlocal download_attempts
        assert url == "https://scapi.net/result.png"
        download_attempts += 1
        if download_attempts == 1:
            raise requests.exceptions.SSLError("unexpected eof")
        return _ImageResponse()

    monkeypatch.setattr(media_module._SESSION, "post", lambda *args, **kwargs: _SubmitResponse())
    monkeypatch.setattr(media_module._SESSION, "get", _get)
    monkeypatch.setattr(media_module.time, "sleep", lambda *_args: None)
    processor = ProductImageProcessor(lambda: {})
    monkeypatch.setattr(
        processor,
        "_poll_wuyin_image_result",
        lambda *_args, **_kwargs: "https://scapi.net/result.png",
    )

    content, content_type = processor._request_wuyin_image(
        {
            "base_url": "https://api.wuyinkeji.com",
            "api_key": "secret",
            "image_size": "2K",
        },
        "prompt",
        [(b"reference", "source.png", "image/png", "https://example.test/source.png")],
        timeout_seconds=600,
    )

    assert content == b"generated-image"
    assert content_type == "image/png"
    assert download_attempts == 2


def test_wuyin_result_download_falls_back_to_http_after_https_ssl_failures(monkeypatch) -> None:
    class _SubmitResponse:
        ok = True

        def json(self) -> dict:
            return {"code": 200, "data": {"id": "task-1"}}

        def close(self) -> None:
            return

    class _ImageResponse:
        content = b"generated-image"
        headers = {"Content-Type": "image/png"}

        def raise_for_status(self) -> None:
            return

        def close(self) -> None:
            return

    download_urls: list[str] = []

    def _get(url: str, **kwargs):  # noqa: ANN003
        download_urls.append(url)
        if url.startswith("https://"):
            raise requests.exceptions.SSLError("unexpected eof")
        return _ImageResponse()

    monkeypatch.setattr(media_module._SESSION, "post", lambda *args, **kwargs: _SubmitResponse())
    monkeypatch.setattr(media_module._SESSION, "get", _get)
    monkeypatch.setattr(media_module.time, "sleep", lambda *_args: None)
    processor = ProductImageProcessor(lambda: {})
    monkeypatch.setattr(
        processor,
        "_poll_wuyin_image_result",
        lambda *_args, **_kwargs: "https://scapi.net/result.png",
    )

    content, content_type = processor._request_wuyin_image(
        {
            "base_url": "https://api.wuyinkeji.com",
            "api_key": "secret",
            "image_size": "2K",
        },
        "prompt",
        [(b"reference", "source.png", "image/png", "https://example.test/source.png")],
        timeout_seconds=600,
    )

    assert content == b"generated-image"
    assert content_type == "image/png"
    assert download_urls == [
        "https://scapi.net/result.png",
        "https://scapi.net/result.png",
        "https://scapi.net/result.png",
        "http://scapi.net/result.png",
    ]


def test_four_grid_prompt_forbids_all_typography_and_requires_validated_dividers() -> None:
    assert "zero AI-added visible text" in GRID_IMAGE_PROMPT
    assert "No AI-generated copy" in GRID_IMAGE_PROMPT
    assert "No headline, fact card, or typography is added after splitting" in GRID_IMAGE_PROMPT
    assert "separator is mandatory and is validated" in GRID_IMAGE_PROMPT
    assert "uniform, and uninterrupted" in GRID_RUNTIME_CONTRACT
    assert "zero AI-added visible text" in GRID_IMAGE_PROMPT_B
    assert "pure macro crop without the complete sellable product is forbidden" in GRID_IMAGE_PROMPT_B
    assert "Do not invent packaging" in GRID_IMAGE_PROMPT_B


def test_split_four_grid_uses_exact_center_and_preserves_outer_edges() -> None:
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_four_grid(_media(_grid_bytes()))
    assert len(parts) == 5
    expected = [(210, 30, 30), (30, 180, 50), (30, 70, 210), (220, 180, 30)]
    for part, color in zip(parts[:4], expected):
        with Image.open(BytesIO(part.content)) as image:
            assert image.size == (800, 800)
            actual = image.convert("RGB").getpixel((400, 400))
            assert all(abs(value - wanted) <= 4 for value, wanted in zip(actual, color))


def test_split_four_grid_accepts_provider_1k_fallback() -> None:
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_four_grid(_media(_grid_bytes(1024)))
    assert len(parts) == 5
    for part in parts:
        with Image.open(BytesIO(part.content)) as image:
            assert image.size == (800, 800)


def test_split_four_grid_rejects_source_below_1k_with_dimensions() -> None:
    processor = ProductImageProcessor(lambda: {})
    with pytest.raises(MediaProcessingError, match=r"900x900.*at least 1024px"):
        processor.split_four_grid(_media(_grid_bytes(900)))


def test_split_premium_four_grid_preserves_approximately_2k_panels() -> None:
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_premium_four_grid(_media(_grid_bytes(4096)))

    assert [part.stage for part in parts] == [
        "premium_image_1",
        "premium_image_2",
        "premium_image_3",
        "premium_image_4",
        "premium_image_summary",
    ]
    for part in parts[:4]:
        with Image.open(BytesIO(part.content)) as image:
            assert image.size == (2048, 2048)
            assert image.format == "JPEG"
            assert JpegImagePlugin.get_sampling(image) == 0
    with Image.open(BytesIO(parts[-1].content)) as image:
        assert image.size == (800, 800)


def test_split_premium_four_grid_accepts_2k_gateway_output_and_uses_exact_center() -> None:
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_premium_four_grid(_media(_grid_bytes(2048)))
    assert len(parts) == 5
    for part in parts[:4]:
        with Image.open(BytesIO(part.content)) as opened:
            assert opened.size == (1024, 1024)


def test_split_premium_four_grid_rejects_too_small_or_repeated_panels() -> None:
    processor = ProductImageProcessor(lambda: {})
    with pytest.raises(MediaProcessingError, match="cannot be split"):
        processor.split_premium_four_grid(_media(_grid_bytes(1700)))

    repeated = Image.new("RGB", (2048, 2048), (30, 70, 120))
    buffer = BytesIO()
    repeated.save(buffer, format="PNG")
    with pytest.raises(MediaProcessingError, match="cannot be split"):
        processor.split_premium_four_grid(_media(buffer.getvalue()))


def test_premium_prompt_requires_exact_split_safe_4k_grid() -> None:
    assert "4096 x 4096" in PREMIUM_IMAGE_PROMPT
    assert "exactly FOUR equal square panels" in PREMIUM_IMAGE_PROMPT
    assert "50% vertical center" in PREMIUM_IMAGE_PROMPT
    assert "50% horizontal center" in PREMIUM_IMAGE_PROMPT
    assert "Nothing may cross either center divider" in PREMIUM_IMAGE_PROMPT


def test_split_two_grid_preserves_two_landscape_panels() -> None:
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_two_grid(_media(_two_grid_bytes()), start_index=3)

    assert [part.stage for part in parts] == ["grid_image_3", "grid_image_4"]
    expected = [(210, 30, 30), (30, 70, 210)]
    for part, color in zip(parts, expected):
        with Image.open(BytesIO(part.content)) as image:
            assert image.size == (800, 800)
            actual = image.convert("RGB").getpixel((400, 400))
            assert all(abs(value - wanted) <= 4 for value, wanted in zip(actual, color))


def test_split_four_grid_falls_back_to_center_when_no_divider_evidence() -> None:
    # 无分隔线证据时回退正中切分（面板独立性校验只在面板内出现完整长分隔线时拒绝）。
    image = Image.new("RGB", (2048, 2048), (40, 80, 120))
    for x in range(2048):
        color = (40 + x // 16, 80, 120)
        image.paste(color, (x, 0, x + 1, 2048))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    processor = ProductImageProcessor(lambda: {})
    parts = processor.split_four_grid(_media(buffer.getvalue()))
    assert len(parts) == 5


def test_split_four_grid_accepts_one_small_shifted_separator_as_local_fallback() -> None:
    image = Image.new("RGB", (2048, 2048), (34, 72, 118))
    image.paste((235, 235, 235), (1060, 0, 1072, 2048))
    image.paste((235, 235, 235), (0, 1060, 2048, 1072))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    processor = ProductImageProcessor(lambda: {})
    assert len(processor.split_four_grid(_media(buffer.getvalue()))) == 5


def _image_provider_config(source_path, *, retries: int = 3):
    return {
        "image": {
            "base_url": "https://provider.example/v1",
            "api_key": "configured",
            "model": "gpt-image-test",
            "reference_model": "gpt-image-test",
            "reference_model_1k": "gpt-image-test-1k",
            "image_size": "2048x2048",
        },
        "limits": {"image_retry_attempts": retries},
        "source_path": str(source_path),
    }


def test_image_http_400_is_not_retried(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise MediaProcessingError("bad request", status_code=400)

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(fail))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))
    with pytest.raises(MediaProcessingError) as caught:
        processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])
    assert calls == 1
    assert caught.value.status_class == "non_retryable_4xx"
    assert caught.value.attempt_count == 1


def test_image_timeout_with_unknown_outcome_is_not_retried(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls = 0

    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise requests.Timeout("provider timed out")

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(fail))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))
    with pytest.raises(MediaProcessingError) as caught:
        processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])
    assert calls == 1
    assert caught.value.status_class == "unknown_outcome_timeout"


def test_transient_provider_cooldown_skips_to_backup_but_never_removes_only_route(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls: list[str] = []

    config = _image_provider_config(source)
    config["backup_image"] = {
        "base_url": "https://backup.example/v1",
        "api_key": "configured",
        "model": "gpt-image-backup",
        "reference_model": "gpt-image-backup",
        "image_size": "2048x2048",
    }
    config["limits"]["image_provider_strategy"] = "primary_first"

    def respond(provider, *_args, **_kwargs):
        calls.append(provider["name"])
        if provider["name"].startswith("primary"):
            raise requests.Timeout("primary timed out")
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: config)
    for _ in range(2):
        with pytest.raises(MediaProcessingError, match="primary"):
            processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])

    media = processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])

    assert calls == [
        "primary:gpt-image-test",
        "primary:gpt-image-test",
        "backup:gpt-image-backup",
    ]
    assert media.provider == "backup:gpt-image-backup"

    only_primary = ProductImageProcessor(lambda: _image_provider_config(source))
    only_key = only_primary._provider_health_key(only_primary._providers(_image_provider_config(source))[0])
    only_primary._provider_cooldown_until[only_key] = time.monotonic() + 60
    assert len(only_primary._provider_order(only_primary._providers(_image_provider_config(source)), _image_provider_config(source))) == 1


def test_image_early_500_retries_only_once(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls = 0

    def respond(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise MediaProcessingError("server error", status_code=500)
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))
    media = processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=[str(source)],
        layout_scaffold=True,
    )
    assert calls == 2
    assert media.attempt_count == 2
    assert media.reference_count == 1


def test_layout_scaffold_keeps_original_1688_image_as_first_provider_reference(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "original-1688.png"
    source.write_bytes(_grid_bytes())
    captured_names: list[str] = []

    def respond(_provider, _prompt, references, **_kwargs):
        captured_names.extend(name for _content, name, _content_type in references)
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))

    processor.generate(
        stage="grid_image",
        prompt="contract",
        reference_values=[str(source)],
        layout_scaffold=True,
    )

    assert captured_names == ["original-1688.png", "fixed-four-grid-layout.png"]


def test_repair_keeps_original_1688_image_before_previous_generated_image(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "original-1688.png"
    source.write_bytes(_grid_bytes())
    captured_names: list[str] = []

    def respond(_provider, _prompt, references, **_kwargs):
        captured_names.extend(name for _content, name, _content_type in references)
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))

    media = processor.repair_generated(
        stage="detail_image",
        prompt="repair contract",
        prior_content=_grid_bytes(),
        prior_content_type="image/png",
        reference_values=[str(source)],
    )

    assert captured_names == ["original-1688.png", "generated_previous.png"]
    assert media.reference_count == 1


def test_second_grid_attempt_uses_only_the_remaining_total_timeout_budget(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    clock = [0.0]
    request_timeouts: list[float] = []

    def respond(*_args, **kwargs):
        request_timeouts.append(float(kwargs["timeout_seconds"]))
        if len(request_timeouts) == 1:
            clock[0] = 625.0
            raise MediaProcessingError("server error", status_code=500)
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(
        "wh_local.modules.product_processing.infrastructure.media.time.monotonic",
        lambda: clock[0],
    )
    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))

    media = processor.generate(stage="grid_image", prompt="contract", reference_values=[str(source)])

    assert media.attempt_count == 2
    assert request_timeouts == [600.0, 35.0]


def test_failed_slot_model_override_does_not_change_regular_2k_grid_profile(monkeypatch, tmp_path) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(_grid_bytes())
    calls: list[dict[str, str | None]] = []

    def respond(_provider, _prompt, _references, **kwargs):
        calls.append(
            {
                "reference_model": kwargs.get("reference_model"),
                "image_size": kwargs.get("image_size"),
            }
        )
        return _grid_bytes(), "image/png"

    monkeypatch.setattr(ProductImageProcessor, "_request_edit", staticmethod(respond))
    processor = ProductImageProcessor(lambda: _image_provider_config(source))

    one_k = processor.generate(
        stage="grid_image_3",
        prompt="fill failed slot",
        reference_values=[str(source)],
        image_size="1024x1024",
        model_override="gpt-image-test-1k",
    )
    two_k = processor.generate(
        stage="grid_image",
        prompt="regular four grid",
        reference_values=[str(source)],
    )

    assert one_k.model == "gpt-image-test-1k"
    assert two_k.model == "gpt-image-test"
    assert calls == [
        {"reference_model": "gpt-image-test-1k", "image_size": "1024x1024"},
        {"reference_model": None, "image_size": None},
    ]


def test_compose_grid_summary_uses_replaced_normalized_slots() -> None:
    processor = ProductImageProcessor(lambda: {})
    colors = [(210, 30, 30), (30, 180, 50), (30, 70, 210), (220, 180, 30)]
    parts: list[GeneratedMedia] = []
    for index, color in enumerate(colors, start=1):
        image = Image.new("RGB", (800, 800), color)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=94, subsampling=0)
        parts.append(
            GeneratedMedia(
                stage=f"grid_image_{index}",
                content=buffer.getvalue(),
                content_type="image/jpeg",
                suffix=".jpg",
                provider="test",
                model="test",
                reference_count=1,
            )
        )

    summary = processor.compose_grid_summary(parts)

    assert summary.stage == "grid_image_summary"
    assert summary.content_type == "image/jpeg"
    with Image.open(BytesIO(summary.content)) as image:
        assert image.size == (800, 800)
        samples = [image.convert("RGB").getpixel(point) for point in ((200, 200), (600, 200), (200, 600), (600, 600))]
    for actual, expected in zip(samples, colors):
        assert all(abs(value - wanted) <= 5 for value, wanted in zip(actual, expected))


def test_reference_download_cache_single_flights_concurrent_reads(monkeypatch) -> None:
    calls = 0
    calls_lock = threading.Lock()

    def download(_url: str) -> tuple[bytes, str]:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.04)
        return b"same-image", "image/jpeg"

    monkeypatch.setattr(media_module, "_download_reference_image", download)
    processor = ProductImageProcessor(lambda: {"limits": {"reference_download_cache_entries": 2}})

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _index: processor._load_references(["https://example.com/source.jpg"], limit=1),
                range(4),
            )
        )

    assert calls == 1
    assert [result[0][0] for result in results] == [b"same-image"] * 4


def test_prime_references_uses_existing_safe_single_flight_cache(monkeypatch) -> None:
    calls = 0

    def download(_url: str) -> tuple[bytes, str]:
        nonlocal calls
        calls += 1
        return b"same-image", "image/jpeg"

    monkeypatch.setattr(media_module, "_download_reference_image", download)
    processor = ProductImageProcessor(lambda: {})
    reference = "https://example.com/source.jpg"

    assert processor.prime_references([reference]) == 1
    assert processor._load_references([reference], limit=1)[0][0] == b"same-image"
    assert calls == 1


def test_reference_download_cache_is_bounded_and_does_not_cache_failures(monkeypatch) -> None:
    calls: dict[str, int] = {}

    def download(url: str) -> tuple[bytes, str]:
        calls[url] = calls.get(url, 0) + 1
        if url.endswith("flaky.jpg") and calls[url] == 1:
            raise requests.ConnectionError("temporary")
        return url.encode(), "image/jpeg"

    monkeypatch.setattr(media_module, "_download_reference_image", download)
    processor = ProductImageProcessor(lambda: {"limits": {"reference_download_cache_entries": 1}})
    flaky = "https://example.com/flaky.jpg"
    first = "https://example.com/first.jpg"
    second = "https://example.com/second.jpg"

    with pytest.raises(MediaProcessingError, match="download failed"):
        processor._load_references([flaky], limit=1)
    assert processor._load_references([flaky], limit=1)[0][0] == flaky.encode()
    processor._load_references([first], limit=1)
    processor._load_references([second], limit=1)
    processor._load_references([first], limit=1)

    assert calls[flaky] == 2
    assert calls[first] == 2
    assert calls[second] == 1


def test_reference_loading_fails_closed_when_first_image_is_unavailable(monkeypatch) -> None:
    calls: list[str] = []

    def download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        if url.endswith("original-main.jpg"):
            raise requests.ConnectionError("main unavailable")
        return b"fallback-image", "image/jpeg"

    monkeypatch.setattr(media_module, "_download_reference_image", download)
    processor = ProductImageProcessor(lambda: {})

    with pytest.raises(MediaProcessingError, match="required first reference"):
        processor._load_references(
            [
                "https://example.com/original-main.jpg",
                "https://example.com/detail.jpg",
            ],
            limit=2,
        )

    assert calls == ["https://example.com/original-main.jpg"]


def test_provider_config_uses_image_gpt_with_explicit_1k_reference_profile(monkeypatch) -> None:
    monkeypatch.setattr(provider_config_module, "_try_system_runtime_config", lambda: None)
    monkeypatch.setenv("WH_AI_API_KEY", "configured")

    provider = provider_config_module.resolve_ai_provider()

    assert provider["image_model"] == "image_gpt"
    assert provider["reference_image_model"] == "image_gpt"
    assert provider["image_size"] == "2048x2048"
    assert provider["reference_image_model_1k"] == "gpt-image-2-1k"
    assert provider["reference_image_size_1k"] == "1024x1024"
    assert provider["premium_image_model"] == "gpt-image-2-4k"
    assert provider["premium_image_size"] == "4096x4096"


def test_provider_config_routes_saved_image_key_to_wuyin(monkeypatch) -> None:
    runtime_config = SimpleNamespace(
        text_ai=SimpleNamespace(base_url="https://text.example/v1", api_key="text-key"),
        image_ai=SimpleNamespace(
            base_url="https://legacy-image.example/v1",
            api_key="image-key",
            model="gpt-image-2-2k",
            configured=True,
        ),
        backup_image_ai=SimpleNamespace(configured=False),
        cos=SimpleNamespace(configured=False),
        limits={},
        updates={},
    )
    monkeypatch.setattr(provider_config_module, "_try_system_runtime_config", lambda: runtime_config)
    monkeypatch.delenv("WH_IMAGE_AI_BASE_URL", raising=False)

    provider = provider_config_module.resolve_ai_provider()

    assert provider["_sys_image_ai"]["base_url"] == "https://api.wuyinkeji.com"


def test_b_grid_quality_failure_never_triggers_a_paid_repair(monkeypatch) -> None:
    class _Processor:
        repair_calls = 0

        @staticmethod
        def validate_four_grid(_media) -> None:
            return None

        def repair_generated(self, **_kwargs):
            self.repair_calls += 1
            return _media(_grid_bytes())

    service = object.__new__(service_module.ProductProcessingService)
    processor = _Processor()
    monkeypatch.setattr(
        service_module,
        "inspect_visible_text",
        lambda _content: {"chinese": [], "prominent": ["FACTORY DIRECT"]},
    )
    monkeypatch.setattr(service_module, "_media_types", lambda: (object, RuntimeError, ValueError))

    # 质量门为软性：B 模板禁止付费重绘，检出问题只留痕 quality_unresolved，不再 raise 阻断，
    # 图片整组失败由 _process_one 回退来源图继续（用户要求不卡流程）。
    notes: list[str] = []
    result = service._repair_until_clean(
        processor,
        "grid_image",
        "four_grid",
        _media(_grid_bytes()),
        ["https://example.com/source.jpg"],
        notes,
        allow_paid_repair=False,
    )

    assert processor.repair_calls == 0
    assert result is not None
    assert "four_grid:quality_unresolved" in notes


def test_local_detail_reads_split_media_bytes_without_redownloading(monkeypatch) -> None:
    monkeypatch.setattr(
        service_module,
        "fetch_public_image",
        lambda *_args, **_kwargs: pytest.fail("split media must not be downloaded from COS"),
    )
    media = _media(_grid_bytes())
    assert service_module.ProductProcessingService._local_source_bytes(media) == media.content


def test_ocr_inspection_flags_large_added_copy_but_not_small_product_mark(monkeypatch) -> None:
    class _Engine:
        def __call__(self, _array):
            return (
                [
                    # 跨面板海报横幅：高 220px(10.7%)、宽 900px(44%) → prominent
                    [[[100, 120], [1000, 120], [1000, 340], [100, 340]], "FACTORY DIRECT", 0.99],
                    # 产品本体小标记：宽 100px(4.9%) → 不拦
                    [[[1500, 1500], [1600, 1500], [1600, 1530], [1500, 1530]], "JOKER", 0.98],
                    # 中文硬拦截（不受 prominent 放宽影响）
                    [[[100, 1700], [260, 1700], [260, 1740], [100, 1740]], "麻将", 0.97],
                ],
                None,
            )

    monkeypatch.setattr(ocr_gate, "_get_engine", lambda: _Engine())
    inspection = ocr_gate.inspect_visible_text(_grid_bytes())
    assert inspection is not None
    assert inspection["prominent"] == ["FACTORY DIRECT"]
    assert inspection["chinese"] == ["麻将"]


def test_ocr_inference_uses_dedicated_two_worker_gate(monkeypatch) -> None:
    active = 0
    maximum = 0
    lock = threading.Lock()

    class _Engine:
        def __call__(self, _array):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.04)
            with lock:
                active -= 1
            return [], None

    monkeypatch.setattr(ocr_gate, "_get_engine", lambda: _Engine())
    monkeypatch.setattr(ocr_gate, "_OCR_INFERENCE_SEMAPHORE", threading.BoundedSemaphore(2))

    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _index: ocr_gate.inspect_visible_text(_grid_bytes()), range(5)))

    assert results == [{"chinese": [], "prominent": []}] * 5
    assert maximum == 2


def test_ocr_inspection_ignores_large_print_on_product_face(monkeypatch) -> None:
    """麻将牌这类「产品本体印刷大字符」不得被误判为 AI 显著文字：
    字符再大，只要宽度局限在单面板内（<30%），就不再触发 prominent，避免重绘死循环。"""
    class _Engine:
        def __call__(self, array):
            height, width = array.shape[:2]
            scale = width / 2048.0  # 假引擎坐标基于 2048 空间，按输入实际尺寸等比换算
            return (
                [
                    # 牌面数字：高 200px(9.8%) 但宽仅 400px(19.5%) → 产品印刷标记，放过
                    [
                        [[800 * scale, 900 * scale], [1200 * scale, 900 * scale],
                         [1200 * scale, 1100 * scale], [800 * scale, 1100 * scale]],
                        "1234567890",
                        0.99,
                    ],
                ],
                None,
            )

    monkeypatch.setattr(ocr_gate, "_get_engine", lambda: _Engine())
    inspection = ocr_gate.inspect_visible_text(_grid_bytes())
    assert inspection is not None
    assert inspection["prominent"] == []
    assert inspection["chinese"] == []


def test_grid_repair_prompt_preserves_product_print_and_replaces_chinese() -> None:
    """重绘提示词不得自相矛盾：只删 AI 添加的跨面板文字；
    产品本体印刷字符/图案是设计（保留），产品本体印刷中文则换成英文。"""
    assert "Remove ONLY AI-added cross-panel headlines" in GRID_IMAGE_REPAIR_PROMPT
    assert "spans two or more panels must be deleted" in GRID_IMAGE_REPAIR_PROMPT
    assert "are PRODUCT DESIGN and must be kept" in GRID_IMAGE_REPAIR_PROMPT
    assert "mahjong tile faces" in GRID_IMAGE_REPAIR_PROMPT
    assert "replace that printed text with the equivalent English text" in GRID_IMAGE_REPAIR_PROMPT
    assert "never reproduce Chinese characters" in GRID_IMAGE_REPAIR_PROMPT


def test_dianxiaomi_image_filter_rejects_local_and_private_urls() -> None:
    assert _http_urls(
        [
            "https://bucket.cos.ap-guangzhou.myqcloud.com/a.jpg",
            "http://127.0.0.1:8010/a.jpg",
            "http://192.168.1.9/a.jpg",
            "/pp-media/a.jpg",
            r"C:\\temp\\a.jpg",
        ]
    ) == ["https://bucket.cos.ap-guangzhou.myqcloud.com/a.jpg"]


def test_content_addressed_cos_publish_reuses_the_same_object(monkeypatch) -> None:
    class _Missing(Exception):
        def get_status_code(self):
            return 404

    class _Client:
        def __init__(self) -> None:
            self.keys: set[str] = set()
            self.put_calls = 0

        def head_object(self, *, Bucket: str, Key: str):
            assert Bucket == "bucket-1"
            if Key not in self.keys:
                raise _Missing()
            return {
                "x-cos-meta-sha256": hashlib.sha256(b"dimension-jpeg").hexdigest(),
                "Content-Length": str(len(b"dimension-jpeg")),
                "Content-Type": "image/jpeg",
            }

        def put_object(
            self,
            *,
            Bucket: str,
            Key: str,
            Body: bytes,
            ContentType: str,
            Metadata: dict[str, str],
        ):
            assert Bucket == "bucket-1"
            assert Body == b"dimension-jpeg"
            assert ContentType == "image/jpeg"
            assert Metadata == {"x-cos-meta-sha256": hashlib.sha256(Body).hexdigest()}
            self.keys.add(Key)
            self.put_calls += 1
            return {}

    client = _Client()
    monkeypatch.setitem(
        sys.modules,
        "qcloud_cos",
        SimpleNamespace(CosConfig=lambda **_kwargs: object(), CosS3Client=lambda _config: client),
    )
    processor = ProductImageProcessor(
        lambda: {
            "cos": {
                "bucket": "bucket-1",
                "region": "ap-guangzhou",
                "secret_id": "configured",
                "secret_key": "configured",
            }
        }
    )
    content = b"dimension-jpeg"
    digest = hashlib.sha256(content).hexdigest()
    media = GeneratedMedia(
        stage="dimension",
        content=content,
        content_type="image/jpeg",
        suffix=".jpg",
        provider="local",
        model="pillow",
        reference_count=1,
    )
    first = processor.upload_content_addressed_to_cos(
        media,
        namespace="workspace-a",
        content_hash=digest,
        collection="preview-final",
    )
    second = processor.upload_content_addressed_to_cos(
        media,
        namespace="workspace-a",
        content_hash=digest,
        collection="preview-final",
    )
    assert first == second
    assert "/preview-final/workspace-a/" in first
    assert first.endswith(f"/{digest}.jpg")
    assert client.put_calls == 1
    assert processor.is_configured_cos_url(first) is True
    assert processor.is_configured_cos_url("https://other.example.com/old.jpg") is False


def test_system_settings_cos_credentials_feed_internal_media_config(monkeypatch) -> None:
    monkeypatch.setattr(service_module, "_cos_local_config_paths", lambda: ())
    for key in ("WH_COS_BUCKET", "WH_COS_REGION", "WH_COS_SECRET_ID", "WH_COS_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        service_module,
        "resolve_ai_provider",
        lambda: {
            "_sys_cos": {
                "bucket": "settings-bucket",
                "region": "ap-guangzhou",
                "secret_id": "runtime-id",
                "secret_key": "runtime-key",
            }
        },
    )
    config = service_module.ProductProcessingService._media_config_provider()
    assert config["cos"] == {
        "bucket": "settings-bucket",
        "region": "ap-guangzhou",
        "secret_id": "runtime-id",
        "secret_key": "runtime-key",
    }
