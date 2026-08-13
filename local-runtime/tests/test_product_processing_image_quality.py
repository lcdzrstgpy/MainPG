from __future__ import annotations

import hashlib
import sys
from io import BytesIO
from types import SimpleNamespace

import pytest
import requests
from PIL import Image

from wh_local.modules.product_processing import service as service_module
from wh_local.modules.product_processing.domain.prompts import (
    GRID_IMAGE_PROMPT,
    GRID_IMAGE_PROMPT_B,
    GRID_RUNTIME_CONTRACT,
)
from wh_local.modules.product_processing.domain.workbooks import _http_urls
from wh_local.modules.product_processing.infrastructure import ocr_gate
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


def test_split_four_grid_rejects_low_resolution_source() -> None:
    processor = ProductImageProcessor(lambda: {})
    with pytest.raises(MediaProcessingError, match="cannot be split"):
        processor.split_four_grid(_media(_grid_bytes(1024)))


def test_split_four_grid_rejects_continuous_poster_without_center_dividers() -> None:
    image = Image.new("RGB", (2048, 2048), (40, 80, 120))
    for x in range(2048):
        color = (40 + x // 16, 80, 120)
        image.paste(color, (x, 0, x + 1, 2048))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    processor = ProductImageProcessor(lambda: {})
    with pytest.raises(MediaProcessingError, match="cannot be split"):
        processor.split_four_grid(_media(buffer.getvalue()))


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

    with pytest.raises(ValueError, match="停止付费重绘"):
        service._repair_until_clean(
            processor,
            "grid_image",
            "four_grid",
            _media(_grid_bytes()),
            ["https://example.com/source.jpg"],
            [],
            allow_paid_repair=False,
        )

    assert processor.repair_calls == 0


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
                    [[[100, 120], [1000, 120], [1000, 260], [100, 260]], "FACTORY DIRECT", 0.99],
                    [[[1500, 1500], [1600, 1500], [1600, 1530], [1500, 1530]], "JOKER", 0.98],
                    [[[100, 1700], [260, 1700], [260, 1740], [100, 1740]], "麻将", 0.97],
                ],
                None,
            )

    monkeypatch.setattr(ocr_gate, "_get_engine", lambda: _Engine())
    inspection = ocr_gate.inspect_visible_text(_grid_bytes())
    assert inspection is not None
    assert inspection["prominent"] == ["FACTORY DIRECT"]
    assert inspection["chinese"] == ["麻将"]


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
