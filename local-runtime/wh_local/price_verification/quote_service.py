"""Persisted, read-only quote snapshots and their local exports."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, fields
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from .contracts import PluginCommandRequest, PriceVerificationActor, PriceVerificationContractError
from .exports import ExportedQuoteRun, export_quote_snapshot
from .plugin.service import PluginBridgeService
from .plugin.shared_gateway import SharedPluginGateway
from .quote_normalizer import (
    QuoteCounts,
    QuoteItem,
    QuotePreview,
    is_complete_quote,
    needs_review,
    normalize_price_quote_discovery,
    quote_product_count,
)
from .repository import (
    PluginCommandRecord,
    PriceVerificationNotFound,
    PriceVerificationRepository,
    QuoteRunRecord,
)


_MONEY_FIELDS = frozenset(
    {
        "original_declared_price_cny",
        "adjusted_declared_price_cny",
        "new_declared_price_cny",
    }
)
_QUOTE_FIELDS = frozenset(field.name for field in fields(QuoteItem))


class QuoteService:
    """Queue read-only capture commands and materialize immutable snapshots.

    The service deliberately never accepts raw capture data for export. A
    command result is normalized once when it reaches its terminal succeeded
    state, then every preview and export is reconstructed from SQLite.
    """

    def __init__(
        self,
        *,
        repository: PriceVerificationRepository,
        output_root: str | Path,
        plugin_gateway: SharedPluginGateway | None = None,
        plugin_bridge: PluginBridgeService | None = None,
    ) -> None:
        if not isinstance(repository, PriceVerificationRepository):
            raise TypeError("repository must be PriceVerificationRepository")
        if plugin_gateway is None and not isinstance(plugin_bridge, PluginBridgeService):
            raise TypeError("plugin_gateway or plugin_bridge is required")
        if plugin_gateway is not None and not isinstance(plugin_gateway, SharedPluginGateway):
            raise TypeError("plugin_gateway must be SharedPluginGateway")
        self._repository = repository
        self._plugin_gateway = plugin_gateway
        self._plugin_bridge = plugin_bridge
        self._output_root = Path(output_root)

    def queue_collection(
        self,
        actor: PriceVerificationActor,
        *,
        session_id: str,
        payload: Mapping[str, Any] | None = None,
        idempotency_key: str,
    ) -> PluginCommandRecord:
        """Queue a workspace-owned, read-only Temu quote capture.

        Checking the session through the bridge prevents a caller from using a
        session identifier that is merely known, but not owned by its workspace.
        The repository enforces the idempotency boundary during insertion.
        """
        actor = _actor(actor)
        if not isinstance(session_id, str) or not session_id.strip():
            raise PriceVerificationContractError("session_id is required")
        if self._plugin_gateway is not None:
            return self._plugin_gateway.queue_command(
                actor,
                session_id=session_id,
                command_type="temu_price_quote_discovery",
                payload={} if payload is None else payload,
                idempotency_key=idempotency_key,
            )
        assert self._plugin_bridge is not None
        owned_session_ids = {session.session_id for session in self._plugin_bridge.list_sessions(actor)}
        if session_id not in owned_session_ids:
            raise PriceVerificationNotFound("resource not found")
        request = PluginCommandRequest(
            command_type="temu_price_quote_discovery",
            payload={} if payload is None else payload,
            idempotency_key=idempotency_key,
        )
        return self._repository.create_command(
            workspace_id=actor.workspace_id,
            session_id=session_id,
            request=request,
        )

    def materialize_completed_command(
        self, actor: PriceVerificationActor, command: PluginCommandRecord
    ) -> QuoteRunRecord:
        """Normalize one succeeded capture and save a fresh immutable run."""
        actor = _actor(actor)
        if not isinstance(command, PluginCommandRecord):
            raise TypeError("command must be PluginCommandRecord")
        persisted_command = (
            self._plugin_gateway.get_command(actor, command.command_id)
            if self._plugin_gateway is not None
            else self._repository.get_command(
                workspace_id=actor.workspace_id, command_id=command.command_id
            )
        )
        if persisted_command.command_type != "temu_price_quote_discovery":
            raise PriceVerificationContractError("command must be a Temu price quote discovery")
        if persisted_command.status != "succeeded":
            raise ValueError("quote command must have succeeded before materialization")

        normalized = normalize_price_quote_discovery(persisted_command.result)
        snapshots = tuple(_quote_snapshot(item, index=index) for index, item in enumerate(normalized.quotes))
        return self._repository.create_quote_run(
            workspace_id=actor.workspace_id,
            command_id=persisted_command.command_id,
            items=snapshots,
            status="succeeded",
            adapter_version="quote-normalizer-v1",
            captured_at=_captured_at(normalized, fallback=persisted_command.updated_at),
        )

    def get_preview(self, actor: PriceVerificationActor, run_id: str) -> QuotePreview:
        """Reconstruct a preview solely from the saved quote snapshot."""
        actor = _actor(actor)
        run = self._repository.get_quote_run(workspace_id=actor.workspace_id, run_id=run_id)
        quotes = [_quote_from_snapshot(snapshot) for snapshot in run.items]
        complete_quotes = [quote for quote in quotes if is_complete_quote(quote)]
        network_evidence = sum(quote.network_evidence_count for quote in quotes)
        dom_evidence = sum(quote.dom_evidence_count for quote in quotes)
        return QuotePreview(
            quotes=quotes,
            counts=QuoteCounts(
                quotes=len(quotes),
                complete_quotes=len(complete_quotes),
                review_quotes=sum(needs_review(quote) for quote in quotes),
                network_records=network_evidence,
                raw_network_records=network_evidence,
                ignored_network_records=0,
                dom_rows=dom_evidence,
                raw_dom_rows=dom_evidence,
                ignored_dom_rows=0,
                dom_rows_ignored_by_popup_state=0,
                platform_item_quotes=quote_product_count(quotes),
                skc_quotes=quote_product_count(quotes),
                complete_skc_quotes=quote_product_count(complete_quotes),
            ),
            confidence_counts=dict(Counter(quote.source_confidence for quote in quotes)),
            authenticity_status_counts=dict(Counter(quote.authenticity_status for quote in quotes)),
        )

    def export_run(self, actor: PriceVerificationActor, run_id: str) -> ExportedQuoteRun:
        """Export only a persisted, workspace-owned snapshot below output_root."""
        actor = _actor(actor)
        run = self._repository.get_quote_run(workspace_id=actor.workspace_id, run_id=run_id)
        preview = self.get_preview(actor, run_id)
        return export_quote_snapshot(output_root=self._output_root, run=run, preview=preview)

    def record_decision(
        self,
        actor: PriceVerificationActor,
        run_id: str,
        quote_key: str,
        decision: str,
        note: str = "",
    ) -> Any:
        actor = _actor(actor)
        return self._repository.record_quote_decision(
            workspace_id=actor.workspace_id,
            quote_run_id=run_id,
            quote_key=quote_key,
            decision=decision,
            decided_by=actor.actor_id,
            note=note,
        )

    def list_current_decisions(self, actor: PriceVerificationActor, run_id: str) -> tuple[Any, ...]:
        actor = _actor(actor)
        return self._repository.list_current_quote_decisions(
            workspace_id=actor.workspace_id, quote_run_id=run_id
        )


def _actor(value: PriceVerificationActor) -> PriceVerificationActor:
    if not isinstance(value, PriceVerificationActor):
        raise TypeError("actor must be PriceVerificationActor")
    return value


def _captured_at(preview: QuotePreview, *, fallback: str) -> str:
    return next((item.captured_at for item in preview.quotes if item.captured_at), fallback)


def _quote_snapshot(item: QuoteItem, *, index: int) -> Mapping[str, Any]:
    snapshot = asdict(item)
    snapshot["quote_key"] = item.quote_key.strip() or _quote_key(item, index=index)
    return snapshot


def _quote_key(item: QuoteItem, *, index: int) -> str:
    if item.quote_key.strip():
        return item.quote_key.strip()
    values = (item.skc_id, item.sku_id, item.spu_or_goods_id, item.site)
    key = "|".join(value.strip() for value in values if value and value.strip())
    return key or f"quote-{index}"


def _quote_from_snapshot(snapshot: Mapping[str, Any]) -> QuoteItem:
    values = {key: value for key, value in snapshot.items() if key in _QUOTE_FIELDS}
    for key in _MONEY_FIELDS:
        values[key] = _decimal_or_none(values.get(key))
    extra_images = values.get("extra_image_urls")
    values["extra_image_urls"] = list(extra_images) if isinstance(extra_images, list) else []
    return QuoteItem(**values)


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise PriceVerificationContractError("persisted quote price is invalid") from error
