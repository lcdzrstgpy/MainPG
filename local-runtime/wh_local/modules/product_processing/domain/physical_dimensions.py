from __future__ import annotations

import re
import math
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field


DimensionProvenance = Literal[
    "source_confirmed",
    "manual_confirmed",
    "unconfirmed",
    "package_estimate",
]
DimensionAxis = Literal["length", "width", "height"]

_NUMBER = r"\d+(?:\.\d+)?"
_UNIT = r"mm|cm|毫米|厘米"
_TRIPLE = re.compile(
    rf"({_NUMBER})\s*({_UNIT})?\s*[xX*×]\s*"
    rf"({_NUMBER})\s*({_UNIT})?\s*[xX*×]\s*"
    rf"({_NUMBER})\s*({_UNIT})?",
    re.I,
)
_SINGLE = re.compile(rf"^\s*({_NUMBER})\s*({_UNIT})?\s*$", re.I)
_KEY_UNIT = re.compile(rf"(?:\(|（|\[)?\s*({_UNIT})\s*(?:\)|）|\])?", re.I)
_AXIS_TOKEN = re.compile(
    r"length|width|height|长度|宽度|高度|长|宽|高", re.I
)
_PRODUCT_MARKERS = ("产品", "商品", "成品", "product", "item")
_PACKAGE_MARKERS = ("包装", "包裹", "外箱", "package", "carton", "shipping")
# 无产品/商品前缀但明确是尺寸的键（TEMU/1688 属性常直接写「尺寸」「长」「宽」「高」）。
_DIMENSION_KEY_MARKERS = ("尺寸", "规格", "大小", "长", "宽", "高", "dimension", "size")


class DimensionValue(BaseModel):
    value_cm: float | None = None
    provenance: DimensionProvenance = "unconfirmed"
    evidence_ref: str = ""

    @property
    def drawable(self) -> bool:
        return (
            self.value_cm is not None
            and self.value_cm > 0
            and self.provenance in {"source_confirmed", "manual_confirmed"}
        )


class PhysicalDimensions(BaseModel):
    length: DimensionValue = Field(default_factory=DimensionValue)
    width: DimensionValue = Field(default_factory=DimensionValue)
    height: DimensionValue = Field(default_factory=DimensionValue)
    conflict: bool = False

    @property
    def drawable(self) -> bool:
        """Whether at least one independently trusted dimension can be annotated."""

        return any(item.drawable for item in self.values())

    @property
    def drawable_fields(self) -> tuple[DimensionAxis, ...]:
        return tuple(
            axis
            for axis, item in zip(
                ("length", "width", "height"), self.values(), strict=True
            )
            if item.drawable
        )

    def values(self) -> tuple[DimensionValue, DimensionValue, DimensionValue]:
        return self.length, self.width, self.height


def extract_physical_dimensions(raw: dict[str, Any]) -> PhysicalDimensions:
    """Extract only field-labelled, unit-bearing product-body dimensions.

    A triple is mapped only when its axis order is explicit. Generic triples, title
    text and image-derived guesses remain unconfirmed. Package evidence is retained
    per field but never becomes drawable.
    """

    attributes = raw.get("source_attributes") or {}
    if not isinstance(attributes, dict):
        return PhysicalDimensions()

    candidates: dict[DimensionAxis, list[DimensionValue]] = {
        "length": [],
        "width": [],
        "height": [],
    }
    for raw_key, raw_value in attributes.items():
        key = str(raw_key).strip()
        key_text = key.casefold()
        provenance = _key_provenance(key_text)
        if provenance is None:
            continue
        evidence_ref = f"source_attributes.{raw_key}"
        value_text = str(raw_value or "").strip()

        triple = _TRIPLE.fullmatch(value_text)
        if triple:
            axes = _axis_order(f"{key_text} {value_text.casefold()}")
            values_cm = _triple_values_cm(triple)
            if len(axes) != 3 or len(set(axes)) != 3 or values_cm is None:
                continue
            for axis, value_cm in zip(axes, values_cm, strict=True):
                candidates[axis].append(
                    DimensionValue(
                        value_cm=value_cm,
                        provenance=provenance,
                        evidence_ref=evidence_ref,
                    )
                )
            continue

        axis = _single_axis(key_text)
        single = _SINGLE.fullmatch(value_text)
        if axis is None or single is None:
            continue
        unit = single.group(2) or _unit_from_key(key_text)
        if not unit:
            continue
        candidates[axis].append(
            DimensionValue(
                value_cm=_to_centimeters(float(single.group(1)), unit),
                provenance=provenance,
                evidence_ref=evidence_ref,
            )
        )

    resolved: dict[DimensionAxis, DimensionValue] = {}
    conflict = False
    for axis, values in candidates.items():
        product_values = [
            item for item in values if item.provenance == "source_confirmed"
        ]
        preferred = product_values or values
        distinct = {item.value_cm for item in preferred}
        if len(distinct) > 1:
            resolved[axis] = DimensionValue()
            conflict = True
        elif preferred:
            resolved[axis] = preferred[0]
        else:
            resolved[axis] = DimensionValue()

    return PhysicalDimensions(
        length=resolved["length"],
        width=resolved["width"],
        height=resolved["height"],
        conflict=conflict,
    )


