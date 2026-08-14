from __future__ import annotations

import json
from pathlib import Path

from wh_local.modules.product_processing.domain.models import DailySelectionHandoffEnvelope
from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.service import ProductProcessingService


def _service(tmp_path: Path) -> ProductProcessingService:
    return ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def _payload() -> dict:
    return {
        "candidate": {
            "candidate_id": "c-1",
            "offer_id": "o-1",
            "source_platform": "1688",
            "source_url": "https://detail.1688.com/x.html",
            "source_title": "Test product",
        },
        "images": {
            "main": "https://img.example.com/main.jpg",
            "gallery": ["https://img.example.com/g1.jpg", "https://img.example.com/s1.jpg"],
            "detail": ["https://img.example.com/d1.jpg"],
            "sku": ["https://img.example.com/s1.jpg", "https://img.example.com/s2.jpg"],
        },
        "skus": [
            {
                "sku_id": "S1",
                "image_url": "https://img.example.com/s1.jpg",
                "spec_text": "Red / L",
                "attributes": {"color": "Red", "size": "L"},
            },
            {
                "sku_id": "S2",
                "image_url": "https://img.example.com/s2.jpg",
                "spec_text": "Blue / M",
                "attributes": {"color": "Blue", "size": "M"},
            },
        ],
        "attributes": {},
        "source_evidence": [],
        "selection_metadata": {},
    }


def _handoff(payload: dict) -> DailySelectionHandoffEnvelope:
    return DailySelectionHandoffEnvelope(
        handoff_id="h-1",
        run_id="run-1",
        candidate_id="c-1",
        workspace_id="ws",
        payload_json=json.dumps(payload, ensure_ascii=False),
        status="pending",
        idempotency_key="idem-1",
        created_at="2026-08-13T00:00:00+00:00",
    )


def test_v2_ingress_binds_all_roles_and_replay_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    handoff = _handoff(_payload())
    result = service.consume_daily_selection_handoffs([handoff])
    assert result["created"] == 1
    draft = result["drafts"][0]
    assert draft["media_contract_version"] == 2

    bindings = service.media_assets.list_bindings("ws", product_draft_id=draft["id"])
    by_role: dict[str, list[dict]] = {}
    for binding in bindings:
        by_role.setdefault(binding["role"], []).append(binding)

    assert len(by_role["main"]) == 1
    assert len(by_role["gallery"]) == 2
    assert len(by_role["detail"]) == 1
    assert len(by_role["sku"]) == 2

    sku_bindings = sorted(by_role["sku"], key=lambda b: b["sku_id"])
    assert [b["sku_id"] for b in sku_bindings] == ["S1", "S2"]
    assert sku_bindings[0]["variant_label"] == "Red / L"

    # A URL shared between gallery and SKU still has two distinct bindings.
    s1_gallery = next(b for b in by_role["gallery"] if b["sort_order"] == 1)
    s1_sku = sku_bindings[0]
    assert s1_gallery["asset_id"] == s1_sku["asset_id"]
    assert s1_gallery["id"] != s1_sku["id"]

    asset = service.media_assets.get_asset(s1_gallery["asset_id"], "ws")
    assert asset["status"] == "pending"

    # Replay is idempotent: no new draft, no new bindings.
    replay = service.consume_daily_selection_handoffs([handoff])
    assert replay["created"] == 0
    assert replay["replayed"] == 1
    bindings_after = service.media_assets.list_bindings("ws", product_draft_id=draft["id"])
    assert len(bindings_after) == len(bindings)
