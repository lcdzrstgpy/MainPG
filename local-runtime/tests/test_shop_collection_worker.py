from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from wh_local.db import connect, init_db
from wh_local.data_collection.shop_repository import ShopCollectionRepository
from wh_local.data_collection.shop_worker import ShopCollectionWorker


@dataclass
class Result:
    response: dict
    error: object | None = None
    audits: tuple = ()

    @property
    def ok(self) -> bool:
        return self.error is None


class FakeProvider:
    def __init__(self, pages: dict[int, dict], *, fail_ids: set[str] | None = None) -> None:
        self.pages = pages
        self.fail_ids = fail_ids or set()
        self.detail_calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def search_shop(self, seller_nick: str, page: int) -> Result:
        return Result(self.pages[page])

    def get_item_detail(self, offer_id: str) -> Result:
        self.detail_calls.append(offer_id)
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        # 延长详情耗时窗口，确保同一批并发的 3 个线程能同时观察到活跃计数，
        # 避免本机线程调度太快导致并发峰值测不到（时序敏感断言稳定化）。
        time.sleep(0.08)
        with self.lock:
            self.active -= 1
        if offer_id in self.fail_ids:
            return Result({}, error=type("Error", (), {"code": "invalid_request", "message": "gone"})())
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


def test_worker_pages_sequentially_deduplicates_and_caps_detail_concurrency_at_three(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop"
    )
    provider = FakeProvider(
        {
            1: {"items": [{"offer_id": str(value), "source_url": f"https://detail.1688.com/offer/{value}.html", "title": str(value)} for value in range(1, 6)], "has_next": True},
            2: {"items": [{"offer_id": str(value), "source_url": f"https://detail.1688.com/offer/{value}.html", "title": str(value)} for value in range(5, 9)], "has_next": False},
        }
    )
    intakes: list[str] = []
    worker = ShopCollectionWorker(
        repository=repository,
        provider_config_resolver=lambda actor: {"enabled": True},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: (
            intakes.append(payload["candidate"]["candidate_id"])
            or {"action": "created", "draft": {"draft_id": payload["candidate"]["candidate_id"]}}
        ),
        page_normalizer=_page,
        detail_normalizer=_detail,
    )

    worker.process_batch("batch-1")

    batch = repository.get_batch(workspace_id="default", batch_id="batch-1")
    assert batch.status == "completed"
    assert batch.pages_fetched == 2
    assert batch.discovered_count == 8
    assert batch.duplicate_count == 1
    assert batch.succeeded_count == 8
    assert provider.max_active == 3
    assert len(set(intakes)) == 8


def test_worker_reserves_shared_api_budget_before_listing_and_detail_calls(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-budget",
        workspace_id="default",
        actor_id="actor-a",
        shop_sid="b2b-shop",
    )
    provider = FakeProvider(
        {1: {"items": [{"offer_id": "1"}], "has_next": False}}
    )

    class RecordingBudget:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def reserve(self, **values: object) -> SimpleNamespace:
            self.calls.append(dict(values))
            return SimpleNamespace(reservation_granted=True)

    budget = RecordingBudget()
    worker = ShopCollectionWorker(
        repository=repository,
        provider_config_resolver=lambda actor: {"api_key": "key", "api_secret": "secret"},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: {"action": "created", "draft": {}},
        page_normalizer=_page,
        detail_normalizer=_detail,
        budget=budget,
    )

    worker.process_batch("batch-budget")

    assert [call["api_calls"] for call in budget.calls] == [1, 1]
    assert {call["workspace_id"] for call in budget.calls} == {"default"}
    assert len({call["provider_fingerprint"] for call in budget.calls}) == 1
    with connect(repository.database_path) as connection:
        operations = connection.execute(
            """SELECT operation, reservation_granted
            FROM shop_collection_api_calls
            WHERE batch_id = 'batch-budget' ORDER BY call_id"""
        ).fetchall()
    assert [(row["operation"], row["reservation_granted"]) for row in operations] == [
        ("item_search_shop", 1),
        ("item_get", 1),
    ]


