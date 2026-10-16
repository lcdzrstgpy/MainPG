"""Deterministic fission variant planning and dynamic registry matching."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .contracts import MAX_FISSION_STYLES, MIN_FISSION_STYLES
from .fission_prompts import (
    default_fission_composition,
    compile_fission_prompt,
)
from .prompts import assign_style_colors, assign_style_elements, split_element_keywords
from .replica_capabilities import match_capabilities
from .replica_planner import derive_rules
from .replica_specs import PresentationPreferences, normalize_target_analysis
from .replica_specs import normalize_source_analysis


class FissionPlanningError(ValueError):
    """The requested count cannot be represented by unique deterministic plans."""


# 复刻的硬约束里有两条与裂变的「受控变体」语义正好相反：它要求照抄 A 的主体与配色、
# 并禁止新增颜色。它们被 derive_rules 原样搬进裂变提示词后，会直接压掉 PART 3 的
# 按款轮换（实测 12 款生图几乎同图），因此裂变必须把它排除并按下面的改写版替代。
FISSION_EXCLUDED_CONSTRAINTS = frozenset(
    {
        "只能复制原素材图 A 的图案内容（主体、配色、排布关系）。",
        "不能新增图案元素、颜色、文字、Logo 或贴纸。",
    }
)
# 改写版：禁止「本款未指定」的新增，从而允许 PART 3 轮换出来的风格/元素/配色生效。
FISSION_VARIANT_CONSTRAINT = "除本款 PART 3 指定的风格、元素与配色外，不得新增其他图案元素、颜色、文字、Logo 或贴纸。"


@dataclass(frozen=True)
class FissionVariantPlan:
    style_index: int
    style_family: str
    primary_elements: tuple[str, ...]
    secondary_elements: tuple[str, ...]
    accent_elements: tuple[str, ...]
    palette_primary: str
    palette_secondary: str
    palette_accent: str
    composition_template_id: str
    composition_template_version: str
    capability_registry_version: int
    rule_registry_version: int
    combination_fingerprint: str
    capability_ids: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    prompt_snapshot: str = ""

    @property
    def fingerprint(self) -> str:
        return self.combination_fingerprint

    def to_payload(self) -> dict[str, Any]:
        return {
            "style_index": self.style_index,
            "style_family": self.style_family,
            "primary_elements": list(self.primary_elements),
            "secondary_elements": list(self.secondary_elements),
            "accent_elements": list(self.accent_elements),
            "palette_primary": self.palette_primary,
            "palette_secondary": self.palette_secondary,
            "palette_accent": self.palette_accent,
            "composition_template_id": self.composition_template_id,
            "composition_template_version": self.composition_template_version,
            "capability_registry_version": self.capability_registry_version,
            "rule_registry_version": self.rule_registry_version,
            "combination_fingerprint": self.combination_fingerprint,
            "fingerprint": self.combination_fingerprint,
            "capability_ids": list(self.capability_ids),
            "rules": list(self.rules),
            "prompt_snapshot": self.prompt_snapshot,
        }


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _pool(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = _text(item)
        key = token.casefold()
        if token and key not in seen:
            result.append(token)
            seen.add(key)
    return result


def _int_version(snapshot: Mapping[str, Any], *names: str) -> int:
    for name in names:
        value = snapshot.get(name)
        if isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _target_value(target: Mapping[str, Any], field: str) -> Any:
    value = target.get(field)
    if isinstance(value, Mapping) and "value" in value:
        return value.get("value")
    return value


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (list, tuple, set)):
        return any(_text(item).casefold() == _text(expected).casefold() for item in actual)
    return _text(expected).casefold() in _text(actual).casefold()


def _predicate_matches(predicate: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    field = str(predicate.get("field") or predicate.get("fact_field") or "")
    operator = str(predicate.get("operator") or "")
    expected = predicate.get("value")
    if not field:
        return False
    actual = _target_value(target, field)
    if operator == "exists":
        return bool(actual) is bool(expected if expected is not None else True)
    if operator == "eq":
        if isinstance(actual, bool) or isinstance(expected, bool):
            return actual == expected
        return _text(actual).casefold() == _text(expected).casefold()
    if operator == "contains":
        return _contains(actual, expected)
    if operator == "in":
        return any(_predicate_matches({"field": field, "operator": "eq", "value": item}, target) for item in (expected or []))
    if operator in {"gt", "gte"}:
        try:
            return actual > expected if operator == "gt" else actual >= expected
        except TypeError:
            return False
    return False


def _dynamic_registry_matches(analysis: Mapping[str, Any], registry: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    source = analysis.get("source_spec") if isinstance(analysis.get("source_spec"), Mapping) else {}
    target = analysis.get("target_spec") if isinstance(analysis.get("target_spec"), Mapping) else {}
    facts: dict[str, Any] = dict(source)
    facts.update(target)
    facts.setdefault("artwork_motifs", source.get("artwork_elements", source.get("artwork_motifs", ())))
    facts.setdefault("artwork_layout", source.get("artwork_composition", source.get("artwork_layout", "")))
    template_id = _text(registry.get("composition_template_id"))
    composition = analysis.get("composition_snapshot")
    if isinstance(composition, Mapping):
        template_id = _text(composition.get("template_id")) or template_id
    # Composition is passed separately to the planner, so callers may attach its ID
    # to the registry snapshot used for dynamic candidate evaluation.
    capability_ids: list[str] = []
    capability_by_id: dict[str, Mapping[str, Any]] = {}
    capabilities = registry.get("capabilities", ())
    capability_candidates = [item for item in capabilities if isinstance(item, Mapping)] if isinstance(capabilities, Sequence) else []
    mappings = registry.get("product_mappings", ())
    product_type = _text(_target_value(facts, "product_type")).casefold()
    mapped_ids: set[str] = set()
    for mapping in mappings if isinstance(mappings, Sequence) else ():
        if isinstance(mapping, Mapping) and _text(mapping.get("product_type")).casefold() == product_type:
            mapped_ids.update(str(value) for value in mapping.get("capability_ids", ()) if str(value))

    for item in capability_candidates:
        candidate_id = str(item.get("candidate_id") or item.get("capability_id") or "").strip()
        required_facts = item.get("required_facts") or ()
        required_present = all(bool(_target_value(facts, str(field))) for field in required_facts)
        conditions = item.get("activation_conditions") or ()
        conditions_match = bool(conditions) and all(
            isinstance(condition, Mapping) and _predicate_matches(condition, facts)
            for condition in conditions
        )
        mapped = candidate_id in mapped_ids
        compatible_templates = _pool(item.get("compatible_templates"))
        template_compatible = not compatible_templates or (template_id and template_id in compatible_templates)
        if (
            candidate_id
            and item.get("generation_strategy", "grid") == "grid"
            and required_present
            and template_compatible
            and (conditions_match or (mapped and not conditions))
        ):
            capability_ids.append(candidate_id)
            capability_by_id[candidate_id] = item
    # Remove every participant in an incompatibility pair, independent of registry order.
    incompatible: set[str] = set()
    active = set(capability_ids)
    for candidate_id in active:
        conflicts = {str(value) for value in capability_by_id[candidate_id].get("incompatible_capabilities", ())}
        if conflicts & active:
            incompatible.add(candidate_id)
            incompatible.update(conflicts & active)
    capability_ids = [candidate_id for candidate_id in capability_ids if candidate_id not in incompatible]
    rule_text: list[str] = []
    for item in registry.get("rules", ()) if isinstance(registry.get("rules", ()), Sequence) else ():
        if not isinstance(item, Mapping):
            continue
        capabilities = {str(value) for value in item.get("capability_ids", ())}
        predicate = item.get("predicate")
        if isinstance(predicate, Mapping) and _predicate_matches(predicate, facts) and (not capabilities or capabilities <= set(capability_ids)):
            constraints = item.get("constraints") or item.get("generation_constraints") or ()
            rule_text.extend(_pool(constraints))
    for candidate_id in capability_ids:
        candidate = capability_by_id[candidate_id]
        rule_text.extend(_pool(candidate.get("generated_constraints") or ()))
        rule_text.extend(_pool(candidate.get("forbidden_conditions") or ()))
    return tuple(sorted(set(capability_ids))), tuple(sorted(set(rule_text)))


def _public_capabilities(analysis: Mapping[str, Any]) -> tuple[str, ...]:
    target = analysis.get("target_spec")
    if not isinstance(target, Mapping):
        return ()
    # normalize_target_analysis accepts plain values and supplies safe defaults for fields
    # that the fission analyzer intentionally does not expose in its short result.
    normalized = normalize_target_analysis(dict(target), "fission-target")
    return tuple(match_capabilities(normalized, PresentationPreferences()).capabilities)


def _public_rules(analysis: Mapping[str, Any], public_capabilities: Sequence[str]) -> tuple[str, ...]:
    source_raw = analysis.get("source_spec") if isinstance(analysis.get("source_spec"), Mapping) else {}
    target_raw = analysis.get("target_spec") if isinstance(analysis.get("target_spec"), Mapping) else {}
    source = normalize_source_analysis(
        {
            "source_product_name": "",
            "artwork_motifs": source_raw.get("artwork_elements", ()),
            "artwork_colors": (),
            "artwork_layout": source_raw.get("artwork_composition", ""),
            "artwork_density": "",
            "artwork_repeat_pattern": "",
            "artwork_text": source_raw.get("artwork_text", ""),
            "artwork_print_region": "",
            "source_product_structure": "",
            "source_background_elements": (),
            "source_people_and_props": (),
            "source_watermarks_and_labels": (),
        },
        "fission-source",
    )
    target = normalize_target_analysis(dict(target_raw), "fission-target")
    capabilities = match_capabilities(target, PresentationPreferences())
    # Keep registry-derived IDs out of the replica rule derivation; learned constraints
    # are matched and added separately from the dynamic registry DSL.
    derived = derive_rules(source, target, capabilities, PresentationPreferences())
    _ = public_capabilities
    # 剔除复刻专属的「照抄 A / 禁止新增颜色」两条，再补上裂变自己的变体约束，
    # 否则 PART 3 的按款轮换会被这两条硬约束推翻。
    kept = (
        line
        for line in (*derived.hard_constraints, *derived.generation_constraints)
        if line not in FISSION_EXCLUDED_CONSTRAINTS
    )
    return tuple(dict.fromkeys((*kept, FISSION_VARIANT_CONSTRAINT)))


def _fingerprint(style: str, elements: Sequence[str], palette: Sequence[str]) -> str:
    canonical = json.dumps(
        {
            "style": _text(style).casefold(),
            "elements": sorted({_text(item).casefold() for item in elements if _text(item)}),
            "palette": [_text(item).casefold() for item in palette],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _remap_legacy_arguments(
    count: Any,
    seed: Any,
    registry_snapshot: Any,
    composition: Any,
    requested_count: Any,
    batch_id: Any,
    composition_snapshot: Any,
) -> tuple[int, str, Mapping[str, Any], Mapping[str, Any] | None]:
    # The implementation plan used (analysis, registry, composition, count, batch_id),
    # while service adapters use (analysis, count, seed, registry, composition). Support
    # both forms so persisted callers can migrate without changing semantics.
    if isinstance(count, Mapping):
        legacy_registry, legacy_composition, legacy_count, legacy_batch = count, seed, registry_snapshot, composition
        count, seed, registry_snapshot, composition = legacy_count, legacy_batch, legacy_registry, legacy_composition
    if requested_count is not None:
        count = requested_count
    if batch_id is not None:
        seed = batch_id
    if composition_snapshot is not None:
        composition = composition_snapshot
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not MIN_FISSION_STYLES <= count <= MAX_FISSION_STYLES
    ):
        raise FissionPlanningError(
            f"requested_count must be between {MIN_FISSION_STYLES} and {MAX_FISSION_STYLES}"
        )
    if not _text(seed):
        raise FissionPlanningError("batch seed is required")
    registry = registry_snapshot if isinstance(registry_snapshot, Mapping) else {}
    comp = composition if isinstance(composition, Mapping) and composition else default_fission_composition()
    return count, _text(seed), registry, comp


def plan_fission_variants(
    analysis: Mapping[str, Any],
    count: int | Mapping[str, Any] | None = None,
    seed: str | Mapping[str, Any] | None = None,
    registry_snapshot: Mapping[str, Any] | int | None = None,
    composition: Mapping[str, Any] | str | None = None,
    *,
    requested_count: int | None = None,
    batch_id: str | None = None,
    composition_snapshot: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build deterministic, unique variant plans and freeze their prompt snapshots."""
    if not isinstance(analysis, Mapping):
        raise FissionPlanningError("analysis must be a mapping")
    requested, stable_seed, registry, composition_map = _remap_legacy_arguments(
        count, seed, registry_snapshot, composition, requested_count, batch_id, composition_snapshot
    )
    styles = _pool(analysis.get("style_vocabulary"))
    elements = split_element_keywords(_pool(analysis.get("element_vocabulary")))
    palettes = _pool(analysis.get("palette_vocabulary"))
    if not styles or not elements or not palettes:
        raise FissionPlanningError("analysis candidate pools must not be empty")
    registry_for_matching = dict(registry)
    registry_for_matching.setdefault("composition_template_id", composition_map.get("template_id"))
    dynamic_caps, dynamic_rules = _dynamic_registry_matches(analysis, registry_for_matching)
    common_capabilities = _public_capabilities(analysis)
    capability_ids = tuple(dict.fromkeys((*common_capabilities, *dynamic_caps)))
    all_rules = tuple(dict.fromkeys((*_public_rules(analysis, common_capabilities), *dynamic_rules)))
    cap_version = _int_version(registry, "capability_registry_version", "capability_version")
    rule_version = _int_version(registry, "rule_registry_version", "rule_version")
    used: set[str] = set()
    results: list[dict[str, Any]] = []
    # Include a salt block in the existing Tier/color helpers. This preserves their
    # 60/30/10 and Hero/Narrative/Rhythm semantics while extending beyond one period.
    attempts = max(100, requested * 30)
    for ordinal in range(attempts):
        if len(results) == requested:
            break
        style_index = len(results) + 1
        block = ordinal // max(1, len(styles))
        family = styles[(ordinal + style_index - 1) % len(styles)]
        salt = f"{stable_seed}:{block}"
        element_assignment = assign_style_elements(elements, (ordinal % len(elements)) + 1, salt)
        color_assignment = assign_style_colors(palettes, (ordinal % len(palettes)) + 1, salt)
        primary = tuple(_pool([element_assignment.get("hero", "")]))
        secondary = tuple(_pool(element_assignment.get("narrative", [])))
        accent = tuple(_pool(element_assignment.get("rhythm", [])))
        selected_elements = (*primary, *secondary, *accent)
        palette = (
            _text(color_assignment.get("base")),
            _text(color_assignment.get("structural")),
            _text(color_assignment.get("highlight")),
        )
        # A one-color palette is legal input but cannot provide three distinct roles;
        # preserve the color and let the prompt state the fallback role explicitly.
        if not palette[0]:
            palette = (palettes[0], palettes[0], palettes[0])
        elif not palette[1]:
            palette = (palette[0], palette[0], palette[0])
        elif not palette[2]:
            palette = (palette[0], palette[1], palette[0])
        fingerprint = _fingerprint(family, selected_elements, palette)
        if fingerprint in used:
            continue
        used.add(fingerprint)
        variant = FissionVariantPlan(
            style_index=style_index,
            style_family=family,
            primary_elements=primary,
            secondary_elements=secondary,
            accent_elements=accent,
            palette_primary=palette[0],
            palette_secondary=palette[1],
            palette_accent=palette[2],
            composition_template_id=_text(composition_map.get("template_id")) or "fission_default_v1",
            composition_template_version=_text(composition_map.get("template_version")) or "1",
            capability_registry_version=cap_version,
            rule_registry_version=rule_version,
            combination_fingerprint=fingerprint,
            capability_ids=capability_ids,
            rules=all_rules,
        )
        payload = variant.to_payload()
        payload["prompt_snapshot"] = compile_fission_prompt(
            payload,
            analysis.get("source_spec") if isinstance(analysis.get("source_spec"), Mapping) else {},
            analysis.get("target_spec") if isinstance(analysis.get("target_spec"), Mapping) else {},
            all_rules,
            composition_map,
            attempt=1,
        )
        results.append(payload)
    if len(results) != requested:
        raise FissionPlanningError(
            f"requested {requested} variants exceeds the available unique combination space ({len(results)})"
        )
    return results


__all__ = ["FissionPlanningError", "FissionVariantPlan", "plan_fission_variants"]
