from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from pathlib import Path
from threading import Barrier
import time

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from wh_local.modules.product_processing.infrastructure.assets import ProductProcessingAssets
from wh_local.modules.product_processing.infrastructure.database import create_database
from wh_local.modules.product_processing.infrastructure.media_asset_orm import MediaAssetRow
from wh_local.modules.product_processing.infrastructure.orm import ProductDraftRow
from wh_local.modules.product_processing.infrastructure.repository import ProductProcessingRepository
from wh_local.modules.product_processing.infrastructure.repository import StaleShopIntakeFence
from wh_local.data_collection.shop_repository import ShopCollectionRepository
from wh_local.db import connect
from wh_local.modules.product_processing.domain.models import DailySelectionHandoffEnvelope
from wh_local.modules.product_processing.service import ProductProcessingService


def _service(tmp_path: Path) -> ProductProcessingService:
    return ProductProcessingService(
        ProductProcessingRepository(create_database("sqlite:///:memory:")),
        ProductProcessingAssets(tmp_path / "assets"),
    )


def _fenced_service(tmp_path: Path) -> tuple[ProductProcessingService, ShopCollectionRepository, dict[str, str]]:
    path = tmp_path / "fenced.sqlite3"
    shops = ShopCollectionRepository(path)
    shops.create_batch(
        batch_id="batch-fenced", workspace_id="default", actor_id="actor", shop_sid="shop"
    )
    shops.record_shop_page(
        batch_id="batch-fenced", page=1,
        items=({"offer_id": "offer-1", "source_url": "https://detail.1688.com/offer/offer-1.html"},),
        has_next=False,
    )
    shops.transition_batch("batch-fenced", "resolving")
    shops.transition_batch("batch-fenced", "listing")
    shops.transition_batch("batch-fenced", "enriching")
    batch = shops.claim_batch(batch_id="batch-fenced", owner="batch-worker", lease_seconds=60)
    assert batch is not None
    item = shops.claim_pending_items(batch_id="batch-fenced", owner="item-worker", limit=1, lease_seconds=60)[0]
    service = ProductProcessingService(
        ProductProcessingRepository(create_database(f"sqlite:///{path.as_posix()}")),
        ProductProcessingAssets(tmp_path / "fenced-assets"),
    )
    return service, shops, {
        "batch_id": batch.batch_id,
        "batch_lease_owner": batch.lease_owner,
        "batch_lease_token": batch.lease_token,
        "item_id": item.item_id,
        "item_lease_owner": item.lease_owner,
        "item_lease_token": item.lease_token,
        "offer_id": item.offer_id,
    }


def _candidate(**overrides: object) -> dict[str, object]:
    candidate: dict[str, object] = {
        "candidate_id": "1688:offer-1",
        "offer_id": "offer-1",
        "source_platform": "1688",
        "source_url": "https://detail.1688.com/offer-1.html",
        "source_title": "Original shop item",
        "main_image_url": "https://img.example.com/main.jpg",
        "source_image_urls": [
            "https://img.example.com/main.jpg",
            "https://img.example.com/gallery.jpg",
        ],
        "source_detail_image_urls": ["https://img.example.com/detail.jpg"],
        "source_variant_records": [
            {
                "sku_id": "sku-red",
                "image_url": "https://img.example.com/sku-red.jpg",
                "spec_text": "Red",
                "attributes": {"color": "Red"},
            }
        ],
        "source_attributes": {"material": "cotton"},
        "price_cny": 12.5,
    }
    candidate.update(overrides)
    return candidate


def _intake(service: ProductProcessingService, candidate: dict[str, object], *, batch_id: str = "batch-1", workspace_id: str = "ws") -> dict:
    return service.intake_shop_candidate(
        batch_id=batch_id,
        workspace_id=workspace_id,
        candidate=candidate,
    )


