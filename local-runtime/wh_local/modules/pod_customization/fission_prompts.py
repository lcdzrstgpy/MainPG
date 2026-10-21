"""Deterministic prompt compiler for POD fission variant plans.

The compiler receives only frozen facts and a selected variant.  Candidate pools,
registry IDs and model metadata stay outside the generated prompt; the provider
gets the concrete visual choices and the product rules it must obey.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


# 编译器版本：改动任一输出语句结构都必须递增（随变体计划冻结）。
# v3：A 的构图降级为参考；新增满印锁（edge-to-edge、正面+两侧连续、禁止成片空底），
#     去掉"按 A 的覆盖密度"这句反向指令。
FISSION_PROMPT_COMPILER_VERSION = "pod-fission-prompt-v3"
FISSION_DEFAULT_TEMPLATE_ID = "fission_default_v1"
FISSION_DEFAULT_TEMPLATE_VERSION = "1"

_TEXT_LIMIT = 400
_ITEM_LIMIT = 120
_UNSAFE_WHITESPACE = re.compile(r"[\r\n\t]+")
_QUOTES = re.compile(r"[\"'`]+")


def _safe(value: Any, *, limit: int = _TEXT_LIMIT) -> str:
    text = " ".join(str(value or "").split()).strip()
    text = _UNSAFE_WHITESPACE.sub(" ", text)
    text = _QUOTES.sub("", text)
    return text[:limit]


def _items(value: Any, *, limit: int = 12) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value[:limit]:
        text = _safe(item, limit=_ITEM_LIMIT)
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _field(mapping: Mapping[str, Any], name: str, default: Any = "") -> Any:
    value = mapping.get(name, default)
    if isinstance(value, Mapping) and "value" in value:
        return value.get("value", default)
    return value


def default_fission_composition() -> dict[str, Any]:
    return {
        "template_id": FISSION_DEFAULT_TEMPLATE_ID,
        "template_version": FISSION_DEFAULT_TEMPLATE_VERSION,
        "panels": [
            {"slot_id": "panel_1", "role": "primary_image", "shot": "wide lifestyle"},
            {"slot_id": "panel_2", "role": "pattern_detail", "shot": "macro detail"},
            {"slot_id": "panel_3", "role": "structure_or_usage", "shot": "three-quarter"},
            {"slot_id": "panel_4", "role": "material_image", "shot": "clean neutral"},
        ],
    }


def _composition_lines(composition: Mapping[str, Any]) -> list[str]:
    panels = composition.get("panels")
    if not isinstance(panels, Sequence) or isinstance(panels, (str, bytes, bytearray)):
        panels = default_fission_composition()["panels"]
    by_id: dict[str, Mapping[str, Any]] = {
        str(panel.get("slot_id")): panel
        for panel in panels
        if isinstance(panel, Mapping) and panel.get("slot_id")
    }
    lines: list[str] = []
    roles = (
        ("panel_1", "top-left"),
        ("panel_2", "top-right"),
        ("panel_3", "bottom-left"),
        ("panel_4", "bottom-right"),
    )
    for slot_id, position in roles:
        panel = by_id.get(slot_id, {})
        role = _safe(panel.get("role", ""), limit=100) or "ecommerce product image"
        shot = _safe(panel.get("shot", ""), limit=220) or "product view"
        lines.append(f"{slot_id} ({position}): role={role}; shot={shot}.")
    return lines


def compile_fission_prompt(
    plan: Mapping[str, Any] | Any,
    source_spec: Mapping[str, Any],
    target_spec: Mapping[str, Any],
    rules: Sequence[Any] | None,
    composition: Mapping[str, Any] | None,
    attempt: int = 1,
) -> str:
    """Compile one immutable fission prompt from a frozen plan.

    ``attempt=2`` preserves every selected variant field and only appends a
    consistency correction.  It never reassigns elements or colors.
    """
    if attempt not in {1, 2}:
        raise ValueError("attempt must be 1 or 2")
    plan_map: Mapping[str, Any]
    if isinstance(plan, Mapping):
        plan_map = plan
    elif hasattr(plan, "to_payload"):
        plan_map = plan.to_payload()
    else:
        plan_map = vars(plan)
    source = source_spec if isinstance(source_spec, Mapping) else {}
    target = target_spec if isinstance(target_spec, Mapping) else {}
    source_layout = _safe(_field(source, "artwork_composition"), limit=240)
    layout_phrase = source_layout or "match the arrangement and density of A"
    selected_rules = rules or plan_map.get("rules") or ()
    composition_map = composition if isinstance(composition, Mapping) and composition else default_fission_composition()
    primary = _items(plan_map.get("primary_elements"), limit=1)
    secondary = _items(plan_map.get("secondary_elements"), limit=2)
    accent = _items(plan_map.get("accent_elements"), limit=4)
    palette_primary = _safe(plan_map.get("palette_primary"), limit=100)
    palette_secondary = _safe(plan_map.get("palette_secondary"), limit=100)
    palette_accent = _safe(plan_map.get("palette_accent"), limit=100)
    printable = _items(_field(target, "printable_regions"), limit=20)
    non_printable = _items(_field(target, "non_printable_regions"), limit=20)
    product_details: list[str] = []
    for name, label in (
        ("inner_outer_structure", "Interior, exterior and lining"),
        ("openings_and_hardware", "Openings and hardware"),
        ("product_orientation", "Product orientation"),
        ("supported_usage_modes", "Supported usage"),
        ("occlusion_risk", "Occlusion boundaries"),
        ("manufacturing_process", "Manufacturing process"),
    ):
        value = _field(target, name)
        detail = ", ".join(_items(value, limit=20)) if isinstance(value, (list, tuple)) else _safe(value)
        if detail:
            product_details.append(f"{label}: {detail}.")
    rule_lines: list[str] = []
    for rule in selected_rules:
        if isinstance(rule, Mapping):
            values = rule.get("constraints") or rule.get("generation_constraints") or ()
        else:
            values = (rule,)
        for value in _items(values, limit=12):
            rule_lines.append(value)
    lines = [
        f"PROMPT COMPILER VERSION: {FISSION_PROMPT_COMPILER_VERSION}",
        "=== PART 1 · GLOBAL FISSION CONTRACT",
        "Create exactly one square 2x2 ecommerce contact sheet with exactly four equal panels.",
        "Reference image 1 is A, the artwork style and element source; reference image 2 is B, the target product.",
        "Treat A as a library of transferable style and artwork elements, never as a whole-image template: do not reproduce A's artwork as-is, and never copy A's product, people, room, props, labels, watermarks, or background.",
        "PART 3 defines the frozen variant of this design and overrides any reading of A as something to copy verbatim: when another line seems to ask for A's exact artwork, follow PART 3.",
        "All four panels show the same B product, the same selected variant, and one consistent design. Do not create extra products, logos, stickers, captions, or new text.",
        "=== PART 2 · TARGET PRODUCT FACTS AND HARD BOUNDARIES",
        f"Product: {_safe(_field(target, 'product_name')) or 'POD product'}; type: {_safe(_field(target, 'product_type'), limit=120) or 'product'}.",
        f"Components: {_safe(_field(target, 'component_count'), limit=30) or '1'}; structure: {_safe(_field(target, 'component_structure')) or 'preserve the reference product structure'}.",
        f"Material: {_safe(_field(target, 'material')) or 'preserve the reference material'}; surface: {_safe(_field(target, 'surface_texture')) or 'preserve the reference texture'}.",
        *product_details,
        f"Artwork may appear only on printable regions: {', '.join(printable) or 'only the explicitly printable product surface'}.",
        f"Never print across non-printable regions: {', '.join(non_printable) or 'seams, hardware, openings, lining, and every physically non-printable area'}.",
        "Preserve B's silhouette, proportions, component count, openings, hardware, orientation, and material. Do not substitute a different product.",
        *(["Deterministic product rules:", *[f"- {line}." for line in rule_lines]] if rule_lines else []),
        "=== PART 3 · FROZEN VARIANT AND FOUR-PANEL SHOT PLAN",
        f"Pattern layout: use A only as a reference for how these motifs are combined ({layout_phrase}); do not copy A's composition literally. Build a seamless all-over repeat of the selected motifs across the whole printable surface.",
        "ALL-OVER PRINT LOCK: cover every printable region edge to edge, and continue the print across the front panel and both side panels up to the zipper and the bottom seam, leaving only a thin non-printed margin at those seams. Natural thin spacing between motifs is fine, but large blank base-fabric areas, a single floating motif or an isolated centered emblem are failures.",
        "Any plain fabric or empty space surrounding the motifs in reference image 1 is NOT part of the design; never reproduce it as blank fabric, a blank band or a plain shell.",
        f"Primary motif (Tier 1 Hero, highest density): repeat {', '.join(primary) or 'the selected artwork anchor'} many times across the whole printable area as the dominant pattern, not a single one-off object.",
        f"Secondary elements (Tier 2 Narrative, medium density): repeat {', '.join(secondary) or 'none'} throughout the surface as supporting motifs.",
        f"Accent elements (Tier 3 Rhythm, lowest density): scatter {', '.join(accent) or 'none'} as small rhythm details between the hero motifs; never leave empty ground.",
        f"Color harmony is fixed at 60-30-10: primary 60%={palette_primary or 'primary palette role'}, secondary 30%={palette_secondary or 'secondary palette role'}, accent 10%={palette_accent or 'accent palette role'}.",
        "Build the design from these motifs and this palette as a seamless all-over repeat; the result must be recognizably this variant, not a reprint of A's original artwork.",
        "Keep the selected motifs and color roles unchanged in every panel; adapt only scale and perspective to B's geometry.",
        *_composition_lines(composition_map),
        "=== PART 4 · OUTPUT FORMAT AND FINAL PROHIBITIONS",
        "Fixed panel order: top-left panel_1 primary image; top-right panel_2 pattern detail; bottom-left panel_3 structure or usage; bottom-right panel_4 material image.",
        "Do not swap panel roles, crop away required product parts, add a second design, invent text, or expose internal candidate labels.",
        "Before returning the sheet, check that the design stays inside printable regions and the complete B product structure is visible where the panel role requires it.",
    ]
    if attempt == 2:
        lines.extend(
            (
                "RETRY ATTEMPT 2 OF 2",
                "Strengthen only consistency: keep the same product structure, same selected elements, same palette roles, and same four-panel order; correct omissions or boundary violations.",
            )
        )
    return "\n".join(line for line in lines if line)


__all__ = [
    "FISSION_DEFAULT_TEMPLATE_ID",
    "FISSION_DEFAULT_TEMPLATE_VERSION",
    "FISSION_PROMPT_COMPILER_VERSION",
    "compile_fission_prompt",
    "default_fission_composition",
]