def test_failed_details_make_partial_batch_and_retry_only_failed_items(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop"
    )
    provider = FakeProvider(
        {1: {"items": [
            {"offer_id": "1", "source_url": "https://detail.1688.com/offer/1.html", "title": "1"},
            {"offer_id": "2", "source_url": "https://detail.1688.com/offer/2.html", "title": "2"},
        ], "has_next": False}},
        fail_ids={"2"},
    )
    worker = ShopCollectionWorker(
        repository=repository,
        provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: {"action": "created", "draft": {}},
        page_normalizer=_page,
    )
    worker.process_batch("batch-1")
    assert repository.get_batch(workspace_id="default", batch_id="batch-1").status == "partial"

    provider.fail_ids.clear()
    worker.retry_failed(workspace_id="default", batch_id="batch-1")
    worker.process_batch("batch-1")
    batch = repository.get_batch(workspace_id="default", batch_id="batch-1")
    assert batch.status == "completed"
    assert provider.detail_calls.count("1") == 1
    assert provider.detail_calls.count("2") == 2


def test_seed_offer_is_reused_to_resolve_shop_before_listing(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a",
        shop_sid="pending:12345678", seed_offer_id="12345678",
    )

    class SeedProvider(FakeProvider):
        def get_item_detail(self, offer_id: str) -> Result:
            if offer_id == "12345678" and not self.detail_calls:
                self.detail_calls.append(offer_id)
                return Result({"item": {
                    "num_iid": offer_id,
                    "title": "Seed Product",
                    "detail_url": f"https://detail.1688.com/offer/{offer_id}.html",
                    "seller_info": {"sid": "b2b-resolved", "shop_name": "Test Shop"},
                }})
            return super().get_item_detail(offer_id)

    provider = SeedProvider({1: {"items": [{
        "offer_id": "12345678",
        "source_url": "https://detail.1688.com/offer/12345678.html",
        "title": "Seed Product",
    }], "has_next": False}})
    worker = ShopCollectionWorker(
        repository=repository,
        provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: {"action": "created", "draft": {}},
        page_normalizer=_page,
    )
    worker.process_batch("batch-1")
    batch = repository.get_batch(workspace_id="default", batch_id="batch-1")
    assert batch.shop_sid == "b2b-resolved"
    assert batch.shop_name == "Test Shop"
    assert batch.status == "completed"
    assert batch.succeeded_count == 1
    assert provider.detail_calls == ["12345678"]


def test_production_normalizers_page_all_results_and_drop_raw_description_html(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop"
    )

    class CanonicalProvider(FakeProvider):
        def get_item_detail(self, offer_id: str) -> Result:
            self.detail_calls.append(offer_id)
            return Result({"item": {
                "num_iid": offer_id,
                "title": f"Product {offer_id}",
                "detail_url": f"https://detail.1688.com/offer/{offer_id}.html",
                "price": "12.5",
                "original_price": "20",
                "stock": 11,
                "unit": "件",
                "brand": "Test Brand",
                "desc": '<p>secret markup<img src="//img.example.test/detail.jpg"></p>',
            }})

    provider = CanonicalProvider({
        1: {"items": {"item": [{"num_iid": "1"}, {"title": "missing"}], "total_results": 2, "page_size": 1}},
        2: {"items": {"item": [{"num_iid": "2"}], "total_results": 2, "page_size": 1}},
    })
    captured: list[dict] = []
    worker = ShopCollectionWorker(
        repository=repository,
        provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: (
            captured.append(payload["candidate"]) or {"action": "created", "draft": {}}
        ),
    )

    worker.process_batch("batch-1")

    batch = repository.get_batch(workspace_id="default", batch_id="batch-1")
    assert batch.status == "completed"
    assert batch.pages_fetched == 2
    assert batch.discovered_count == 2
    assert batch.missing_id_count == 1
    assert {candidate["candidate_id"] for candidate in captured} == {"1688:1", "1688:2"}
    assert captured[0]["original_price_cny"] == "20"
    assert captured[0]["stock_quantity"] == 11
    assert "desc" not in captured[0]["raw_payload"].get("item", {})
    assert captured[0]["source_detail_image_urls"] == ["https://img.example.test/detail.jpg"]


