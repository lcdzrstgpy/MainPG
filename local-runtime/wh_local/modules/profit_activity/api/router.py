from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import FileResponse

from ..domain.models import ProfitSettings, ProfitSiteProfile
from ..infrastructure.repository import SettingsSnapshot
from ..service import ProfitActivityConflict, ProfitActivityNotFound, ProfitActivityService, _local_iso
from .schemas import ArchiveRequest, FilterRequest, SettingsUpdateRequest, SiteProfilePayload
from ....session import Actor, actor_from_bearer_token, actor_has_permission, require_permission
from ....config import default_config
from ....db import connect
from ....price_verification.repository import PriceVerificationRepository
from ....data_collection.plugin_queue import DataCollectionPluginQueue, TEMU_FLUX_ACCEL


logger = logging.getLogger("wh_local.profit_activity")


def create_profit_activity_router(
    service: ProfitActivityService,
    database_path: Path | None = None,
    plugin_queue: DataCollectionPluginQueue | None = None,
) -> APIRouter:
    """Router contract for the complete Profit Activity screen."""
    router = APIRouter(prefix="/profit-activity", tags=["profit_activity"])

    def delete_product_and_source_links(skc: str, site: str, actor: Actor, *, allow_company_delete: bool) -> dict[str, Any]:
        result = service.delete_product(skc, site, actor, allow_company_delete=allow_company_delete)
        if result.get("status") == "deleted" and database_path is not None:
            PriceVerificationRepository(database_path).soft_remove_skc_source_links_for_skc(
                workspace_id=actor.workspace_id,
                skc_id=skc,
                now=_local_iso(datetime.now(timezone.utc)),
            )
        return result

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

    @router.get("/sites")
    def list_sites(actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.read", database_path)
        return {"sites": service.list_sites(actor)}

    @router.post("/sites", status_code=status.HTTP_201_CREATED)
    def create_site(body: SiteProfilePayload, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.settings_manage", database_path)
        try:
            return {"site": service.create_site(ProfitSiteProfile(**body.model_dump()), actor)}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.put("/sites/{site_code}")
    def update_site(site_code: str, body: SiteProfilePayload, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.settings_manage", database_path)
        if site_code.upper() != body.site_code:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "site_code_path_mismatch")
        try:
            return {"site": service.update_site(ProfitSiteProfile(**body.model_dump()), actor)}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

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
    def list_records(site_code: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), scope: str = "default", actor: Actor = Depends(profit_activity_actor)) -> dict[str, list[dict[str, Any]]]:
        require_permission(actor, "profit_activity.read", database_path)
        include_company = _include_company(scope, actor, database_path)
        return {"items": [_record_response(row) for row in service.list_records(site_code, offset, limit, actor, include_workspace_shared=include_company)]}

    @router.post("/filter-runs", status_code=status.HTTP_201_CREATED)
    def run_filter(body: FilterRequest, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.filter", database_path)
        try:
            run, decision_items = service.run_filter(**body.model_dump(), actor=actor, include_workspace_shared=actor_has_permission(actor, "profit_activity.company_read", database_path))
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return _run_response(run, decision_items)

    @router.get("/filter-runs/{run_id}")
    def get_filter_run(run_id: int, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.read", database_path)
        try:
            run, decisions = service.get_filter_run(run_id, actor)
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return _run_response(run, decisions)

    @router.get("/filter-runs/{run_id}/download")
    def filter_run_download(run_id: int, kind: Literal["eligible", "excluded"] = "eligible", actor: Actor = Depends(profit_activity_actor)):
        require_permission(actor, "profit_activity.export", database_path)
        try:
            path = service.export_filter_run(run_id, kind, actor)
        except (ProfitActivityNotFound, ValueError) as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        return FileResponse(path, filename=path.name, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @router.get("/products")
    def list_products(site: str | None = None, site_code: str | None = None, skcs: str = "", product_ids: str = "", scope: str = "default", owner_user_id: int | None = None, source_type: str = "", actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.read", database_path)
        requested = [item.strip() for item in re.split(r"[\s,，]+", product_ids or skcs) if item.strip()]
        include_company = _include_company(scope, actor, database_path)
        products = service.list_products(
            site=site or site_code,
            skcs=requested,
            source_type=source_type.strip() or None,
            actor=actor,
            include_workspace_shared=include_company,
        )
        _enrich_product_source_images(products, database_path, actor.workspace_id)
        return {"products": products, "scope": scope, "owner_user_id": owner_user_id}

    @router.get("/products/{skc}/sources")
    def product_sources(skc: str, site: str = "US", actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
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
        # 非核价入库产品：直接按货源组逐组展示（每组一条，含各自的截图与组号，
        # 不按 URL 去重，避免同 URL 多组时只显示第一组、图片无法按组对应）。
        # 核价入库产品保持原逻辑：联查核价链接表展示价格/运费/解除关联。
        if str((product or {}).get("source_type") or "") != "price_verification":
            result["links"] = _product_source_group_links(product)
            return result
        source_urls = [str(group.get("source_url") or "").strip() for group in (product.get("source_groups") or [])]
        source_urls = [url for url in source_urls if url]
        legacy_url = str((product or {}).get("source_url") or "").strip()
        if legacy_url and legacy_url not in source_urls:
            source_urls.append(legacy_url)
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
                # 仅非核价入库产品：核价链接表可能只匹配到部分货源组（例如手工
                # 导入的链接未做核价），这里把未匹配的 source_groups 链接补全，
                # 保证侧边栏打开即显示全部链接。核价入库产品保持原行为。
                if str((product or {}).get("source_type") or "") != "price_verification":
                    matched = {str(link.get("source_url") or "").strip() for link in result["links"]}
                    for fallback_link in _product_source_fallbacks(product, source_urls):
                        url = str(fallback_link.get("source_url") or "").strip()
                        if url and url not in matched:
                            result["links"].append(fallback_link)
            finally:
                conn.close()
        except Exception:
            # 核价链接表不存在（独立部署的产品库）或查询失败时，回退展示自身货源组链接。
            result["links"] = _product_source_fallbacks(product, source_urls)
        # 核价链接本身不保存截图；按 source_url 从产品货源组匹配截图与组号，
        # 保证货源侧边栏每条链接都能显示对应的货源图。
        result["links"] = _attach_source_group_images(result["links"], product)
        return result

    @router.post("/products")
    async def create_product(request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.write", database_path)
        try:
            payload, image, attachment_image, source_image, source_group_images = await _product_form(request)
            return {"product": service.upsert_product(payload, actor=actor, allow_company_write=actor_has_permission(actor, "profit_activity.company_write", database_path), require_complete_profile=True, image=image, attachment_image=attachment_image, source_image=source_image, source_group_images=source_group_images)}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.post("/products/{skc}/update")
    async def update_product_form(skc: str, request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        """更新产品（例如上传/替换 SKC 对应图）。

        与 PATCH 保存售价/成本/重量一致，不要求产品资料完整（老数据可能缺
        货源图/备注），只更新提交的字段与图片，其余沿用当前记录。
        """
        require_permission(actor, "profit_activity.write", database_path)
        try:
            payload, image, attachment_image, source_image, source_group_images = await _product_form(request)
            # 路径参数始终指向修改前的商品 ID；请求体可提交新的 product_id。
            payload["current_skc"] = skc
            return {"product": service.upsert_product(payload, actor=actor, allow_company_write=actor_has_permission(actor, "profit_activity.company_write", database_path), require_complete_profile=False, image=image, attachment_image=attachment_image, source_image=source_image, source_group_images=source_group_images)}
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
    def delete_product(skc: str, site: str = "US", owner_user_id: int | None = None, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.delete", database_path)
        return delete_product_and_source_links(skc, site, actor, allow_company_delete=actor_has_permission(actor, "profit_activity.company_delete", database_path))

    @router.delete("/products")
    async def delete_products(request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.delete", database_path)
        payload = await request.json()
        site = str(payload.get("site") or "US")
        results = [delete_product_and_source_links(str(skc), site, actor, allow_company_delete=actor_has_permission(actor, "profit_activity.company_delete", database_path)) for skc in payload.get("skcs", [])]
        return {"deleted": sum(item["status"] == "deleted" for item in results), "results": results}

    @router.get("/products/{skc}/image")
    def product_image(skc: str, site: str = "US", kind: str = "product", group: int = 0, index: int = 0, actor: Actor = Depends(profit_activity_actor)):
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
    def rebuild_catalog(site: str = "US", sites: str = "", scope: str = "default", actor: Actor = Depends(profit_activity_actor)):
        require_permission(actor, "profit_activity.export", database_path)
        selected = [item.strip().upper() for item in re.split(r"[,，\s]+", sites) if item.strip()]
        if not selected:
            selected = [site]
        path = service.create_catalog(selected, actor, include_workspace_shared=_include_company(scope, actor, database_path))
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
            filename = str(getattr(file, "filename", "activity.xlsx") or "activity.xlsx")
            await file.close()
            scope = str(form.get("scope") or "default")
            task_id = service.start_activity_filter(content, filename, str(form.get("site") or "US"), actor, include_workspace_shared=_include_company(scope, actor, database_path))
            return {"task_id": task_id, "filter_task_id": task_id, "operation_task_id": task_id, "status": "running", "task_status": "running"}
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    @router.post("/activity-filter/{task_id}/dispatch-flux-accel")
    async def dispatch_flux_accel(task_id: int, request: Request, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        """把活动过滤结果里的可申报商品下发到流量加速插件。

        商品标识 + 最低申报价来自 ``eligible_activity_products``，插件领取后
        在 TEMU 流量分析页采集「日常申报价 + 三档加权价」并生成活动价表格。
        """
        require_permission(actor, "profit_activity.filter", database_path)
        if plugin_queue is None:
            logger.error("dispatch_flux_accel: plugin queue unavailable (task %s)", task_id)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "plugin queue is unavailable")
        payload = await request.json()
        session_id = _session_id(payload.get("session_id"))
        logger.info(
            "dispatch_flux_accel: actor=%s workspace=%s task=%s session=%s",
            actor.id, actor.workspace_id, task_id, session_id,
        )
        try:
            products = service.eligible_activity_products(task_id, actor)
        except ProfitActivityNotFound as exc:
            logger.warning("dispatch_flux_accel: task %s not found", task_id)
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        if not products:
            logger.warning("dispatch_flux_accel: task %s has no eligible products", task_id)
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "no eligible products to dispatch")
        logger.info("dispatch_flux_accel: task %s dispatching %d products", task_id, len(products))
        try:
            command = plugin_queue.queue_command(
                actor_id=actor.id,
                workspace_id=actor.workspace_id,
                session_id=session_id,
                command_type=TEMU_FLUX_ACCEL,
                payload={"task_id": task_id, "products": products},
                idempotency_key=f"flux-accel:{actor.workspace_id}:{task_id}",
            )
        except PermissionError as exc:
            logger.warning("dispatch_flux_accel: session %s not found for actor %s", session_id, actor.id)
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            logger.warning("dispatch_flux_accel: queue rejected command: %s", exc)
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        logger.info("dispatch_flux_accel: queued command %s (type=%s status=%s)", command.command_id, command.command_type, command.status)
        return {
            "task_id": task_id,
            "command_id": command.command_id,
            "command_type": command.command_type,
            "status": command.status,
            "product_count": len(products),
        }

    @router.get("/activity-filter/tasks")
    def list_filter_tasks(limit: int = Query(20, ge=1, le=100), actor: Actor = Depends(profit_activity_actor)) -> list[dict[str, Any]]:
        require_permission(actor, "profit_activity.read", database_path)
        return service.list_filter_tasks(actor, limit=limit)

    @router.get("/activity-filter/tasks/{task_id}")
    def filter_task(task_id: int, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.read", database_path)
        try:
            return service.get_filter_task_legacy(task_id, actor)
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.get("/activity-filter/{task_id}/eligible")
    def activity_filter_eligible(task_id: int, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.read", database_path)
        try:
            return {"products": service.eligible_activity_products(task_id, actor)}
        except ProfitActivityNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    @router.post("/activity-filter/{task_id}/pause")
    def pause_filter_task(task_id: int, actor: Actor = Depends(profit_activity_actor)) -> dict[str, Any]:
        require_permission(actor, "profit_activity.filter", database_path)
        try:
            return service.pause_activity_filter(task_id, actor)
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


def _session_id(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "session_id is required") from exc
    if parsed < 1:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "session_id must be positive")
    return parsed


async def _product_form(request: Request) -> tuple[dict[str, Any], tuple[str, bytes] | None, tuple[str, bytes] | None, tuple[str, bytes] | None, dict[int, list[tuple[str, bytes]]]]:
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
        if not isinstance(payload, dict):
            raise ValueError("product payload must be an object")
        return payload, None, None, None, {}
    form = await request.form()
    payload = {key: value for key, value in form.items() if not hasattr(value, "read")}
    image = await _uploaded_file(form.get("image"))
    attachment_image = await _uploaded_file(form.get("attachment_image"))
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
    return payload, image, attachment_image, source_image, groups


async def _uploaded_file(value: Any) -> tuple[str, bytes] | None:
    if value is None or not hasattr(value, "read"):
        return None
    content = await value.read()
    await value.close()
    return (str(getattr(value, "filename", "upload.bin") or "upload.bin"), content) if content else None


def _attach_source_group_images(links: list[dict[str, Any]], product: dict[str, Any]) -> list[dict[str, Any]]:
    """把产品货源组里的截图（标准审核表截图1/2/3）按 source_url 匹配到每条链接。

    核价链接（price_verification_skc_source_links）只保存 1688 主图 URL，
    不保存审核表截图；这里从产品自身 source_groups 中按链接地址补齐
    group（真实组号）与 image_paths，供货源侧边栏展示对应货源图。
    """
    groups = product.get("source_groups") or []
    group_by_url: dict[str, tuple[int, list[str]]] = {}
    for group_index, group in enumerate(groups):
        url = str(group.get("source_url") or "").strip()
        if url and url not in group_by_url:
            images = [path for path in group.get("image_paths", []) if str(path)]
            group_by_url[url] = (group_index, images)
    for link in links:
        url = str(link.get("source_url") or "").strip()
        if url and url in group_by_url and not link.get("image_paths"):
            group_index, images = group_by_url[url]
            link["group"] = group_index
            link["image_paths"] = images
    return links


def _enrich_product_source_images(products: list[dict[str, Any]], database_path: Path | None, workspace_id: str) -> None:
    """让产品库图片列与“打开”货源抽屉使用同一张货源主图。

    核价关联的 1688 主图保存在 price_verification_skc_source_links 中，
    旧产品库记录未必把该地址写进 source_groups。列表返回前补齐即可，
    不改动既有产品数据，也不会覆盖人工上传的本地货源截图。
    """
    if not products or database_path is None:
        return
    product_ids = [str(item.get("skc") or "").strip() for item in products]
    product_ids = [item for item in product_ids if item]
    if not product_ids:
        return
    try:
        links = PriceVerificationRepository(database_path).list_active_skc_source_links_for_skcs(
            workspace_id=workspace_id,
            skc_ids=product_ids,
        )
    except Exception:
        # 产品库可以独立运行；核价表尚未初始化时保持原有返回，不影响查询。
        return

    links_by_skc: dict[str, list[Any]] = {}
    for link in links:
        if str(link.main_image_url or "").strip():
            links_by_skc.setdefault(str(link.skc_id), []).append(link)

    for product in products:
        product_links = links_by_skc.get(str(product.get("skc") or ""), [])
        if not product_links:
            continue
        groups = product.get("source_groups")
        if not isinstance(groups, list):
            groups = []
            product["source_groups"] = groups
        groups_by_url = {
            str(group.get("source_url") or "").strip(): group
            for group in groups
            if isinstance(group, dict) and str(group.get("source_url") or "").strip()
        }
        for link in product_links:
            source_url = str(link.source_url or "").strip()
            group = groups_by_url.get(source_url)
            if group is None:
                group = {"source_url": source_url, "image_paths": [], "cost": None}
                groups.append(group)
                if source_url:
                    groups_by_url[source_url] = group
            group.setdefault("image_paths", [])
            group["main_image_url"] = str(link.main_image_url or "").strip()
        product["source_main_image_url"] = str(product_links[0].main_image_url or "").strip()


def _product_source_group_links(product: dict[str, Any]) -> list[dict[str, Any]]:
    """按产品货源组逐组生成链接卡片（每组一条，不去重）。

    非核价入库产品的货源信息保存在 product.source_groups（source_url + image_paths），
    每组可能有相同的 source_url 但各自独立的截图。这里按组索引生成，
    保证侧边栏打开即显示全部链接，且每组图片与组号一一对应，
    避免按 URL 去重后同 URL 多组只显示第一组、修改第二组图片看不到效果。
    """
    groups = product.get("source_groups") or []
    links: list[dict[str, Any]] = []
    for group_index, group in enumerate(groups):
        url = str(group.get("source_url") or "").strip()
        if not url:
            continue
        images = [path for path in group.get("image_paths", []) if str(path)]
        links.append({
            "id": -(len(links) + 1),
            "batch_id": "",
            "skc_id": product.get("skc") or "",
            "offer_id": "",
            "source_url": url,
            "source_title": "货源链接",
            "main_image_url": "",
            "image_paths": images,
            "group": group_index,
            "price_cny": group.get("cost"),
            "moq": None,
            "domestic_freight_cny": None,
            "source_decision": "manual",
            "note": "",
            "status": "active",
        })
    return links


def _product_source_fallbacks(product: dict[str, Any], source_urls: list[str]) -> list[dict[str, Any]]:
    """核价表无该 SKC 的货源明细时，用产品自身货源组生成基础链接卡片。

    链接的“成本”来自货源组的 cost 字段（标准审核表“成本2/成本3”等列），
    图片来自货源组 image_paths 的缩略展示（前端经 /image 接口按组加载）。
    """
    groups = product.get("source_groups") or []
    group_by_url: dict[str, tuple[int, dict[str, Any]]] = {}
    for group_index, group in enumerate(groups):
        url = str(group.get("source_url") or "").strip()
        if url and url not in group_by_url:
            group_by_url[url] = (group_index, group)
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url in source_urls:
        if url in seen:
            continue
        seen.add(url)
        group_index, group = group_by_url.get(url, (0, {"source_url": url, "image_paths": [], "cost": None}))
        images = [path for path in group.get("image_paths", []) if str(path)]
        links.append({
            "id": -(len(links) + 1),
            "batch_id": "",
            "skc_id": product.get("skc") or "",
            "offer_id": "",
            "source_url": url,
            "source_title": "导入货源链接",
            "main_image_url": "",
            "image_paths": images,
            "group": group_index,
            "price_cny": group.get("cost"),
            "moq": None,
            "domestic_freight_cny": None,
            "source_decision": "manual",
            "note": "",
            "status": "active",
        })
    return links


def _settings_response(snapshot: SettingsSnapshot) -> dict[str, Any]:
    return {"revision": snapshot.revision, "settings": asdict(snapshot.settings)}


def _record_response(row) -> dict[str, Any]:
    return {key: getattr(row, key) for key in ("id", "site_code", "skc", "note", "selling_price", "cost_price", "weight_kg", "domestic_fee", "shipping_subsidy", "shipping_cost", "end_fee", "total_cost", "gross_profit", "net_profit", "profit_rate", "calculation_hash", "settings_revision", "revision", "created_at", "updated_at")}


def _run_response(run, decisions=None) -> dict[str, Any]:
    result = {key: getattr(run, key) for key in ("id", "site_code", "rule_version", "minimum_net_profit", "minimum_profit_rate", "retained_count", "excluded_count")}
    result["created_at"] = _local_iso(run.created_at)
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
