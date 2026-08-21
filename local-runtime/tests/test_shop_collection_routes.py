from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wh_local.db import init_db
from wh_local.data_collection.routes import DailySelectionActor
from wh_local.data_collection.shop_repository import ShopCollectionRepository
from wh_local.data_collection.shop_routes import ShopCollectionRouteDependencies, create_shop_collection_router


class WorkerStub:
    def __init__(self) -> None:
        self.notifications = 0

    def notify(self) -> None:
        self.notifications += 1


def _client(
    tmp_path: Path, *, provider_error: Exception | None = None, race_action: str = "",
) -> tuple[TestClient, WorkerStub]:
    database = tmp_path / "runtime.sqlite3"
    init_db(database)
    worker = WorkerStub()

    def resolve_provider(actor):
        if provider_error is not None:
            raise provider_error
        return {"enabled": True}

    repository = ShopCollectionRepository(database)
    if race_action:
        transition = repository.transition_batch

        def racing_transition(batch_id: str, status: str, **kwargs):
            if status == race_action:
                competing = "cancelling" if status == "pausing" else "failed"
                transition(batch_id, competing, expected_statuses={"queued"})
            return transition(batch_id, status, **kwargs)

        repository.transition_batch = racing_transition  # type: ignore[method-assign]

    router = create_shop_collection_router(
        ShopCollectionRouteDependencies(
            resolve_actor=lambda: DailySelectionActor(actor_id="actor-a", workspace_id="default"),
            database_path=database,
            provider_config_resolver=resolve_provider,
            worker=worker,
            repository=repository,
        )
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=not race_action), worker


def test_create_list_detail_items_and_workspace_safe_shape(tmp_path: Path) -> None:
    client, worker = _client(tmp_path)
    response = client.post(
        "/desktop/data-collection/shop-batches",
        json={"source_input": "https://shop-test.1688.com/page/offerlist.htm?spm=x &offerId-12345678&td page id=x"},
    )
    assert response.status_code == 202
    batch = response.json()
    assert batch["status"] == "queued"
    assert batch["seed_offer_id"] == "12345678"
    assert "api_key" not in response.text
    assert worker.notifications == 1

    listed = client.get("/desktop/data-collection/shop-batches").json()
    assert listed["total"] == 1
    assert listed["items"][0]["batch_id"] == batch["batch_id"]
    assert client.get(f"/desktop/data-collection/shop-batches/{batch['batch_id']}").status_code == 200
    assert client.get(f"/desktop/data-collection/shop-batches/{batch['batch_id']}/items").json() == {"items": [], "total": 0}


def test_routes_map_validation_conflict_not_found_and_provider_unavailable(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    assert client.post("/desktop/data-collection/shop-batches", json={"source_input": "bad sid"}).status_code == 422
    created = client.post("/desktop/data-collection/shop-batches", json={"source_input": "b2b-direct-shop"})
    assert created.status_code == 202
    assert client.post("/desktop/data-collection/shop-batches", json={"source_input": "b2b-direct-shop"}).status_code == 409
    assert client.get("/desktop/data-collection/shop-batches/missing").status_code == 404
    assert client.post("/desktop/data-collection/shop-batches/missing/pause").status_code == 404

    unavailable, _ = _client(tmp_path / "unavailable", provider_error=RuntimeError("secret=must-not-leak"))
    response = unavailable.post("/desktop/data-collection/shop-batches", json={"source_input": "b2b-another-shop"})
    assert response.status_code == 503
    assert "must-not-leak" not in response.text


def test_pause_resume_cancel_and_retry_state_conflicts(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    batch_id = client.post(
        "/desktop/data-collection/shop-batches", json={"source_input": "b2b-action-shop"}
    ).json()["batch_id"]
    assert client.post(f"/desktop/data-collection/shop-batches/{batch_id}/pause").json()["status"] == "pausing"
    assert client.post(f"/desktop/data-collection/shop-batches/{batch_id}/resume").status_code == 409
    assert client.post(f"/desktop/data-collection/shop-batches/{batch_id}/cancel").json()["status"] == "cancelling"
    assert client.post(f"/desktop/data-collection/shop-batches/{batch_id}/retry-failed").status_code == 409


@pytest.mark.parametrize(("action", "target"), (("pause", "pausing"), ("cancel", "cancelling")))
def test_control_compare_and_swap_race_maps_to_conflict(
    tmp_path: Path, action: str, target: str,
) -> None:
    client, _ = _client(tmp_path, race_action=target)
    batch_id = client.post(
        "/desktop/data-collection/shop-batches", json={"source_input": "b2b-racing-shop"}
    ).json()["batch_id"]

    response = client.post(f"/desktop/data-collection/shop-batches/{batch_id}/{action}")

    assert response.status_code == 409
