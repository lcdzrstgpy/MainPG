"""FastAPI routes for the local price-verification plugin bridge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..contracts import PriceVerificationActor, PriceVerificationContractError, safe_json_value
from ..repository import PluginCommandRecord
from .service import PluginAuthenticationError, PluginBridgeService, PluginLeaseError


@dataclass(frozen=True)
class PluginBridgeRouteDependencies:
    service: PluginBridgeService
    resolve_actor: Callable[[], Any]


class _ConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    browser_name: str
    capabilities: Mapping[str, Any] = Field(default_factory=dict)
    plugin_version: str = ""


class _PollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str
    limit: int = Field(default=10, ge=1, le=50)


class _ResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_token: str
    command_id: str
    status: str
    result: Mapping[str, Any] = Field(default_factory=dict)


def register_plugin_bridge_routes(
    router: APIRouter, dependencies: PluginBridgeRouteDependencies
) -> None:
    """Register precisely the three local bridge endpoints on ``router``."""

    def actor_dependency(actor: Any = Depends(dependencies.resolve_actor)) -> PriceVerificationActor:
        if isinstance(actor, PriceVerificationActor):
            return actor
        try:
            return PriceVerificationActor.model_validate(actor)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=401, detail="authenticated workspace required") from error

    @router.post("/plugin/connect")
    def connect(
        request: _ConnectRequest = Body(...),
        authorization: str | None = Header(default=None),
        actor: PriceVerificationActor = Depends(actor_dependency),
    ) -> Mapping[str, str]:
        pairing_code = _bearer_token(authorization)
        try:
            # Validate at the HTTP boundary as well as in the service.
            capabilities = safe_json_value(request.capabilities)
            session = dependencies.service.connect(
                pairing_code,
                browser_name=request.browser_name,
                capabilities=capabilities,
                plugin_version=request.plugin_version,
            )
        except PluginAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except PriceVerificationContractError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if session.workspace_id != actor.workspace_id:
            raise HTTPException(status_code=401, detail="pairing code is not valid for this workspace")
        return {"session_id": session.session_id, "session_token": session.token, "status": session.status}

    @router.post("/plugin/poll")
    def poll(
        request: _PollRequest = Body(...),
        authorization: str | None = Header(default=None),
    ) -> list[Mapping[str, Any]]:
        _reject_authorization(authorization)
        try:
            commands = dependencies.service.poll(request.session_token, limit=request.limit)
        except PluginAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except PriceVerificationContractError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return [_command_response(command) for command in commands]

    @router.post("/plugin/result")
    def receive_result(
        request: _ResultRequest = Body(...),
        authorization: str | None = Header(default=None),
    ) -> Mapping[str, Any]:
        _reject_authorization(authorization)
        try:
            # Rejection here prevents hostile input from reaching service or storage.
            result = safe_json_value(request.result)
            command = dependencies.service.receive_result(
                request.session_token, request.command_id, request.status, result
            )
        except PluginAuthenticationError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except (PluginLeaseError, PriceVerificationContractError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return _command_response(command)


def _bearer_token(value: str | None) -> str:
    if not isinstance(value, str) or not value.startswith("Bearer ") or not value[7:].strip():
        raise HTTPException(status_code=401, detail="missing pairing-code bearer token")
    return value[7:].strip()


def _reject_authorization(value: str | None) -> None:
    if value is not None:
        raise HTTPException(status_code=401, detail="session_token must be supplied only in JSON")


def _command_response(command: PluginCommandRecord) -> Mapping[str, Any]:
    return {
        "command_id": command.command_id,
        "command_type": command.command_type,
        "payload": command.payload,
        "result": command.result,
        "status": command.status,
        "lease_expires_at": command.lease_expires_at,
        "created_at": command.created_at,
        "updated_at": command.updated_at,
    }
