from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from wh_local.modules.product_processing.infrastructure.grid_layout import (
    GRID_DIVIDER_RGB,
    GridLayoutError,
    GridSplitGuides,
    build_grid_scaffold,
    extract_grid_panels,
    locate_split_guides,
    validate_panel_independence,
)


def _png(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _grid(*, x_start: int = 1016, y_start: int = 1016, extra_vertical: int | None = None) -> bytes:
    image = Image.new("RGB", (2048, 2048), (210, 30, 30))
    image.paste((30, 180, 50), (x_start + 16, 0, 2048, y_start))
    image.paste((30, 70, 210), (0, y_start + 16, x_start, 2048))
    image.paste((220, 180, 30), (x_start + 16, y_start + 16, 2048, 2048))
    image.paste(GRID_DIVIDER_RGB, (x_start, 0, x_start + 16, 2048))
    image.paste(GRID_DIVIDER_RGB, (0, y_start, 2048, y_start + 16))
    if extra_vertical is not None:
        image.paste(GRID_DIVIDER_RGB, (extra_vertical, 0, extra_vertical + 8, 2048))
    return _png(image)


def test_build_grid_scaffold_has_fixed_canvas_and_safe_dividers() -> None:
    source = Image.new("RGB", (640, 480), (190, 30, 30))
    scaffold = build_grid_scaffold(_png(source))
    with Image.open(BytesIO(scaffold)) as image:
        assert image.size == (2048, 2048)
        assert image.convert("RGB").getpixel((1024, 100)) == GRID_DIVIDER_RGB
        assert image.convert("RGB").getpixel((100, 1024)) == GRID_DIVIDER_RGB
        # The source is fitted inside each panel and never touches the 10% safe margin.
        assert image.convert("RGB").getpixel((40, 40)) != (190, 30, 30)
        assert image.convert("RGB").getpixel((512, 512)) == (190, 30, 30)


def test_exact_grid_uses_fixed_guides_and_extracts_four_squares() -> None:
    content = _grid()
    guides = locate_split_guides(content)
    assert guides == GridSplitGuides(1016, 1032, 1016, 1032, "fixed")
    panels = extract_grid_panels(content, guides)
    assert [panel.size for panel in panels] == [(1016, 1016)] * 4


def test_small_center_shift_uses_adaptive_guides() -> None:
    content = _grid(x_start=1000, y_start=1038)
    guides = locate_split_guides(content)
    assert guides == GridSplitGuides(1000, 1016, 1038, 1054, "adaptive")
    assert len(extract_grid_panels(content, guides)) == 4


def test_far_shift_is_rejected_instead_of_guessed() -> None:
    with pytest.raises(GridLayoutError, match="adaptive corridor"):
        locate_split_guides(_grid(x_start=900, y_start=900))


def test_multiple_divider_candidates_are_rejected() -> None:
    with pytest.raises(GridLayoutError, match="multiple divider"):
        locate_split_guides(_grid(extra_vertical=990))


def test_panel_with_internal_long_divider_is_rejected() -> None:
    panel = Image.new("RGB", (1016, 1016), (45, 80, 120))
    panel.paste((244, 244, 244), (164, 0, 170, 1016))
    with pytest.raises(GridLayoutError, match="internal divider"):
        validate_panel_independence(panel)


def test_normal_panel_with_product_edges_is_not_rejected() -> None:
    panel = Image.new("RGB", (1016, 1016), (235, 232, 225))
    panel.paste((60, 82, 100), (220, 220, 780, 780))
    validate_panel_independence(panel)
