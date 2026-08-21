from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.app import main as app_main
from wh_local.data_collection.contracts import ApiEvidence
from wh_local.data_collection.provider import ProviderCallResult
from wh_local.data_collection.service import DailySelectionActor
from wh_local.data_collection.shop_repository import ShopCollectionRepository
from wh_local.data_collection.shop_routes import (
    ShopCollectionRouteDependencies,
    create_shop_collection_router,
)
from wh_local.data_collection.shop_worker import ShopCollectionWorker
from wh_local.db import connect, init_db


class IntegrationProvider:
    def search_shop(self, seller_nick: str, page: int) -> ProviderCallResult:
        assert seller_nick == "b2b-integration-shop"
        assert page == 1
        return ProviderCallResult(
            response={
                "items": {
                    "item": [{"num_iid": "90000001"}],
                    "total_results": 1,
                    "page_size": 20,
                }
            },
            audits=(ApiEvidence(provider="onebound-1688", operation="item_search_shop"),),
        )

    def get_item_detail(self, offer_id: str) -> ProviderCallResult:
        assert offer_id == "90000001"
        return ProviderCallResult(
            response={
                "item": {
                    "num_iid": offer_id,
                    "title": "Integration Product",
                    "detail_url": f"https://detail.1688.com/offer/{offer_id}.html",
                    "price": "18.8",
                    "item_imgs": ["https://img.example.test/main.jpg"],
                    "desc": '<img src="https://img.example.test/detail.jpg">',
                }
            },
            audits=(ApiEvidence(provider="onebound-1688", operation="item_get"),),
        )


def test_shop_modules_create_workspace_owned_product_draft(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    init_db(database)
    product_processing = app_main._product_processing_service(database)
    repository = ShopCollectionRepository(database)
    worker = ShopCollectionWorker(
        repository=repository,
        provider_config_resolver=lambda actor: {"enabled": True},
        provider_factory=lambda config: IntegrationProvider(),
        intake_shop_candidate=product_processing.intake_shop_candidate,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker.start()
        try:
            yield
        finally:
            worker.close()

    application = FastAPI(lifespan=lifespan)
    application.include_router(
        create_shop_collection_router(
            ShopCollectionRouteDependencies(
                resolve_actor=lambda: DailySelectionActor(
                    actor_id="actor-1", workspace_id="default"
                ),
                database_path=database,
                provider_config_resolver=lambda actor: {"enabled": True},
                worker=worker,
                repository=repository,
            )
        )
    )

    with TestClient(application) as client:
        created = client.post(
            "/desktop/data-collection/shop-batches",
            json={"source_input": "b2b-integration-shop"},
        )
        assert created.status_code == 202
        batch_id = created.json()["batch_id"]

        deadline = time.monotonic() + 3
        batch = created.json()
        while time.monotonic() < deadline and batch["status"] not in {
            "completed", "partial", "failed", "cancelled"
        }:
            time.sleep(0.02)
            response = client.get(
                f"/desktop/data-collection/shop-batches/{batch_id}"
            )
            assert response.status_code == 200
            batch = response.json()

        assert batch["status"] == "completed"
        assert batch["succeeded_count"] == 1
        assert batch["created_count"] == 1

    with connect(database) as connection:
        draft = connection.execute(
            """SELECT workspace_id, candidate_id, selection_run_id, source_type, status, raw_payload_json
            FROM product_processing_drafts WHERE candidate_id = '1688:90000001'"""
        ).fetchone()
    assert draft is not None
    assert dict(draft) | {} == {
        "workspace_id": "default",
        "candidate_id": "1688:90000001",
        "selection_run_id": batch_id,
        "source_type": "onebound_api",
        "status": "draft",
        "raw_payload_json": draft["raw_payload_json"],
    }
    assert "<img" not in draft["raw_payload_json"]
