from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from wh_local.price_verification.repository import BatchSelectionRecord, SkcSourceLinkRecord
from wh_local.price_verification.sourcing.service import SourcingService


class _Repo:
    def __init__(self, batch: Any, selection: BatchSelectionRecord, links: list[SkcSourceLinkRecord]):
        self.batch = batch
        self.selection = selection
        self.links = links

    def get_quote_capture_batch(self, *, workspace_id: str, batch_id: str) -> Any:
        return self.batch

    def get_batch_selection_by_skc(self, *, workspace_id: str, batch_id: str, skc_id: str) -> BatchSelectionRecord:
        return self.selection

    def list_skc_source_links(self, *, workspace_id: str, batch_id: str, skc_id: str | None = None):
        return tuple(self.links)


class _Library:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def upsert_product(self, payload: dict[str, Any], *, actor: Any = None) -> dict[str, Any]:
        self.calls.append(dict(payload))
        return dict(payload)


def _service(repo: _Repo, library: _Library) -> SourcingService:
    service = object.__new__(SourcingService)
    service._repository = repo
    service._product_library_service = library
    return service


def _selection() -> BatchSelectionRecord:
    return BatchSelectionRecord(
        id=1, workspace_id="ws", batch_id="b1", skc_id="SKC001",
        site="US", adjusted_min="20", status="retained", main_image_url="http://img/main.jpg",
    )


def _link(offer_id: str, price: str) -> SkcSourceLinkRecord:
    return SkcSourceLinkRecord(
        id=int(offer_id), workspace_id="ws", batch_id="b1", skc_id="SKC001",
        offer_id=offer_id, source_url=f"https://detail.1688.com/offer/{offer_id}.html",
        price_cny=price, moq="1", domestic_freight_cny="0",
    )


def _batch() -> Any:
    return SimpleNamespace(archive_product_id_type="SKC", store_name="测试店")


def test_sync_uses_cheapest_link_for_source_url_and_cost() -> None:
    # 两条货源：高价在前、低价在后，最低价的 offer 是 222222222。
    repo = _Repo(
        _batch(),
        _selection(),
        [_link("111111111", "10"), _link("222222222", "5")],
    )
    library = _Library()
    actor = SimpleNamespace(workspace_id="ws", actor_id="a")

    _service(repo, library)._sync_skc_to_product_library(actor, batch_id="b1", skc_id="SKC001")

    assert library.calls
    call = library.calls[0]
    # 顶层 source_url 必须与成本同源（最低价那条），而不是列表第一条。
    assert "222222222" in call["source_url"]
    groups = json.loads(call["source_groups_json"])
    assert [group["offer_id"] for group in groups] == ["111111111", "222222222"]


def test_sync_clears_sources_when_last_link_is_removed() -> None:
    repo = _Repo(
        _batch(),
        _selection(),
        [],
    )
    library = _Library()
    actor = SimpleNamespace(workspace_id="ws", actor_id="a")

    _service(repo, library)._sync_skc_to_product_library(actor, batch_id="b1", skc_id="SKC001")

    # 移除最后一条关联后，仍应写入产品库，清空货源链接而非残留旧数据。
    assert library.calls
    call = library.calls[0]
    assert call["source_url"] == ""
    assert call["source_groups_json"] == "[]"
