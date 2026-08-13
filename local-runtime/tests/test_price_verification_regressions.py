from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from wh_local.data_collection.plugin_queue import (
    SOURCE_BROWSER_IMAGE_SEARCH,
    DataCollectionPluginQueue,
)
from wh_local.data_collection.routes import PluginResultRequest
from wh_local.price_verification.contracts import PluginCommandRequest, PriceVerificationActor
from wh_local.price_verification.plugin.service import PluginBridgeService
from wh_local.price_verification.quote_normalizer import QuoteItem, dedupe_quotes
from wh_local.price_verification.repository import PriceVerificationRepository
from wh_local.price_verification.sourcing.contracts import SourceSearchTask
from wh_local.price_verification.sourcing.identity import evaluate_product_evidence
from wh_local.price_verification.sourcing.onebound_adapter import OneBoundSourceAdapter
from wh_local.price_verification.sourcing.service import (
    ProductLibraryLookupError,
    SourcingService,
    build_source_preview,
)


def _append_chunk(
    repository: PriceVerificationRepository,
    *,
    batch_id: str,
    digest: str,
    page_url: str,
    quote_key: str,
    capture: dict[str, object] | None = None,
) -> None:
    repository.append_quote_capture_chunk(
        workspace_id="workspace-1",
        batch_id=batch_id,
        content_sha256=digest,
        page_url=page_url,
        capture=capture or {"quote_key": quote_key},
        items=({"quote_key": quote_key, "skc_id": quote_key},),
        captured_at="2026-08-13T00:00:00+00:00",
    )


def test_capture_batch_replaces_only_same_page_and_rolls_back_failed_replace(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = PriceVerificationRepository(tmp_path / "quotes.sqlite3")
    batch = repository.create_quote_capture_batch(
        workspace_id="workspace-1", name="batch", created_by="actor-1"
    )
    _append_chunk(repository, batch_id=batch.batch_id, digest="1" * 64, page_url="https://temu.example/a", quote_key="A1")
    _append_chunk(repository, batch_id=batch.batch_id, digest="2" * 64, page_url="https://temu.example/b", quote_key="B1")
    _append_chunk(repository, batch_id=batch.batch_id, digest="3" * 64, page_url="https://temu.example/a", quote_key="A2")

    chunks = repository.list_quote_capture_chunks(workspace_id="workspace-1", batch_id=batch.batch_id)
    assert {(chunk.page_url, chunk.items[0]["quote_key"]) for chunk in chunks} == {
        ("https://temu.example/a", "A2"),
        ("https://temu.example/b", "B1"),
    }

    from wh_local.price_verification import repository as repository_module

    failed_capture = {"will": "fail"}
    original_dumps = repository_module.safe_json_dumps

    def fail_new_capture(value: object) -> str:
        if value is failed_capture:
            raise RuntimeError("serialization failed")
        return original_dumps(value)

    monkeypatch.setattr(repository_module, "safe_json_dumps", fail_new_capture)
    with pytest.raises(RuntimeError, match="serialization failed"):
        _append_chunk(
            repository,
            batch_id=batch.batch_id,
            digest="4" * 64,
            page_url="https://temu.example/a",
            quote_key="A3",
            capture=failed_capture,
        )
    chunks = repository.list_quote_capture_chunks(workspace_id="workspace-1", batch_id=batch.batch_id)
    assert {(chunk.page_url, chunk.items[0]["quote_key"]) for chunk in chunks} == {
        ("https://temu.example/a", "A2"),
        ("https://temu.example/b", "B1"),
    }


def test_network_and_dom_rows_with_different_explicit_skus_stay_separate() -> None:
    rows = dedupe_quotes(
        (
            QuoteItem(skc_id="SKC-1", sku_id="SKU-RED", site="US", capture_method="network_json"),
            QuoteItem(skc_id="SKC-1", sku_id="SKU-BLUE", site="US", capture_method="dom_table"),
        )
    )
    assert [row.sku_id for row in rows] == ["SKU-RED", "SKU-BLUE"]


def test_generic_single_title_overlap_is_not_product_identity() -> None:
    status, evidence = evaluate_product_evidence(
        {"product_title": "women summer dress"},
        {"source_title": "women travel backpack"},
    )
    assert status == "conflict"
    assert evidence == ("product_title_mismatch",)


def test_legacy_queue_accepts_sent_and_recovers_expired_claim(tmp_path) -> None:
    assert PluginResultRequest(
        session_token="session", command_id=1, status="sent", result={}
    ).status == "sent"
    database = tmp_path / "queue.sqlite3"
    queue = DataCollectionPluginQueue(database)
    session = queue.create_session(
        actor_id="actor-1",
        workspace_id="workspace-1",
        capabilities={SOURCE_BROWSER_IMAGE_SEARCH: True},
    )
    queued = queue.queue_command(
        actor_id="actor-1",
        workspace_id="workspace-1",
        session_id=session["session_id"],
        command_type=SOURCE_BROWSER_IMAGE_SEARCH,
        payload={"tasks": []},
        idempotency_key="request-1",
    )
    claimed = queue.poll(session["session_token"])
    assert [item.command_id for item in claimed] == [queued.command_id]
    assert queue.receive_result(
        session_token=session["session_token"],
        command_id=queued.command_id,
        status="sent",
        result={"acknowledged": True},
    ).status == "running"

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE data_collection_plugin_commands SET updated_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", queued.command_id),
        )
    reclaimed = queue.poll(session["session_token"])
    assert [item.command_id for item in reclaimed] == [queued.command_id]
    assert reclaimed[0].status == "sent"


