from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from wh_local.data_collection.plugin_queue import DataCollectionPluginQueue
from wh_local.price_verification.contracts import PriceVerificationActor
from wh_local.price_verification.plugin.shared_gateway import SharedPluginGateway
from wh_local.price_verification.quote_normalizer import (
    normalize_price_quote_discovery,
    official_temu_link,
)
from wh_local.price_verification.quote_service import QuoteService
from wh_local.price_verification.repository import PriceVerificationRepository
from wh_local.price_verification.sourcing.service import (
    IncompleteRetainedQuotesError,
    NoRetainedQuotesError,
    QuoteDecisionRequiredError,
    SourcingService,
)
from wh_local.price_verification.sourcing.contracts import SourceSearchTask
from wh_local.price_verification.sourcing.onebound_adapter import OneBoundSourceAdapter


def _actor() -> PriceVerificationActor:
    return PriceVerificationActor(actor_id="user-1", workspace_id="workspace-A")


def _services(tmp_path: Path):
    database_path = tmp_path / "runtime.sqlite3"
    repository = PriceVerificationRepository(database_path)
    queue = DataCollectionPluginQueue(database_path)
    session = queue.create_session(
        actor_id="user-1",
        workspace_id="workspace-A",
        capabilities={
            "temu_price_quote_discovery": True,
            "source_browser_image_search": True,
        },
    )
    gateway = SharedPluginGateway(queue)
    quote_service = QuoteService(
        repository=repository,
        plugin_gateway=gateway,
        output_root=tmp_path / "outputs",
    )
    sourcing_service = SourcingService(repository=repository, plugin_gateway=gateway)
    return repository, queue, session, gateway, quote_service, sourcing_service


def _seed_quote_run(repository: PriceVerificationRepository) -> str:
    return repository.create_quote_run(
        workspace_id="workspace-A",
        command_id="1",
        items=[
            {
                "quote_key": "quote-1",
                "skc_id": "SKC-SAME",
                "sku_id": "SKU-1",
                "spu_or_goods_id": "1001",
                "official_link_url": "https://www.temu.com/goods.html?goods_id=1001",
                "main_image_url": "https://img.example/1001.jpg",
                "adjusted_declared_price_cny": "19.90",
                "product_title": "商品一",
            },
            {
                "quote_key": "quote-2",
                "skc_id": "SKC-SAME",
                "sku_id": "SKU-2",
                "spu_or_goods_id": "1002",
                "official_link_url": "https://www.temu.com/goods.html?goods_id=1002",
                "main_image_url": "https://img.example/1002.jpg",
                "adjusted_declared_price_cny": "29.90",
                "product_title": "商品二",
            },
        ],
    ).run_id


def test_quote_normalizer_preserves_or_builds_official_temu_link() -> None:
    preview = normalize_price_quote_discovery(
        {
            "records": [
                {
                    "url": "https://seller.temu.com/bargain-no-bom/batch/info/query",
                    "method": "POST",
                    "status": 200,
                    "capturedAt": "2026-08-05T00:00:00Z",
                    "responseJson": {
                        "priceReviewItemList": [
                            {
                                "skcId": "SKC-1",
                                "skuInfoList": [
                                    {
                                        "skuId": "SKU-1",
                                        "goodsId": "1001",
                                        "suggestSupplyPrice": "19.90",
                                        "productUrl": "https://www.temu.com/goods.html?goods_id=1001&refer_page_name=search",
                                        "imageUrl": "https://img.example/1001.jpg",
                                    }
                                ],
                            },
                            {
                                "skcId": "SKC-2",
                                "skuInfoList": [
                                    {
                                        "skuId": "SKU-2",
                                        "goodsId": "1002",
                                        "suggestSupplyPrice": "29.90",
                                        "imageUrl": "https://img.example/1002.jpg",
                                    }
                                ],
                            },
                        ]
                    },
                }
            ]
        }
    )

    links = {item.sku_id: item.official_link_url for item in preview.quotes}
    assert links["SKU-1"] == "https://www.temu.com/goods.html?goods_id=1001"
    assert links["SKU-2"] == "https://www.temu.com/goods.html?goods_id=1002"


