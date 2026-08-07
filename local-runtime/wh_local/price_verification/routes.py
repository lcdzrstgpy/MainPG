"""HTTP adapters for the workspace-isolated price-verification workflow."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import ValidationError

from ..data_collection.plugin_queue import DataCollectionPluginQueue

from .contracts import (
    ALLOWED_PLUGIN_COMMAND_TYPES,
    PriceVerificationActor,
    PriceVerificationContractError,
    safe_json_value,
)
from .plugin.service import (
    PluginAuthenticationError,
    PluginLeaseError,
    PluginResourceNotFound,
)
from .plugin.shared_gateway import SharedPluginGateway
from .quote_service import CaptureBatchRequiredError, QuoteService
from .repository import (
    PluginCommandRecord,
    PriceVerificationNotFound,
    PriceVerificationRepository,
    QuoteRunRecord,
    SourcingRunRecord,
)
from .sourcing.onebound_adapter import OneBoundSourceAdapter
from .sourcing.service import (
    QuoteDecisionRequiredError,
    SourcingService,
)


@dataclass(frozen=True)
class PriceVerificationRouteDependencies:
    """Host-owned adapters required to expose this module over HTTP."""

    resolve_actor: Callable[..., Any]
    database_path: str | Path
    output_root: str | Path
    provider_config_resolver: Callable[[PriceVerificationActor], Mapping[str, Any]] | None = None
    provider_factory: Callable[[Mapping[str, Any]], Any] | None = None
    plugin_queue: DataCollectionPluginQueue | None = None
    draft_writer: Callable[[Mapping[str, Any]], tuple[Mapping[str, Any], bool]] | None = None
    # Optional profit-activity product library: retained SKCs with active 1688
    # source links are auto-synced here after link/unlink operations.
    product_library_service: Any | None = None

    def build_services(self) -> tuple[
        PriceVerificationRepository, SharedPluginGateway, QuoteService, SourcingService
    ]:
        repository = PriceVerificationRepository(self.database_path)
        gateway = SharedPluginGateway(
            self.plugin_queue or DataCollectionPluginQueue(self.database_path)
        )
        quote = QuoteService(
            repository=repository,
            plugin_gateway=gateway,
            output_root=self.output_root,
        )
        sourcing = SourcingService(
            repository=repository,
            plugin_gateway=gateway,
            product_library_service=self.product_library_service,
        )
        return repository, gateway, quote, sourcing


def register_price_verification_routes(
    router: APIRouter, dependencies: PriceVerificationRouteDependencies
) -> None:
    """Register formal, bridge, and local-demo aliases over one service graph."""
    repository, gateway, quote_service, sourcing_service = dependencies.build_services()

    def actor_dependency(
        actor_value: Any = Depends(dependencies.resolve_actor),
    ) -> PriceVerificationActor:
        if isinstance(actor_value, PriceVerificationActor):
            return actor_value
        try:
            return PriceVerificationActor.model_validate(actor_value)
        except (ValidationError, TypeError, ValueError) as error:
            actor_id = getattr(actor_value, "id", None)
            if isinstance(actor_id, str) and actor_id.strip():
                return PriceVerificationActor(actor_id=actor_id, workspace_id=actor_id)
            raise HTTPException(status_code=401, detail="authenticated workspace required") from error

    @router.post("/api/v1/price-verification/plugin/pairing-codes")
    def issue_pairing_code(
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        del actor
        raise HTTPException(
            status_code=409,
            detail="price verification uses the existing data-collection plugin connection",
        )

    @router.get("/api/v1/price-verification/plugin/sessions")
    @router.get("/plugin/sessions")
    def list_plugin_sessions(
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        return {"sessions": [_session_response(session) for session in gateway.list_sessions(actor)]}

    @router.get("/api/v1/price-verification/plugin/package")
    @router.get("/plugin/package")
    def plugin_package(
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        del actor
        return _plugin_package_response()

    @router.get("/api/v1/price-verification/plugin/download")
    @router.get("/plugin/download")
    def plugin_download(
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        del actor
        return _plugin_package_response()

    @router.get("/api/v1/price-verification/prescreen")
    def get_prescreen(
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            return quote_service.get_prescreen(actor)
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.put("/api/v1/price-verification/prescreen")
    def set_prescreen(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            return quote_service.set_prescreen(
                actor,
                min_adjusted_price_cny=_text(request.get("min_adjusted_price_cny")),
            )
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/capture-batches")
    def create_capture_batch(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            batch = quote_service.create_capture_batch(
                actor,
                name=_required(request, "name"),
                make_current=bool(request.get("make_current", True)),
            )
            return _capture_batch_response(batch)
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/api/v1/price-verification/capture-batches")
    def list_capture_batches(
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            return {"batches": [_capture_batch_response(item) for item in quote_service.list_capture_batches(actor)]}
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/api/v1/price-verification/capture-batches/{batch_id}")
    def get_capture_batch(
        batch_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            batch = quote_service.get_capture_batch(actor, batch_id)
            chunks = repository.list_quote_capture_chunks(
                workspace_id=actor.workspace_id, batch_id=batch_id
            )
            return {
                **_capture_batch_response(batch),
                "chunks": [_capture_chunk_response(chunk) for chunk in chunks],
            }
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/capture-batches/{batch_id}/activate")
    def activate_capture_batch(
        batch_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            return _capture_batch_response(quote_service.activate_capture_batch(actor, batch_id))
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/capture-batches/{batch_id}/snapshots")
    def save_capture_batch_snapshot(
        batch_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            return _quote_run_response(quote_service.save_capture_batch_snapshot(actor, batch_id))
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/api/v1/price-verification/capture-batches/{batch_id}/items")
    def list_capture_batch_items(
        batch_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            return {
                "batch_id": batch_id,
                "items": list(quote_service.list_capture_batch_review_items(actor, batch_id)),
            }
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/capture-batches/{batch_id}/drafts")
    def confirm_capture_batch_drafts(
        batch_id: str,
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            quote_keys = request.get("quote_keys")
            if not isinstance(quote_keys, list) or not quote_keys:
                raise PriceVerificationContractError("quote_keys must be a non-empty list")
            return quote_service.confirm_batch_quotes_to_draft(
                actor,
                batch_id,
                quote_keys=[str(key) for key in quote_keys],
                draft_writer=dependencies.draft_writer,
                note=_text(request.get("note")),
            )
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/capture-batches/{batch_id}/selections")
    def stage_batch_selections(
        batch_id: str,
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        """First-panel confirm: reassemble checked SKC rows into the pending review list."""
        try:
            skc_ids = request.get("skc_ids")
            if not isinstance(skc_ids, list) or not skc_ids:
                raise PriceVerificationContractError("skc_ids must be a non-empty list")
            return {
                "batch_id": batch_id,
                "selections": list(
                    quote_service.stage_batch_selections(
                        actor,
                        batch_id,
                        skc_ids=[str(item) for item in skc_ids],
                        max_candidates=_positive_int(request.get("max_candidates"), "max_candidates")
                        if request.get("max_candidates") not in (None, "")
                        else None,
                    )
                ),
            }
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/api/v1/price-verification/capture-batches/{batch_id}/selections")
    def list_batch_selections(
        batch_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            return {
                "batch_id": batch_id,
                "selections": list(quote_service.list_batch_selections(actor, batch_id)),
            }
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/capture-batches/{batch_id}/selections/{selection_id}/review")
    def review_batch_selection(
        batch_id: str,
        selection_id: int,
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        """Second-panel final decision: retained → draft pool; deleted → drop."""
        try:
            return quote_service.review_batch_selection(
                actor,
                batch_id,
                selection_id=selection_id,
                decision=_required(request, "decision"),
                max_candidates=_positive_int(request.get("max_candidates", 10), "max_candidates"),
                draft_writer=dependencies.draft_writer,
                note=_text(request.get("note")),
            )
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/capture-batches/{batch_id}/sourcing")
    def source_batch_selections(
        batch_id: str,
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        """Run the established OB 1688 image-search chain for retained selections.

        The data-collection module already verified the chain: download the
        reference image, upload it to OB (upload_img), then run item_search_img.
        Results return as a preview grouped by SKC with each selection's cap.
        """
        try:
            if dependencies.provider_config_resolver is None or dependencies.provider_factory is None:
                raise HTTPException(status_code=503, detail="OneBound provider is unavailable")
            return sourcing_service.search_batch_selections_by_image(
                actor,
                batch_id=batch_id,
                provider_factory=lambda: dependencies.provider_factory(
                    dependencies.provider_config_resolver(actor)
                ),
                ranking_mode=_text(request.get("ranking_mode")) or "similarity",
                skc_ids=_text_list(request.get("skc_ids")),
                keyword_search=bool(request.get("keyword_search")),
            )
        except HTTPException:
            raise
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/capture-batches/{batch_id}/source-profit-preview")
    def preview_source_candidate_profit(
        batch_id: str,
        request: Mapping[str, Any] = Body(...),
    ) -> Mapping[str, Any]:
        """Recompute one candidate's profit against the Temu adjusted price (weight adjustable)."""
        del batch_id
        try:
            return dict(
                sourcing_service.preview_candidate_profit(
                    site=_required(request, "site"),
                    selling_price=_required_value(request, "selling_price"),
                    price=_required_value(request, "price"),
                    moq=request.get("moq"),
                    domestic_freight=request.get("domestic_freight"),
                    weight_kg=request.get("weight_kg", 0.5),
                )
            )
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/capture-batches/{batch_id}/skc-source-links")
    def link_skc_source(
        batch_id: str,
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        """Link one 1688 offer to a retained Temu SKC for later dropshipping lookup."""
        try:
            return sourcing_service.link_skc_source(
                actor,
                batch_id=batch_id,
                skc_id=_required(request, "skc_id"),
                offer_id=_required(request, "offer_id"),
                source_url=_required(request, "source_url"),
                source_title=_text(request.get("source_title")),
                main_image_url=_text(request.get("main_image_url")),
                price_cny=request.get("price_cny"),
                moq=request.get("moq"),
                domestic_freight_cny=request.get("domestic_freight_cny"),
                source_decision=_text(request.get("source_decision")),
                note=_text(request.get("note")),
            )
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/api/v1/price-verification/capture-batches/{batch_id}/skc-source-links")
    def list_skc_source_links(
        batch_id: str,
        actor: PriceVerificationActor = Depends(actor_dependency),
        skc_id: str | None = Query(default=None),
    ) -> Mapping[str, Any]:
        try:
            return {
                "links": [
                    dict(link)
                    for link in sourcing_service.list_skc_source_links(
                        actor, batch_id=batch_id, skc_id=skc_id
                    )
                ]
            }
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.delete("/api/v1/price-verification/capture-batches/{batch_id}/skc-source-links/{link_id}")
    def remove_skc_source_link(
        batch_id: str,
        link_id: int,
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        del batch_id
        try:
            return dict(sourcing_service.remove_skc_source_link(actor, link_id=link_id))
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/plugin/price-verification/capture-batches/current/chunks")
    def capture_current_price_quote_page(request: Mapping[str, Any] = Body(...)) -> Mapping[str, Any]:
        """Authenticated direct upload from the existing browser-plugin session only."""
        try:
            session_token = _required(request, "session_token")
            plugin_actor = gateway.actor_for_session(session_token)
        except (PriceVerificationNotFound, PriceVerificationContractError) as error:
            raise HTTPException(status_code=401, detail="invalid plugin session") from error
        try:
            chunk = quote_service.capture_current_page(
                plugin_actor,
                _mapping(request.get("capture"), "capture"),
                page_url=_text(request.get("page_url")),
            )
            batch = repository.get_current_quote_capture_batch(workspace_id=plugin_actor.workspace_id)
            return {
                "batch": _capture_batch_response(batch),
                "chunk": _capture_chunk_response(chunk),
                "message": f"核价本页已入库：{chunk.item_count} 条",
            }
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/quote-runs")
    def create_or_materialize_quote_run(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            command_id = _text(request.get("command_id"))
            if command_id:
                return _quote_run_response(
                    quote_service.materialize_completed_command(
                        actor, gateway.get_command(actor, command_id)
                    )
                )
            command = quote_service.queue_collection(
                actor,
                session_id=_required(request, "session_id"),
                payload=_mapping(request.get("payload"), "payload"),
                idempotency_key=_idempotency_key(request),
            )
            return _command_response(command)
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/api/v1/price-verification/quote-runs/{run_id}")
    def get_quote_run(
        run_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            return _quote_run_response(repository.get_quote_run(workspace_id=actor.workspace_id, run_id=run_id))
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/api/v1/price-verification/quote-runs/{run_id}/items")
    def get_quote_run_items(
        run_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            preview = quote_service.get_preview(actor, run_id)
            return _quote_preview_response(run_id, preview)
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/quote-runs/{run_id}/decisions")
    def record_quote_decision(
        run_id: str,
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            return quote_service.record_decision(
                actor,
                run_id,
                _required(request, "quote_key"),
                _required(request, "decision"),
                _text(request.get("note")),
            ).model_dump(mode="json")
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/api/v1/price-verification/quote-runs/{run_id}/decisions")
    def list_quote_decisions(
        run_id: str,
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            return {
                "run_id": run_id,
                "decisions": [
                    item.model_dump(mode="json")
                    for item in quote_service.list_current_decisions(actor, run_id)
                ],
            }
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/quote-runs/{run_id}/exports")
    def export_quote_run(
        run_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            return _export_response(quote_service.export_run(actor, run_id))
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/sourcing-runs")
    def create_sourcing_run(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            command = sourcing_service.queue_browser_search(
                actor,
                session_id=_required(request, "session_id"),
                quote_run_id=_required(request, "quote_run_id"),
                idempotency_key=_idempotency_key(request),
                max_quotes=_positive_int(request.get("max_quotes", 50), "max_quotes"),
            )
            return _command_response(command)
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/api/v1/price-verification/sourcing-runs/{run_id}")
    def get_sourcing_run(
        run_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            run = repository.get_sourcing_run(workspace_id=actor.workspace_id, run_id=run_id)
            return {**_sourcing_run_response(run), "preview": sourcing_service.preview(actor, run_id)}
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/api/v1/price-verification/sourcing-runs/{run_id}/retry")
    def retry_sourcing_run(
        run_id: str,
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            return _command_response(
                sourcing_service.retry_failed_items(
                    actor,
                    sourcing_run_id=run_id,
                    session_id=_required(request, "session_id"),
                    idempotency_key=_idempotency_key(request),
                    max_quotes=_positive_int(request.get("max_quotes", 50), "max_quotes"),
                )
            )
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/plugin/sessions/{session_id}/commands")
    def queue_plugin_command(
        session_id: str,
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            command_type = _required(request, "command_type")
            if command_type == "temu_price_quote_discovery":
                command = quote_service.queue_collection(
                    actor,
                    session_id=session_id,
                    payload=_mapping(request.get("payload"), "payload"),
                    idempotency_key=_idempotency_key(request),
                )
            elif command_type == "source_browser_image_search":
                command = sourcing_service.queue_browser_search(
                    actor,
                    session_id=session_id,
                    quote_run_id=_quote_run_id_for_request(actor, request, repository, gateway, quote_service),
                    idempotency_key=_idempotency_key(request),
                    max_quotes=_positive_int(request.get("max_quotes", 50), "max_quotes"),
                )
            else:
                raise PriceVerificationContractError("unsupported plugin command type")
            return _command_response(command)
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/plugin/commands/{command_id}")
    def get_plugin_command(
        command_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            return _command_response(gateway.get_command(actor, command_id))
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.get("/plugin/latest-command")
    def latest_plugin_command(
        command_type: str | None = Query(default=None),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        commands = _recent_commands(gateway, actor, command_type=command_type, limit=1)
        return {"command": commands[0] if commands else None}

    @router.get("/plugin/recent-commands")
    def recent_plugin_commands(
        command_type: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=50),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        return {"commands": _recent_commands(gateway, actor, command_type=command_type, limit=limit)}

    @router.post("/local/price-quote-discovery/preview")
    def legacy_quote_preview(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            run_id = _quote_run_id_for_request(actor, request, repository, gateway, quote_service)
            return _quote_preview_response(run_id, quote_service.get_preview(actor, run_id))
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/local/price-quote-discovery/export")
    def legacy_quote_export(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            run_id = _quote_run_id_for_request(actor, request, repository, gateway, quote_service)
            return _export_response(quote_service.export_run(actor, run_id))
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/local/source-discovery/browser-search/payload")
    def legacy_source_browser_payload(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            quote_run_id = _quote_run_id_for_request(actor, request, repository, gateway, quote_service)
            session_id = _required(request, "session_id")
            command = sourcing_service.queue_browser_search(
                actor,
                session_id=session_id,
                quote_run_id=quote_run_id,
                idempotency_key=_idempotency_key(request),
                max_quotes=_positive_int(request.get("max_quotes", 50), "max_quotes"),
            )
            return _command_response(command)
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/local/source-discovery/browser-search/preview")
    def legacy_source_browser_preview(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            run_id = _sourcing_run_id_for_request(actor, request, repository, gateway, sourcing_service)
            return sourcing_service.preview(actor, run_id)
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)

    @router.post("/local/source-discovery/onebound-search/preview")
    def legacy_onebound_preview(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            if dependencies.provider_config_resolver is None or dependencies.provider_factory is None:
                raise HTTPException(status_code=503, detail="OneBound provider is unavailable")
            quote_run_id = _quote_run_id_for_request(actor, request, repository, gateway, quote_service)
            tasks = sourcing_service.retained_search_tasks(
                actor,
                quote_run_id=quote_run_id,
                max_quotes=_positive_int(request.get("max_quotes", 50), "max_quotes"),
            )
            adapter = OneBoundSourceAdapter(
                repository,
                lambda: dependencies.provider_factory(dependencies.provider_config_resolver(actor)),
            )
            return adapter.search_by_image(actor, tasks)
        except HTTPException:
            raise
        except Exception as error:
            logging.getLogger(__name__).warning("capture chunk rejected: %s", error)
            _raise_http(error)


def _quote_run_id_for_request(
    actor: PriceVerificationActor,
    request: Mapping[str, Any],
    repository: PriceVerificationRepository,
    gateway: SharedPluginGateway,
    quote_service: QuoteService,
) -> str:
    run_id = _text(request.get("run_id") or request.get("quote_run_id"))
    if run_id:
        repository.get_quote_run(workspace_id=actor.workspace_id, run_id=run_id)
        return run_id
    command_id = _required(request, "command_id")
    command = gateway.get_command(actor, command_id)
    return quote_service.materialize_completed_command(actor, command).run_id


def _sourcing_run_id_for_request(
    actor: PriceVerificationActor,
    request: Mapping[str, Any],
    repository: PriceVerificationRepository,
    gateway: SharedPluginGateway,
    sourcing_service: SourcingService,
) -> str:
    run_id = _text(request.get("sourcing_run_id") or request.get("run_id"))
    if run_id:
        repository.get_sourcing_run(workspace_id=actor.workspace_id, run_id=run_id)
        return run_id
    command_id = _required(request, "source_command_id")
    command = gateway.get_command(actor, command_id)
    return sourcing_service.materialize_browser_result(
        actor,
        command,
        quote_run_id=_text(request.get("quote_run_id")) or None,
    ).run_id


def _recent_commands(
    gateway: SharedPluginGateway,
    actor: PriceVerificationActor,
    *,
    command_type: str | None,
    limit: int,
) -> list[Mapping[str, Any]]:
    """Read command summaries only; all mutations remain in module services."""
    if command_type is not None and command_type not in ALLOWED_PLUGIN_COMMAND_TYPES:
        raise HTTPException(status_code=422, detail="unsupported plugin command type")
    return [
        _command_response(command)
        for command in gateway.list_commands(
            actor, command_type=command_type, limit=limit
        )
    ]


def _command_response(command: PluginCommandRecord) -> Mapping[str, Any]:
    return command.model_dump(mode="json")


def _quote_run_response(run: QuoteRunRecord) -> Mapping[str, Any]:
    return run.model_dump(mode="json", exclude={"items"})


def _sourcing_run_response(run: SourcingRunRecord) -> Mapping[str, Any]:
    return run.model_dump(mode="json", exclude={"candidates"})


def _capture_batch_response(batch: Any) -> Mapping[str, Any]:
    return batch.model_dump(mode="json")


def _capture_chunk_response(chunk: Any) -> Mapping[str, Any]:
    return chunk.model_dump(mode="json", exclude={"capture", "items"})


def _quote_preview_response(run_id: str, preview: Any) -> Mapping[str, Any]:
    return {
        "run_id": run_id,
        "quotes": [asdict(quote) for quote in preview.quotes],
        "counts": asdict(preview.counts),
        "confidence_counts": dict(preview.confidence_counts),
        "authenticity_status_counts": dict(preview.authenticity_status_counts),
        "open_api_status": "not_configured",
    }


def _export_response(exported: Any) -> Mapping[str, Any]:
    return {
        "run_id": exported.run_id,
        "workbook_path": str(exported.workbook_path),
        "endpoint_report_path": str(exported.endpoint_report_path),
    }


def _session_response(session: Any) -> Mapping[str, Any]:
    return {
        "id": session.session_id,
        "workspace_id": session.workspace_id,
        "browser": session.browser,
        "plugin_version": session.plugin_version,
        "capabilities": session.capabilities,
        "status": session.status,
        "created_at": session.created_at,
        "last_seen_at": session.last_seen_at,
    }


def _plugin_package_response() -> Mapping[str, Any]:
    return {
        "package_url": "/plugin/package",
        "download_url": "/plugin/download",
        "capabilities": sorted(ALLOWED_PLUGIN_COMMAND_TYPES),
        "status": "extension_not_installed",
    }


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PriceVerificationContractError(f"{field_name} must be a mapping")
    safe = safe_json_value(value)
    if not isinstance(safe, Mapping):  # defensive: safe_json_value preserves mappings.
        raise PriceVerificationContractError(f"{field_name} must be a mapping")
    return safe


def _required(request: Mapping[str, Any], name: str) -> str:
    value = _text(request.get(name))
    if not value:
        raise PriceVerificationContractError(f"{name} is required")
    return value


def _required_value(request: Mapping[str, Any], name: str) -> object:
    value = request.get(name)
    if value is None or value == "":
        raise PriceVerificationContractError(f"{name} is required")
    return value


def _idempotency_key(request: Mapping[str, Any]) -> str:
    return _text(request.get("idempotency_key")) or uuid4().hex


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool):
        raise PriceVerificationContractError(f"{field_name} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise PriceVerificationContractError(f"{field_name} must be a positive integer") from error
    if number < 1:
        raise PriceVerificationContractError(f"{field_name} must be a positive integer")
    return number


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _text_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        return None
    return [_text(item) for item in value if _text(item)]


def _raise_http(error: Exception) -> None:
    if isinstance(error, HTTPException):
        raise error
    if isinstance(error, (PriceVerificationNotFound, PluginResourceNotFound)):
        raise HTTPException(status_code=404, detail="resource not found") from error
    if isinstance(error, PluginAuthenticationError):
        raise HTTPException(status_code=401, detail=str(error)) from error
    if isinstance(error, QuoteDecisionRequiredError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(error, CaptureBatchRequiredError):
        raise HTTPException(status_code=409, detail=str(error)) from error
    if isinstance(
        error,
        (PriceVerificationContractError, PluginLeaseError, ValidationError, ValueError, TypeError),
    ):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error