def test_listing_never_fetches_more_than_one_hundred_pages(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop"
    )

    class EndlessProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__({})
            self.pages_seen: list[int] = []

        def search_shop(self, seller_nick: str, page: int) -> Result:
            self.pages_seen.append(page)
            return Result({"items": [], "has_next": True})

    provider = EndlessProvider()
    worker = ShopCollectionWorker(
        repository=repository, provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: {"action": "created", "draft": {}},
        page_normalizer=_page,
    )
    worker.process_batch("batch-1")

    assert provider.pages_seen == list(range(1, 101))
    assert repository.get_batch(workspace_id="default", batch_id="batch-1").pages_fetched == 100


def test_transient_item_detail_failure_retries_then_succeeds(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop"
    )

    class FlakyProvider(FakeProvider):
        def get_item_detail(self, offer_id: str) -> Result:
            self.detail_calls.append(offer_id)
            if len(self.detail_calls) < 3:
                error = type("Error", (), {"code": "timeout", "message": "temporary"})()
                return Result({}, error=error)
            return Result({"offer_id": offer_id, "title": "Recovered"})

    provider = FlakyProvider({1: {"items": [{"offer_id": "1"}], "has_next": False}})
    worker = ShopCollectionWorker(
        repository=repository, provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: {"action": "created", "draft": {}},
        page_normalizer=_page, detail_normalizer=_detail, retry_delay_seconds=0,
    )
    worker.process_batch("batch-1")

    assert provider.detail_calls == ["1", "1", "1"]
    assert repository.get_batch(workspace_id="default", batch_id="batch-1").status == "completed"


