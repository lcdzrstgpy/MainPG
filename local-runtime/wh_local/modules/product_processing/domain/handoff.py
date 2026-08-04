from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import DailySelectionCandidate, DailySelectionHandoffEnvelope


def candidate_from_handoff(handoff: DailySelectionHandoffEnvelope) -> DailySelectionCandidate:
    """Translate data_collection's nested payload_json into the candidate contract."""
    try:
        payload = json.loads(handoff.payload_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("daily-selection handoff payload_json is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("daily-selection handoff payload_json must contain an object")
    candidate = dict(payload.get("candidate") or {})
    images = dict(payload.get("images") or {})
    selection = dict(payload.get("selection_metadata") or {})
    candidate.update(
        {
            "candidate_id": handoff.candidate_id,
            "main_image_url": images.get("main"),
            "source_image_urls": images.get("gallery") or [],
            "source_detail_image_urls": images.get("detail") or [],
            "source_variant_records": payload.get("skus") or [],
            "source_attributes": payload.get("attributes") or {},
            "evidence": payload.get("source_evidence") or [],
            **selection,
            "raw_payload": {
                "daily_selection_handoff_payload": payload,
                "handoff_payload_sha256": hashlib.sha256(handoff.payload_json.encode("utf-8")).hexdigest(),
            },
        }
    )
    return DailySelectionCandidate.model_validate(candidate)