def test_quote_normalizer_keeps_official_slug_link_without_goods_query() -> None:
    assert official_temu_link(
        {
            "productUrl": (
                "https://www.temu.com/womens-shirt-g-601099999999999.html"
                "?refer_page_name=search&session_id=do-not-keep"
            )
        }
    ) == "https://www.temu.com/womens-shirt-g-601099999999999.html"


def test_source_search_rejects_quote_run_without_human_decisions(tmp_path: Path) -> None:
    repository, _, session, _, _, sourcing_service = _services(tmp_path)
    quote_run_id = _seed_quote_run(repository)

    with pytest.raises(QuoteDecisionRequiredError):
        sourcing_service.queue_browser_search(
            _actor(),
            session_id=str(session["session_id"]),
            quote_run_id=quote_run_id,
            idempotency_key="source-1",
        )


def test_source_search_queues_only_retained_links(tmp_path: Path) -> None:
    repository, _, session, _, quote_service, sourcing_service = _services(tmp_path)
    quote_run_id = _seed_quote_run(repository)
    quote_service.record_decision(_actor(), quote_run_id, "quote-1", "retained", "保留")
    quote_service.record_decision(_actor(), quote_run_id, "quote-2", "rejected", "拒绝")

    command = sourcing_service.queue_browser_search(
        _actor(),
        session_id=str(session["session_id"]),
        quote_run_id=quote_run_id,
        idempotency_key="source-1",
    )

    assert [task["quote_key"] for task in command.payload["tasks"]] == ["quote-1"]
    assert command.payload["tasks"][0]["task_key"] == "quote-1"
    assert command.payload["tasks"][0]["official_link_url"].endswith("goods_id=1001")
    assert command.payload["tasks"][0]["selected_price_cny"] == "19.90"


def test_two_retained_links_of_same_skc_remain_separate_tasks(tmp_path: Path) -> None:
    repository, _, session, _, quote_service, sourcing_service = _services(tmp_path)
    quote_run_id = _seed_quote_run(repository)
    for quote_key in ("quote-1", "quote-2"):
        quote_service.record_decision(_actor(), quote_run_id, quote_key, "retained", "")

    command = sourcing_service.queue_browser_search(
        _actor(),
        session_id=str(session["session_id"]),
        quote_run_id=quote_run_id,
        idempotency_key="source-separate",
    )

    assert [task["task_key"] for task in command.payload["tasks"]] == ["quote-1", "quote-2"]
    assert [task["source_quote_keys"] for task in command.payload["tasks"]] == [
        ["quote-1"],
        ["quote-2"],
    ]


def test_source_search_rejects_all_rejected_quote_run(tmp_path: Path) -> None:
    repository, _, session, _, quote_service, sourcing_service = _services(tmp_path)
    quote_run_id = _seed_quote_run(repository)
    for quote_key in ("quote-1", "quote-2"):
        quote_service.record_decision(_actor(), quote_run_id, quote_key, "rejected", "")

    with pytest.raises(NoRetainedQuotesError):
        sourcing_service.queue_browser_search(
            _actor(),
            session_id=str(session["session_id"]),
            quote_run_id=quote_run_id,
            idempotency_key="source-none",
        )


def test_source_search_rejects_retained_link_with_incomplete_evidence(tmp_path: Path) -> None:
    repository, _, session, _, quote_service, sourcing_service = _services(tmp_path)
    quote_run_id = repository.create_quote_run(
        workspace_id="workspace-A",
        command_id="1",
        items=[
            {
                "quote_key": "quote-incomplete",
                "skc_id": "SKC-1",
                "sku_id": "SKU-1",
                "official_link_url": "",
                "main_image_url": "https://img.example/1001.jpg",
                "adjusted_declared_price_cny": "19.90",
            }
        ],
    ).run_id
    quote_service.record_decision(
        _actor(), quote_run_id, "quote-incomplete", "retained", ""
    )

    with pytest.raises(IncompleteRetainedQuotesError, match="quote-incomplete"):
        sourcing_service.queue_browser_search(
            _actor(),
            session_id=str(session["session_id"]),
            quote_run_id=quote_run_id,
            idempotency_key="source-incomplete",
        )