def test_price_plugin_queue_normalizes_sent_ack_to_running(tmp_path) -> None:
    repository = PriceVerificationRepository(tmp_path / "plugin.sqlite3")
    actor = PriceVerificationActor(actor_id="actor-1", workspace_id="workspace-1")
    service = PluginBridgeService(repository=repository)
    pairing = service.issue_pairing_code(actor)
    session = service.connect(
        pairing.code,
        browser_name="Edge",
        capabilities={"temu_price_quote_discovery": True},
        actor=actor,
    )
    command = repository.create_command(
        workspace_id=actor.workspace_id,
        session_id=session.session_id,
        request=PluginCommandRequest(
            command_type="temu_price_quote_discovery",
            payload={},
            idempotency_key="request-1",
        ),
    )
    assert service.poll(session.token)[0].command_id == command.command_id
    acknowledged = service.receive_result(
        session.token, command.command_id, "sent", {"acknowledged": True}
    )
    assert acknowledged.status == "running"


def _retained_selection(repository: PriceVerificationRepository, batch_id: str, skc_id: str) -> None:
    record = repository.upsert_batch_selection(
        workspace_id="workspace-1",
        batch_id=batch_id,
        skc_id=skc_id,
        quote_keys=(skc_id,),
        product_title=f"Product {skc_id}",
        main_image_url=f"https://images.example/{skc_id}.jpg",
        official_link_url=f"https://www.temu.com/goods.html?goods_id={skc_id}",
        site="US",
        source_confidence="high",
        authenticity_status="verified",
        sku_prices=(),
        original_min="20",
        original_max="20",
        adjusted_min="18",
        adjusted_max="18",
        now="2026-08-13T00:00:00+00:00",
    )
    repository.update_batch_selection_review(
        workspace_id="workspace-1",
        selection_id=record.id,
        decision="retained",
        max_candidates=5,
        now="2026-08-13T00:00:01+00:00",
    )


