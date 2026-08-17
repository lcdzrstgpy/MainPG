"""HTTP adapters for the workspace-isolated price-verification workflow."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query
from pydantic import ValidationError

from .contracts import (
    ALLOWED_PLUGIN_COMMAND_TYPES,
    PriceVerificationActor,
    PriceVerificationContractError,
    safe_json_value,
)
from .plugin.routes import PluginBridgeRouteDependencies, register_plugin_bridge_routes
from .plugin.service import (
    PluginAuthenticationError,
    PluginBridgeService,
    PluginLeaseError,
    PluginResourceNotFound,
)
from .quote_service import QuoteService
from .repository import (
    PluginCommandRecord,
    PriceVerificationNotFound,
    PriceVerificationRepository,
    QuoteRunRecord,
    SourcingRunRecord,
)
from .sourcing.onebound_adapter import OneBoundSourceAdapter
from .sourcing.service import SourcingService
from .sourcing.task_builder import build_source_browser_image_search_payload


@dataclass(frozen=True)
class PriceVerificationRouteDependencies:
    """Host-owned adapters required to expose this module over HTTP."""

    resolve_actor: Callable[..., Any]
    database_path: str | Path
    output_root: str | Path
    provider_config_resolver: Callable[[PriceVerificationActor], Mapping[str, Any]] | None = None
    provider_factory: Callable[[Mapping[str, Any]], Any] | None = None

    def build_services(self) -> tuple[
        PriceVerificationRepository, PluginBridgeService, QuoteService, SourcingService
    ]:
        repository = PriceVerificationRepository(self.database_path)
        bridge = PluginBridgeService(repository=repository)
        quote = QuoteService(
            repository=repository,
            plugin_bridge=bridge,
            output_root=self.output_root,
        )
        sourcing = SourcingService(repository=repository, plugin_bridge=bridge)
        return repository, bridge, quote, sourcing


def register_price_verification_routes(
    router: APIRouter, dependencies: PriceVerificationRouteDependencies
) -> None:
    """Register formal, bridge, and local-demo aliases over one service graph."""
    repository, bridge, quote_service, sourcing_service = dependencies.build_services()

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

    @router.post("/plugin/connect")
    def connect_plugin_with_pairing_code(
        request: Mapping[str, Any] = Body(...),
        authorization: str | None = Header(default=None),
    ) -> Mapping[str, str]:
        """Consume the opaque pairing code without invoking host business auth.

        The pairing code is already a short-lived, single-use credential tied
        to its persisted workspace.  Supplying it to the host's business actor
        resolver would mistake it for an administrator bearer token.
        """
        try:
            pairing_code = _pairing_code(authorization)
            session = bridge.connect(
                pairing_code,
                browser_name=_required(request, "browser_name"),
                capabilities=_mapping(request.get("capabilities"), "capabilities"),
                plugin_version=_text(request.get("plugin_version")),
            )
            return {
                "session_id": session.session_id,
                "session_token": session.token,
                "status": session.status,
            }
        except Exception as error:
            _raise_http(error)

    # The bridge's poll/result routes use only the plugin session token in
    # JSON.  The dedicated connect route above is registered first so its
    # pairing-code authentication remains independent from host business auth.
    register_plugin_bridge_routes(
        router,
        PluginBridgeRouteDependencies(service=bridge, resolve_actor=actor_dependency),
    )

    @router.post("/api/v1/price-verification/plugin/pairing-codes")
    def issue_pairing_code(
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        pairing = bridge.issue_pairing_code(actor)
        return {"id": pairing.pairing_id, "code": pairing.code, "expires_at": pairing.expires_at}

    @router.get("/api/v1/price-verification/plugin/sessions")
    @router.get("/plugin/sessions")
    def list_plugin_sessions(
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        return {"sessions": [_session_response(session) for session in bridge.list_sessions(actor)]}

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
                        actor, repository.get_command(workspace_id=actor.workspace_id, command_id=command_id)
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
            _raise_http(error)

    @router.get("/api/v1/price-verification/quote-runs/{run_id}")
    def get_quote_run(
        run_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            return _quote_run_response(repository.get_quote_run(workspace_id=actor.workspace_id, run_id=run_id))
        except Exception as error:
            _raise_http(error)

    @router.get("/api/v1/price-verification/quote-runs/{run_id}/items")
    def get_quote_run_items(
        run_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            preview = quote_service.get_preview(actor, run_id)
            return _quote_preview_response(run_id, preview)
        except Exception as error:
            _raise_http(error)

    @router.post("/api/v1/price-verification/quote-runs/{run_id}/exports")
    def export_quote_run(
        run_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            return _export_response(quote_service.export_run(actor, run_id))
        except Exception as error:
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
            _raise_http(error)

    @router.get("/api/v1/price-verification/sourcing-runs/{run_id}")
    def get_sourcing_run(
        run_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            run = repository.get_sourcing_run(workspace_id=actor.workspace_id, run_id=run_id)
            return {**_sourcing_run_response(run), "preview": sourcing_service.preview(actor, run_id)}
        except Exception as error:
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
                    quote_run_id=_quote_run_id_for_request(actor, request, repository, quote_service),
                    idempotency_key=_idempotency_key(request),
                    max_quotes=_positive_int(request.get("max_quotes", 50), "max_quotes"),
                )
            else:
                raise PriceVerificationContractError("unsupported plugin command type")
            return _command_response(command)
        except Exception as error:
            _raise_http(error)

    @router.get("/plugin/commands/{command_id}")
    def get_plugin_command(
        command_id: str, actor: PriceVerificationActor = Depends(actor_dependency)
    ) -> Mapping[str, Any]:
        try:
            return _command_response(repository.get_command(workspace_id=actor.workspace_id, command_id=command_id))
        except Exception as error:
            _raise_http(error)

    @router.get("/plugin/latest-command")
    def latest_plugin_command(
        command_type: str | None = Query(default=None),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        commands = _recent_commands(repository, actor, command_type=command_type, limit=1)
        return {"command": commands[0] if commands else None}

    @router.get("/plugin/recent-commands")
    def recent_plugin_commands(
        command_type: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=50),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        return {"commands": _recent_commands(repository, actor, command_type=command_type, limit=limit)}

    @router.post("/local/price-quote-discovery/preview")
    def legacy_quote_preview(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            run_id = _quote_run_id_for_request(actor, request, repository, quote_service)
            return _quote_preview_response(run_id, quote_service.get_preview(actor, run_id))
        except Exception as error:
            _raise_http(error)

    @router.post("/local/price-quote-discovery/export")
    def legacy_quote_export(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            run_id = _quote_run_id_for_request(actor, request, repository, quote_service)
            return _export_response(quote_service.export_run(actor, run_id))
        except Exception as error:
            _raise_http(error)

    @router.post("/local/source-discovery/browser-search/payload")
    def legacy_source_browser_payload(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            quote_run_id = _quote_run_id_for_request(actor, request, repository, quote_service)
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
            _raise_http(error)

    @router.post("/local/source-discovery/browser-search/preview")
    def legacy_source_browser_preview(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            run_id = _sourcing_run_id_for_request(actor, request, repository, sourcing_service)
            return sourcing_service.preview(actor, run_id)
        except Exception as error:
            _raise_http(error)

    @router.post("/local/source-discovery/onebound-search/preview")
    def legacy_onebound_preview(
        request: Mapping[str, Any] = Body(...),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, Any]:
        try:
            if dependencies.provider_config_resolver is None or dependencies.provider_factory is None:
                raise HTTPException(status_code=503, detail="OneBound provider is unavailable")
            quote_run_id = _quote_run_id_for_request(actor, request, repository, quote_service)
            run = repository.get_quote_run(workspace_id=actor.workspace_id, run_id=quote_run_id)
            tasks = build_source_browser_image_search_payload(
                run.items,
                max_quotes=_positive_int(request.get("max_quotes", 50), "max_quotes"),
            ).tasks
            adapter = OneBoundSourceAdapter(
                repository,
                lambda: dependencies.provider_factory(dependencies.provider_config_resolver(actor)),
            )
            return adapter.search_by_image(actor, tasks)
        except HTTPException:
            raise
        except Exception as error:
            _raise_http(error)


def _quote_run_id_for_request(
    actor: PriceVerificationActor,
    request: Mapping[str, Any],
    repository: PriceVerificationRepository,
    quote_service: QuoteService,
) -> str:
    run_id = _text(request.get("run_id") or request.get("quote_run_id"))
    if run_id:
        repository.get_quote_run(workspace_id=actor.workspace_id, run_id=run_id)
        return run_id
    command_id = _required(request, "command_id")
    command = repository.get_command(workspace_id=actor.workspace_id, command_id=command_id)
    return quote_service.materialize_completed_command(actor, command).run_id


def _sourcing_run_id_for_request(
    actor: PriceVerificationActor,
    request: Mapping[str, Any],
    repository: PriceVerificationRepository,
    sourcing_service: SourcingService,
) -> str:
    run_id = _text(request.get("sourcing_run_id") or request.get("run_id"))
    if run_id:
        repository.get_sourcing_run(workspace_id=actor.workspace_id, run_id=run_id)
        return run_id
    command_id = _required(request, "source_command_id")
    command = repository.get_command(workspace_id=actor.workspace_id, command_id=command_id)
    return sourcing_service.materialize_browser_result(
        actor,
        command,
        quote_run_id=_text(request.get("quote_run_id")) or None,
    ).run_id


def _recent_commands(
    repository: PriceVerificationRepository,
    actor: PriceVerificationActor,
    *,
    command_type: str | None,
    limit: int,
) -> list[Mapping[str, Any]]:
    """Read command summaries only; all mutations remain in module services."""
    if command_type is not None and command_type not in ALLOWED_PLUGIN_COMMAND_TYPES:
        raise HTTPException(status_code=422, detail="unsupported plugin command type")
    clauses = ["workspace_id = ?"]
    values: list[Any] = [actor.workspace_id]
    if command_type:
        clauses.append("command_type = ?")
        values.append(command_type)
    values.append(limit)
    with repository._connect() as connection:
        rows = connection.execute(
            "SELECT command_id FROM price_verification_plugin_commands WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, command_id DESC LIMIT ?",
            values,
        ).fetchall()
    return [
        _command_response(repository.get_command(workspace_id=actor.workspace_id, command_id=row["command_id"]))
        for row in rows
    ]


def _command_response(command: PluginCommandRecord) -> Mapping[str, Any]:
    return command.model_dump(mode="json")


def _quote_run_response(run: QuoteRunRecord) -> Mapping[str, Any]:
    return run.model_dump(mode="json", exclude={"items"})


def _sourcing_run_response(run: SourcingRunRecord) -> Mapping[str, Any]:
    return run.model_dump(mode="json", exclude={"candidates"})


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


def _pairing_code(authorization: str | None) -> str:
    if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
        raise PluginAuthenticationError("missing pairing-code bearer token")
    value = authorization.removeprefix("Bearer ").strip()
    if not value:
        raise PluginAuthenticationError("missing pairing-code bearer token")
    return value


def _raise_http(error: Exception) -> None:
    if isinstance(error, HTTPException):
        raise error
    if isinstance(error, (PriceVerificationNotFound, PluginResourceNotFound)):
        raise HTTPException(status_code=404, detail="resource not found") from error
    if isinstance(error, PluginAuthenticationError):
        raise HTTPException(status_code=401, detail=str(error)) from error
    if isinstance(
        error,
        (PriceVerificationContractError, PluginLeaseError, ValidationError, ValueError, TypeError),
    ):
        raise HTTPException(status_code=422, detail=str(error)) from error
    raise error