def test_shop_candidate_intake_creates_v2_draft_and_media_bindings(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = _intake(service, _candidate())

    assert result["action"] == "created"
    draft = result["draft"]
    assert draft["status"] == "draft"
    assert draft["source_type"] == "onebound_api"
    assert draft["selection_run_id"] == "batch-1"
    assert draft["media_contract_version"] == 2
    assert {binding["role"] for binding in service.media_assets.list_bindings("ws", product_draft_id=draft["id"])} == {
        "main",
        "gallery",
        "detail",
        "sku",
    }


@pytest.mark.parametrize("invalidate", ["cancel", "stale_item_token"])
def test_cancelled_or_stale_shop_fence_never_creates_a_draft(
    tmp_path: Path, invalidate: str
) -> None:
    service, shops, fence = _fenced_service(tmp_path)
    if invalidate == "cancel":
        shops.transition_batch(
            "batch-fenced", "cancelling", expected_statuses={"enriching"},
            owner=fence["batch_lease_owner"], lease_token=fence["batch_lease_token"],
        )
    else:
        fence["item_lease_token"] = "stale-token"

    with pytest.raises(StaleShopIntakeFence):
        service.intake_shop_candidate(
            batch_id="batch-fenced", workspace_id="default", candidate=_candidate(),
            shop_fence=fence,
        )

    assert service.repository.draft_by_candidate("1688:offer-1", "default") is None


def test_concurrent_cancel_commits_before_intake_fence_and_prevents_draft(tmp_path: Path) -> None:
    service, shops, fence = _fenced_service(tmp_path)
    connection = connect(shops.database_path)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "UPDATE shop_collection_batches SET status = 'cancelling' WHERE batch_id = 'batch-fenced'"
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            service.intake_shop_candidate,
            batch_id="batch-fenced", workspace_id="default", candidate=_candidate(), shop_fence=fence,
        )
        time.sleep(0.05)
        assert not future.done()
        connection.commit()
        connection.close()
        with pytest.raises(StaleShopIntakeFence):
            future.result(timeout=5)

    assert service.repository.draft_by_candidate("1688:offer-1", "default") is None


