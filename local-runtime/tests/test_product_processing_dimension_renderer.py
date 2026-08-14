from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageFont
from pydantic import ValidationError

from wh_local.modules.product_processing.infrastructure.assets import (
    ProductProcessingAssets,
)
from wh_local.modules.product_processing.infrastructure.dimension_renderer import (
    DimensionAnnotation,
    DimensionRenderRequest,
    DimensionRenderer,
    _FONT_PATH,
    _fit_label_inside_safe_margin,
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


def test_renderer_applies_thin_and_thick_line_presets() -> None:
    def rendered_line_depth(line_width: str) -> int:
        output = DimensionRenderer().render(
            DimensionRenderRequest(
                source_bytes=_source_bytes(),
                annotations=[_length_annotation(line_width=line_width)],
            )
        )
        rendered = Image.open(BytesIO(output.master_png_bytes)).convert("RGB")
        return sum(rendered.getpixel((1000, y)) != (255, 255, 255) for y in range(1570, 1631))

    assert rendered_line_depth("thick") > rendered_line_depth("thin")


def test_renderer_rejects_unknown_line_width() -> None:
    with pytest.raises(ValidationError):
        _length_annotation(line_width="extra-thick")


def test_renderer_draws_gray_dashed_annotation_with_visible_gaps() -> None:
    output = DimensionRenderer().render(
        DimensionRenderRequest(
            source_bytes=_source_bytes(),
            annotations=[_length_annotation(style="gray_dashed")],
        )
    )
    rendered = Image.open(BytesIO(output.master_png_bytes)).convert("RGB")
    samples = [rendered.getpixel((x, 1600)) for x in range(400, 1600)]

    assert any(pixel == (123, 135, 148) for pixel in samples)
    assert any(pixel == (255, 255, 255) for pixel in samples)


def test_renderer_nudges_edge_label_inside_safe_margin() -> None:
    request = DimensionRenderRequest(
        source_bytes=_source_bytes(),
        annotations=[
            _length_annotation(
                value_cm=13,
                start=(0.94, 0.36),
                end=(0.94, 0.74),
                label=(0.9443, 0.50),
            )
        ],
    )

    output = DimensionRenderer().render(request)
    rendered = Image.open(BytesIO(output.jpeg_bytes))

    assert rendered.size == (2000, 2000)
    assert output.content_hash


@pytest.mark.parametrize(
    "label",
    [
        (0.0, 0.5),
        (1.0, 0.5),
        (0.5, 0.0),
        (0.5, 1.0),
        (0.0, 0.0),
        (1.0, 1.0),
    ],
)
def test_renderer_accepts_labels_at_canvas_edges(
    label: tuple[float, float],
) -> None:
    output = DimensionRenderer().render(
        DimensionRenderRequest(
            source_bytes=_source_bytes(),
            annotations=[_length_annotation(label=label)],
        )
    )

    assert output.width == 2000
    assert output.height == 2000
    assert output.content_hash


def test_label_fitting_preserves_center_and_clamps_edge() -> None:
    image = Image.new("RGB", (2000, 2000), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(_FONT_PATH), 88)

    centered = _fit_label_inside_safe_margin(
        draw,
        (1000, 1000),
        "10 cm",
        font=font,
        stroke_width=5,
        size=2000,
    )
    edge = _fit_label_inside_safe_margin(
        draw,
        (0, 1000),
        "10 cm",
        font=font,
        stroke_width=5,
        size=2000,
    )

    assert centered == (1000, 1000)
    assert 0 < edge[0] < 1000
    assert edge[1] == 1000


@pytest.mark.parametrize(
    "label",
    [(-0.001, 0.5), (1.001, 0.5), (float("nan"), 0.5), (float("inf"), 0.5)],
)
def test_renderer_rejects_labels_outside_normalized_canvas(
    label: tuple[float, float],
) -> None:
    request = DimensionRenderRequest(
        source_bytes=_source_bytes(),
        annotations=[_length_annotation(label=label)],
    )

    with pytest.raises(ValueError, match="dimension_coordinate_invalid"):
        DimensionRenderer().render(request)


def test_label_fitting_rejects_text_larger_than_canvas() -> None:
    class OversizedDraw:
        @staticmethod
        def textbbox(*_args, **_kwargs) -> tuple[int, int, int, int]:
            return (-1, 0, 2100, 100)

    font = ImageFont.truetype(str(_FONT_PATH), 88)

    with pytest.raises(ValueError, match="dimension_label_outside_safe_margin"):
        _fit_label_inside_safe_margin(
            OversizedDraw(),
            (1000, 1000),
            "oversized",
            font=font,
            stroke_width=5,
            size=2000,
        )


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
