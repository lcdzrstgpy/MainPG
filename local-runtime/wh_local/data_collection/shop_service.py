"""Application service for whole-shop batch commands and queries."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from .service import DailySelectionActor
from .shop_contracts import ShopBatch, ShopBatchItemPage, ShopBatchPage
from .shop_repository import InvalidShopBatchTransition, ShopCollectionRepository


class ShopCollectionInputError(ValueError):
    pass


class ShopCollectionConflict(ValueError):
    pass


class ShopCollectionProviderUnavailable(RuntimeError):
    pass


class ShopCollectionService:
    def __init__(
        self,
        *,
        repository: ShopCollectionRepository,
        provider_config_resolver: Callable[[DailySelectionActor], Mapping[str, Any]],
        worker: Any,
        batch_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.repository = repository
        self._provider_config_resolver = provider_config_resolver
        self._worker = worker
        self._batch_id_factory = batch_id_factory or (lambda: f"shop-{uuid.uuid4().hex}")

    def create_batch(self, *, actor: DailySelectionActor, source_input: str) -> ShopBatch:
        shop_sid, seed_offer_id, shop_url = _parse_source_input(source_input)
        try:
            config = self._provider_config_resolver(actor)
        except Exception as error:
            raise ShopCollectionProviderUnavailable("shop collection provider is unavailable") from error
        if not isinstance(config, Mapping) or config.get("enabled", True) is False:
            raise ShopCollectionProviderUnavailable("shop collection provider is unavailable")
        batch = self.repository.create_batch(
            batch_id=self._batch_id_factory(),
            workspace_id=actor.workspace_id,
            actor_id=actor.actor_id,
            shop_sid=shop_sid,
            shop_url=shop_url,
            seed_offer_id=seed_offer_id,
            max_pages=100,
        )
        self._worker.notify()
        return batch

    def list_batches(self, *, actor: DailySelectionActor, limit: int, offset: int) -> ShopBatchPage:
        return ShopBatchPage(
            items=self.repository.list_batches(workspace_id=actor.workspace_id, limit=limit, offset=offset),
            total=self.repository.count_batches(workspace_id=actor.workspace_id),
        )

    def get_batch(self, *, actor: DailySelectionActor, batch_id: str) -> ShopBatch:
        return self.repository.get_batch(workspace_id=actor.workspace_id, batch_id=batch_id)

    def list_items(self, *, actor: DailySelectionActor, batch_id: str, limit: int, offset: int) -> ShopBatchItemPage:
        return ShopBatchItemPage(
            items=self.repository.list_items(
                workspace_id=actor.workspace_id, batch_id=batch_id, limit=limit, offset=offset
            ),
            total=self.repository.count_items(workspace_id=actor.workspace_id, batch_id=batch_id),
        )

    def pause(self, *, actor: DailySelectionActor, batch_id: str) -> ShopBatch:
        batch = self.get_batch(actor=actor, batch_id=batch_id)
        allowed = {"queued", "resolving", "listing", "enriching"}
        if batch.status not in allowed:
            raise ShopCollectionConflict("batch cannot be paused in its current state")
        result = self._transition(batch_id, "pausing", allowed)
        self._worker.notify()
        return result

    def resume(self, *, actor: DailySelectionActor, batch_id: str) -> ShopBatch:
        batch = self.get_batch(actor=actor, batch_id=batch_id)
        if batch.status != "paused":
            raise ShopCollectionConflict("only paused batches can be resumed")
        result = self._transition(batch_id, "queued", {"paused"})
        self._worker.notify()
        return result

    def cancel(self, *, actor: DailySelectionActor, batch_id: str) -> ShopBatch:
        batch = self.get_batch(actor=actor, batch_id=batch_id)
        allowed = {"queued", "resolving", "listing", "enriching", "pausing", "paused"}
        if batch.status not in allowed:
            raise ShopCollectionConflict("batch cannot be cancelled in its current state")
        result = self._transition(batch_id, "cancelling", allowed)
        self._worker.notify()
        return result

    def retry_failed(self, *, actor: DailySelectionActor, batch_id: str) -> ShopBatch:
        batch = self.get_batch(actor=actor, batch_id=batch_id)
        if batch.status not in {"failed", "partial"}:
            raise ShopCollectionConflict("only failed or partial batches can be retried")
        self.repository.reset_failed_items(batch_id=batch_id)
        try:
            result = self.repository.transition_batch(
                batch_id, "queued", expected_statuses={"failed", "partial"}
            )
        except InvalidShopBatchTransition as error:
            raise ShopCollectionConflict(str(error)) from error
        self._worker.notify()
        return result

    def _transition(self, batch_id: str, status: str, expected: set[str]) -> ShopBatch:
        try:
            return self.repository.transition_batch(
                batch_id, status, expected_statuses=expected
            )
        except InvalidShopBatchTransition as error:
            raise ShopCollectionConflict(str(error)) from error


def _parse_source_input(source_input: str) -> tuple[str, str, str]:
    if not isinstance(source_input, str) or not source_input.strip():
        raise ShopCollectionInputError("source_input is required")
    value = source_input.strip()
    if len(value) > 4096:
        raise ShopCollectionInputError("source_input is too long")

    if re.fullmatch(r"[A-Za-z_@-][A-Za-z0-9_@-]{2,127}", value) and "://" not in value:
        try:
            from .shop_parsing import validate_shop_sid

            return validate_shop_sid(value), "", ""
        except ImportError:
            return value, "", ""

    try:
        from .shop_parsing import extract_1688_offer_id

        offer_id = extract_1688_offer_id(value)
    except ImportError:
        offer_id = _fallback_offer_id(value)
    except ValueError as error:
        raise ShopCollectionInputError(str(error)) from error
    return f"pending:{offer_id}", offer_id, value if "://" in value else ""


def _fallback_offer_id(value: str) -> str:
    compact = re.sub(r"\s+", "", value)
    if compact.isdigit() and 8 <= len(compact) <= 20:
        return compact
    if "1688.com" not in compact.casefold():
        raise ShopCollectionInputError("only a 1688 shop link, offer link, offer ID, or shop SID is supported")
    for pattern in (r"/offer/(\d{8,20})(?:\.html)?", r"offerId(?:=|-|%3D)(\d{8,20})"):
        match = re.search(pattern, compact, re.IGNORECASE)
        if match:
            return match.group(1)
    raise ShopCollectionInputError("the 1688 link does not contain an offer ID")
