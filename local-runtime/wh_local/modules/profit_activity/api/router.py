from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from ..domain.models import ProfitSettings
from ..infrastructure.repository import SettingsSnapshot
from ..service import ProfitActivityConflict, ProfitActivityNotFound, ProfitActivityService
from .schemas import ArchiveRequest, FilterRequest, SettingsUpdateRequest
from ....session import Actor, actor_from_bearer_token, actor_has_permission, require_permission
from ....config import default_config
from ....db import connect


def create_profit_activity_router(service: ProfitActivityService, database_path: Path | None = None) -> APIRouter:
    """Router contract for the complete Profit Activity screen."""
    router = APIRouter(prefix="/profit-activity", tags=["profit_activity"])

    def profit_activity_actor(
        authorization: str | None = Header(default=None),
    ) -> Actor:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        return actor_from_bearer_token(
            authorization.removeprefix("Bearer ").strip(),
            database_path,
        )

    @router.get("/settings")
    def get_settings(actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.read", database_path)
        return service.legacy_settings(actor)

    @router.put("/settings")
    async def update_settings(request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.settings_manage", database_path)
        payload = await request.json()
        try:
            if isinstance(payload, dict) and "settings" in payload:
                parsed = SettingsUpdateRequest.model_validate(payload)
                snapshot = service.update_settings(parsed.expected_revision, ProfitSettings(**parsed.settings.model_dump()), actor)
                return _settings_response(snapshot)
            return service.update_legacy_settings(payload if isinstance(payload, dict) else {}, actor)
        except ProfitActivityConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.post("/calculate")
    async def calculate(request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.read", database_path)
        try:
            payload = await request.json()
            legacy = service.calculate_legacy(payload, actor)
            preview = legacy["calculation"].copy()
            preview["site_code"] = preview["site"]
            return {**legacy, "preview": preview}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Current module API kept for callers created before legacy screen parity.
    @router.post("/records", status_code=status.HTTP_201_CREATED)
    def archive(body: ArchiveRequest, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.write", database_path)
        try:
            record = service.archive(**body.model_dump(), actor=actor)
        except ProfitActivityConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        return _record_response(record)

    @router.get("/records")
    def list_records(site_code: Literal["US", "CO", "EC"] | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), scope: str = "default", actor: Actor = Depends(profit_activity_actor)) -> dict[str, list[dict[str, Any]]]:
        require_permission(actor, "profit_activity.read", database_path)
        include_company = _include_company(scope, actor, database_path)
        return {"items": [_record_response(row) for row in service.list_records(site_code, offset, limit, actor, include_workspace_shared=include_company)]}

    @router.post("/filter-runs", status_code=status.HTTP_201_CREATED)
    def run_filter(body: FilterRequest, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.filter", database_path)
        run, decision_items = service.run_filter(**body.model_dump(), actor=actor, include_workspace_shared=actor_has_permission(actor, "profit_activity.company_read", database_path))
        return _run_response(run, decision_items)

    @router.get("/filter-runs/{run_id}")
    def get_filter_run(run_id: int, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.read", database_path)
        try:
            run, decisions = service.get_filter_run(run_id, actor)
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return _run_response(run, decisions)

    @router.get("/products")
    def list_products(site: Literal["US", "CO", "EC"] | None = None, site_code: Literal["US", "CO", "EC"] | None = None, skcs: str = "", scope: str = "default", owner_user_id: int | None = None, source_type: str = "", actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.read", database_path)
        requested = [item.strip() for item in re.split(r"[\s,，]+", skcs) if item.strip()]
        include_company = _include_company(scope, actor, database_path)
        return {"products": service.list_products(site=site or site_code, skcs=requested, source_type=source_type.strip() or None, actor=actor, include_workspace_shared=include_company), "scope": scope, "owner_user_id": owner_user_id}

    @router.get("/products/{skc}/sources")
    def product_sources(skc: str, site: Literal["US", "CO", "EC"] = "US", actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        """Return the active 1688 source links associated with one product.

        产品库的 source_groups_json 契约只保留 source_url + image_paths，
        这里在同库联查核价模块的 price_verification_skc_source_links，
        返回每条 1688 链接的完整明细（价格/起订量/运费/链接 id/batch_id），
        供产品库右侧货源侧边栏展示、调价重算与解除关联。
        """
        require_permission(actor, "profit_activity.read", database_path)
        db_path = database_path or default_config().database_path
        try:
            products = service.list_products(site=site, skcs=[skc], actor=actor)
        except Exception:
            products = []
        product = products[0] if products else None
        result: dict[str, Any] = {
            "skc": skc,
            "site": site,
            "product_title": (product or {}).get("note") or "",
            "selling_price": (product or {}).get("selling_price"),
            "links": [],
        }
        if product is None:
            return result
        source_urls = [str(group.get("source_url") or "").strip() for group in (product.get("source_groups") or [])]
        source_urls = [url for url in source_urls if url]
        if not source_urls:
            return result
        try:
            conn = connect(db_path)
            try:
                placeholders = ",".join("?" for _ in source_urls)
                rows = conn.execute(
                    f"""
                    SELECT id, batch_id, skc_id, offer_id, source_url, source_title,
                           main_image_url, price_cny, moq, domestic_freight_cny,
                           source_decision, note
                    FROM price_verification_skc_source_links
                    WHERE source_url IN ({placeholders}) AND skc_id = ? AND status = 'active'
                    ORDER BY id ASC
                    """,
                    (*source_urls, skc),
                ).fetchall()
                result["links"] = [dict(row) for row in rows]
            finally:
                conn.close()
        except Exception:
            # 核价链接表不存在（独立部署的产品库）或查询失败时保持空列表。
            result["links"] = []
        return result

    @router.post("/products")
    async def create_product(request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.write", database_path)
        try:
            payload, image, source_image, source_group_images = await _product_form(request)
            return {"product": service.upsert_product(payload, actor=actor, allow_company_write=actor_has_permission(actor, "profit_activity.company_write", database_path), require_complete_profile=True, image=image, source_image=source_image, source_group_images=source_group_images)}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.post("/products/{skc}/update")
    async def update_product_form(skc: str, request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.write", database_path)
        try:
            payload, image, source_image, source_group_images = await _product_form(request)
            payload["skc"] = skc
            return {"product": service.upsert_product(payload, actor=actor, allow_company_write=actor_has_permission(actor, "profit_activity.company_write", database_path), require_complete_profile=True, image=image, source_image=source_image, source_group_images=source_group_images)}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.patch("/products/{skc}")
    async def update_product_values(skc: str, request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.write", database_path)
        try:
            return {"product": service.update_product_values(skc, await request.json(), actor, allow_company_write=actor_has_permission(actor, "profit_activity.company_write", database_path))}
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.delete("/products/{skc}")
    def delete_product(skc: str, site: Literal["US", "CO", "EC"] = "US", owner_user_id: int | None = None, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.delete", database_path)
        return service.delete_product(skc, site, actor, allow_company_delete=actor_has_permission(actor, "profit_activity.company_delete", database_path))

    @router.delete("/products")
    async def delete_products(request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.delete", database_path)
        payload = await request.json()
        site = str(payload.get("site") or "US")
        results = [service.delete_product(str(skc), site, actor, allow_company_delete=actor_has_permission(actor, "profit_activity.company_delete", database_path)) for skc in payload.get("skcs", [])]
        return {"deleted": sum(item["status"] == "deleted" for item in results), "results": results}

    @router.get("/products/{skc}/image")
    def product_image(skc: str, site: Literal["US", "CO", "EC"] = "US", kind: str = "product", group: int = 0, index: int = 0, actor: Actor = Depends(profit_activity_actor)):
        require_permission(actor, "profit_activity.read", database_path)
        try:
            path = service.image_path(skc, site, kind, group, index, actor)
        except (ProfitActivityNotFound, ValueError) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return FileResponse(path, filename=path.name)

    @router.post("/products/import/preview")
    async def preview_import(request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.import", database_path)
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
            return service.preview_import(content, filename, str(form.get("site") or "US"), actor)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.post("/products/import/confirm")
    async def confirm_import(request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.import", database_path)
        payload = await request.json()
        try:
            return service.confirm_import(str(payload.get("import_id") or ""), payload.get("selected_row_ids"), str(payload.get("on_conflict") or "skip"), actor)
        except (ProfitActivityNotFound, ValueError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.get("/products/import/sessions/latest")
    def latest_import_session(actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any] | None:
        require_permission(actor, "profit_activity.import", database_path)
        return service.latest_import_session(actor)

    @router.get("/products/import/sessions")
    def list_import_sessions(actor: Actor = Depends(profit_activity_actor)) -> list[dict[str, Any]]:
        require_permission(actor, "profit_activity.import", database_path)
        return service.list_import_sessions(actor)

    @router.get("/products/import/{import_id}/image/{row_id}")
    def import_preview_image(
        import_id: str,
        row_id: str,
        kind: Literal["product", "source"] = "product",
        variant: Literal["original", "thumb", "thumbnail", "preview"] = "original",
        actor: Actor = Depends(profit_activity_actor),
    ):
        """Return an embedded workbook image for the requested preview row.

        ``variant`` is accepted for the original front-end contract.  Local
        storage keeps the source bytes, so each supported variant returns that
        original image instead of making an extra lossy derivative.
        """
        require_permission(actor, "profit_activity.import", database_path)
        try:
            path = service.import_image_path(import_id, row_id, kind, actor)
        except (ProfitActivityNotFound, ValueError) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return FileResponse(path, filename=path.name)

    @router.get("/products/import/tasks/{task_id}")
    def import_task(task_id: int, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.import", database_path)
        try:
            return service.get_import_task(task_id, actor)
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.post("/catalog/rebuild")
    def rebuild_catalog(site: Literal["US", "CO", "EC"] = "US", scope: str = "default", actor: Actor = Depends(profit_activity_actor)):
        require_permission(actor, "profit_activity.export", database_path)
        path = service.create_catalog(site, actor, include_workspace_shared=_include_company(scope, actor, database_path))
        return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @router.post("/activity-filter")
    async def activity_filter(request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.filter", database_path)
        form = await request.form()
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is required")
        try:
            content = await file.read()
            await file.close()
            scope = str(form.get("scope") or "default")
            return service.filter_activity_template(content, str(getattr(file, "filename", "activity.xlsx") or "activity.xlsx"), str(form.get("site") or "US"), actor, include_workspace_shared=_include_company(scope, actor, database_path))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.get("/activity-filter/tasks/{task_id}")
    def filter_task(task_id: int, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.read", database_path)
        try:
            return service.get_filter_task_legacy(task_id, actor)
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.get("/activity-filter/{task_id}/download")
    def filter_download(task_id: int, kind: Literal["filtered", "removed"] = "filtered", actor: Actor = Depends(profit_activity_actor)):
        require_permission(actor, "profit_activity.export", database_path)
        try:
            path = service.output_path(task_id, kind, actor)
        except (ProfitActivityNotFound, ValueError) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @router.post("/activity-filter/{task_id}/save")
    def filter_save(task_id: int, kind: Literal["filtered", "removed"] = "filtered", actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.export", database_path)
        try:
            saved_path = service.save_filter_output(task_id, kind, actor)
        except (ProfitActivityNotFound, ValueError) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return {"saved_path": str(saved_path)}

    return router


def _include_company(scope: str, actor: Actor, database_path: Path | None) -> bool:
    if scope not in {"company", "workspace", "shared"}:
        return False
    require_permission(actor, "profit_activity.company_read", database_path)
    return True


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
        if key.startswith("source_group_images_"):
            suffix = key.removeprefix("source_group_images_")
        elif key.startswith("source_group_image_"):
            suffix = key.removeprefix("source_group_image_")
        else:
            continue
        uploaded = await _uploaded_file(value)
        if uploaded is None:
            continue
        try:
            index = int(suffix)
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
        result["decisions"] = [
            {
                "record_id": item.get("record_id") if isinstance(item, dict) else item.record_id,
                "skc": item.get("skc") if isinstance(item, dict) else None,
                "site": item.get("site") if isinstance(item, dict) else None,
                "decision": item.get("decision") if isinstance(item, dict) else item.decision,
                "reason_code": item.get("reason_code") if isinstance(item, dict) else item.reason_code,
            }
            for item in decisions
        ]
    return result