def test_shop_candidate_intake_replay_refreshes_one_existing_draft(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = _intake(service, _candidate())

    replay = _intake(service, _candidate(), batch_id="batch-2")

    assert replay["action"] == "refreshed"
    assert replay["draft"]["id"] == created["draft"]["id"]
    assert replay["draft"]["selection_run_id"] == "batch-2"
    assert len(service.list_drafts(None, 10, 0, summary=False, workspace_id="ws")["drafts"]) == 1


def test_shop_candidate_intake_refreshes_draft_fields(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = _intake(service, _candidate())

    refreshed = _intake(
        service,
        _candidate(source_title="Updated shop item", price_cny=18.75),
        batch_id="batch-2",
    )

    assert refreshed["action"] == "refreshed"
    assert refreshed["draft"]["id"] == created["draft"]["id"]
    assert refreshed["draft"]["title"] == "Updated shop item"
    assert refreshed["draft"]["cost"] == 18.75


def test_shop_candidate_intake_skips_processing_and_processed_drafts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = _intake(service, _candidate())
    draft_id = created["draft"]["id"]

    service.repository.mark_drafts_status([draft_id], "processing", workspace_id="ws")
    processing = _intake(service, _candidate(source_title="must not replace"), batch_id="batch-2")
    service.repository.mark_drafts_status([draft_id], "processed", workspace_id="ws")
    processed = _intake(service, _candidate(source_title="must not replace either"), batch_id="batch-3")

    assert processing["action"] == "skipped"
    assert processing["draft"]["status"] == "processing"
    assert processed["action"] == "skipped"
    assert processed["draft"]["status"] == "processed"
    assert service.repository.get_draft(draft_id, include_deleted=True, workspace_id="ws")["title"] == "Original shop item"


def test_shop_candidate_intake_revives_deleted_draft(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = _intake(service, _candidate())
    service.delete_drafts([created["draft"]["id"]], workspace_id="ws")

    revived = _intake(service, _candidate(source_title="Revived shop item"), batch_id="batch-2")

    assert revived["action"] == "refreshed"
    assert revived["draft"]["id"] == created["draft"]["id"]
    assert revived["draft"]["status"] == "draft"
    assert revived["draft"]["title"] == "Revived shop item"


def test_shop_candidate_intake_is_workspace_scoped(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _intake(service, _candidate(), workspace_id="first")
    second = _intake(service, _candidate(), workspace_id="second")

    assert first["action"] == "created"
    assert second["action"] == "created"
    assert first["draft"]["id"] != second["draft"]["id"]
    assert service.media_assets.list_bindings("first", product_draft_id=second["draft"]["id"]) == []


def test_shop_candidate_intake_reconciles_active_media_bindings(tmp_path: Path) -> None:
    service = _service(tmp_path)
    created = _intake(service, _candidate())
    draft_id = created["draft"]["id"]

    refreshed = _intake(
        service,
        _candidate(
            main_image_url="https://img.example.com/new-main.jpg",
            source_image_urls=["https://img.example.com/new-gallery.jpg"],
            source_detail_image_urls=[],
            source_variant_records=[],
        ),
    )

    assert refreshed["action"] == "refreshed"
    bindings = service.media_assets.list_bindings("ws", product_draft_id=draft_id)
    assert [(binding["role"], binding["sort_order"]) for binding in bindings] == [
        ("main", 0),
        ("gallery", 0),
    ]
    sources = {
        service.media_assets.get_asset(binding["asset_id"], "ws")["source_url"]
        for binding in bindings
    }
    assert sources == {
        "https://img.example.com/new-main.jpg",
        "https://img.example.com/new-gallery.jpg",
    }


def test_shop_candidate_intake_enforces_unique_direct_candidate_identity(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _intake(service, _candidate())

    with pytest.raises(IntegrityError):
        with service.repository.database.sessions.begin() as session:
            session.add(
                ProductDraftRow(
                    workspace_id="ws",
                    source_type="onebound_api",
                    candidate_id="1688:offer-1",
                    title="duplicate direct candidate",
                    product_name="duplicate direct candidate",
                )
            )
            session.flush()


def test_shop_candidate_intake_concurrent_replay_creates_one_draft_and_shared_asset(tmp_path: Path) -> None:
    database_path = tmp_path / "shop-intake.sqlite3"
    database_url = f"sqlite:///{database_path.as_posix()}"
    services = [
        ProductProcessingService(
            ProductProcessingRepository(create_database(database_url)),
            ProductProcessingAssets(tmp_path / f"assets-{index}"),
        )
        for index in range(2)
    ]
    start = Barrier(2)

    def intake(index: int) -> dict:
        start.wait()
        return _intake(services[index], _candidate())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(intake, index) for index in range(2)]
        results = [future.result() for future in futures]

    assert sorted(result["action"] for result in results) == ["created", "refreshed"]
    with services[0].repository.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(ProductDraftRow)) == 1
        assert session.scalar(select(func.count()).select_from(MediaAssetRow)) == 4


def test_shop_candidate_intake_concurrent_in_memory_replay_uses_one_static_pool_transaction(tmp_path: Path) -> None:
    service = _service(tmp_path)
    start = Barrier(2)

    def intake() -> dict:
        start.wait()
        return _intake(service, _candidate())

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result() for future in [executor.submit(intake) for _ in range(2)]]

    assert sorted(result["action"] for result in results) == ["created", "refreshed"]
    assert len(service.list_drafts(None, 10, 0, summary=False, workspace_id="ws")["drafts"]) == 1


def test_shop_candidate_intake_keeps_handoff_history_separate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    handoff = DailySelectionHandoffEnvelope(
        handoff_id="handoff-1",
        run_id="confirmed-run",
        candidate_id="1688:offer-1",
        workspace_id="ws",
        payload_json=json.dumps(
            {
                "candidate": {
                    "candidate_id": "1688:offer-1",
                    "offer_id": "offer-1",
                    "source_platform": "1688",
                    "source_url": "https://detail.1688.com/confirmed.html",
                    "source_title": "Confirmed history",
                },
                "images": {"main": "", "gallery": [], "detail": []},
                "skus": [],
                "attributes": {},
                "selection_metadata": {},
            }
        ),
        status="pending",
        idempotency_key="confirmed-idempotency",
        created_at="2026-08-20T00:00:00+00:00",
    )
    historical = service.consume_daily_selection_handoffs([handoff])["drafts"][0]

    shop = _intake(service, _candidate(source_title="Current shop snapshot"))
    unchanged = service.repository.get_draft(historical["id"], include_deleted=True, workspace_id="ws")

    assert shop["action"] == "created"
    assert shop["draft"]["id"] != historical["id"]
    assert unchanged["handoff_id"] == "handoff-1"
    assert unchanged["handoff_idempotency_key"] == "confirmed-idempotency"
    assert unchanged["title"] == "Confirmed history"


def test_shop_candidate_intake_drops_nested_secrets_binary_and_non_json_values(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = _intake(
        service,
        _candidate(
            source_title="Safe name; Bearer top-secret",
            raw_payload={
                "client_secret": "api-secret",
                "remoteToken": "remote-session-secret",
                "ARK-API-KEY": "ark-provider-secret",
                "wuyin_api_key": "wuyin-provider-secret",
                "remote_token_count": 3,
                "nested": {
                    "authorization": "Bearer nested-secret",
                    "note": "token=inline-secret",
                    "binary": b"raw-bytes",
                },
                "items": [memoryview(b"more-bytes"), {"session": "session-secret"}],
                "price": Decimal("9.5"),
                "unsupported": object(),
            },
            binary=b"top-level-bytes",
            unsupported={"not-json"},
        ),
    )

    persisted = json.dumps(result["draft"]["raw_payload"], ensure_ascii=False)
    assert result["draft"]["title"] == "Safe name; Bearer [redacted]"
    for secret in (
        "api-secret",
        "remote-session-secret",
        "ark-provider-secret",
        "wuyin-provider-secret",
        "nested-secret",
        "inline-secret",
        "session-secret",
        "raw-bytes",
        "more-bytes",
    ):
        assert secret not in persisted
    assert "client_secret" not in persisted
    assert "authorization" not in persisted
    assert "binary" not in persisted
    assert "unsupported" not in persisted
    assert result["draft"]["raw_payload"]["raw_payload"]["remote_token_count"] == 3
    assert result["draft"]["raw_payload"]["raw_payload"]["price"] == "9.5"