def prefill_physical_dimensions(result: dict[str, Any]) -> PhysicalDimensions:
    """Return canvas fields with processing-table estimates visible but untrusted.

    Explicit product-body evidence remains authoritative.  The product-processing
    table's ``product_dimensions`` values are useful as an editing starting point,
    but most of them are packaging/AI estimates, so they must be confirmed by the
    user before a dimension line can be rendered.
    """

    try:
        current = PhysicalDimensions.model_validate(result.get("physical_dimensions") or {})
    except (TypeError, ValueError):
        current = PhysicalDimensions()
    estimates = result.get("product_dimensions") or {}
    if not isinstance(estimates, dict):
        return current
    source = str(estimates.get("source") or "processing_table")
    values: dict[DimensionAxis, DimensionValue] = {}
    for axis in ("length", "width", "height"):
        existing = getattr(current, axis)
        if existing.value_cm is not None or existing.evidence_ref:
            values[axis] = existing
            continue
        try:
            candidate = float(estimates.get(f"{axis}_cm"))
        except (TypeError, ValueError):
            candidate = 0
        if not math.isfinite(candidate) or candidate <= 0:
            values[axis] = existing
            continue
        values[axis] = DimensionValue(
            value_cm=candidate,
            provenance="package_estimate",
            evidence_ref=f"product_dimensions.{axis}_cm:{source}",
        )
    return PhysicalDimensions(
        length=values["length"],
        width=values["width"],
        height=values["height"],
        conflict=current.conflict,
    )


def _key_provenance(key_text: str) -> DimensionProvenance | None:
    if any(marker in key_text for marker in _PACKAGE_MARKERS):
        return "package_estimate"
    if any(marker in key_text for marker in _PRODUCT_MARKERS):
        return "source_confirmed"
    # 无产品/商品前缀但明确是尺寸的键：当作来源证据，仍需带轴与单位才能上画布。
    if any(marker in key_text for marker in _DIMENSION_KEY_MARKERS):
        return "source_confirmed"
    return None


def _axis_order(text: str) -> tuple[DimensionAxis, ...]:
    return tuple(_axis_name(match.group(0)) for match in _AXIS_TOKEN.finditer(text))


def _single_axis(key_text: str) -> DimensionAxis | None:
    axes = _axis_order(key_text)
    return axes[0] if len(axes) == 1 else None


def _axis_name(token: str) -> DimensionAxis:
    token = token.casefold()
    if token in {"length", "长度", "长"}:
        return "length"
    if token in {"width", "宽度", "宽"}:
        return "width"
    return "height"


def _triple_values_cm(match: re.Match[str]) -> tuple[float, float, float] | None:
    numbers = (float(match.group(1)), float(match.group(3)), float(match.group(5)))
    units = (match.group(2), match.group(4), match.group(6))
    present_units = [unit for unit in units if unit]
    if not present_units:
        return None
    if len(present_units) == 1 and units[2]:
        effective_units: Iterable[str] = (units[2], units[2], units[2])
    elif all(units):
        effective_units = (str(units[0]), str(units[1]), str(units[2]))
    else:
        return None
    return tuple(
        _to_centimeters(number, unit)
        for number, unit in zip(numbers, effective_units, strict=True)
    )


def _unit_from_key(key_text: str) -> str:
    match = _KEY_UNIT.search(key_text)
    return match.group(1) if match else ""


def _to_centimeters(value: float, unit: str) -> float:
    return value * 0.1 if unit.casefold() in {"mm", "毫米"} else value
