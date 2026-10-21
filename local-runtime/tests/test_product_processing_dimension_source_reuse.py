from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from wh_local.modules.product_processing.domain import dimension_source_reuse as reuse


def test_dimension_text_score_detects_numbers_with_units() -> None:
    units, tokens = reuse.dimension_text_score(["尺寸 20 cm × 10 cm"])
    assert units == 2
    assert tokens >= 2
    assert reuse.is_dimension_image(["Length 7.9 inch", "Width 3.9 inch"]) is True
    assert reuse.is_dimension_image(["Brand story and material"]) is False
    assert reuse.is_dimension_image(None) is False


def test_find_dimension_source_index_returns_matching_candidate() -> None:
    texts = {
        b"a": ["Brand story"],
        b"b": ["Size", "20 cm", "10 cm"],
        b"c": ["More details"],
    }

    assert reuse.find_dimension_source_index(
        [b"a", b"b", b"c"], recognize=lambda content: texts[content]
    ) == 1
    assert reuse.find_dimension_source_index(
        [b"a", b"c"], recognize=lambda content: texts[content]
    ) is None


def _jpeg(size: tuple[int, int], color: tuple[int, int, int] = (20, 20, 20)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def test_clean_product_canvas_outputs_white_square(monkeypatch: pytest.MonkeyPatch) -> None:
    mask = Image.new("L", (400, 300), 0)
    ImageDraw.Draw(mask).rectangle((100, 60, 300, 240), fill=255)
    monkeypatch.setattr(reuse, "_subject_mask", lambda image: mask)

    cleaned = reuse.clean_product_canvas(_jpeg((400, 300)), side=320)

    assert cleaned is not None
    assert cleaned[:2] == b"\xff\xd8"
    output = Image.open(BytesIO(cleaned))
    assert output.size == (320, 320)
    # 主体之外的区域被纯白画布覆盖（JPEG 允许极小误差）。
    assert all(channel >= 245 for channel in output.getpixel((2, 2)))


def test_clean_product_canvas_rejects_unreliable_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    mask = Image.new("L", (400, 300), 0)
    # 1px 细线：前景占比远低于 2%，属于不可信分割。
    ImageDraw.Draw(mask).line((0, 0, 399, 299), fill=255, width=1)
    monkeypatch.setattr(reuse, "_subject_mask", lambda image: mask)

    assert reuse.clean_product_canvas(_jpeg((400, 300))) is None


def test_clean_product_canvas_returns_none_without_segmentation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reuse, "_subject_mask", lambda image: None)

    assert reuse.clean_product_canvas(_jpeg((400, 300))) is None


def test_prefetch_dimension_base_marks_checked_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from wh_local.modules.product_processing import service as service_module

    class _Response:
        content = b"source-bytes"

    service = service_module.ProductProcessingService.__new__(
        service_module.ProductProcessingService
    )
    service._public_image_fetcher = lambda url: _Response()

    monkeypatch.setattr(reuse, "find_dimension_source_index", lambda candidates: 0)
    monkeypatch.setattr(reuse, "clean_product_canvas", lambda content: b"clean-base")
    assert service._prefetch_dimension_base(["a", "b"]) == {
        "checked": True,
        "base": b"clean-base",
    }

    monkeypatch.setattr(reuse, "find_dimension_source_index", lambda candidates: None)
    assert service._prefetch_dimension_base(["a"]) == {"checked": True, "base": None}

    def _raise(url: str):
        raise RuntimeError("network down")

    service._public_image_fetcher = _raise
    assert service._prefetch_dimension_base(["a"]) == {"checked": False, "base": None}
