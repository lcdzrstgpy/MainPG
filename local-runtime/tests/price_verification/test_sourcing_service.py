from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.contracts import PriceVerificationActor  # noqa: E402
from wh_local.price_verification.quote_normalizer import QuoteItem  # noqa: E402
from wh_local.price_verification.plugin.service import PluginBridgeService  # noqa: E402
from wh_local.price_verification.repository import PriceVerificationRepository  # noqa: E402
from wh_local.price_verification.sourcing.service import SourcingService, build_source_preview  # noqa: E402


def complete_quote(skc_id: str, *, sku_attributes: str = "红色") -> QuoteItem:
    return QuoteItem(
        skc_id=skc_id,
        sku_id=f"SKU-{skc_id}",
        product_title="同款收纳盒",
        sku_attribute_text=sku_attributes,
        original_declared_price_cny=Decimal("20"),
        main_image_url="https://images.example/product.jpg",
    )


def source_result_with_candidate(**candidate: object) -> dict[str, object]:
    return {"items": [{"skc_id": "SKC-1", "status": "succeeded", "candidates": [candidate]}]}


def test_same_product_with_closed_cost_is_recommended() -> None:
    preview = build_source_preview(
        [complete_quote("SKC-1")],
        source_result_with_candidate(price=10, freight=2, title="同款收纳盒 红色", variants=["红色"]),
    )

    assert preview["items"][0]["candidates"][0]["source_decision"] == "recommended"


def test_variant_conflict_requires_review() -> None:
    preview = build_source_preview(
        [complete_quote("SKC-1", sku_attributes="红色")],
        source_result_with_candidate(title="蓝色款", price=10, freight=2, variants=["蓝色"]),
    )

    assert preview["items"][0]["source_review_candidates"]
    assert preview["items"][0]["source_decision"] == "review"


def test_missing_sku_evidence_creates_validation_target() -> None:
    preview = build_source_preview(
        [complete_quote("SKC-1")],
        source_result_with_candidate(title="同款收纳盒", price=10, freight=2),
    )

    assert preview["items"][0]["source_decision"] == "sku_validation"
    assert preview["source_sku_validation_targets"][0]["skc_id"] == "SKC-1"


def test_preview_keeps_partial_successes_and_only_failed_items_retryable() -> None:
    preview = build_source_preview(
        [complete_quote("SKC-1"), complete_quote("SKC-2")],
        {
            "items": [
                {"skc_id": "SKC-1", "status": "succeeded", "candidates": [{"title": "同款收纳盒 红色", "price": 10, "freight": 2, "variants": ["红色"]}]},
                {"skc_id": "SKC-2", "status": "failed", "error": "page unavailable"},
            ]
        },
    )

    assert preview["counts"]["recommended_quotes"] == 1
    assert preview["counts"]["failed_quotes"] == 1
    assert preview["retry_quote_keys"] == ["SKC-2:SKU-SKC-2"]


def test_retry_materialization_preserves_parent_successes(tmp_path: Path) -> None:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    bridge = PluginBridgeService(repository=repository)
    actor = PriceVerificationActor(actor_id="user-a", workspace_id="workspace-a")
    pairing = bridge.issue_pairing_code(actor)
    session = bridge.connect(pairing.code, browser_name="Edge", capabilities={})
    quote_run = repository.create_quote_run(
        workspace_id=actor.workspace_id,
        command_id="quote-command",
        items=[
            {"quote_key": "SKC-1:SKU-SKC-1", "skc_id": "SKC-1", "sku_id": "SKU-SKC-1", "product_title": "同款收纳盒", "sku_attribute_text": "红色", "original_declared_price_cny": "20", "main_image_url": "https://images.example/one.jpg"},
            {"quote_key": "SKC-2:SKU-SKC-2", "skc_id": "SKC-2", "sku_id": "SKU-SKC-2", "product_title": "同款收纳盒", "sku_attribute_text": "红色", "original_declared_price_cny": "20", "main_image_url": "https://images.example/two.jpg"},
        ],
    )
    service = SourcingService(repository=repository, plugin_bridge=bridge)
    first = service.queue_browser_search(actor, session_id=session.session_id, quote_run_id=quote_run.run_id, idempotency_key="first")
    bridge.poll(session.token)
    bridge.receive_result(session.token, first.command_id, "succeeded", {
        "items": [
            {"quote_key": "SKC-1:SKU-SKC-1", "status": "succeeded", "candidates": [{"title": "同款收纳盒 红色", "variants": ["红色"], "price": 10, "freight": 2}]},
            {"quote_key": "SKC-2:SKU-SKC-2", "status": "failed", "error": "temporary"},
        ]
    })
    first_run = service.materialize_browser_result(actor, first)
    retry = service.retry_failed_items(actor, sourcing_run_id=first_run.run_id, session_id=session.session_id, idempotency_key="retry")
    bridge.poll(session.token)
    bridge.receive_result(session.token, retry.command_id, "succeeded", {
        "items": [{"quote_key": "SKC-2:SKU-SKC-2", "status": "succeeded", "candidates": [{"title": "同款收纳盒 红色", "variants": ["红色"], "price": 11, "freight": 2}]}]
    })
    retried_run = service.materialize_browser_result(actor, retry)

    preview = service.preview(actor, retried_run.run_id)
    assert preview["counts"]["recommended_quotes"] == 2
    assert preview["retry_quote_keys"] == []
