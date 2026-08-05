from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from .domain.engine import activity_decision, calculate_profit, validate_settings
from .domain.models import ProfitPreview, ProfitSettings, SiteCode
from .infrastructure.database import ProfitActivityDatabase, create_database
from .infrastructure.repository import ProfitActivityRepository, SettingsRevisionConflict, SettingsSnapshot
from .infrastructure.assets import ensure_writable_directory, resolve_asset, save_asset
from .domain.workbooks import (
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
        if "activity_filter_rule_version" in payload:
            values["rule_version"] = payload["activity_filter_rule_version"]
        settings = ProfitSettings(**_decimal_settings(values))
        if settings.save_root:
            ensure_writable_directory(Path(settings.save_root))
        self.update_settings(int(payload.get("expected_revision", snapshot.revision)), settings, actor)
        return self.legacy_settings(actor)

    def calculate(self, site_code: SiteCode, selling_price: Decimal, cost_price: Decimal, weight_kg: Decimal, actor: Any | None = None) -> dict[str, Any]:
        snapshot = self.get_settings(actor)
        preview = calculate_profit(site_code=site_code, selling_price=selling_price, cost_price=cost_price, weight_kg=weight_kg, settings=snapshot.settings)
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

    def list_records(self, site_code: SiteCode | None, offset: int, limit: int, actor: Any | None = None, *, include_workspace_shared: bool = False):
        context = _actor_context(actor)
        return self._repository.list_records(context.workspace_id, site_code, offset, limit, actor_id=context.actor_id, include_workspace_shared=include_workspace_shared or context.is_admin)

    def list_products(self, *, site: SiteCode | None = None, skcs: list[str] | None = None, actor: Any | None = None, include_workspace_shared: bool = False) -> list[dict[str, Any]]:
        context = _actor_context(actor)
        records = self.list_records(site, 0, 10_000, actor, include_workspace_shared=include_workspace_shared)
        requested = {item.strip() for item in (skcs or []) if item.strip()}
        if requested:
            records = [record for record in records if record.skc in requested]
        return [_product_payload(record, context) for record in records]

    def upsert_product(self, payload: dict[str, Any], *, actor: Any | None = None, allow_company_write: bool = False, require_complete_profile: bool = False, image: tuple[str, bytes] | None = None, source_image: tuple[str, bytes] | None = None, source_group_images: dict[int, list[tuple[str, bytes]]] | None = None) -> dict[str, Any]:
        context = _actor_context(actor)
        site = _site(payload.get("site", payload.get("site_code", "US")))
        skc = str(payload.get("skc") or payload.get("skc_id") or "").strip()
        if not skc:
            raise ValueError("skc is required")
        settings = self.get_settings(actor)
        preview = calculate_profit(site_code=site, selling_price=_decimal(payload.get("selling_price")), cost_price=_decimal(payload.get("cost_price")), weight_kg=_decimal(payload.get("weight_kg")), settings=settings.settings)
        current = self._repository.find_product(skc, site, context.workspace_id)
        if current is not None and current.created_by != context.actor_id and not (allow_company_write or context.is_admin):
            raise ProfitActivityConflict("profit_activity_company_write_required")
        root = self._asset_root(settings.settings)
        image_path = current.image_path if current else ""
        source_groups = _source_groups(payload.get("source_groups_json"), current.source_groups_json if current else "[]")
        if image:
            image_path = save_asset(root, site=site, skc=skc, kind="product", filename=image[0], content=image[1])
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
            if not note:
                missing.append("note_required")
            if missing:
                raise ValueError(",".join(missing))
        record = self._repository.upsert_record(
            workspace_id=context.workspace_id, created_by=context.actor_id, created_by_username=context.username,
            skc=skc, note=note, preview=preview,
            calculation_hash=_calculation_hash(preview, settings.revision), settings_revision=settings.revision,
            refund_rate=settings.settings.ec_refund_rate if site == "EC" else settings.settings.refund_rate,
            visibility=str(payload.get("visibility") or (current.visibility if current else "shared")), source_url=source_url,
            image_path=image_path, source_image_path=source_image_path, source_groups=source_groups,
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
        }
        return self.upsert_product(merged, actor=actor, allow_company_write=allow_company_write, require_complete_profile=True)

    def delete_product(self, skc: str, site: SiteCode, actor: Any | None = None, *, allow_company_delete: bool = False) -> dict[str, Any]:
        context = _actor_context(actor)
        current = self._repository.find_product(skc, site, context.workspace_id)
        if current is not None and current.created_by != context.actor_id and not (allow_company_delete or context.is_admin):
            raise ProfitActivityConflict("profit_activity_company_delete_required")
        return {"status": "deleted" if self._repository.delete_product(skc, site, context.workspace_id) else "not_found", "skc": skc, "site": site}

    def preview_import(self, workbook: bytes, original_filename: str, site: SiteCode, actor: Any | None = None) -> dict[str, Any]:
        context = _actor_context(actor)
        rows = parse_product_workbook(workbook, site, self._repository.product_keys(context.workspace_id))
        import_id = uuid.uuid4().hex
        image_rows = extract_product_workbook_images(workbook)
        root = self._asset_root(self.get_settings(actor).settings)
        for row in rows:
            extracted = image_rows.get(row["row_id"], {})
            product_images = extracted.get("product", [])
            source_images = extracted.get("source", [])
            if product_images:
                filename, content = product_images[0]
                row["product_image_path"] = save_asset(root, site=site, skc=f"import_{import_id}_{row['row_id']}", kind="preview_product", filename=filename, content=content)
                row["has_product_image"] = True
            if source_images:
                filename, content = source_images[0]
                row["source_image_path"] = save_asset(root, site=site, skc=f"import_{import_id}_{row['row_id']}", kind="preview_source", filename=filename, content=content)
                row["has_source_image"] = True
        self._repository.save_import_session(context.workspace_id, import_id, original_filename, site, rows)
        summary = _import_summary(rows)
        return {"import_id": import_id, "original_filename": original_filename, "site": site, "summary": summary, "rows": rows}

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
            exists = self._repository.find_product(row["skc"], _site(session.site), context.workspace_id) is not None
            if exists and on_conflict == "skip":
                skipped += 1
                continue
            image = _asset_tuple(row.get("product_image_path"))
            source_image = _asset_tuple(row.get("source_image_path"))
            product = self.upsert_product(
                {**row, "site": session.site, "visibility": "shared"},
                image=image,
                actor=actor,
                allow_company_write=True,
                source_image=source_image,
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

    def create_catalog(self, site: SiteCode, actor: Any | None = None, *, include_workspace_shared: bool = False) -> Path:
        workbook = new_workbook()
        sheet = workbook.active
        sheet.title = "products"
        sheet.append(["site", "SKC", "售价", "成本", "重量KG", "备注", "货源", "利润", "利润率"])
        for product in self.list_products(site=site, actor=actor, include_workspace_shared=include_workspace_shared):
            sheet.append([product["site"], product["skc"], product["selling_price"], product["cost_price"], product["weight_kg"], product["note"], product["source_url"], product["net_profit"], product["profit_rate"]])
        root = self._asset_root(self.get_settings(actor).settings)
        path = root / f"{site}_product_catalog.xlsx"
        path.write_bytes(workbook_bytes(workbook))
        return path

    def filter_activity_template(self, workbook: bytes, original_filename: str, site: SiteCode, actor: Any | None = None, *, include_workspace_shared: bool = False) -> dict[str, Any]:
        context = _actor_context(actor)
        products = {product["skc"]: product for product in self.list_products(site=site, actor=actor, include_workspace_shared=include_workspace_shared)}
        settings = self.get_settings(actor).settings
        def evaluate(skc: str, price: Decimal) -> dict[str, Any]:
            product = products.get(skc)
            if product is None:
                return {"keep": False, "decision": "excluded", "reason_code": "missing_product", "net_profit": None, "profit_rate": None}
            preview = calculate_profit(
                site_code=site,
                selling_price=price,
                cost_price=Decimal(str(product["cost_price"])),
                weight_kg=Decimal(str(product["weight_kg"])),
                settings=settings,
            )
            decision, reason = activity_decision(preview, settings)
            return {
                "keep": decision == "eligible", "decision": decision, "reason_code": reason,
                "net_profit": float(preview.net_profit), "profit_rate": float(preview.profit_rate),
                "net_profit_passed": preview.net_profit >= settings.activity_min_net_profit,
                "profit_rate_passed": preview.profit_rate >= settings.activity_profit_rate_threshold,
            }

        filtered = filter_activity_workbook(workbook, site=site, evaluate=evaluate)
        root = self._asset_root(settings) / "activity_outputs"
        root.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:12]
        filtered_path = root / f"eligible_{token}.xlsx"
        removed_path = root / f"excluded_{token}.xlsx"
        filtered_path.write_bytes(filtered.pop("filtered_bytes"))
        removed_path.write_bytes(filtered.pop("removed_bytes"))
        result = {
            "site": site, "requested_site": site, "site_auto_switched": False,
            "original_filename": original_filename, "filtered_path": str(filtered_path), "removed_path": str(removed_path),
            "threshold": float(settings.activity_min_net_profit), "min_net_profit_threshold": float(settings.activity_min_net_profit),
            "profit_rate_threshold": float(settings.activity_profit_rate_threshold), "activity_profit_rate_threshold": float(settings.activity_profit_rate_threshold),
            "activity_filter_rule_version": settings.rule_version,
            **filtered,
        }
        task = self._repository.create_filter_task(context.workspace_id, result)
        return {"task_id": task.id, "filter_task_id": task.id, "operation_task_id": task.id, "status": "completed", **result}

    def get_filter_task_legacy(self, task_id: int, actor: Any | None = None) -> dict[str, Any]:
        context = _actor_context(actor)
        task = self._repository.get_filter_task(task_id, context.workspace_id)
        if task is None:
            raise ProfitActivityNotFound("filter_task_not_found")
        return {"task_id": task.id, "filter_task_id": task.id, "operation_task_id": task.id, "status": task.status, **json.loads(task.result_json)}

    def output_path(self, task_id: int, kind: str, actor: Any | None = None) -> Path:
        result = self.get_filter_task_legacy(task_id, actor)
        if kind not in {"filtered", "removed"}:
            raise ValueError("kind must be filtered or removed")
        return Path(result[f"{kind}_path"])

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
        groups = _source_groups(product.source_groups_json, "[]")
        paths = groups[group].get("image_paths", []) if group < len(groups) else []
        return resolve_asset(paths[index] if index < len(paths) else product.source_image_path)

    def _asset_root(self, settings: ProfitSettings) -> Path:
        return ensure_writable_directory(Path(settings.save_root) if settings.save_root else self._output_root())

    def _output_root(self) -> Path:
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
        decisions = []
        for record in self._repository.get_records_for_filter(context.workspace_id, site_code, record_ids, actor_id=context.actor_id, include_workspace_shared=include_workspace_shared or context.is_admin):
            preview = ProfitPreview(
                site_code=record.site_code, selling_price=record.selling_price, cost_price=record.cost_price, weight_kg=record.weight_kg,
                domestic_fee=record.domestic_fee, shipping_subsidy=record.shipping_subsidy, shipping_cost=record.shipping_cost,
                end_fee=record.end_fee, total_cost=record.total_cost, gross_profit=record.gross_profit,
                net_profit=record.net_profit, profit_rate=record.profit_rate,
            )
            decision, reason = activity_decision(preview, settings)
            decisions.append((record.id, decision, reason))
        return self._repository.create_activity_run(context.workspace_id, site_code, settings, decisions)

    def get_filter_run(self, run_id: int, actor: Any | None = None):
        context = _actor_context(actor)
        result = self._repository.get_activity_run(run_id, context.workspace_id)
        if result is None:
            raise ProfitActivityNotFound("activity_run_not_found")
        return result


def create_profit_activity_service(database_url: str | Path | None = None) -> ProfitActivityService:
    database = create_database(database_url)
    return ProfitActivityService(ProfitActivityRepository(database.sessions), database)


def _calculation_hash(preview: ProfitPreview, settings_revision: int) -> str:
    payload = {"preview": {key: str(value) for key, value in asdict(preview).items()}, "settings_revision": settings_revision}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _site(value: Any) -> SiteCode:
    site = str(value or "US").upper()
    if site not in {"US", "CO", "EC"}:
        raise ValueError("site must be US, CO or EC")
    return site  # type: ignore[return-value]


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError("numeric value is required") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("numeric value must be positive")
    return result


def _decimal_settings(values: dict[str, Any]) -> dict[str, Any]:
    decimal_names = set(ProfitSettings.__dataclass_fields__) - {"save_root", "rule_version"}
    return {key: (_decimal_setting(value) if key in decimal_names else int(value) if key == "rule_version" else str(value)) for key, value in values.items()}


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
            result.append({"source_url": str(item.get("source_url") or ""), "image_paths": [str(path) for path in item.get("image_paths", []) if str(path)]})
    return result


def _ensure_group(groups: list[dict[str, Any]], index: int) -> dict[str, Any]:
    while len(groups) <= index:
        groups.append({"source_url": "", "image_paths": []})
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
        "id": record.id, "site": record.site_code, "site_code": record.site_code, "skc": record.skc,
        "visibility": record.visibility, "created_by": record.created_by, "created_by_username": record.created_by_username,
        "workspace_id": record.workspace_id, "workspace_code": actor.workspace_code, "workspace_name": actor.workspace_name,
        "is_owner": is_owner, "can_edit": is_owner or actor.is_admin,
        "image_path": record.image_path, "source_image_path": record.source_image_path, "source_groups": groups,
        "selling_price": float(record.selling_price), "cost_price": float(record.cost_price), "weight_kg": float(record.weight_kg),
        "source_url": record.source_url, "note": record.note, "domestic_fee": float(record.domestic_fee),
        "shipping_subsidy": float(record.shipping_subsidy), "refund_rate": float(record.refund_rate) if hasattr(record, "refund_rate") else 0.0,
        "shipping_cost": float(record.shipping_cost), "end_fee": float(record.end_fee), "total_cost": float(record.total_cost),
        "gross_profit": float(record.gross_profit), "net_profit": float(record.net_profit), "profit_rate": float(record.profit_rate),
        "created_at": record.created_at.isoformat() if record.created_at else "", "updated_at": record.updated_at.isoformat() if record.updated_at else "",
    }


