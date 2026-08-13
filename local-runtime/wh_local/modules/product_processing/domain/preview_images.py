from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


MANIFEST_KEY = "image_manifest_v2"
SLOT_INDEX = {
    "carousel.hero": 0,
    "carousel.detail": 1,
    "carousel.lifestyle": 2,
    "carousel.dimension_background": 3,
}


def _ordered_ids(values: Iterable[Any]) -> tuple[str, ...]:
    """Return non-empty IDs once, preserving their first-seen order."""

    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        asset_id = str(value or "").strip()
        if asset_id and asset_id not in seen:
            seen.add(asset_id)
            ordered.append(asset_id)
    return tuple(ordered)


def _id_values(value: Any) -> Iterable[Any]:
    # A malformed string is one value, not an iterable of one-character IDs.
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, Mapping):
        return value
    return ()


@dataclass(frozen=True)
class PreviewImageManifest:
    """The version-two precheck image manifest.

    Tuple fields make the value immutable while :meth:`as_dict` retains the
    explicit JSON arrays required by the API and persistence contracts.
    """

    main_asset_id: str = ""
    carousel_asset_ids: tuple[str, ...] = ()
    detail_asset_ids: tuple[str, ...] = ()
    semantic_asset_ids: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "main_asset_id", str(self.main_asset_id or "").strip())
        object.__setattr__(self, "carousel_asset_ids", _ordered_ids(_id_values(self.carousel_asset_ids)))
        object.__setattr__(self, "detail_asset_ids", _ordered_ids(_id_values(self.detail_asset_ids)))
        raw_semantics = (
            self.semantic_asset_ids
            if isinstance(self.semantic_asset_ids, Mapping)
            else {}
        )
        semantics = {
            str(slot_id or "").strip(): str(asset_id or "").strip()
            for slot_id, asset_id in raw_semantics.items()
            if str(slot_id or "").strip() and str(asset_id or "").strip()
        }
        object.__setattr__(self, "semantic_asset_ids", semantics)

    @classmethod
    def from_value(cls, value: Any) -> "PreviewImageManifest":
        raw = value if isinstance(value, Mapping) else {}
        return cls(
            main_asset_id=str(raw.get("main_asset_id") or "").strip(),
            carousel_asset_ids=_ordered_ids(_id_values(raw.get("carousel_asset_ids"))),
            detail_asset_ids=_ordered_ids(_id_values(raw.get("detail_asset_ids"))),
            semantic_asset_ids=(
                dict(raw.get("semantic_asset_ids") or {})
                if isinstance(raw.get("semantic_asset_ids"), Mapping)
                else {}
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "main_asset_id": self.main_asset_id,
            "carousel_asset_ids": list(self.carousel_asset_ids),
            "detail_asset_ids": list(self.detail_asset_ids),
            "semantic_asset_ids": dict(self.semantic_asset_ids or {}),
        }

    def live_asset_ids(self) -> tuple[str, ...]:
        return _ordered_ids(
            (self.main_asset_id, *self.carousel_asset_ids, *self.detail_asset_ids)
        )


def replace_carousel_slot(
    manifest: PreviewImageManifest,
    slot_id: str,
    asset_id: str,
) -> PreviewImageManifest:
    """Replace a semantic carousel position, appending when it is not present."""

    normalized = str(asset_id or "").strip()
    if slot_id not in SLOT_INDEX or not normalized:
        raise ValueError("invalid carousel slot replacement")
    values = list(manifest.carousel_asset_ids)
    semantics = dict(manifest.semantic_asset_ids or {})
    previous_asset_id = semantics.get(slot_id, "")
    index = (
        values.index(previous_asset_id)
        if previous_asset_id and previous_asset_id in values
        else SLOT_INDEX[slot_id]
    )
    if index < len(values):
        values[index] = normalized
    else:
        values.append(normalized)
    semantics[slot_id] = normalized
    return PreviewImageManifest(
        main_asset_id=manifest.main_asset_id,
        carousel_asset_ids=_ordered_ids(values),
        detail_asset_ids=manifest.detail_asset_ids,
        semantic_asset_ids=semantics,
    )


def snapshot_hash(entries: Sequence[Mapping[str, Any]]) -> str:
    """Hash a snapshot canonically while retaining list order as data."""

    payload = json.dumps(
        list(entries),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
