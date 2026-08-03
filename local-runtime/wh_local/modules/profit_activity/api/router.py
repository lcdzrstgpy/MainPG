from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, status

from ..domain.models import ProfitSettings
from ..infrastructure.repository import SettingsSnapshot
from ..service import ProfitActivityConflict, ProfitActivityNotFound, ProfitActivityService
from .schemas import ArchiveRequest, CalculateRequest, FilterRequest, SettingsUpdateRequest


def create_profit_activity_router(service: ProfitActivityService) -> APIRouter:
    """返回可由主应用挂载的 Router；不在模块内创建 FastAPI app。"""
    router = APIRouter(prefix="/profit-activity", tags=["profit_activity"])

    @router.get("/settings")
    def get_settings() -> dict[str, Any]:
        return _settings_response(service.get_settings())

    @router.put("/settings")
    def update_settings(body: SettingsUpdateRequest) -> dict[str, Any]:
        try:
            snapshot = service.update_settings(body.expected_revision, ProfitSettings(**body.settings.model_dump()))
        except ProfitActivityConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return _settings_response(snapshot)

    @router.post("/calculate")
    def calculate(body: CalculateRequest) -> dict[str, Any]:
        result = service.calculate(**body.model_dump())
        return {**result, "preview": asdict(result["preview"])}

    @router.post("/records", status_code=status.HTTP_201_CREATED)
    def archive(body: ArchiveRequest) -> dict[str, Any]:
        try:
            record = service.archive(**body.model_dump())
        except ProfitActivityConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return _record_response(record)

    @router.get("/records")
    def list_records(site_code: Literal["US", "CO", "EC"] | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)) -> dict[str, list[dict[str, Any]]]:
        return {"items": [_record_response(row) for row in service.list_records(site_code, offset, limit)]}

    @router.post("/filter-runs", status_code=status.HTTP_201_CREATED)
    def run_filter(body: FilterRequest) -> dict[str, Any]:
        return _run_response(service.run_filter(**body.model_dump()))

    @router.get("/filter-runs/{run_id}")
    def get_filter_run(run_id: int) -> dict[str, Any]:
        try:
            run, decisions = service.get_filter_run(run_id)
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return _run_response(run, decisions)

    return router


def _settings_response(snapshot: SettingsSnapshot) -> dict[str, Any]:
    return {"revision": snapshot.revision, "settings": asdict(snapshot.settings)}


def _record_response(row) -> dict[str, Any]:
    return {key: getattr(row, key) for key in ("id", "site_code", "skc", "note", "selling_price", "cost_price", "weight_kg", "domestic_fee", "shipping_subsidy", "shipping_cost", "end_fee", "total_cost", "gross_profit", "net_profit", "profit_rate", "calculation_hash", "settings_revision", "revision", "created_at", "updated_at")}


def _run_response(run, decisions=None) -> dict[str, Any]:
    result = {key: getattr(run, key) for key in ("id", "site_code", "rule_version", "minimum_net_profit", "minimum_profit_rate", "retained_count", "excluded_count", "created_at")}
    if decisions is not None:
        result["decisions"] = [{"record_id": item.record_id, "decision": item.decision, "reason_code": item.reason_code} for item in decisions]
    return result
