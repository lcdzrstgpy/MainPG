from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import threading
import uuid
from dataclasses import dataclass
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, get_type_hints

from .domain.engine import ProfitValidationError, activity_decision, calculate_profit, validate_settings
from .domain.models import ProfitPreview, ProfitSettings, ProfitSiteProfile, SiteCode
from .infrastructure.database import ProfitActivityDatabase, create_database
from .infrastructure.repository import ProfitActivityRepository, SettingsRevisionConflict, SettingsSnapshot
from .infrastructure.assets import ensure_writable_directory, resolve_asset, save_asset
from .domain.workbooks import (
    FilterPausedError,
    extract_product_workbook_images,
    filter_activity_workbook,
    new_workbook,
    parse_product_workbook,
    workbook_bytes,
)


class ProfitActivityConflict(ValueError):
    pass


class ProfitActivityNotFound(ValueError):
    pass


def _require_activity_thresholds(settings: ProfitSettings) -> None:
    if not settings.activity_threshold_configured:
        raise ProfitValidationError("activity_threshold_not_configured")


@dataclass(frozen=True)
class ProfitActivityActorContext:
    actor_id: str = "local-demo-admin"
    username: str = "local-demo"
    role: str = "admin"
    workspace_id: str = "default"
    workspace_code: str = "local-demo"
    workspace_name: str = "本地演示工作区"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class ProfitActivityService:
    def __init__(self, repository: ProfitActivityRepository, database: ProfitActivityDatabase | None = None) -> None:
        self._repository = repository
        self._database = database
        # 活动过滤后台任务：task_id -> 暂停事件（set=暂停），用于暂停正在运行的过滤
        self._filter_events: dict[int, threading.Event] = {}
        self._filter_events_lock = threading.Lock()

    def close(self) -> None:
        """释放数据库连接；供应用 shutdown 与测试清理调用。"""
        if self._database is not None:
            self._database.dispose()

    def get_settings(self, actor: Any | None = None) -> SettingsSnapshot:
        context = _actor_context(actor)
        return self._repository.get_settings(context.workspace_id)

    def legacy_settings(self, actor: Any | None = None) -> dict[str, Any]:
        snapshot = self.get_settings(actor)
        settings = asdict(snapshot.settings)
        settings["save_root"] = str(self._asset_root(snapshot.settings))
        settings["activity_filter_rule_version"] = settings["rule_version"]
        settings["revision"] = snapshot.revision
        settings["workspace"] = asdict(_actor_context(actor))
        return settings

    def list_sites(self, actor: Any | None = None) -> list[dict[str, Any]]:
        context = _actor_context(actor)
        builtin = [
            {"site_code": "US", "display_name": "美区", "builtin": True},
            {"site_code": "CO", "display_name": "哥伦比亚", "builtin": True},
            {"site_code": "EC", "display_name": "厄瓜多尔", "builtin": True},
        ]
        return [*builtin, *[{**asdict(profile), "builtin": False} for profile in self._repository.list_sites(context.workspace_id)]]

    def create_site(self, profile: ProfitSiteProfile, actor: Any | None = None) -> dict[str, Any]:
        if profile.site_code in {"US", "CO", "EC"}:
            raise ValueError("site_code_already_exists")
        context = _actor_context(actor)
        return {**asdict(self._repository.create_site(context.workspace_id, profile)), "builtin": False}

    def update_site(self, profile: ProfitSiteProfile, actor: Any | None = None) -> dict[str, Any]:
        context = _actor_context(actor)
        return {**asdict(self._repository.update_site(context.workspace_id, profile)), "builtin": False}

    def _resolve_site(self, value: Any, actor: Any | None = None) -> tuple[SiteCode, ProfitSiteProfile | None]:
        site = _site(value)
        if site in {"US", "CO", "EC"}:
            return site, None
        profile = self._repository.get_site(_actor_context(actor).workspace_id, site)
        if profile is None:
            raise ValueError("site_not_found")
        return site, profile

    def update_settings(self, expected_revision: int, settings: ProfitSettings, actor: Any | None = None) -> SettingsSnapshot:
        context = _actor_context(actor)
        try:
            validate_settings(settings)
            if settings.save_root:
                ensure_writable_directory(Path(settings.save_root))
            return self._repository.update_settings(expected_revision, settings, context.workspace_id)
        except SettingsRevisionConflict as exc:
            raise ProfitActivityConflict("settings_revision_conflict") from exc

    def update_legacy_settings(self, payload: dict[str, Any], actor: Any | None = None) -> dict[str, Any]:
        snapshot = self.get_settings(actor)
        values = asdict(snapshot.settings)
        for name in values:
            if name not in payload:
                continue
            values[name] = payload[name]
        threshold_fields = {"activity_min_net_profit", "activity_profit_rate_threshold"}
        if "activity_threshold_configured" not in payload and threshold_fields.intersection(payload):
            values["activity_threshold_configured"] = True
        if "activity_filter_rule_version" in payload:
            values["rule_version"] = payload["activity_filter_rule_version"]
        settings = ProfitSettings(**_decimal_settings(values))
        if settings.save_root:
            ensure_writable_directory(Path(settings.save_root))
        self.update_settings(int(payload.get("expected_revision", snapshot.revision)), settings, actor)
        return self.legacy_settings(actor)

    def calculate(self, site_code: SiteCode, selling_price: Decimal, cost_price: Decimal, weight_kg: Decimal, actor: Any | None = None) -> dict[str, Any]:
        snapshot = self.get_settings(actor)
        site_code, custom_site = self._resolve_site(site_code, actor)
        preview = calculate_profit(site_code=site_code, selling_price=selling_price, cost_price=cost_price, weight_kg=weight_kg, settings=snapshot.settings, custom_site=custom_site)
        return {
            "preview": preview,
            "settings_revision": snapshot.revision,
            "calculation_hash": _calculation_hash(preview, snapshot.revision),
            "archive_allowed": preview.net_profit >= 0,
            "confirmation_required": "negative_profit" if preview.net_profit < 0 else None,
        }

    def calculate_legacy(self, payload: dict[str, Any], actor: Any | None = None) -> dict[str, Any]:
        site = _site(payload.get("site", payload.get("site_code", "US")))
        result = self.calculate(site, _decimal(payload.get("selling_price")), _decimal(payload.get("cost_price")), _decimal(payload.get("weight_kg")), actor)
        calculation = asdict(result["preview"])
        calculation["site"] = calculation.pop("site_code")
        return {"calculation": calculation, "settings": self.legacy_settings(actor)}

    def archive(self, *, site_code: SiteCode, skc: str, note: str, selling_price: Decimal, cost_price: Decimal, weight_kg: Decimal, calculation_hash: str, settings_revision: int, confirm_negative_profit: bool, actor: Any | None = None):
        context = _actor_context(actor)
        current = self.calculate(site_code, selling_price, cost_price, weight_kg, actor)
        if current["settings_revision"] != settings_revision:
            raise ProfitActivityConflict("settings_revision_conflict")
        if not hmac.compare_digest(current["calculation_hash"], calculation_hash):
            raise ProfitActivityConflict("profit_calculation_changed")
        preview: ProfitPreview = current["preview"]
        if preview.net_profit < 0 and not confirm_negative_profit:
            raise ProfitActivityConflict("negative_profit_confirmation_required")
        return self._repository.upsert_record(workspace_id=context.workspace_id, created_by=context.actor_id, created_by_username=context.username, skc=skc, note=note, preview=preview, calculation_hash=calculation_hash, settings_revision=settings_revision)

    def list_records(self, site_code: SiteCode | None, offset: int, limit: int, actor: Any | None = None, *, include_workspace_shared: bool = False, source_type: str | None = None):
        context = _actor_context(actor)
        return self._repository.list_records(context.workspace_id, site_code, offset, limit, actor_id=context.actor_id, include_workspace_shared=include_workspace_shared or context.is_admin, source_type=source_type)

    def list_products(self, *, site: SiteCode | None = None, skcs: list[str] | None = None, source_type: str | None = None, actor: Any | None = None, include_workspace_shared: bool = False) -> list[dict[str, Any]]:
        context = _actor_context(actor)
        if site is not None:
            site, _ = self._resolve_site(site, actor)
        records = self.list_records(site, 0, 10_000, actor, include_workspace_shared=include_workspace_shared, source_type=source_type)
        requested = {item.strip() for item in (skcs or []) if item.strip()}
        if requested:
            records = [record for record in records if record.skc in requested]
        return [_product_payload(record, context) for record in records]

    def upsert_product(self, payload: dict[str, Any], *, actor: Any | None = None, allow_company_write: bool = False, require_complete_profile: bool = False, image: tuple[str, bytes] | None = None, attachment_image: tuple[str, bytes] | None = None, source_image: tuple[str, bytes] | None = None, source_group_images: dict[int, list[tuple[str, bytes]]] | None = None) -> dict[str, Any]:
        context = _actor_context(actor)
        site, custom_site = self._resolve_site(payload.get("site", payload.get("site_code", "US")), actor)
        # Keep the storage column named skc for backward compatibility, while
        # accepting SKU / SKC / SPU / product_id as the business identifier.
        skc = _product_id_from_payload(payload)
        if not skc:
            raise ValueError("product_id is required")
        settings = self.get_settings(actor)
        current = self._repository.find_product(skc, site, context.workspace_id)
        if current is not None and current.created_by != context.actor_id and not (allow_company_write or context.is_admin):
            raise ProfitActivityConflict("profit_activity_company_write_required")
        # 数值字段缺失时回退当前记录，保证“仅上传/替换图片”这类局部更新也能保存。
        preview = calculate_profit(
            site_code=site,
            selling_price=_decimal(payload.get("selling_price", current.selling_price if current else None)),
            cost_price=_decimal(payload.get("cost_price", current.cost_price if current else None)),
            weight_kg=_decimal(payload.get("weight_kg", current.weight_kg if current else None)),
            settings=settings.settings,
            custom_site=custom_site,
        )
        root = self._asset_root(settings.settings)
        image_path = current.image_path if current else ""
        attachment_image_path = "" if str(payload.get("clear_attachment_image") or "").lower() in {"1", "true", "yes"} else (current.attachment_image_path if current else "")
        source_type = str(payload.get("source_type") or (current.source_type if current else "manual"))
        source_main_image_url = (
            str(payload.get("source_main_image_url") or (current.source_main_image_url if current else "")).strip()
            if source_type == "price_verification" else ""
        )
        source_groups = _source_groups(payload.get("source_groups_json"), current.source_groups_json if current else "[]")
        payload_source_url = str(payload.get("source_url") or "").strip()
        if payload_source_url and not any(str(group.get("source_url") or "").strip() for group in source_groups):
            # 兼容旧字段：Excel 导入/旧表单只传单个 source_url，把它并入货源组，
            # 保证产品库货源列/侧边栏能展示该链接。
            if not source_groups:
                source_groups.append({"source_url": "", "image_paths": []})
            source_groups[0]["source_url"] = payload_source_url
        if image:
            image_path = save_asset(root, site=site, skc=skc, kind="product", filename=image[0], content=image[1])
        if attachment_image:
            attachment_image_path = save_asset(root, site=site, skc=skc, kind="attachment", filename=attachment_image[0], content=attachment_image[1])
        if source_image:
            _ensure_group(source_groups, 0)["image_paths"].append(save_asset(root, site=site, skc=skc, kind="source", filename=source_image[0], content=source_image[1]))
        for group_index, files in (source_group_images or {}).items():
            group = _ensure_group(source_groups, group_index)
            for filename, content in files:
                group["image_paths"].append(save_asset(root, site=site, skc=skc, kind="source", filename=filename, content=content))
        source_url = str(payload.get("source_url") or "").strip() or next((str(group.get("source_url") or "") for group in source_groups if group.get("source_url")), "")
        source_image_path = next((path for group in source_groups for path in group.get("image_paths", []) if path), current.source_image_path if current else "")
        note = str(payload.get("note") or "").strip()[:500]
        if require_complete_profile:
            missing = []
            if not image_path:
                missing.append("product_image_required")
            if not source_url:
                missing.append("source_url_required")
            if not source_image_path:
                missing.append("source_image_required")
            if missing:
                raise ValueError(",".join(missing))
        record = self._repository.upsert_record(
            workspace_id=context.workspace_id, created_by=context.actor_id, created_by_username=context.username,
            skc=skc, note=note, preview=preview,
            calculation_hash=_calculation_hash(preview, settings.revision), settings_revision=settings.revision,
            refund_rate=custom_site.refund_rate if custom_site else settings.settings.ec_refund_rate if site == "EC" else settings.settings.refund_rate,
            visibility=str(payload.get("visibility") or (current.visibility if current else "shared")),
            source_type=source_type,
            source_url=source_url,
            image_path=image_path, attachment_image_path=attachment_image_path, source_main_image_url=source_main_image_url,
            source_image_path=source_image_path, source_groups=source_groups,
        )
        return _product_payload(record, context)

    def update_product_values(self, skc: str, payload: dict[str, Any], actor: Any | None = None, *, allow_company_write: bool = False) -> dict[str, Any]:
        context = _actor_context(actor)
        site = _site(payload.get("site", "US"))
        current = self._repository.find_product(skc, site, context.workspace_id)
        if current is None:
            raise ProfitActivityNotFound("product_not_found")
        merged = {
            "site": site, "skc": skc, "selling_price": payload.get("selling_price", current.selling_price),
            "cost_price": payload.get("cost_price", current.cost_price), "weight_kg": payload.get("weight_kg", current.weight_kg),
            "note": payload.get("note", current.note), "visibility": payload.get("visibility", current.visibility),
            "source_url": payload.get("source_url", current.source_url), "source_groups_json": payload.get("source_groups_json", current.source_groups_json),
            "source_type": current.source_type, "source_main_image_url": current.source_main_image_url,
        }
        return self.upsert_product(merged, actor=actor, allow_company_write=allow_company_write, require_complete_profile=False)

    def delete_product(self, skc: str, site: SiteCode, actor: Any | None = None, *, allow_company_delete: bool = False) -> dict[str, Any]:
        context = _actor_context(actor)
        site, _ = self._resolve_site(site, actor)
        # 核价及货源自动入库的产品属于公司级产品库（跨工作区可见），删除时同样按该语义定位。
        current = self._repository.find_product(skc, site, context.workspace_id, include_price_verification=True)
        if current is not None and current.created_by != context.actor_id and not (allow_company_delete or context.is_admin):
            raise ProfitActivityConflict("profit_activity_company_delete_required")
        return {"status": "deleted" if self._repository.delete_product(skc, site, context.workspace_id, include_price_verification=True) else "not_found", "skc": skc, "site": site}

    def preview_import(self, workbook: bytes, original_filename: str, site: SiteCode, actor: Any | None = None) -> dict[str, Any]:
        context = _actor_context(actor)
        site, _ = self._resolve_site(site, actor)
        rows = parse_product_workbook(workbook, site, self._repository.product_keys(context.workspace_id))
        import_id = uuid.uuid4().hex
        image_rows = extract_product_workbook_images(workbook)
        root = self._asset_root(self.get_settings(actor).settings)
        for row in rows:
            extracted = image_rows.get(row["row_id"], {})
            product_images = extracted.get("product", [])
            source_images = extracted.get("source", [])
            row_site = _site(row.get("site") or site)
            if product_images:
                filename, content = product_images[0]
                row["product_image_path"] = save_asset(root, site=row_site, skc=f"import_{import_id}_{row['row_id']}", kind="preview_product", filename=filename, content=content)
                row["has_product_image"] = True
            if source_images:
                filename, content = source_images[0]
                row["source_image_path"] = save_asset(root, site=row_site, skc=f"import_{import_id}_{row['row_id']}", kind="preview_source", filename=filename, content=content)
                row["has_source_image"] = True
            group_paths: dict[str, list[str]] = {}
            for key, files in extracted.items():
                if not key.startswith("source_"):
                    continue
                group_index = key.split("_", 1)[1]
                saved = [
                    save_asset(root, site=row_site, skc=f"import_{import_id}_{row['row_id']}", kind=f"preview_source_{group_index}", filename=filename, content=content)
                    for filename, content in files
                ]
                if saved:
                    group_paths[group_index] = saved
            if group_paths:
                row["source_group_images"] = group_paths
        session_site = _site(rows[0].get("site")) if rows else _site(site)
        self._repository.save_import_session(context.workspace_id, import_id, original_filename, session_site, rows)
        summary = _import_summary(rows)
        site_counts: dict[str, int] = {}
        for row in rows:
            row_site = _site(row.get("site") or site)
            site_counts[row_site] = site_counts.get(row_site, 0) + 1
        summary["sites"] = site_counts
        return {"import_id": import_id, "original_filename": original_filename, "site": session_site, "summary": summary, "rows": rows}

    def latest_import_session(self, actor: Any | None = None) -> dict[str, Any] | None:
        context = _actor_context(actor)
        session = self._repository.latest_import_session(context.workspace_id)
        if session is None:
            return None
        return self._import_session_payload(session)

    def list_import_sessions(self, actor: Any | None = None, *, limit: int = 3) -> list[dict[str, Any]]:
        context = _actor_context(actor)
        return [self._import_session_payload(session) for session in self._repository.list_import_sessions(context.workspace_id, limit)]

    def _import_session_payload(self, session) -> dict[str, Any]:
        rows = json.loads(session.rows_json)
        return {
            "import_id": session.import_id,
            "original_filename": session.original_filename,
            "site": session.site,
            "created_at": _local_iso(session.created_at) if session.created_at else None,
            "summary": _import_summary(rows),
            "rows": rows,
        }

    def confirm_import(self, import_id: str, selected_row_ids: list[str] | None, on_conflict: str, actor: Any | None = None) -> dict[str, Any]:
        context = _actor_context(actor)
        session = self._repository.get_import_session(import_id, context.workspace_id)
        if session is None:
            raise ProfitActivityNotFound("import_not_found")
        if on_conflict not in {"skip", "replace"}:
            raise ValueError("on_conflict must be skip or replace")
        rows = json.loads(session.rows_json)
        selected = set(selected_row_ids) if selected_row_ids is not None else {row["row_id"] for row in rows if row["status"] == "ready"}
        products: list[dict[str, Any]] = []
        imported = skipped = replaced = 0
        for row in rows:
            if row["row_id"] not in selected or row["status"] != "ready":
                continue
            row_site = _site(row.get("site") or session.site)
            exists = self._repository.find_product(row["skc"], row_site, context.workspace_id) is not None
            if exists and on_conflict == "skip":
                skipped += 1
                continue
            image = _asset_tuple(row.get("product_image_path"))
            source_image = _asset_tuple(row.get("source_image_path"))
            source_groups = row.get("source_groups") or []
            payload = {**row, "site": row_site, "visibility": "shared"}
            if source_groups:
                payload["source_groups_json"] = json.dumps(source_groups)
            group_images: dict[int, list[tuple[str, bytes]]] = {}
            for group_index, paths in (row.get("source_group_images") or {}).items():
                files = [file for file in (_asset_tuple(path) for path in paths) if file]
                if files:
                    group_images[int(group_index)] = files
            product = self.upsert_product(
                payload,
                image=image,
                actor=actor,
                allow_company_write=True,
                source_image=source_image,
                source_group_images=group_images or None,
            )
            products.append(product)
            if exists:
                replaced += 1
            else:
                imported += 1
        result = {"imported": imported, "skipped": skipped, "replaced": replaced, "products": products, "summary": _import_summary(rows)}
        task = self._repository.create_import_task(context.workspace_id, import_id, result)
        return {"task_id": task.id, "status": "completed", "task_status": "completed", "task": {"id": task.id, "status": "completed", "updated_at": task.updated_at}, **result}

    def get_import_task(self, task_id: int, actor: Any | None = None) -> dict[str, Any]:
        context = _actor_context(actor)
        task = self._repository.get_import_task(task_id, context.workspace_id)
        if task is None:
            raise ProfitActivityNotFound("import_task_not_found")
        return {"task": {"id": task.id, "status": task.status, "updated_at": task.updated_at}, "result": json.loads(task.result_json), "blockers": []}

    def create_catalog(self, sites: list[SiteCode], actor: Any | None = None, *, include_workspace_shared: bool = False) -> Path:
        workbook = new_workbook()
        sheet = workbook.active
        sheet.title = "products"
        sheet.append(["site", "SKC", "售价", "成本", "重量KG", "备注", "货源", "利润", "利润率"])
        for site in sites:
            site, _ = self._resolve_site(site, actor)
            for product in self.list_products(site=site, actor=actor, include_workspace_shared=include_workspace_shared):
                sheet.append([product["site"], product["skc"], product["selling_price"], product["cost_price"], product["weight_kg"], product["note"], product["source_url"], product["net_profit"], product["profit_rate"]])
        root = self._asset_root(self.get_settings(actor).settings)
        path = root / "product_catalog.xlsx"
        path.write_bytes(workbook_bytes(workbook))
        return path

    def filter_activity_template(self, workbook: bytes, original_filename: str, site: SiteCode, actor: Any | None = None, *, include_workspace_shared: bool = False, should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
        context = _actor_context(actor)
        settings = self.get_settings(actor).settings
        _require_activity_thresholds(settings)
        site, custom_site = self._resolve_site(site, actor)
        products = {product["skc"]: product for product in self.list_products(site=site, actor=actor, include_workspace_shared=include_workspace_shared)}
        def evaluate(candidate_ids: list[str], price: Decimal) -> dict[str, Any]:
            for candidate_id in candidate_ids:
                product = products.get(candidate_id)
                if product is None:
                    continue
                preview = calculate_profit(
                    site_code=site,
                    selling_price=price,
                    cost_price=Decimal(str(product["cost_price"])),
                    weight_kg=Decimal(str(product["weight_kg"])),
                    settings=settings,
                    custom_site=custom_site,
                )
                decision, reason = activity_decision(preview, settings)
                return {
                    "keep": decision == "eligible", "decision": decision, "reason_code": reason,
                    "net_profit": float(preview.net_profit), "profit_rate": float(preview.profit_rate),
                    "net_profit_passed": preview.net_profit >= settings.activity_min_net_profit,
                    "profit_rate_passed": preview.profit_rate >= settings.activity_profit_rate_threshold,
                    "matched_id": candidate_id,
                }
            return {"keep": False, "decision": "excluded", "reason_code": "missing_product", "net_profit": None, "profit_rate": None}

        filtered = filter_activity_workbook(workbook, site=site, evaluate=evaluate, should_stop=should_stop)
        root = self._asset_root(settings) / "activity_outputs"
        root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:12]
        filtered_path = root / f"eligible_{token}.xlsx"
        removed_path = root / f"excluded_{token}.xlsx"
        filtered_path.write_bytes(filtered.pop("filtered_bytes"))
        removed_path.write_bytes(filtered.pop("removed_bytes"))
        return self._trim_filter_result({
            "site": site, "requested_site": site, "site_auto_switched": False,
            "original_filename": original_filename, "filtered_path": str(filtered_path), "removed_path": str(removed_path),
            "threshold": float(settings.activity_min_net_profit), "min_net_profit_threshold": float(settings.activity_min_net_profit),
            "profit_rate_threshold": float(settings.activity_profit_rate_threshold), "activity_profit_rate_threshold": float(settings.activity_profit_rate_threshold),
            "activity_filter_rule_version": settings.rule_version,
            **filtered,
        })

    def start_activity_filter(self, workbook: bytes, original_filename: str, site: SiteCode, actor: Any | None = None, *, include_workspace_shared: bool = False) -> int:
        """异步启动活动过滤：立即返回任务编号，过滤在后台线程执行，支持暂停。"""
        _require_activity_thresholds(self.get_settings(actor).settings)
        context = _actor_context(actor)
        task = self._repository.create_filter_task(
            context.workspace_id, "running",
            {"original_filename": original_filename, "site": site, "started_at": _local_iso(datetime.now(timezone.utc))},
        )
        event = threading.Event()
        with self._filter_events_lock:
            self._filter_events[task.id] = event

        def run() -> None:
            try:
                result = self.filter_activity_template(
                    workbook, original_filename, site, actor,
                    include_workspace_shared=include_workspace_shared,
                    should_stop=event.is_set,
                )
                self._repository.update_filter_task(task.id, context.workspace_id, "completed", result)
            except FilterPausedError:
                self._repository.update_filter_task(
                    task.id, context.workspace_id, "paused",
                    {"original_filename": original_filename, "site": site, "paused_at": _local_iso(datetime.now(timezone.utc))},
                )
            except Exception as exc:  # noqa: BLE001 - 后台任务统一落库，避免线程静默失败
                self._repository.update_filter_task(
                    task.id, context.workspace_id, "failed",
                    {"original_filename": original_filename, "site": site, "error": str(exc)},
                )
            finally:
                with self._filter_events_lock:
                    self._filter_events.pop(task.id, None)

        threading.Thread(target=run, daemon=True, name=f"activity-filter-{task.id}").start()
        return task.id

    def pause_activity_filter(self, task_id: int, actor: Any | None = None) -> dict[str, Any]:
        """暂停正在运行的活动过滤任务，返回任务当前状态。"""
        context = _actor_context(actor)
        with self._filter_events_lock:
            event = self._filter_events.get(task_id)
            if event is not None:
                event.set()
        task = self._repository.get_filter_task(task_id, context.workspace_id)
        if task is None:
            raise ProfitActivityNotFound("filter_task_not_found")
        return {"task_id": task.id, "filter_task_id": task.id, "operation_task_id": task.id, "status": task.status, "created_at": _local_iso(task.created_at) if task.created_at else None, **json.loads(task.result_json)}

    def list_filter_tasks(self, actor: Any | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
        """返回当前工作区的历史活动过滤任务，最新在前；剥离大字段仅保留统计与状态。"""
        context = _actor_context(actor)
        tasks = self._repository.list_filter_tasks(context.workspace_id, limit)
        payloads: list[dict[str, Any]] = []
        for task in tasks:
            result = json.loads(task.result_json)
            # 历史列表不需要剔除明细/逐条判定等大字段，避免一次返回超大 JSON
            result.pop("removed_rows", None)
            result.pop("activity_decisions", None)
            payloads.append({
                "task_id": task.id, "filter_task_id": task.id, "operation_task_id": task.id,
                "status": task.status, "created_at": _local_iso(task.created_at) if task.created_at else None,
                **result,
            })
        return payloads

    @staticmethod
    def _trim_filter_result(result: dict[str, Any]) -> dict[str, Any]:
        """裁剪过滤结果中的大字段，避免超大 JSON 拖垮轮询/历史/前端。

        剔除明细与逐条判定在历史任务里不需要全量返回（前端只用统计和文件路径），
        落库与查询时只保留前 100 条用于展示。
        """
        for key in ("removed_rows", "activity_decisions"):
            value = result.get(key)
            if isinstance(value, list):
                result[key] = value[:100]
        return result

    def get_filter_task_legacy(self, task_id: int, actor: Any | None = None) -> dict[str, Any]:
        context = _actor_context(actor)
        task = self._repository.get_filter_task(task_id, context.workspace_id)
        if task is None:
            raise ProfitActivityNotFound("filter_task_not_found")
        return {"task_id": task.id, "filter_task_id": task.id, "operation_task_id": task.id, "status": task.status, "created_at": _local_iso(task.created_at), **self._trim_filter_result(json.loads(task.result_json))}

    def eligible_activity_products(self, task_id: int, actor: Any | None = None) -> list[dict[str, Any]]:
        """返回活动过滤任务里「可申报」商品的标识与最低申报价格列表。

        供申报价计算器使用：每个可申报商品按 product_id 归组，返回该商品出现的
        全部标识（SKC/SKU/SPU 等 candidate_ids）以及其中最低的申报价格。
        """
        context = _actor_context(actor)
        task = self._repository.get_filter_task(task_id, context.workspace_id)
        if task is None:
            raise ProfitActivityNotFound("filter_task_not_found")
        decisions = (json.loads(task.result_json) or {}).get("activity_decisions") or []
        grouped: dict[str, dict[str, Any]] = {}
        for item in decisions:
            if not isinstance(item, dict):
                continue
            if item.get("decision") != "eligible" and not item.get("keep"):
                continue
            product_id = str(item.get("product_id") or item.get("skc") or "").strip()
            price = item.get("price")
            if not product_id or price is None:
                continue
            try:
                price_value = float(price)
            except (TypeError, ValueError):
                continue
            entry = grouped.setdefault(product_id, {"identifiers": {product_id}, "price": price_value})
            candidate_ids = item.get("candidate_ids")
            if isinstance(candidate_ids, list):
                for candidate in candidate_ids:
                    candidate_id = str(candidate or "").strip()
                    if candidate_id:
                        entry["identifiers"].add(candidate_id)
            entry["price"] = min(entry["price"], price_value)
        return [
            {
                "product_id": product_id,
                "identifiers": sorted(entry["identifiers"]),
                "price": entry["price"],
            }
            for product_id, entry in grouped.items()
        ]

    def output_path(self, task_id: int, kind: str, actor: Any | None = None) -> Path:
        result = self.get_filter_task_legacy(task_id, actor)
        if kind not in {"filtered", "removed"}:
            raise ValueError("kind must be filtered or removed")
        return Path(result[f"{kind}_path"])

    def save_filter_output(self, task_id: int, kind: str, actor: Any | None = None) -> Path:
        """把已生成的可申报/剔除文件复制到本地保存目录（save_root），方便用户直接在该目录取用。"""
        import shutil

        result = self.get_filter_task_legacy(task_id, actor)
        if kind not in {"filtered", "removed"}:
            raise ValueError("kind must be filtered or removed")
        source = Path(result[f"{kind}_path"])
        if not source.exists():
            raise ProfitActivityNotFound("filter_output_missing")
        names = {"filtered": "可申报产品", "removed": "剔除产品"}
        root = self._asset_root(self.get_settings(actor).settings)
        target = root / f"{names[kind]}_{task_id}.xlsx"
        shutil.copyfile(source, target)
        return target

    def import_image_path(self, import_id: str, row_id: str, kind: str, actor: Any | None = None) -> Path:
        context = _actor_context(actor)
        session = self._repository.get_import_session(import_id, context.workspace_id)
        if session is None:
            raise ProfitActivityNotFound("import_not_found")
        rows = json.loads(session.rows_json)
        row = next((item for item in rows if item.get("row_id") == row_id), None)
        if row is None:
            raise ProfitActivityNotFound("import_row_not_found")
        field = "product_image_path" if kind == "product" else "source_image_path"
        return resolve_asset(str(row.get(field) or ""))

    def image_path(self, skc: str, site: SiteCode, kind: str, group: int = 0, index: int = 0, actor: Any | None = None) -> Path:
        context = _actor_context(actor)
        product = self._repository.find_product(skc, site, context.workspace_id)
        if product is None:
            raise ProfitActivityNotFound("product_not_found")
        if kind == "product":
            return resolve_asset(product.image_path)
        if kind == "attachment":
            return resolve_asset(product.attachment_image_path)
        groups = _source_groups(product.source_groups_json, "[]")
        paths = groups[group].get("image_paths", []) if group < len(groups) else []
        return resolve_asset(paths[index] if index < len(paths) else product.source_image_path)

    def _asset_root(self, settings: ProfitSettings) -> Path:
        return ensure_writable_directory(Path(settings.save_root) if settings.save_root else self._output_root())

    def _output_root(self) -> Path:
        # 打包场景：安装目录可能只读（如 Program Files），回退到 %APPDATA%\MainPG 可写目录
        if getattr(sys, "frozen", False):
            appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
            root = appdata / "MainPG" / "outputs" / "profit_activity"
        else:
            root = Path(os.getenv("PROFIT_ACTIVITY_OUTPUT_DIR") or Path(__file__).resolve().parents[4] / "real-workbench" / "employee_workbench" / "outputs" / "profit_activity")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _write_filter_outputs(self, kept: list[list[Any]], removed: list[list[Any]]) -> tuple[Path, Path]:
        root = self._output_root()
        token = uuid.uuid4().hex[:12]
        paths = (root / f"eligible_{token}.xlsx", root / f"excluded_{token}.xlsx")
        for path, rows in zip(paths, (kept, removed)):
            workbook = new_workbook()
            sheet = workbook.active
            sheet.title = "activity"
            sheet.append(["SKC", "decision", "reason_code", "net_profit", "profit_rate"])
            for row in rows:
                sheet.append(row)
            path.write_bytes(workbook_bytes(workbook))
        return paths

    def run_filter(self, site_code: SiteCode | None, record_ids: list[int] | None, actor: Any | None = None, *, include_workspace_shared: bool = False):
        context = _actor_context(actor)
        settings = self.get_settings(actor).settings
        _require_activity_thresholds(settings)
        records = self._repository.get_records_for_filter(context.workspace_id, site_code, record_ids, actor_id=context.actor_id, include_workspace_shared=include_workspace_shared or context.is_admin)
        decisions = []
        for record in records:
            preview = ProfitPreview(
                site_code=record.site_code, selling_price=record.selling_price, cost_price=record.cost_price, weight_kg=record.weight_kg,
                domestic_fee=record.domestic_fee, shipping_subsidy=record.shipping_subsidy, shipping_cost=record.shipping_cost,
                end_fee=record.end_fee, total_cost=record.total_cost, gross_profit=record.gross_profit,
                net_profit=record.net_profit, profit_rate=record.profit_rate,
            )
            decision, reason = activity_decision(preview, settings)
            decisions.append((record, decision, reason))
        run = self._repository.create_activity_run(
            context.workspace_id, site_code, settings,
            [(record.id, decision, reason) for record, decision, reason in decisions],
        )
        return run, [
            {
                "record_id": record.id,
                "skc": record.skc,
                "site": record.site_code,
                "decision": decision,
                "reason_code": reason,
            }
            for record, decision, reason in decisions
        ]

    def get_filter_run(self, run_id: int, actor: Any | None = None):
        context = _actor_context(actor)
        result = self._repository.get_activity_run(run_id, context.workspace_id)
        if result is None:
            raise ProfitActivityNotFound("activity_run_not_found")
        return result

    def export_filter_run(self, run_id: int, kind: str, actor: Any | None = None) -> Path:
        """按“产品过滤”批次导出可申报/剔除产品 Excel 报告，与页面统计口径一致。"""
        context = _actor_context(actor)
        result = self._repository.get_activity_run(run_id, context.workspace_id)
        if result is None:
            raise ProfitActivityNotFound("activity_run_not_found")
        run, decisions = result
        if kind not in {"eligible", "excluded"}:
            raise ValueError("kind must be eligible or excluded")
        decision_kind = "eligible" if kind == "eligible" else "excluded"
        record_ids = [item.record_id for item in decisions if item.decision == decision_kind]
        records = self._repository.get_records_for_filter(context.workspace_id, run.site_code, record_ids, actor_id=context.actor_id, include_workspace_shared=True)
        by_id = {record.id: record for record in records}
        workbook = new_workbook()
        sheet = workbook.active
        sheet.title = "products"
        sheet.append(["站点", "SKC", "售价", "成本", "重量KG", "净利润", "利润率", "判定", "原因"])
        for item in decisions:
            if item.decision != decision_kind:
                continue
            record = by_id.get(item.record_id)
            if record is None:
                continue
            sheet.append([
                record.site_code, record.skc, float(record.selling_price), float(record.cost_price),
                float(record.weight_kg), float(record.net_profit), float(record.profit_rate),
                "可申报" if decision_kind == "eligible" else "剔除", item.reason_code,
            ])
        root = self._asset_root(self.get_settings(actor).settings) / "activity_outputs"
        root.mkdir(parents=True, exist_ok=True)
        names = {"eligible": "可申报产品", "excluded": "剔除产品"}
        path = root / f"{names[kind]}_{run_id}.xlsx"
        path.write_bytes(workbook_bytes(workbook))
        return path


def create_profit_activity_service(database_url: str | Path | None = None) -> ProfitActivityService:
    database = create_database(database_url)
    return ProfitActivityService(ProfitActivityRepository(database.sessions), database)


def _calculation_hash(preview: ProfitPreview, settings_revision: int) -> str:
    payload = {"preview": {key: str(value) for key, value in asdict(preview).items()}, "settings_revision": settings_revision}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _site(value: Any) -> SiteCode:
    site = str(value or "US").upper()
    if not re.fullmatch(r"[A-Z0-9_]{2,12}", site):
        raise ValueError("site_code_invalid")
    return site


def _product_id_from_payload(payload: dict[str, Any]) -> str:
    for key in ("product_id", "sku", "skc", "skc_id", "spu", "spu_id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError("numeric value is required") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("numeric value must be positive")
    return result


_SETTINGS_FIELD_TYPES = get_type_hints(ProfitSettings)


def _decimal_settings(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (
            _decimal_setting(value)
            if _SETTINGS_FIELD_TYPES.get(key) is Decimal
            else int(value)
            if _SETTINGS_FIELD_TYPES.get(key) is int
            else value is True
            if _SETTINGS_FIELD_TYPES.get(key) is bool
            else str(value)
        )
        for key, value in values.items()
    }


def _decimal_setting(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ValueError("settings value must be numeric") from exc


def _source_groups(value: Any, fallback: str) -> list[dict[str, Any]]:
    raw = value if isinstance(value, list) else None
    if raw is None:
        try:
            raw = json.loads(str(value or fallback))
        except (TypeError, ValueError):
            raw = []
    result = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            cost = item.get("cost")
            result.append({
                "source_url": str(item.get("source_url") or ""),
                "image_paths": [str(path) for path in item.get("image_paths", []) if str(path)],
                "cost": float(cost) if cost is not None else None,
                "source_title": str(item.get("source_title") or ""),
                "main_image_url": str(item.get("main_image_url") or ""),
                "offer_id": str(item.get("offer_id") or ""),
                "price_cny": item.get("price_cny"),
                "moq": item.get("moq"),
                "domestic_freight_cny": item.get("domestic_freight_cny"),
                "source_decision": str(item.get("source_decision") or ""),
                "note": str(item.get("note") or ""),
                "profit": item.get("profit") if isinstance(item.get("profit"), dict) else None,
            })
    return result


def _ensure_group(groups: list[dict[str, Any]], index: int) -> dict[str, Any]:
    while len(groups) <= index:
        groups.append({"source_url": "", "image_paths": [], "cost": None})
    return groups[index]


def _actor_context(actor: Any | None = None) -> ProfitActivityActorContext:
    if actor is None:
        return ProfitActivityActorContext()
    return ProfitActivityActorContext(
        actor_id=str(getattr(actor, "id", getattr(actor, "actor_id", "")) or "local-demo-admin"),
        username=str(getattr(actor, "username", "") or "local-demo"),
        role=str(getattr(actor, "role", "") or "operator"),
        workspace_id=str(getattr(actor, "workspace_id", "") or "default"),
        workspace_code=str(getattr(actor, "workspace_code", "") or getattr(actor, "workspace_id", "") or "default"),
        workspace_name=str(getattr(actor, "workspace_name", "") or "本地演示工作区"),
    )


def _product_payload(record, actor: ProfitActivityActorContext) -> dict[str, Any]:
    groups = _source_groups(record.source_groups_json, "[]")
    is_owner = record.created_by == actor.actor_id
    return {
        "id": record.id, "site": record.site_code, "site_code": record.site_code,
        "skc": record.skc, "product_id": record.skc, "product_id_label": "商品ID",
        "visibility": record.visibility, "source_type": record.source_type,
        "created_by": record.created_by, "created_by_username": record.created_by_username,
        "workspace_id": record.workspace_id, "workspace_code": actor.workspace_code, "workspace_name": actor.workspace_name,
        "is_owner": is_owner, "can_edit": is_owner or actor.is_admin,
        "image_path": record.image_path, "attachment_image_path": record.attachment_image_path,
        "source_main_image_url": record.source_main_image_url,
        "source_image_path": record.source_image_path, "source_groups": groups,
        "selling_price": float(record.selling_price), "cost_price": float(record.cost_price), "weight_kg": float(record.weight_kg),
        "source_url": record.source_url, "note": record.note, "domestic_fee": float(record.domestic_fee),
        "shipping_subsidy": float(record.shipping_subsidy), "refund_rate": float(record.refund_rate) if hasattr(record, "refund_rate") else 0.0,
        "shipping_cost": float(record.shipping_cost), "end_fee": float(record.end_fee), "total_cost": float(record.total_cost),
        "gross_profit": float(record.gross_profit), "net_profit": float(record.net_profit), "profit_rate": float(record.profit_rate),
        "library_created_at": record.created_at.isoformat() if record.created_at else "",
        "created_at": record.created_at.isoformat() if record.created_at else "", "updated_at": record.updated_at.isoformat() if record.updated_at else "",
    }


def _preview_from_product(product: dict[str, Any]) -> ProfitPreview:
    return ProfitPreview(site_code=_site(product["site"]), selling_price=Decimal(str(product["selling_price"])), cost_price=Decimal(str(product["cost_price"])), weight_kg=Decimal(str(product["weight_kg"])), domestic_fee=Decimal(str(product["domestic_fee"])), shipping_subsidy=Decimal(str(product["shipping_subsidy"])), shipping_cost=Decimal(str(product["shipping_cost"])), end_fee=Decimal(str(product["end_fee"])), total_cost=Decimal(str(product["total_cost"])), gross_profit=Decimal(str(product["gross_profit"])), net_profit=Decimal(str(product["net_profit"])), profit_rate=Decimal(str(product["profit_rate"])))


def _import_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"total_rows": len(rows), "importable_rows": sum(row["status"] == "ready" for row in rows), "warning_rows": sum(bool(row["warnings"]) for row in rows), "blocked_rows": sum(row["status"] != "ready" for row in rows), "duplicate_rows": sum(bool(row["is_duplicate"]) for row in rows), "default_selected_rows": sum(row["status"] == "ready" and not row["is_duplicate"] for row in rows)}


def _local_iso(value: Any) -> str:
    """SQLite 存储的是 UTC 时间且无时区信息，这里按 UTC 解析后转成服务器本地时区输出。"""
    try:
        return value.replace(tzinfo=timezone.utc).astimezone().isoformat(timespec="minutes")
    except (TypeError, ValueError, AttributeError):
        return str(value)


def _asset_tuple(path_value: Any) -> tuple[str, bytes] | None:
    """Convert a persisted preview image into the normal upload input shape."""
    if not path_value:
        return None
    try:
        path = resolve_asset(str(path_value))
    except ValueError:
        return None
    return path.name, path.read_bytes()
