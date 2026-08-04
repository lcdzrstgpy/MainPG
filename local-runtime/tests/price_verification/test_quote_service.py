from __future__ import annotations

import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from wh_local.price_verification.contracts import (  # noqa: E402
    PluginCommandRequest,
    PriceVerificationActor,
)
from wh_local.price_verification.plugin.service import PluginBridgeService  # noqa: E402
from wh_local.price_verification.quote_service import QuoteService  # noqa: E402
from wh_local.price_verification.repository import (  # noqa: E402
    PluginCommandRecord,
    PriceVerificationNotFound,
    PriceVerificationRepository,
)


def actor(workspace_id: str) -> PriceVerificationActor:
    return PriceVerificationActor(actor_id=f"user-{workspace_id}", workspace_id=workspace_id)


@pytest.fixture
def repository(tmp_path: Path) -> PriceVerificationRepository:
    repository = PriceVerificationRepository(tmp_path / "runtime.sqlite3")
    yield repository
    repository.close()


@pytest.fixture
def service(repository: PriceVerificationRepository, tmp_path: Path) -> QuoteService:
    return QuoteService(
        repository=repository,
        plugin_bridge=PluginBridgeService(repository=repository),
        output_root=tmp_path / "outputs",
    )


def completed_quote_command(repository: PriceVerificationRepository, skc_id: str) -> PluginCommandRecord:
    session = repository.create_plugin_session(
        workspace_id="A", session_token_hash=(skc_id.encode().hex() * 64)[:64], browser="Edge"
    )
    command = repository.create_command(
        workspace_id="A",
        session_id=session.session_id,
        request=PluginCommandRequest(
            command_type="temu_price_quote_discovery", payload={}, idempotency_key=f"request-{skc_id}"
        ),
    )
    repository.lease_plugin_commands(
        workspace_id="A",
        session_id=session.session_id,
        command_types=("temu_price_quote_discovery",),
        now="2026-08-04T09:00:00+00:00",
        lease_expires_at="2026-08-04T09:02:00+00:00",
        limit=1,
    )
    return repository.record_plugin_result(
        workspace_id="A",
        session_id=session.session_id,
        command_id=command.command_id,
        status="succeeded",
        result={
            "records": [
                {
                    "method": "GET",
                    "url": "https://seller.temu.example/price/quote",
                    "capturedAt": "2026-08-04T09:00:00+00:00",
                    "status": 200,
                    "responseJson": {
                        "data": {
                            "priceReviewItemList": [
                                {
                                    "skcId": skc_id,
                                    "skuId": f"SKU-{skc_id}",
                                    "site": "US",
                                    "supplyPrice": "20.00",
                                    "suggestSupplyPrice": "18.90",
                                    "productTitle": "Test product",
                                    "mainImageUrl": "https://images.example/product.jpg",
                                }
                            ]
                        }
                    },
                }
            ]
        },
        now="2026-08-04T09:00:01+00:00",
    )


def test_each_completed_capture_creates_a_new_immutable_snapshot(
    service: QuoteService, repository: PriceVerificationRepository
) -> None:
    first = service.materialize_completed_command(actor("A"), completed_quote_command(repository, "SKC-1"))
    second = service.materialize_completed_command(actor("A"), completed_quote_command(repository, "SKC-2"))

    assert first.run_id != second.run_id
    assert service.get_preview(actor("A"), first.run_id).quotes[0].skc_id == "SKC-1"


def test_queue_uses_the_read_only_quote_command_and_workspace_idempotency(
    service: QuoteService, repository: PriceVerificationRepository
) -> None:
    session = repository.create_plugin_session(
        workspace_id="A", session_token_hash="a" * 64, browser="Edge"
    )

    first = service.queue_collection(
        actor("A"), session_id=session.session_id, payload={"query": "lamp"}, idempotency_key="same"
    )
    second = service.queue_collection(
        actor("A"), session_id=session.session_id, payload={"query": "lamp"}, idempotency_key="same"
    )

    assert first.command_type == "temu_price_quote_discovery"
    assert second.command_id == first.command_id


def test_materialize_requires_a_succeeded_quote_command(
    service: QuoteService, repository: PriceVerificationRepository
) -> None:
    session = repository.create_plugin_session(
        workspace_id="A", session_token_hash="a" * 64, browser="Edge"
    )
    queued = service.queue_collection(
        actor("A"), session_id=session.session_id, payload={}, idempotency_key="queued"
    )

    with pytest.raises(ValueError, match="succeeded"):
        service.materialize_completed_command(actor("A"), queued)


def test_preview_is_workspace_isolated(
    service: QuoteService, repository: PriceVerificationRepository
) -> None:
    run = service.materialize_completed_command(actor("A"), completed_quote_command(repository, "SKC-1"))

    with pytest.raises(PriceVerificationNotFound):
        service.get_preview(actor("B"), run.run_id)