def test_two_workers_do_not_process_the_same_batch(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop"
    )
    provider = FakeProvider({1: {"items": [{"offer_id": "1"}], "has_next": False}})
    intakes: list[str] = []
    workers = [
        ShopCollectionWorker(
            repository=ShopCollectionRepository(repository.database_path),
            provider_config_resolver=lambda actor: {}, provider_factory=lambda config: provider,
            intake_shop_candidate=lambda **payload: (
                intakes.append(payload["candidate"]["candidate_id"]) or {"action": "created", "draft": {}}
            ), page_normalizer=_page, detail_normalizer=_detail,
        )
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if repository.get_batch(workspace_id="default", batch_id="batch-1").status == "completed":
            break
        time.sleep(0.01)
    for worker in workers:
        worker.close()

    assert provider.detail_calls == ["1"]
    assert intakes == ["1688:1"]


def test_close_waits_for_listing_and_prevents_persistence_after_shutdown(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop"
    )
    entered = threading.Event()
    release = threading.Event()

    class BlockingProvider(FakeProvider):
        def search_shop(self, seller_nick: str, page: int) -> Result:
            entered.set()
            release.wait(timeout=3)
            return Result({"items": [{"offer_id": "1"}], "has_next": False})

    provider = BlockingProvider({})
    worker = ShopCollectionWorker(
        repository=repository, provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: {"action": "created", "draft": {}},
        page_normalizer=_page, detail_normalizer=_detail,
    )
    worker.start()
    assert entered.wait(timeout=1)
    closer = threading.Thread(target=worker.close)
    closer.start()
    time.sleep(0.03)
    assert closer.is_alive()
    release.set()
    closer.join(timeout=1)

    assert not closer.is_alive()
    assert worker._thread is not None and not worker._thread.is_alive()
    assert repository.count_items(workspace_id="default", batch_id="batch-1") == 0


def test_close_during_detail_fetch_never_intakes_product(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop"
    )
    entered = threading.Event()
    release = threading.Event()

    class BlockingDetailProvider(FakeProvider):
        def get_item_detail(self, offer_id: str) -> Result:
            entered.set()
            release.wait(timeout=3)
            return Result({"offer_id": offer_id, "title": "Product"})

    provider = BlockingDetailProvider({1: {"items": [{"offer_id": "1"}], "has_next": False}})
    intakes: list[str] = []
    worker = ShopCollectionWorker(
        repository=repository, provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: (
            intakes.append(payload["candidate"]["candidate_id"]) or {"action": "created", "draft": {}}
        ), page_normalizer=_page, detail_normalizer=_detail,
    )
    worker.start()
    assert entered.wait(timeout=1)
    closer = threading.Thread(target=worker.close)
    closer.start()
    time.sleep(0.03)
    release.set()
    closer.join(timeout=1)

    assert intakes == []
    assert worker._thread is not None and not worker._thread.is_alive()


def test_reclaimed_batch_waits_for_live_item_lease_before_finalizing(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop"
    )
    repository.record_shop_page(
        batch_id="batch-1", page=1, items=({"offer_id": "1"},), has_next=False
    )
    repository.transition_batch("batch-1", "resolving")
    repository.transition_batch("batch-1", "listing")
    repository.transition_batch("batch-1", "enriching")
    batch_lease = repository.claim_batch(
        batch_id="batch-1", owner="old-batch-worker", lease_seconds=30
    )
    assert batch_lease is not None
    item_lease = repository.claim_pending_items(
        batch_id="batch-1", owner="old-item-worker", limit=1, lease_seconds=30
    )[0]
    now = datetime.now(timezone.utc)
    with connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE shop_collection_batches SET lease_expires_at = ? WHERE batch_id = 'batch-1'",
            ((now - timedelta(seconds=1)).isoformat(),),
        )
        connection.execute(
            "UPDATE shop_collection_items SET lease_expires_at = ? WHERE item_id = ?",
            ((now + timedelta(seconds=0.2)).isoformat(), item_lease.item_id),
        )

    provider = FakeProvider({})
    worker = ShopCollectionWorker(
        repository=repository, provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: provider,
        intake_shop_candidate=lambda **payload: {"action": "created", "draft": {}},
        page_normalizer=_page, detail_normalizer=_detail, unfinished_poll_seconds=0.01,
    )
    worker.start()
    time.sleep(0.05)
    live = repository.get_batch(workspace_id="default", batch_id="batch-1")
    assert live.status == "enriching"
    assert live.succeeded_count == 0

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if repository.get_batch(workspace_id="default", batch_id="batch-1").status == "completed":
            break
        time.sleep(0.01)
    worker.close()

    assert repository.get_batch(workspace_id="default", batch_id="batch-1").status == "completed"
    assert provider.detail_calls == ["1"]


def test_stale_item_token_is_rejected_before_intake(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    batch = repository.create_batch(
        batch_id="batch-1", workspace_id="default", actor_id="actor-a", shop_sid="b2b-shop"
    )
    repository.record_shop_page(
        batch_id="batch-1", page=1, items=({"offer_id": "1"},), has_next=False
    )
    stale = repository.claim_pending_items(
        batch_id="batch-1", owner="old-item-worker", limit=1, lease_seconds=30
    )[0]
    with connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE shop_collection_items SET lease_expires_at = ? WHERE item_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), stale.item_id),
        )
    current = repository.claim_pending_items(
        batch_id="batch-1", owner="new-item-worker", limit=1, lease_seconds=30
    )[0]
    assert current.lease_token != stale.lease_token

    intakes: list[str] = []
    worker = ShopCollectionWorker(
        repository=repository, provider_config_resolver=lambda actor: {},
        provider_factory=lambda config: FakeProvider({}),
        intake_shop_candidate=lambda **payload: (
            intakes.append(payload["candidate"]["candidate_id"]) or {"action": "created", "draft": {}}
        ), page_normalizer=_page, detail_normalizer=_detail,
    )
    provider = FakeProvider({})
    with pytest.raises(Exception) as stale_error:
        worker._enrich_one(provider, batch, stale)

    assert type(stale_error.value).__name__ == "_StaleItemLease"
    assert intakes == []
