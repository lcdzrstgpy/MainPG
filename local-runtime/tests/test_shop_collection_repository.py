from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wh_local.db import connect, init_db
from wh_local.data_collection.shop_repository import (
    ActiveShopBatchExists,
    InvalidShopBatchTransition,
    ShopBatchNotFound,
    ShopCollectionRepository,
)


def _repository(tmp_path: Path) -> ShopCollectionRepository:
    database = tmp_path / "runtime.sqlite3"
    init_db(database)
    with connect(database) as connection:
        connection.executemany(
            "INSERT INTO workspaces (workspace_id, workspace_code, workspace_name) VALUES (?, ?, ?)",
            (
                ("workspace-a", "workspace-a", "Workspace A"),
                ("workspace-b", "workspace-b", "Workspace B"),
            ),
        )
    return ShopCollectionRepository(database)


def test_repository_first_initialization_records_migrations_and_remains_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "repository-first.sqlite3"

    ShopCollectionRepository(database)
    init_db(database)
    init_db(database)
    ShopCollectionRepository(database)

    with connect(database) as connection:
        markers = connection.execute(
            """SELECT migration_id, COUNT(*) AS count
            FROM schema_migrations
            WHERE migration_id IN (
                'data_collection:005_shop_collection',
                'data_collection:006_shop_collection_lease_tokens'
            )
            GROUP BY migration_id ORDER BY migration_id"""
        ).fetchall()
        assert [(row["migration_id"], row["count"]) for row in markers] == [
            ("data_collection:005_shop_collection", 1),
            ("data_collection:006_shop_collection_lease_tokens", 1),
        ]
        assert [
            row["name"]
            for row in connection.execute("PRAGMA table_info(shop_collection_batches)")
            if row["name"] == "lease_token"
        ] == ["lease_token"]


def test_batches_are_workspace_isolated_and_active_shop_is_unique(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = repository.create_batch(
        batch_id="batch-1", workspace_id="workspace-a", actor_id="actor-a", shop_sid="shop-sid"
    )
    assert first.status == "queued"

    with pytest.raises(ActiveShopBatchExists):
        repository.create_batch(
            batch_id="batch-2", workspace_id="workspace-a", actor_id="actor-a", shop_sid="shop-sid"
        )

    second = repository.create_batch(
        batch_id="batch-3", workspace_id="workspace-b", actor_id="actor-b", shop_sid="shop-sid"
    )
    assert second.workspace_id == "workspace-b"
    with pytest.raises(ShopBatchNotFound):
        repository.get_batch(workspace_id="workspace-a", batch_id=second.batch_id)


def test_discovery_is_idempotent_and_updates_durable_checkpoint(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="workspace-a", actor_id="actor-a", shop_sid="shop-sid"
    )

    result = repository.record_shop_page(
        batch_id="batch-1",
        page=1,
        items=(
            {"offer_id": "100", "source_url": "https://detail.1688.com/offer/100.html", "title": "A"},
            {"offer_id": "100", "source_url": "https://detail.1688.com/offer/100.html", "title": "A duplicate"},
            {"offer_id": "", "source_url": "https://example.invalid/no-id", "title": "missing"},
        ),
        has_next=True,
    )
    assert result == {"created": 1, "duplicates": 1, "missing_ids": 1}
    replay = repository.record_shop_page(
        batch_id="batch-1",
        page=1,
        items=({"offer_id": "100", "source_url": "https://detail.1688.com/offer/100.html"},),
        has_next=True,
    )
    assert replay["created"] == 0
    assert replay["duplicates"] == 1

    batch = repository.get_batch(workspace_id="workspace-a", batch_id="batch-1")
    assert batch.next_page == 2
    assert batch.pages_fetched == 1
    assert batch.discovered_count == 1
    items = repository.list_items(workspace_id="workspace-a", batch_id="batch-1", limit=20, offset=0)
    assert [item.offer_id for item in items] == ["100"]


def test_item_source_urls_are_absolute_with_canonical_offer_fallback(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-links",
        workspace_id="workspace-a",
        actor_id="actor-a",
        shop_sid="shop-links",
    )
    repository.record_shop_page(
        batch_id="batch-links",
        page=1,
        items=(
            {"offer_id": "100", "source_url": ""},
            {"offer_id": "101", "source_url": "/offer/101.html"},
            {
                "offer_id": "102",
                "source_url": "https://detail.1688.com/offer/102.html?trace=1",
            },
            {"offer_id": "invalid", "source_url": ""},
        ),
        has_next=False,
    )

    items = repository.list_items(
        workspace_id="workspace-a", batch_id="batch-links", limit=20, offset=0
    )

    assert [item.source_url for item in items] == [
        "https://detail.1688.com/offer/100.html",
        "https://detail.1688.com/offer/101.html",
        "https://detail.1688.com/offer/102.html?trace=1",
        "",
    ]


def test_leases_recover_and_item_counters_follow_terminal_results(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="workspace-a", actor_id="actor-a", shop_sid="shop-sid"
    )
    repository.record_shop_page(
        batch_id="batch-1",
        page=1,
        items=({"offer_id": "100", "source_url": "https://detail.1688.com/offer/100.html"},),
        has_next=False,
    )
    repository.transition_batch("batch-1", "resolving")
    repository.transition_batch("batch-1", "listing")
    repository.transition_batch("batch-1", "enriching")
    batch_claim = repository.claim_next_runnable_batch(owner="batch-worker-a", lease_seconds=30)
    assert batch_claim is not None
    assert repository.claim_next_runnable_batch(owner="batch-worker-b", lease_seconds=30) is None
    claimed = repository.claim_pending_items(batch_id="batch-1", owner="worker-a", limit=3, lease_seconds=30)
    assert [item.detail_status for item in claimed] == ["running"]

    repository.recover_interrupted_work()
    live = repository.list_items(workspace_id="workspace-a", batch_id="batch-1", limit=20, offset=0)
    assert live[0].detail_status == "running"

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE shop_collection_items SET lease_expires_at = ? WHERE item_id = ?",
            (expired, claimed[0].item_id),
        )
        connection.execute(
            "UPDATE shop_collection_batches SET lease_expires_at = ? WHERE batch_id = 'batch-1'",
            (expired,),
        )
    repository.recover_interrupted_work()
    recovered = repository.list_items(workspace_id="workspace-a", batch_id="batch-1", limit=20, offset=0)
    assert recovered[0].detail_status == "pending"

    claimed = repository.claim_pending_items(batch_id="batch-1", owner="worker-b", limit=3, lease_seconds=30)
    repository.complete_item(
        batch_id="batch-1",
        item_id=claimed[0].item_id,
        owner=claimed[0].lease_owner,
        lease_token=claimed[0].lease_token,
        intake_action="created",
        candidate={"candidate_id": "1688:100"},
    )
    batch = repository.get_batch(workspace_id="workspace-a", batch_id="batch-1")
    assert batch.succeeded_count == 1
    assert batch.created_count == 1


