from __future__ import annotations

import io

from PIL import Image, ImageDraw

from wh_local.modules.pod_customization.images import (
    PatternQualityGate,
    split_grid_2x2,
)


def _png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def _pattern(primary: str, secondary: str = "#ffffff") -> bytes:
    image = Image.new("RGB", (128, 128), primary)
    draw = ImageDraw.Draw(image)
    for offset in range(0, 128, 16):
        draw.rectangle((offset, 0, min(offset + 7, 127), 127), fill=secondary)
    return _png(image)


def test_split_grid_returns_four_cells_in_reading_order() -> None:
    grid = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(grid)
    colors = ["#e63946", "#2a9d8f", "#457b9d", "#f4a261"]
    draw.rectangle((0, 0, 127, 127), fill=colors[0])
    draw.rectangle((128, 0, 255, 127), fill=colors[1])
    draw.rectangle((0, 128, 127, 255), fill=colors[2])
    draw.rectangle((128, 128, 255, 255), fill=colors[3])

    cells = split_grid_2x2(_png(grid))

    assert len(cells) == 4
    assert [Image.open(io.BytesIO(cell)).convert("RGB").getpixel((32, 32)) for cell in cells] == [
        (230, 57, 70),
        (42, 157, 143),
        (69, 123, 157),
        (244, 162, 97),
    ]


def test_quality_gate_rejects_invalid_blank_duplicate_and_visible_text_candidates() -> None:
    text_pattern = _pattern("#a832d1", "#f4ddff")

    def inspect_text(content: bytes) -> list[str]:
        pixel = Image.open(io.BytesIO(content)).convert("RGB").getpixel((10, 1))
        return ["SALE"] if pixel[0] > 120 and pixel[2] > 120 else []

    gate = PatternQualityGate(text_inspector=inspect_text)
    first = gate.assess(_pattern("#2244aa", "#dce6ff"), accepted_fingerprints=[])
    duplicate = gate.assess(_pattern("#2244aa", "#dce6ff"), accepted_fingerprints=[first.fingerprint])
    blank = gate.assess(_png(Image.new("RGB", (128, 128), "white")), accepted_fingerprints=[])
    text = gate.assess(text_pattern, accepted_fingerprints=[])
    invalid = gate.assess(b"not an image", accepted_fingerprints=[])

    assert first.accepted is True
    assert duplicate.rejection_reason == "duplicate"
    assert blank.rejection_reason == "invalid"
    assert text.rejection_reason == "text_error"
    assert invalid.rejection_reason == "invalid"
