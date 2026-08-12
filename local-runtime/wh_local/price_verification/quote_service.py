"""Persisted, read-only quote snapshots and their local exports."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, fields
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from .contracts import (
    PluginCommandRequest,
    PriceVerificationActor,
    PriceVerificationContractError,
    safe_json_dumps,
    safe_json_value,
)
from .exports import ExportedQuoteRun, export_quote_snapshot
from .plugin.service import PluginBridgeService
from .plugin.shared_gateway import SharedPluginGateway
from .quote_normalizer import (
    QuoteCounts,
    QuoteItem,
    QuotePreview,
    is_complete_quote,
    needs_review,
    dedupe_quotes,
    normalize_price_quote_discovery,
    quote_product_count,
)
from .repository import (
    BatchSelectionRecord,
    PluginCommandRecord,
    PriceVerificationNotFound,
    PriceVerificationRepository,
    QuoteCaptureBatchRecord,
    QuoteCaptureChunkRecord,
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
_MAX_CAPTURE_SKC_GROUPS = 500
_MAX_CAPTURE_QUOTE_ROWS = 5_000


class CaptureBatchRequiredError(ValueError):
    """Raised when a plugin sends a direct capture before a user selects a batch."""


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

    def create_capture_batch(
        self, actor: PriceVerificationActor, *, name: str, make_current: bool = True
    ) -> QuoteCaptureBatchRecord:
        """Create a user-controlled big batch for direct Temu page captures."""
        actor = _actor(actor)
        return self._repository.create_quote_capture_batch(
            workspace_id=actor.workspace_id,
            name=name,
            created_by=actor.actor_id,
            make_current=make_current,
        )

    def list_capture_batches(self, actor: PriceVerificationActor) -> tuple[QuoteCaptureBatchRecord, ...]:
        actor = _actor(actor)
        return self._repository.list_quote_capture_batches(workspace_id=actor.workspace_id)

    def capture_batches_revision(self, actor: PriceVerificationActor) -> str:
        """核价数据变更指纹，供前端轮询做容器级自动刷新。"""
        actor = _actor(actor)
        return self._repository.capture_batches_revision(workspace_id=actor.workspace_id)

    def activate_capture_batch(
        self, actor: PriceVerificationActor, batch_id: str
    ) -> QuoteCaptureBatchRecord:
        actor = _actor(actor)
        return self._repository.activate_quote_capture_batch(
            workspace_id=actor.workspace_id, batch_id=batch_id
        )

    def get_capture_batch(
        self, actor: PriceVerificationActor, batch_id: str
    ) -> QuoteCaptureBatchRecord:
        actor = _actor(actor)
        return self._repository.get_quote_capture_batch(workspace_id=actor.workspace_id, batch_id=batch_id)

    def capture_current_page(
        self,
        actor: PriceVerificationActor,
        capture: Mapping[str, Any],
        *,
        page_url: str = "",
    ) -> QuoteCaptureChunkRecord:
        """Normalize and persist one read-only current-page capture into the active batch."""
        actor = _actor(actor)
        if not isinstance(capture, Mapping):
            raise PriceVerificationContractError("capture must be a mapping")
        safe_capture = safe_json_value(capture)
        if not isinstance(safe_capture, Mapping):
            raise PriceVerificationContractError("capture must be a mapping")
        normalized = normalize_price_quote_discovery(safe_capture)
        quotes = normalized.quotes
        skc_groups = quote_product_count(quotes)
        if not quotes:
            raise PriceVerificationContractError("one capture page must contain at least one quote row")
        if skc_groups > _MAX_CAPTURE_SKC_GROUPS:
            raise PriceVerificationContractError("one capture page must contain no more than 500 SKC groups")
        if len(quotes) > _MAX_CAPTURE_QUOTE_ROWS:
            raise PriceVerificationContractError("one capture page contains too many SKU quote rows")
        try:
            batch = self._repository.ensure_current_capture_batch(
                workspace_id=actor.workspace_id, created_by=actor.actor_id
            )
        except PriceVerificationNotFound as error:  # pragma: no cover - defensive
            raise CaptureBatchRequiredError("create or activate a capture batch before collecting") from error
        # 覆盖式采集：每次新采集替换上一批数据（报价与待审重组），只保留最新一次结果。
        self._repository.clear_capture_batch_quotes(
            workspace_id=actor.workspace_id, batch_id=batch.batch_id
        )
        snapshots = tuple(_quote_snapshot(item, index=index) for index, item in enumerate(quotes))
        content_sha256 = sha256(safe_json_dumps({"page_url": page_url, "capture": safe_capture}).encode("utf-8")).hexdigest()
        return self._repository.append_quote_capture_chunk(
            workspace_id=actor.workspace_id,
            batch_id=batch.batch_id,
            content_sha256=content_sha256,
            page_url=page_url,
            capture=safe_capture,
            items=snapshots,
            captured_at=_captured_at(normalized, fallback=""),
        )

    def save_capture_batch_snapshot(
        self, actor: PriceVerificationActor, batch_id: str
    ) -> QuoteRunRecord:
        """Freeze all current chunks of a big batch into a new immutable quote run."""
        actor = _actor(actor)
        batch = self._repository.get_quote_capture_batch(workspace_id=actor.workspace_id, batch_id=batch_id)
        chunks = self._repository.list_quote_capture_chunks(
            workspace_id=actor.workspace_id, batch_id=batch.batch_id
        )
        if not chunks:
            raise PriceVerificationContractError("capture batch has no quote chunks")
        quotes = [_quote_from_snapshot(item) for chunk in chunks for item in chunk.items]
        merged = dedupe_quotes(quotes)
        snapshots = tuple(_quote_snapshot(item, index=index) for index, item in enumerate(merged))
        run = self._repository.create_quote_run(
            workspace_id=actor.workspace_id,
            command_id=f"direct-capture-batch:{batch.batch_id}",
            items=snapshots,
            status="succeeded",
            adapter_version="direct-price-capture-v1",
            captured_at=next((chunk.captured_at for chunk in chunks if chunk.captured_at), chunks[0].created_at),
        )
        self._repository.record_quote_capture_batch_snapshot(
            workspace_id=actor.workspace_id, batch_id=batch.batch_id, quote_run_id=run.run_id
        )
        return run

    def get_prescreen(self, actor: PriceVerificationActor) -> Mapping[str, Any]:
        actor = _actor(actor)
        settings = self._repository.get_prescreen_settings(workspace_id=actor.workspace_id)
        return {
            "workspace_id": settings.workspace_id,
            "min_adjusted_price_cny": _decimal_text(settings.min_adjusted_price_cny),
            "updated_at": settings.updated_at,
            "updated_by": settings.updated_by,
        }

    def set_prescreen(
        self,
        actor: PriceVerificationActor,
        *,
        min_adjusted_price_cny: str | None,
    ) -> Mapping[str, Any]:
        """Persist the workbench-wide pre-screen threshold.

        ``min_adjusted_price_cny`` is the minimum adjusted declared price
        (CNY) a captured SKC must reach to enter STEP 02 confirm; a blank
        value disables filtering.
        """
        actor = _actor(actor)
        normalized: str | None = None
        if min_adjusted_price_cny is not None and str(min_adjusted_price_cny).strip():
            amount = _decimal_or_none(str(min_adjusted_price_cny).strip())
            if amount is None or amount < 0:
                raise PriceVerificationContractError("min_adjusted_price_cny must be a non-negative amount")
            normalized = _decimal_text(amount)
        settings = self._repository.set_prescreen_settings(
            workspace_id=actor.workspace_id,
            min_adjusted_price_cny=normalized,
            updated_by=actor.actor_id,
        )
        return {
            "workspace_id": settings.workspace_id,
            "min_adjusted_price_cny": _decimal_text(settings.min_adjusted_price_cny),
            "updated_at": settings.updated_at,
            "updated_by": settings.updated_by,
        }

    def list_capture_batch_review_items(
        self,
        actor: PriceVerificationActor,
        batch_id: str,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return SKC-grouped review rows from a capture batch without saving.

        Each row carries the merged SKU prices (original vs adjusted side by
        side) so the workbench can review and select quotes before any quote
        run snapshot exists.
        """
        actor = _actor(actor)
        self._repository.get_quote_capture_batch(workspace_id=actor.workspace_id, batch_id=batch_id)
        chunks = self._repository.list_quote_capture_chunks(
            workspace_id=actor.workspace_id, batch_id=batch_id
        )
        items = [
            _quote_from_snapshot(item)
            for chunk in chunks
            for item in chunk.items
        ]
        merged = dedupe_quotes(items)
        groups: dict[str, list[QuoteItem]] = {}
        for item in merged:
            # A quote can arrive with only SKU or SPU.  Preserve the existing
            # SKC-first grouping, then use SPU and SKU as compatible product IDs.
            key = item.skc_id or item.spu_or_goods_id or item.sku_id or item.sku_merchant_code or item.sku_true_id or item.product_title or item.quote_key or "other"
            groups.setdefault(key, []).append(item)
        prescreen = self._repository.get_prescreen_settings(workspace_id=actor.workspace_id)
        min_adjusted = _decimal_or_none(prescreen.min_adjusted_price_cny)
        rows: list[Mapping[str, Any]] = []
        for key, members in groups.items():
            adjusted_min = _min_or_none(item.adjusted_declared_price_cny for item in members)
            if min_adjusted is not None:
                # 初筛：调整后申报价缺失或低于阈值时，不进入 STEP 02 人工确认。
                if adjusted_min is None or adjusted_min < min_adjusted:
                    continue
            sku_prices: list[Mapping[str, Any]] = []
            for item in members:
                sku_prices.append(
                    {
                        "sku_id": item.sku_id or item.sku_merchant_code or item.sku_true_id,
                        "sku_attribute_text": item.sku_attribute_text or item.sku_attribute_set,
                        "original_declared_price_cny": _decimal_or_none(item.original_declared_price_cny),
                        "adjusted_declared_price_cny": _decimal_or_none(item.adjusted_declared_price_cny),
                        "new_declared_price_cny": _decimal_or_none(item.new_declared_price_cny),
                    }
                )
            first = members[0]
            rows.append(
                {
                    "skc_id": key,
                    "product_id": key,
                    "product_id_label": "商品ID",
                    "quote_keys": [item.quote_key for item in members],
                    "product_title": next((item.product_title for item in members if item.product_title), first.product_title),
                    "main_image_url": next((item.main_image_url for item in members if item.main_image_url), first.main_image_url),
                    "official_link_url": next((item.official_link_url for item in members if item.official_link_url), first.official_link_url),
                    "site": next((item.site for item in members if item.site), first.site),
                    "source_confidence": next((item.source_confidence for item in members if item.source_confidence), first.source_confidence),
                    "authenticity_status": next((item.authenticity_status for item in members if item.authenticity_status), first.authenticity_status),
                    "sku_count": len(sku_prices),
                    "sku_prices": sku_prices,
                    "original_min": _min_or_none(item.original_declared_price_cny for item in members),
                    "original_max": _max_or_none(item.original_declared_price_cny for item in members),
                    "adjusted_min": _min_or_none(item.adjusted_declared_price_cny for item in members),
                    "adjusted_max": _max_or_none(item.adjusted_declared_price_cny for item in members),
                }
            )
        return tuple(rows)

    def remove_capture_batch_item(
        self,
        actor: PriceVerificationActor,
        batch_id: str,
        skc_id: str,
    ) -> Mapping[str, Any]:
        """Remove one SKC group (and all its SKU quotes) from the current
        capture batch review list."""
        actor = _actor(actor)
        self._repository.get_quote_capture_batch(workspace_id=actor.workspace_id, batch_id=batch_id)
        target = str(skc_id or "").strip()
        if not target:
            raise PriceVerificationContractError("skc_id is required")
        rows = self.list_capture_batch_review_items(actor, batch_id)
        matched = next((row for row in rows if str(row["skc_id"]).strip() == target), None)
        if matched is None:
            raise PriceVerificationContractError(f"SKC {target} 不在当前批次报价审核中")
        removed = self._repository.remove_capture_chunk_quote_items(
            workspace_id=actor.workspace_id,
            batch_id=batch_id,
            quote_keys=matched["quote_keys"],
        )
        return {"batch_id": batch_id, "skc_id": target, "removed": removed}

    def clear_capture_batch_items(
        self,
        actor: PriceVerificationActor,
        batch_id: str,
    ) -> Mapping[str, Any]:
        """Remove every quote row from the current capture batch review list."""
        actor = _actor(actor)
        self._repository.get_quote_capture_batch(workspace_id=actor.workspace_id, batch_id=batch_id)
        removed = self._repository.clear_capture_chunks(
            workspace_id=actor.workspace_id,
            batch_id=batch_id,
        )
        return {"batch_id": batch_id, "removed": removed}

    def confirm_batch_quotes_to_draft(
        self,
        actor: PriceVerificationActor,
        batch_id: str,
        *,
        quote_keys: Iterable[str],
        draft_writer: Callable[[Mapping[str, Any]], tuple[Mapping[str, Any], bool]] | None = None,
        note: str = "",
    ) -> Mapping[str, Any]:
        """Write user-confirmed (retained) quotes of a capture batch into the
        product-processing draft pool.

        Only the SKC groups whose quote keys are passed here are sent to the
        draft pool; rejected rows are intentionally skipped.  The draft writer
        is host-owned (product-processing service) and receives a draft-shaped
        payload per SKC so the workbench never touches that module directly.
        """
        actor = _actor(actor)
        if draft_writer is None:
            raise PriceVerificationContractError("draft writer is not configured")
        requested = {str(key).strip() for key in quote_keys if str(key).strip()}
        if not requested:
            raise PriceVerificationContractError("at least one quote must be confirmed")
        rows = self.list_capture_batch_review_items(actor, batch_id)
        selected = [row for row in rows if any(key in requested for key in row["quote_keys"])]
        created, skipped = 0, 0
        drafts: list[Mapping[str, Any]] = []
        for row in selected:
            price = _preferred_price(row["sku_prices"])
            payload = {
                "source_type": "price_verification",
                "source_ref": row["official_link_url"] or row["skc_id"],
                "candidate_id": f"price-verification:{row['skc_id']}",
                "skc": row["skc_id"],
                "sku": row["sku_prices"][0]["sku_id"] if row["sku_prices"] else None,
                "product_name": row["product_title"],
                "title": row["product_title"],
                "image_url": row["main_image_url"],
                "main_image_url": row["main_image_url"],
                "cost": price,
                "declared_price": _min_or_none(item["original_declared_price_cny"] for item in row["sku_prices"]),
                "source_platform": "temu",
                "source_image_urls": [url for url in (row["main_image_url"],) if url],
                "price_verification_batch_id": batch_id,
                "review_note": note,
            }
            draft, is_created = draft_writer(payload)
            drafts.append({"skc_id": row["skc_id"], "draft_id": draft.get("id"), "created": is_created})
            created += 1 if is_created else 0
            skipped += 0 if is_created else 1
        return {
            "batch_id": batch_id,
            "requested_skc_count": len(selected),
            "created": created,
            "skipped": skipped,
            "drafts": drafts,
        }

    def stage_batch_selections(
        self,
        actor: PriceVerificationActor,
        batch_id: str,
        *,
        skc_ids: Iterable[str],
        max_candidates: int | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        """First-panel confirm: reassemble the checked SKC rows into the pending review list.

        Nothing is written to the draft pool here; the final retained decision
        happens in the second panel so humans get a second chance to drop rows.
        ``max_candidates`` is applied to each staged row as its image-search cap.
        """
        actor = _actor(actor)
        requested = {str(skc).strip() for skc in skc_ids if str(skc).strip()}
        if not requested:
            raise PriceVerificationContractError("at least one SKC must be selected")
        if max_candidates is None:
            max_candidates = 10
        candidate_cap = max(1, min(int(max_candidates), 100))
        rows = self.list_capture_batch_review_items(actor, batch_id)
        selected = [row for row in rows if str(row["skc_id"]).strip() in requested]
        # 每次批次确认都是一次新的选择集；不能把上一轮确认的 SKC 继续带到最终确认或图搜。
        self._repository.replace_batch_selection_scope(
            workspace_id=actor.workspace_id,
            batch_id=batch_id,
            skc_ids=[str(row["skc_id"]) for row in selected],
        )
        now = _now_text()
        staged: list[Mapping[str, Any]] = []
        for row in selected:
            selection = self._repository.upsert_batch_selection(
                workspace_id=actor.workspace_id,
                batch_id=batch_id,
                skc_id=str(row["skc_id"]),
                quote_keys=[str(key) for key in row["quote_keys"]],
                product_title=str(row["product_title"] or ""),
                main_image_url=str(row["main_image_url"] or ""),
                official_link_url=str(row["official_link_url"] or ""),
                site=str(row["site"] or ""),
                source_confidence=str(row["source_confidence"] or ""),
                authenticity_status=str(row["authenticity_status"] or ""),
                sku_prices=[dict(item) for item in row["sku_prices"]],
                original_min=_decimal_text(row["original_min"]),
                original_max=_decimal_text(row["original_max"]),
                adjusted_min=_decimal_text(row["adjusted_min"]),
                adjusted_max=_decimal_text(row["adjusted_max"]),
                max_candidates=candidate_cap,
                now=now,
            )
            staged.append(_selection_response(selection))
        return tuple(staged)

    def list_batch_selections(
        self,
        actor: PriceVerificationActor,
        batch_id: str,
        *,
        include_deleted: bool = False,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return the pending review list rows for the second panel."""
        actor = _actor(actor)
        selections = self._repository.list_batch_selections(
            workspace_id=actor.workspace_id, batch_id=batch_id, include_deleted=include_deleted
        )
        return tuple(_selection_response(selection) for selection in selections)

    def review_batch_selection(
        self,
        actor: PriceVerificationActor,
        batch_id: str,
        *,
        selection_id: int,
        decision: str,
        max_candidates: int | None = None,
        draft_writer: Callable[[Mapping[str, Any]], tuple[Mapping[str, Any], bool]] | None = None,
        note: str = "",
    ) -> Mapping[str, Any]:
        """Second-panel final decision on one pending SKC row.

        ``retained`` freezes the selected price into the product-processing
        draft pool and marks the row ready for sourcing; ``deleted`` drops the
        row from the pending list.
        """
        actor = _actor(actor)
        if decision not in {"retained", "deleted"}:
            raise PriceVerificationContractError("decision must be retained or deleted")
        if max_candidates is None:
            max_candidates = 10
        selection = self._repository.get_batch_selection(
            workspace_id=actor.workspace_id, selection_id=selection_id
        )
        if selection.batch_id != batch_id:
            raise PriceVerificationNotFound("resource not found")
        if selection.status == "deleted":
            raise PriceVerificationNotFound("resource not found")
        draft_id: Any = None
        created = False
        if decision == "retained":
            if draft_writer is None:
                raise PriceVerificationContractError("draft writer is not configured")
            draft, created = draft_writer(
                _draft_payload_from_selection(selection, batch_id=batch_id, note=note)
            )
            draft_id = draft.get("id")
        updated = self._repository.update_batch_selection_review(
            workspace_id=actor.workspace_id,
            selection_id=selection_id,
            decision=decision,
            max_candidates=int(max_candidates),
            now=_now_text(),
        )
        return {
            **_selection_response(updated),
            "draft_id": draft_id,
            "draft_created": created,
        }

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


def _min_or_none(values: Iterable[Any]) -> Decimal | None:
    amounts = [_decimal_or_none(value) for value in values if _decimal_or_none(value) is not None]
    return min(amounts) if amounts else None


def _max_or_none(values: Iterable[Any]) -> Decimal | None:
    amounts = [_decimal_or_none(value) for value in values if _decimal_or_none(value) is not None]
    return max(amounts) if amounts else None


def _preferred_price(sku_prices: Sequence[Mapping[str, Any]]) -> Decimal | None:
    """Prefer the adjusted (recommended) price, then the original declared price."""
    for price in sku_prices:
        if price.get("adjusted_declared_price_cny") is not None:
            return _decimal_or_none(price["adjusted_declared_price_cny"])
    for price in sku_prices:
        if price.get("original_declared_price_cny") is not None:
            return _decimal_or_none(price["original_declared_price_cny"])
    return None


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _decimal_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return str(amount) if amount.is_finite() else None


def _selection_response(selection: BatchSelectionRecord) -> Mapping[str, Any]:
    return {
        "id": selection.id,
        "batch_id": selection.batch_id,
        "skc_id": selection.skc_id,
        "quote_keys": list(selection.quote_keys),
        "product_title": selection.product_title,
        "main_image_url": selection.main_image_url,
        "official_link_url": selection.official_link_url,
        "site": selection.site,
        "source_confidence": selection.source_confidence,
        "authenticity_status": selection.authenticity_status,
        "sku_prices": list(selection.sku_prices),
        "original_min": _decimal_text(selection.original_min),
        "original_max": _decimal_text(selection.original_max),
        "adjusted_min": _decimal_text(selection.adjusted_min),
        "adjusted_max": _decimal_text(selection.adjusted_max),
        "max_candidates": selection.max_candidates,
        "status": selection.status,
        "created_at": selection.created_at,
        "updated_at": selection.updated_at,
    }


def _draft_payload_from_selection(
    selection: BatchSelectionRecord,
    *,
    batch_id: str,
    note: str,
) -> Mapping[str, Any]:
    """Build the product-processing draft payload from a retained SKC row.

    The adjusted (recommended) price wins as cost; the original declared price
    is kept as the declared reference so downstream profit checks can compare.
    """
    price = _preferred_price(selection.sku_prices)
    first_sku = selection.sku_prices[0] if selection.sku_prices else {}
    return {
        "source_type": "price_verification",
        "source_ref": selection.official_link_url or selection.skc_id,
        "candidate_id": f"price-verification:{selection.skc_id}",
        "skc": selection.skc_id,
        "product_id": selection.skc_id,
        "sku": first_sku.get("sku_id") if isinstance(first_sku, Mapping) else None,
        "product_name": selection.product_title,
        "title": selection.product_title,
        "image_url": selection.main_image_url,
        "main_image_url": selection.main_image_url,
        "cost": price,
        "declared_price": _min_or_none(
            item.get("original_declared_price_cny") for item in selection.sku_prices
        ),
        "source_platform": "temu",
        "source_image_urls": [url for url in (selection.main_image_url,) if url],
        "price_verification_batch_id": batch_id,
        "review_note": note,
    }
