from __future__ import annotations

from typing import Any


DEFAULT_SLOT_IDS = (
    "carousel.hero",
    "carousel.detail",
    "carousel.lifestyle",
    "carousel.dimension_background",
)


def base_image_slots(result: dict[str, Any]) -> list[dict[str, str]]:
    manifest = result.get("image_manifest")
    if isinstance(manifest, list) and manifest:
        slots: list[dict[str, str]] = []
        for raw_entry in manifest:
            if not isinstance(raw_entry, dict):
                continue
            slot_id = str(raw_entry.get("slot_id") or "").strip()
            value = str(raw_entry.get("value") or "").strip()
            if not slot_id or not value:
                continue
            entry = {"slot_id": slot_id, "value": value}
            role = str(raw_entry.get("role") or "").strip()
            if role:
                entry["role"] = role
            slots.append(entry)
        if slots:
            return slots

    values = [
        str(value).strip()
        for value in result.get("carousel_image_paths") or []
        if str(value or "").strip()
    ]
    return [
        {
            "slot_id": (
                DEFAULT_SLOT_IDS[index]
                if index < len(DEFAULT_SLOT_IDS)
                else f"carousel.extra.{index + 1}"
            ),
            "value": value,
        }
        for index, value in enumerate(values)
    ]


def apply_slot_overrides(
    result: dict[str, Any], preview_overrides: dict[str, Any]
) -> list[dict[str, str]]:
    preview_overrides = (
        preview_overrides if isinstance(preview_overrides, dict) else {}
    )
    legacy = preview_overrides.get("carousel_images")
    if isinstance(legacy, list) and legacy:
        legacy_result = dict(result)
        # The legacy full-array edit is the baseline even when a generated manifest
        # exists; semantic patches are deliberately applied after this mapping.
        legacy_result["image_manifest"] = []
        legacy_result["carousel_image_paths"] = legacy
        slots = base_image_slots(legacy_result)
    else:
        slots = base_image_slots(result)

    patches = preview_overrides.get("image_slot_overrides") or {}
    if not isinstance(patches, dict):
        return slots
    by_id = {str(slot["slot_id"]): slot for slot in slots}
    for raw_slot_id, patch in patches.items():
        if not isinstance(patch, dict):
            continue
        slot_id = str(raw_slot_id or "").strip()
        url = str(patch.get("url") or "").strip()
        if not slot_id or not url:
            continue
        if slot_id in by_id:
            by_id[slot_id]["value"] = url
        elif slot_id == "carousel.dimension_background":
            insert_at = min(3, len(slots))
            slot = {"slot_id": slot_id, "value": url}
            slots.insert(insert_at, slot)
            by_id[slot_id] = slot
    return slots
