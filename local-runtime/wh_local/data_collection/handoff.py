"""Stable, local-only handoff records for a downstream draft consumer."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter

from .contracts import DailySelectionCandidate


_JSON_VALUE = TypeAdapter(Any)


class DailySelectionHandoff(BaseModel):
    """A pending record that another module may consume without shared imports."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handoff_id: str
    run_id: str
    candidate_id: str
    workspace_id: str
    payload_json: str
    status: Literal["pending", "consumed", "failed"] = "pending"
    idempotency_key: str
    created_at: str


def handoff_idempotency_key(
    *, workspace_id: str, run_id: str, candidate_id: str
) -> str:
    """Return an unambiguous stable digest for database-level idempotency."""
    identity = json.dumps(
        [workspace_id, run_id, candidate_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def build_handoff_payload(candidate: DailySelectionCandidate) -> str:
    """Serialize all data needed by a draft consumer, keeping Decimal exact."""
    sku_records = [
        record.model_dump(mode="python") for record in candidate.source_variant_records
    ]
    payload = {
        "candidate": {
            "candidate_id": candidate.candidate_id,
            "offer_id": candidate.offer_id,
            "source_platform": candidate.source_platform,
            "source_url": candidate.source_url,
            "source_title": candidate.source_title,
            "shop_name": candidate.shop_name,
            "location": candidate.location,
            "price_cny": candidate.price_cny,
            "freight_cny": candidate.freight_cny,
            "min_order_quantity": candidate.min_order_quantity,
            "category_path": candidate.category_path,
            "category_id": candidate.category_id,
        },
        "images": {
            "main": candidate.main_image_url,
            "gallery": list(candidate.source_image_urls),
            "detail": list(candidate.source_detail_image_urls),
            "sku": [
                record.image_url
                for record in candidate.source_variant_records
                if record.image_url is not None
            ],
        },
        "skus": sku_records,
        "attributes": dict(candidate.source_attributes),
        "source_evidence": [
            item.model_dump(mode="python") for item in candidate.evidence
        ],
        "selection_metadata": {
            "selection_score": candidate.selection_score,
            "selection_reasons": list(candidate.selection_reasons),
            "risk_tags": list(candidate.risk_tags),
            "status": candidate.status,
            "captured_fields": list(candidate.captured_fields),
            "missing_capture_fields": list(candidate.missing_capture_fields),
            "score_components": dict(candidate.score_components),
        },
    }
    return _JSON_VALUE.dump_json(payload).decode("utf-8")
