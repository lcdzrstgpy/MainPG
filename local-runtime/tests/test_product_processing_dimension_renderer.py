from io import BytesIO

import pytest
from PIL import Image
from pydantic import ValidationError

from wh_local.modules.product_processing.infrastructure.assets import (
    ProductProcessingAssets,
)
from wh_local.modules.product_processing.infrastructure.dimension_renderer import (
    DimensionAnnotation,
    DimensionRenderRequest,
    DimensionRenderer,
    _format_dimension,
)


def _source_bytes(size: tuple[int, int] = (1000, 1000)) -> bytes:
    source = Image.new("RGB", size, "white")
    buffer = BytesIO()
    source.save(buffer, format="PNG")
    return buffer.getvalue()


def _length_annotation(**overrides: object) -> DimensionAnnotation:
    values = {
        "key": "length",
        "value_cm": 10,
        "start": (0.15, 0.8),
        "end": (0.85, 0.8),
        "label": (0.5, 0.75),
    }
    values.update(overrides)
    return DimensionAnnotation.model_validate(values)


def test_renderer_outputs_crisp_2000_square() -> None:
    source = _source_bytes()
    request = DimensionRenderRequest(
        source_bytes=source,
        annotations=[_length_annotation()],
    )

    output = DimensionRenderer().render(request)
    rendered = Image.open(BytesIO(output.jpeg_bytes))

    assert rendered.size == (2000, 2000)
    assert output.width == 2000
    assert output.height == 2000
    assert output.content_hash
    assert len(output.master_png_bytes) > len(source)


@pytest.mark.parametrize(
    ("render_request", "error_code"),
    [
        (
            DimensionRenderRequest(source_bytes=_source_bytes(), annotations=[]),
            "dimension_annotations_empty",
        ),
        (
            DimensionRenderRequest(
                source_bytes=_source_bytes(),
                annotations=[_length_annotation(value_cm=0)],
            ),
            "dimension_value_invalid",
        ),
        (
            DimensionRenderRequest(
                source_bytes=_source_bytes(),
                annotations=[_length_annotation(label=(0.01, 0.5))],
            ),
            "dimension_label_outside_safe_margin",
        ),
    ],
)
def test_renderer_rejects_invalid_requests(
    render_request: DimensionRenderRequest, error_code: str
) -> None:
    with pytest.raises(ValueError, match=error_code):
        DimensionRenderer().render(render_request)


def test_dimension_asset_writes_are_content_addressed(tmp_path) -> None:
    assets = ProductProcessingAssets(tmp_path)

    first = assets.save_dimension_asset(
        b"same image", kind="master", suffix=".png", workspace_id="workspace-a"
    )
    second = assets.save_dimension_asset(
        b"same image", kind="master", suffix=".png", workspace_id="workspace-a"
    )
    other_workspace = assets.save_dimension_asset(
        b"same image", kind="master", suffix=".png", workspace_id="workspace-b"
    )

    assert first == second
    assert first != other_workspace
    assert first.read_bytes() == b"same image"
    assert first.parent.parent.name == "master"
    assert (
        assets.require_workspace_dimension_asset(
            str(first), workspace_id="workspace-a"
        )
        == first
    )
    with pytest.raises(ValueError, match="outside the workspace root"):
        assets.require_workspace_dimension_asset(
            str(first), workspace_id="workspace-b"
        )


def test_renderer_contract_rejects_client_url_strings() -> None:
    with pytest.raises(ValidationError):
        DimensionRenderRequest(
            source_bytes="https://example.com/untrusted.jpg",
            annotations=[_length_annotation()],
        )


def test_renderer_rejects_unapproved_source_format() -> None:
    source = Image.new("RGB", (20, 20), "white")
    buffer = BytesIO()
    source.save(buffer, format="BMP")
    request = DimensionRenderRequest(
        source_bytes=buffer.getvalue(), annotations=[_length_annotation()]
    )

    with pytest.raises(ValueError, match="dimension_source_format_invalid"):
        DimensionRenderer().render(request)


def test_renderer_formats_supported_display_units_from_canonical_centimeters() -> None:
    assert _format_dimension(30.48, "cm") == "30.48 cm"
    assert _format_dimension(30.48, "mm") == "304.8 mm"
    assert _format_dimension(30.48, "in") == "12 in"
    assert _format_dimension(30.48, "ft") == "1 ft"


def test_source_inspection_accepts_webp_and_reports_real_dimensions() -> None:
    source = Image.new("RGB", (321, 123), "white")
    buffer = BytesIO()
    source.save(buffer, format="WEBP")

    info = DimensionRenderer().inspect_source(buffer.getvalue())

    assert (info.width, info.height) == (321, 123)
    assert info.content_type == "image/webp"
    assert info.suffix == ".webp"
