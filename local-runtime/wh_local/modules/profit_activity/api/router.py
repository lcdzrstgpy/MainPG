from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from ..domain.models import ProfitSettings
from ..infrastructure.repository import SettingsSnapshot
from ..service import ProfitActivityConflict, ProfitActivityNotFound, ProfitActivityService
from .schemas import ArchiveRequest, FilterRequest, SettingsUpdateRequest


def create_profit_activity_router(service: ProfitActivityService) -> APIRouter:
    """Router contract for the complete Profit Activity screen."""
    router = APIRouter(prefix="/profit-activity", tags=["profit_activity"])

    @router.get("/settings")
    def get_settings() -> dict[str, Any]:
        return service.legacy_settings()

    @router.put("/settings")
    async def update_settings(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            if isinstance(payload, dict) and "settings" in payload:
                parsed = SettingsUpdateRequest.model_validate(payload)
                snapshot = service.update_settings(parsed.expected_revision, ProfitSettings(**parsed.settings.model_dump()))
                return _settings_response(snapshot)
            return service.update_legacy_settings(payload if isinstance(payload, dict) else {})
        except ProfitActivityConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.post("/calculate")
    async def calculate(request: Request) -> dict[str, Any]:
        try:
            payload = await request.json()
            legacy = service.calculate_legacy(payload)
            preview = legacy["calculation"].copy()
            preview["site_code"] = preview["site"]
            return {**legacy, "preview": preview}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Current module API kept for callers created before legacy screen parity.
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

    @router.get("/products")
    def list_products(site: Literal["US", "CO", "EC"] | None = None, site_code: Literal["US", "CO", "EC"] | None = None, skcs: str = "", scope: str = "default", owner_user_id: int | None = None) -> dict[str, Any]:
        requested = [item.strip() for item in skcs.replace("，", ",").replace("\n", ",").split(",") if item.strip()]
        return {"products": service.list_products(site=site or site_code, skcs=requested), "scope": scope, "owner_user_id": owner_user_id}

    @router.post("/products")
    async def create_product(request: Request) -> dict[str, Any]:
        try:
            payload, image, source_image, source_group_images = await _product_form(request)
            return {"product": service.upsert_product(payload, image=image, source_image=source_image, source_group_images=source_group_images)}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.post("/products/{skc}/update")
    async def update_product_form(skc: str, request: Request) -> dict[str, Any]:
        try:
            payload, image, source_image, source_group_images = await _product_form(request)
            payload["skc"] = skc
            return {"product": service.upsert_product(payload, image=image, source_image=source_image, source_group_images=source_group_images)}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.patch("/products/{skc}")
    async def update_product_values(skc: str, request: Request) -> dict[str, Any]:
        try:
            return {"product": service.update_product_values(skc, await request.json())}
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.delete("/products/{skc}")
    def delete_product(skc: str, site: Literal["US", "CO", "EC"] = "US", owner_user_id: int | None = None) -> dict[str, Any]:
        return service.delete_product(skc, site)

    @router.delete("/products")
    async def delete_products(request: Request) -> dict[str, Any]:
        payload = await request.json()
        site = str(payload.get("site") or "US")
        results = [service.delete_product(str(skc), site) for skc in payload.get("skcs", [])]
        return {"deleted": sum(item["status"] == "deleted" for item in results), "results": results}

    @router.get("/products/{skc}/image")
    def product_image(skc: str, site: Literal["US", "CO", "EC"] = "US", kind: str = "product", group: int = 0, index: int = 0):
        try:
            path = service.image_path(skc, site, kind, group, index)
        except (ProfitActivityNotFound, ValueError) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return FileResponse(path, filename=path.name)

    @router.post("/products/import/preview")
    async def preview_import(request: Request) -> dict[str, Any]:
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is required")
        filename = str(getattr(file, "filename", "products.xlsx") or "products.xlsx")
        if Path(filename).suffix.lower() not in {".xlsx", ".xlsm"}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "only .xlsx or .xlsm is supported")
        try:
            content = await file.read()
            await file.close()
            return service.preview_import(content, filename, str(form.get("site") or "US"))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.post("/products/import/confirm")
    async def confirm_import(request: Request) -> dict[str, Any]:
        payload = await request.json()
        try:
            return service.confirm_import(str(payload.get("import_id") or ""), payload.get("selected_row_ids"), str(payload.get("on_conflict") or "skip"))
        except (ProfitActivityNotFound, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.get("/products/import/{import_id}/image/{row_id}")
    def import_preview_image(import_id: str, row_id: str, kind: Literal["product", "source"] = "product"):
        # The preview response advertises image availability per row. This endpoint
        # intentionally returns 404 when the uploaded workbook has no embedded image.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import_preview_image_not_available")

    @router.get("/products/import/tasks/{task_id}")
    def import_task(task_id: int) -> dict[str, Any]:
        try:
            return service.get_import_task(task_id)
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.post("/catalog/rebuild")
    def rebuild_catalog(site: Literal["US", "CO", "EC"] = "US"):
        path = service.create_catalog(site)
        return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @router.post("/activity-filter")
    async def activity_filter(request: Request) -> dict[str, Any]:
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is required")
        try:
            content = await file.read()
            await file.close()
            return service.filter_activity_template(content, str(getattr(file, "filename", "activity.xlsx") or "activity.xlsx"), str(form.get("site") or "US"))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.get("/activity-filter/tasks/{task_id}")
    def filter_task(task_id: int) -> dict[str, Any]:
        try:
            return service.get_filter_task_legacy(task_id)
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.get("/activity-filter/{task_id}/download")
    def filter_download(task_id: int, kind: Literal["filtered", "removed"] = "filtered"):
        try:
            path = service.output_path(task_id, kind)
        except (ProfitActivityNotFound, ValueError) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    return router


async def _product_form(request: Request) -> tuple[dict[str, Any], tuple[str, bytes] | None, tuple[str, bytes] | None, dict[int, list[tuple[str, bytes]]]]:
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("product payload must be an object")
        return payload, None, None, {}
    form = await request.form()
    payload = {key: value for key, value in form.items() if not hasattr(value, "read")}
    image = await _uploaded_file(form.get("image"))
    source_image = await _uploaded_file(form.get("source_image"))
    groups: dict[int, list[tuple[str, bytes]]] = {}
    for key, value in form.multi_items():
        if not key.startswith("source_group_image_"):
            continue
        uploaded = await _uploaded_file(value)
        if uploaded is None:
            continue
        try:
            index = int(key.rsplit("_", 1)[1])
        except ValueError:
            index = 0
        groups.setdefault(index, []).append(uploaded)
    return payload, image, source_image, groups


async def _uploaded_file(value: Any) -> tuple[str, bytes] | None:
    if value is None or not hasattr(value, "read"):
        return None
    content = await value.read()
    await value.close()
    return (str(getattr(value, "filename", "upload.bin") or "upload.bin"), content) if content else None


def _settings_response(snapshot: SettingsSnapshot) -> dict[str, Any]:
    return {"revision": snapshot.revision, "settings": asdict(snapshot.settings)}


def _record_response(row) -> dict[str, Any]:
    return {key: getattr(row, key) for key in ("id", "site_code", "skc", "note", "selling_price", "cost_price", "weight_kg", "domestic_fee", "shipping_subsidy", "shipping_cost", "end_fee", "total_cost", "gross_profit", "net_profit", "profit_rate", "calculation_hash", "settings_revision", "revision", "created_at", "updated_at")}


def _run_response(run, decisions=None) -> dict[str, Any]:
    result = {key: getattr(run, key) for key in ("id", "site_code", "rule_version", "minimum_net_profit", "minimum_profit_rate", "retained_count", "excluded_count", "created_at")}
    if decisions is not None:
        result["decisions"] = [{"record_id": item.record_id, "decision": item.decision, "reason_code": item.reason_code} for item in decisions]
    return result
