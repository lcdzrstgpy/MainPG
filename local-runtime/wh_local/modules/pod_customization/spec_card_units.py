"""Derive POD spec-card display/export values from stored centimetre cells."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


SPEC_CARD_DISPLAY_UNIT_CM = "cm"
SPEC_CARD_DISPLAY_UNIT_IN = "in"
SPEC_CARD_DISPLAY_UNITS = (SPEC_CARD_DISPLAY_UNIT_CM, SPEC_CARD_DISPLAY_UNIT_IN)

_DIMENSION_HEADERS = ("Length", "Width", "Height")
_CANONICAL_HEADER = ("SKU", *_DIMENSION_HEADERS)
_INCHES_PER_CENTIMETRE = Decimal("2.54")
_DISPLAY_QUANTUM = Decimal("0.01")


def validate_spec_card_display_unit(unit: object) -> str:
    normalized = str(unit or SPEC_CARD_DISPLAY_UNIT_CM)
    if normalized not in SPEC_CARD_DISPLAY_UNITS:
        raise ValueError("规格卡单位必须是 cm 或 in")
    return normalized


def display_dimension_value(value: object, unit: str) -> str:
    """Return one dimension in the selected output unit without a unit suffix."""

    selected = validate_spec_card_display_unit(unit)
    text = str(value)
    if selected == SPEC_CARD_DISPLAY_UNIT_CM:
        return text
    try:
        centimetres = Decimal(text.strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("规格卡尺寸必须是大于 0 的有效数字") from exc
    if not centimetres.is_finite() or centimetres <= 0:
        raise ValueError("规格卡尺寸必须是大于 0 的有效数字")
    inches = (centimetres / _INCHES_PER_CENTIMETRE).quantize(
        _DISPLAY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    return format(inches, "f").rstrip("0").rstrip(".")


def display_spec_card_cells(
    cells: Sequence[Sequence[str]],
    unit: str,
) -> tuple[tuple[str, ...], ...]:
    """Build image cells in ``unit`` while preserving stored centimetre cells."""

    selected = validate_spec_card_display_unit(unit)
    rows = [list(row) for row in cells]
    if not rows:
        return ()
    # Historical cards could be arbitrary free-form tables. Unit conversion only
    # applies to the current fixed SKU/Length/Width/Height schema; preserving old
    # layouts avoids silently replacing their first row with dimension headers.
    if tuple(rows[0][:4]) != _CANONICAL_HEADER:
        return tuple(tuple(row) for row in rows)
    for offset, label in enumerate(_DIMENSION_HEADERS, start=1):
        if offset < len(rows[0]):
            rows[0][offset] = f"{label} ({selected})"
    for row in rows[1:]:
        for column in range(1, min(4, len(row))):
            row[column] = display_dimension_value(row[column], selected)
    return tuple(tuple(row) for row in rows)
