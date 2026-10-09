from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from wh_local.price_verification.contracts import PriceVerificationActor
from wh_local.price_verification.repository import BatchSelectionRecord, SkcSourceLinkRecord
from wh_local.price_verification.sourcing.service import SourcingService


class _BatchRepo:
    """最小仓储桩：只提供批量完成关联所需的查询与写入。"""

    def __init__(self, selection: BatchSelectionRecord, link_price: str = "5") -> None:
        self.selection = selection
        self.link_price = link_price
        self.saved_session: dict[str, Any] | None = None

    def get_batch_selection_by_skc(self, *, workspace_id: str, batch_id: str, skc_id: str) -> BatchSelectionRecord:
        return self.selection

    def upsert_skc_source_link(self, **kwargs: Any) -> SkcSourceLinkRecord:
        return SkcSourceLinkRecord(
            id=1, workspace_id=kwargs["workspace_id"], batch_id=kwargs["batch_id"],
            skc_id=kwargs["skc_id"], offer_id=kwargs["offer_id"], source_url=kwargs["source_url"],
            price_cny=kwargs.get("price_cny"), weight_kg=kwargs.get("weight_kg"),
            moq=kwargs.get("moq"), domestic_freight_cny=kwargs.get("domestic_freight_cny"),
            source_decision=kwargs.get("source_decision", ""), note=kwargs.get("note", ""),
        )

    def save_batch_sourcing_session(self, **kwargs: Any) -> dict[str, Any]:
        self.saved_session = dict(kwargs)
        return dict(kwargs)


def _candidate(skc_id: str, offer_id: str) -> dict[str, Any]:
    return {
        "skc_id": skc_id, "offer_id": offer_id,
        "source_url": f"https://detail.1688.com/offer/{offer_id}.html",
        "source_title": "", "main_image_url": "",
        "price_cny": "10", "weight_kg": None, "moq": "1", "domestic_freight_cny": "0",
        "source_decision": "",
    }


def _service(repo: _BatchRepo, session: dict[str, Any]) -> tuple[SourcingService, list[str]]:
    service = object.__new__(SourcingService)
    service._repository = repo
    service._product_library_service = None
    synced: list[str] = []

    def _fake_sync(actor: Any, *, batch_id: str, skc_id: str) -> None:
        synced.append(skc_id)

    service._sync_skc_to_product_library = _fake_sync
    service.get_batch_sourcing_state = lambda actor, batch_id: session
    service._product_library_products = lambda actor, batch_id, skc_ids: ()
    return service, synced


def test_complete_batch_sourcing_syncs_each_skc_once() -> None:
    # 三条候选：SKC001 两条、SKC002 一条。
    session = {
        "selected_candidates": (
            _candidate("SKC001", "111111111"),
            _candidate("SKC001", "222222222"),
            _candidate("SKC002", "333333333"),
        ),
        "selected_skc_ids": ("SKC001", "SKC002"),
        "unresolved_skc_ids": (),
        "matched_products": (),
        "preview": {},
    }
    selection = BatchSelectionRecord(
        id=1, workspace_id="ws", batch_id="b1", skc_id="SKC001",
        site="US", adjusted_min="20", status="retained",
    )
    repo = _BatchRepo(selection)
    service, synced = _service(repo, session)

    service.complete_batch_sourcing(
        PriceVerificationActor(actor_id="a", workspace_id="ws"), batch_id="b1"
    )

    # 每个受影响的 SKC 只同步一次（3 条候选 → 2 个 SKC），且一个都不能漏。
    assert synced == ["SKC001", "SKC002"]
    assert repo.saved_session is not None