def _preview_from_product(product: dict[str, Any]) -> ProfitPreview:
    return ProfitPreview(site_code=_site(product["site"]), selling_price=Decimal(str(product["selling_price"])), cost_price=Decimal(str(product["cost_price"])), weight_kg=Decimal(str(product["weight_kg"])), domestic_fee=Decimal(str(product["domestic_fee"])), shipping_subsidy=Decimal(str(product["shipping_subsidy"])), shipping_cost=Decimal(str(product["shipping_cost"])), end_fee=Decimal(str(product["end_fee"])), total_cost=Decimal(str(product["total_cost"])), gross_profit=Decimal(str(product["gross_profit"])), net_profit=Decimal(str(product["net_profit"])), profit_rate=Decimal(str(product["profit_rate"])))


def _import_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"total_rows": len(rows), "importable_rows": sum(row["status"] == "ready" for row in rows), "warning_rows": sum(bool(row["warnings"]) for row in rows), "blocked_rows": sum(row["status"] != "ready" for row in rows), "duplicate_rows": sum(bool(row["is_duplicate"]) for row in rows), "default_selected_rows": sum(row["status"] == "ready" and not row["is_duplicate"] for row in rows)}


def _asset_tuple(path_value: Any) -> tuple[str, bytes] | None:
    """Convert a persisted preview image into the normal upload input shape."""
    if not path_value:
        return None
    try:
        path = resolve_asset(str(path_value))
    except ValueError:
        return None
    return path.name, path.read_bytes()