def test_materialized_source_run_keeps_queue_time_snapshot(tmp_path: Path) -> None:
    repository, queue, session, gateway, quote_service, sourcing_service = _services(tmp_path)
    quote_run_id = _seed_quote_run(repository)
    quote_service.record_decision(_actor(), quote_run_id, "quote-1", "retained", "")
    quote_service.record_decision(_actor(), quote_run_id, "quote-2", "rejected", "")
    queued = sourcing_service.queue_browser_search(
        _actor(),
        session_id=str(session["session_id"]),
        quote_run_id=quote_run_id,
        idempotency_key="source-freeze",
    )
    quote_service.record_decision(_actor(), quote_run_id, "quote-1", "rejected", "后来拒绝")
    queue.poll(str(session["session_token"]))
    queue.receive_result(
        session_token=str(session["session_token"]),
        command_id=int(queued.command_id),
        status="succeeded",
        result={
            "items": [
                {
                    "task_key": "quote-1",
                    "source_quote_keys": ["quote-1"],
                    "status": "succeeded",
                    "candidates": [],
                }
            ]
        },
    )

    sourcing_run = sourcing_service.materialize_browser_result(
        _actor(), gateway.get_command(_actor(), queued.command_id)
    )
    frozen = repository.list_sourcing_run_quotes(
        workspace_id="workspace-A", sourcing_run_id=sourcing_run.run_id
    )

    assert [item.quote_key for item in frozen] == ["quote-1"]
    assert frozen[0].official_link_url.endswith("goods_id=1001")


def test_retry_uses_failed_frozen_retained_link_through_shared_gateway(tmp_path: Path) -> None:
    repository, queue, session, gateway, quote_service, sourcing_service = _services(tmp_path)
    quote_run_id = _seed_quote_run(repository)
    quote_service.record_decision(_actor(), quote_run_id, "quote-1", "retained", "")
    quote_service.record_decision(_actor(), quote_run_id, "quote-2", "rejected", "")
    queued = sourcing_service.queue_browser_search(
        _actor(),
        session_id=str(session["session_id"]),
        quote_run_id=quote_run_id,
        idempotency_key="source-failed",
    )
    queue.poll(str(session["session_token"]))
    queue.receive_result(
        session_token=str(session["session_token"]),
        command_id=int(queued.command_id),
        status="succeeded",
        result={"items": [{"task_key": "quote-1", "status": "failed", "error": "timeout"}]},
    )
    sourcing_run = sourcing_service.materialize_browser_result(
        _actor(), gateway.get_command(_actor(), queued.command_id)
    )

    retry = sourcing_service.retry_failed_items(
        _actor(),
        sourcing_run_id=sourcing_run.run_id,
        session_id=str(session["session_id"]),
        idempotency_key="source-retry",
    )

    assert retry.payload["retry_of_sourcing_run_id"] == sourcing_run.run_id
    assert [task["quote_key"] for task in retry.payload["tasks"]] == ["quote-1"]
    assert retry.payload["source_quotes"][0]["official_link_url"].endswith("goods_id=1001")


def test_onebound_adapter_delegates_single_upload_to_provider_search(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")

    class Provider:
        search_calls = 0

        def upload_reference_image(self, reference_image_url: str):
            raise AssertionError("adapter must not upload before provider search")

        def search_by_image(self, criteria: object):
            self.search_calls += 1
            return SimpleNamespace(response={"items": []}, audits=(), error=None)

        def get_item_detail(self, offer_id: str):
            raise AssertionError("empty search must not request details")

    provider = Provider()
    adapter = OneBoundSourceAdapter(repository, lambda: provider)
    task = SourceSearchTask(
        task_key="quote-1",
        quote_key="quote-1",
        skc_id="SKC-1",
        main_image_url="https://img.example/1001.jpg",
        official_link_url="https://www.temu.com/goods.html?goods_id=1001",
        selected_price_cny="19.90",
        source_quote_keys=("quote-1",),
    )

    result = adapter.search_by_image(_actor(), (task,))

    assert provider.search_calls == 1
    assert result["items"][0]["status"] == "succeeded"