def test_expired_item_is_reclaimed_and_stale_completion_is_rejected(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="workspace-a", actor_id="actor-a", shop_sid="shop-sid"
    )
    repository.record_shop_page(
        batch_id="batch-1", page=1, items=({"offer_id": "100"},), has_next=False
    )
    first = repository.claim_pending_items(
        batch_id="batch-1", owner="worker-a", limit=1, lease_seconds=30
    )[0]
    with connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE shop_collection_items SET lease_expires_at = ? WHERE item_id = ?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), first.item_id),
        )
    second = repository.claim_pending_items(
        batch_id="batch-1", owner="worker-b", limit=1, lease_seconds=30
    )[0]
    assert second.lease_token != first.lease_token
    assert second.attempts == 2

    with pytest.raises(Exception) as stale_error:
        repository.complete_item(
            batch_id="batch-1", item_id=first.item_id, owner=first.lease_owner,
            lease_token=first.lease_token, intake_action="created",
            candidate={"candidate_id": "1688:100"},
        )
    assert type(stale_error.value).__name__ == "ShopLeaseLost"
    repository.complete_item(
        batch_id="batch-1", item_id=second.item_id, owner=second.lease_owner,
        lease_token=second.lease_token, intake_action="created",
        candidate={"candidate_id": "1688:100"},
    )
    assert repository.get_batch(workspace_id="workspace-a", batch_id="batch-1").succeeded_count == 1


def test_batch_transition_expected_state_is_atomic(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.create_batch(
        batch_id="batch-1", workspace_id="workspace-a", actor_id="actor-a", shop_sid="shop-sid"
    )
    repository.transition_batch("batch-1", "pausing", expected_statuses={"queued"})

    with pytest.raises(InvalidShopBatchTransition):
        repository.transition_batch("batch-1", "cancelling", expected_statuses={"queued"})

    assert repository.get_batch(workspace_id="workspace-a", batch_id="batch-1").status == "pausing"
