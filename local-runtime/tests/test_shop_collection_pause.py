from __future__ import annotations

import threading
import time
from pathlib import Path

from wh_local.db import init_db
from wh_local.data_collection.shop_repository import ShopCollectionRepository
from wh_local.data_collection.shop_worker import ShopCollectionWorker


class Result:
    def __init__(self, response: dict, error: object | None = None) -> None:
        self.response = response
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None


class FakeProvider:
    def __init__(self) -> None:
        self.detail_calls: list[str] = []
        self.search_calls: list[int] = []

    def search_shop(self, seller_nick: str, page: int) -> Result:
        self.search_calls.append(page)
        return Result({"items": [{"offer_id": "1", "source_url": "https://detail.1688.com/offer/1.html", "title": "1"}], "has_next": False})

    def get_item_detail(self, offer_id: str) -> Result:
        self.detail_calls.append(offer_id)
        return Result({"offer_id": offer_id, "title": f"Product {offer_id}"})


def _repository(tmp_path: Path) -> ShopCollectionRepository:
    database = tmp_path / "runtime.sqlite3"
    init_db(database)
    return ShopCollectionRepository(database)


def _page(payload: dict, evidence: object = None) -> dict:
    return payload


def _detail(item, result) -> dict:
    return {
        "candidate_id": f"1688:{item.offer_id}",
        "offer_id": item.offer_id,
        "source_platform": "1688",
        "source_url": item.source_url,
        "source_title": result.response["title"],
    }


def _worker(repository: ShopCollectionRepository, provider: FakeProvider, intakes: list[str]) -> ShopCollectionWorker:
    return ShopCollectionWorker(
        repository=repository,
        provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: (
            intakes.append(payload["candidate"]["candidate_id"]) or {"action": "created", "draft": {}}
        ),
        page_normalizer=_page,
        detail_normalizer=_detail,
    )


def test_pause_during_enrich_releases_inflight_item_and_resume_completes(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop",
    )
    repository.record_shop_page(
        batch_id="batch-1", page=1, items=({"offer_id": "1"},), has_next=False,
    )
    repository.transition_batch("batch-1", "resolving")
    repository.transition_batch("batch-1", "listing")
    repository.transition_batch("batch-1", "enriching")

    entered = threading.Event()
    release = threading.Event()

    class BlockingProvider(FakeProvider):
        def get_item_detail(self, offer_id: str) -> Result:
            entered.set()
            release.wait(timeout=3)
            return super().get_item_detail(offer_id)

    provider = BlockingProvider()
    intakes: list[str] = []
    worker = _worker(repository, provider, intakes)

    runner = threading.Thread(target=worker.process_batch, args=("batch-1",))
    runner.start()
    assert entered.wait(timeout=2)

    # The pause request lands while a detail fetch is in-flight.
    repository.transition_batch("batch-1", "pausing", expected_statuses={"enriching"})
    release.set()
    runner.join(timeout=3)

    paused = repository.get_batch(workspace_id="default", batch_id="batch-1")
    assert paused.status == "paused"
    # In-flight result must not be written into the draft pool.
    assert intakes == []
    items = repository.list_items(workspace_id="default", batch_id="batch-1", limit=10, offset=0)
    assert items[0].detail_status == "pending"

    # Resume continues from the released pending item.
    repository.transition_batch("batch-1", "queued", expected_statuses={"paused"})
    worker.process_batch("batch-1")

    resumed = repository.get_batch(workspace_id="default", batch_id="batch-1")
    assert resumed.status == "completed"
    assert resumed.succeeded_count == 1
    assert intakes == ["1688:1"]


def test_pause_between_pages_stops_listing_without_persisting_next_page(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop",
    )
    repository.transition_batch("batch-1", "resolving")
    repository.transition_batch("batch-1", "listing")

    entered = threading.Event()
    release = threading.Event()

    class BlockingProvider(FakeProvider):
        def search_shop(self, seller_nick: str, page: int) -> Result:
            entered.set()
            release.wait(timeout=3)
            return Result({"items": [{"offer_id": "2"}], "has_next": False})

    provider = BlockingProvider()
    worker = _worker(repository, provider, [])

    runner = threading.Thread(target=worker.process_batch, args=("batch-1",))
    runner.start()
    assert entered.wait(timeout=2)

    repository.transition_batch("batch-1", "pausing", expected_statuses={"listing"})
    release.set()
    runner.join(timeout=3)

    paused = repository.get_batch(workspace_id="default", batch_id="batch-1")
    assert paused.status == "paused"
    # The in-flight page result was not persisted as discovered items.
    assert repository.count_items(workspace_id="default", batch_id="batch-1") == 0