def test_complete_sourcing_keeps_unresolved_skc_and_its_preview(tmp_path) -> None:
    repository = PriceVerificationRepository(tmp_path / "sourcing.sqlite3")
    batch = repository.create_quote_capture_batch(
        workspace_id="workspace-1", name="batch", created_by="actor-1"
    )
    _retained_selection(repository, batch.batch_id, "SKC-1")
    _retained_selection(repository, batch.batch_id, "SKC-2")
    repository.save_batch_sourcing_session(
        workspace_id="workspace-1",
        batch_id=batch.batch_id,
        selected_skc_ids=("SKC-1", "SKC-2"),
        unresolved_skc_ids=("SKC-1", "SKC-2"),
        matched_products=(),
        preview={
            "items": [
                {"quote_key": "SKC-1", "skc_id": "SKC-1", "source_decision": "recommended", "all_candidates": []},
                {"quote_key": "SKC-2", "skc_id": "SKC-2", "source_decision": "failed", "all_candidates": []},
            ]
        },
        selected_candidates=(
            {
                "skc_id": "SKC-1",
                "offer_id": "123456",
                "source_url": "https://detail.1688.com/offer/123456.html",
                "source_title": "source",
                "source_decision": "recommended",
            },
        ),
    )
    service = SourcingService(
        repository=repository,
        plugin_bridge=PluginBridgeService(repository=repository),
    )
    state = service.complete_batch_sourcing(
        PriceVerificationActor(actor_id="actor-1", workspace_id="workspace-1"),
        batch_id=batch.batch_id,
    )
    assert state["unresolved_skc_ids"] == ("SKC-2",)
    assert [item["skc_id"] for item in state["preview"]["items"]] == ["SKC-2"]
    assert state["preview"]["search_status"] == "failed"
    assert state["preview"]["all_failed"] is True


@dataclass
class _ProviderState:
    active: int = 0
    maximum: int = 0


def test_onebound_search_is_bounded_parallel_ordered_and_failure_isolated(tmp_path) -> None:
    repository = PriceVerificationRepository(tmp_path / "onebound.sqlite3")
    state = _ProviderState()
    lock = threading.Lock()
    enough_workers = threading.Event()

    class Provider:
        def search_by_image(self, criteria):
            with lock:
                state.active += 1
                state.maximum = max(state.maximum, state.active)
                if state.active >= 3:
                    enough_workers.set()
            enough_workers.wait(timeout=2)
            try:
                if criteria.reference_image_url.endswith("bad.jpg"):
                    raise RuntimeError("provider boom")
                return SimpleNamespace(response={}, audits=(), error=None)
            finally:
                with lock:
                    state.active -= 1

        def get_item_detail(self, offer_id):
            raise AssertionError("empty search results need no detail call")

    tasks = tuple(
        SourceSearchTask(
            task_key=f"SKC-{index}",
            skc_id=f"SKC-{index}",
            main_image_url=f"https://images.example/{'bad' if index == 2 else index}.jpg",
            source_quote_keys=(f"Q-{index}",),
        )
        for index in range(5)
    )
    result = OneBoundSourceAdapter(repository, Provider).search_by_image(
        PriceVerificationActor(actor_id="actor-1", workspace_id="workspace-1"), tasks
    )
    assert 3 <= state.maximum <= 4
    assert [item["skc_id"] for item in result["items"]] == [task.skc_id for task in tasks]
    assert [item["skc_id"] for item in result["items"] if item["status"] == "failed"] == ["SKC-2"]
    assert result["status"] == "partial"
    assert result["all_failed"] is False

    preview = build_source_preview([task.to_payload() for task in tasks], result)
    assert preview["search_status"] == "partial"
    assert preview["failed_skc_ids"] == ["SKC-2"]


def test_product_library_requires_valid_source_and_surfaces_query_failure(tmp_path) -> None:
    repository = PriceVerificationRepository(tmp_path / "library.sqlite3")
    actor = PriceVerificationActor(actor_id="actor-1", workspace_id="workspace-1")

    class Library:
        def list_products(self, **_kwargs):
            return (
                {"skc": "NO-SOURCE", "title": "stale product"},
                {
                    "skc": "VALID",
                    "source_groups": [
                        {
                            "offer_id": "654321",
                            "source_url": "https://detail.1688.com/offer/654321.html",
                        }
                    ],
                },
            )

    service = SourcingService(
        repository=repository,
        plugin_bridge=PluginBridgeService(repository=repository),
        product_library_service=Library(),
    )
    products = service._product_library_products(actor, ("NO-SOURCE", "VALID"))
    assert [product["skc"] for product in products] == ["VALID"]

    class BrokenLibrary:
        def list_products(self, **_kwargs):
            raise TimeoutError("library timed out")

    broken = SourcingService(
        repository=repository,
        plugin_bridge=PluginBridgeService(repository=repository),
        product_library_service=BrokenLibrary(),
    )
    with pytest.raises(ProductLibraryLookupError, match="产品库货源查询失败"):
        broken._product_library_products(actor, ("VALID",))
