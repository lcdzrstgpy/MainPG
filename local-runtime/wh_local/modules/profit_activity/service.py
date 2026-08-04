from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from .domain.engine import activity_decision, calculate_profit
from .domain.models import ProfitPreview, ProfitSettings, SiteCode
from .infrastructure.database import ProfitActivityDatabase, create_database
from .infrastructure.repository import ProfitActivityRepository, SettingsRevisionConflict, SettingsSnapshot
from .infrastructure.assets import resolve_asset, save_asset
from .domain.workbooks import new_workbook, parse_activity_workbook, parse_product_workbook, workbook_bytes


class ProfitActivityConflict(ValueError):
    pass


class ProfitActivityNotFound(ValueError):
    pass


class ProfitActivityService:
    def __init__(self, repository: ProfitActivityRepository, database: ProfitActivityDatabase | None = None) -> None:
        self._repository = repository
        self._database = database

    def close(self) -> None:
        """释放数据库连接；供应用 shutdown 与测试清理调用。"""
        if self._database is not None:
            self._database.dispose()

    def get_settings(self) -> SettingsSnapshot:
        return self._repository.get_settings()

    def legacy_settings(self) -> dict[str, Any]:
        settings = asdict(self.get_settings().settings)
        settings["activity_filter_rule_version"] = settings["rule_version"]
        return settings

    def update_settings(self, expected_revision: int, settings: ProfitSettings) -> SettingsSnapshot:
        try:
            return self._repository.update_settings(expected_revision, settings)
        except SettingsRevisionConflict as exc:
            raise ProfitActivityConflict("settings_revision_conflict") from exc

    def update_legacy_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.get_settings()
        values = asdict(snapshot.settings)
        for name in values:
            if name not in payload:
                continue
            values[name] = payload[name]
        if "activity_filter_rule_version" in payload:
            values["rule_version"] = payload["activity_filter_rule_version"]
        settings = ProfitSettings(**_decimal_settings(values))
        self.update_settings(int(payload.get("expected_revision", snapshot.revision)), settings)
        return self.legacy_settings()

    def calculate(self, site_code: SiteCode, selling_price: Decimal, cost_price: Decimal, weight_kg: Decimal) -> dict[str, Any]:
        snapshot = self._repository.get_settings()
        preview = calculate_profit(site_code=site_code, selling_price=selling_price, cost_price=cost_price, weight_kg=weight_kg, settings=snapshot.settings)
        return {
            "preview": preview,
            "settings_revision": snapshot.revision,
            "calculation_hash": _calculation_hash(preview, snapshot.revision),
            "archive_allowed": preview.net_profit >= 0,
            "confirmation_required": "negative_profit" if preview.net_profit < 0 else None,
        }

    def calculate_legacy(self, payload: dict[str, Any]) -> dict[str, Any]:
        site = _site(payload.get("site", payload.get("site_code", "US")))
        result = self.calculate(site, _decimal(payload.get("selling_price")), _decimal(payload.get("cost_price")), _decimal(payload.get("weight_kg")))
        calculation = asdict(result["preview"])
        calculation["site"] = calculation.pop("site_code")
        return {"calculation": calculation, "settings": self.legacy_settings()}

    def archive(self, *, site_code: SiteCode, skc: str, note: str, selling_price: Decimal, cost_price: Decimal, weight_kg: Decimal, calculation_hash: str, settings_revision: int, confirm_negative_profit: bool):
        current = self.calculate(site_code, selling_price, cost_price, weight_kg)
        if current["settings_revision"] != settings_revision:
            raise ProfitActivityConflict("settings_revision_conflict")
        if not hmac.compare_digest(current["calculation_hash"], calculation_hash):
            raise ProfitActivityConflict("profit_calculation_changed")
        preview: ProfitPreview = current["preview"]
        if preview.net_profit < 0 and not confirm_negative_profit:
            raise ProfitActivityConflict("negative_profit_confirmation_required")
        return self._repository.upsert_record(skc=skc, note=note, preview=preview, calculation_hash=calculation_hash, settings_revision=settings_revision)

    def list_records(self, site_code: SiteCode | None, offset: int, limit: int):
        return self._repository.list_records(site_code, offset, limit)

    def list_products(self, *, site: SiteCode | None = None, skcs: list[str] | None = None) -> list[dict[str, Any]]:
        records = self.list_records(site, 0, 10_000)
        requested = {item.strip() for item in (skcs or []) if item.strip()}
        if requested:
            records = [record for record in records if record.skc in requested]
        return [_product_payload(record) for record in records]

    def upsert_product(self, payload: dict[str, Any], *, image: tuple[str, bytes] | None = None, source_image: tuple[str, bytes] | None = None, source_group_images: dict[int, list[tuple[str, bytes]]] | None = None) -> dict[str, Any]:
        site = _site(payload.get("site", payload.get("site_code", "US")))
        skc = str(payload.get("skc") or payload.get("skc_id") or "").strip()
        if not skc:
            raise ValueError("skc is required")
        settings = self.get_settings()
        preview = calculate_profit(site_code=site, selling_price=_decimal(payload.get("selling_price")), cost_price=_decimal(payload.get("cost_price")), weight_kg=_decimal(payload.get("weight_kg")), settings=settings.settings)
        current = self._repository.find_product(skc, site)
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
        record = self._repository.upsert_record(
            skc=skc, note=str(payload.get("note") or "")[:500], preview=preview,
            calculation_hash=_calculation_hash(preview, settings.revision), settings_revision=settings.revision,
            refund_rate=settings.settings.ec_refund_rate if site == "EC" else settings.settings.refund_rate,
            visibility=str(payload.get("visibility") or (current.visibility if current else "shared")), source_url=source_url,
            image_path=image_path, source_image_path=source_image_path, source_groups=source_groups,
        )
        return _product_payload(record)

    def update_product_values(self, skc: str, payload: dict[str, Any]) -> dict[str, Any]:
        site = _site(payload.get("site", "US"))
        current = self._repository.find_product(skc, site)
        if current is None:
            raise ProfitActivityNotFound("product_not_found")
        merged = {
            "site": site, "skc": skc, "selling_price": payload.get("selling_price", current.selling_price),
            "cost_price": payload.get("cost_price", current.cost_price), "weight_kg": payload.get("weight_kg", current.weight_kg),
            "note": payload.get("note", current.note), "visibility": payload.get("visibility", current.visibility),
            "source_url": payload.get("source_url", current.source_url), "source_groups_json": payload.get("source_groups_json", current.source_groups_json),
        }
        return self.upsert_product(merged)

    def delete_product(self, skc: str, site: SiteCode) -> dict[str, Any]:
        return {"status": "deleted" if self._repository.delete_product(skc, site) else "not_found", "skc": skc, "site": site}

    def preview_import(self, workbook: bytes, original_filename: str, site: SiteCode) -> dict[str, Any]:
        rows = parse_product_workbook(workbook, site, self._repository.product_keys())
        import_id = uuid.uuid4().hex
        self._repository.save_import_session(import_id, original_filename, site, rows)
        summary = _import_summary(rows)
        return {"import_id": import_id, "original_filename": original_filename, "site": site, "summary": summary, "rows": rows}

    def confirm_import(self, import_id: str, selected_row_ids: list[str] | None, on_conflict: str) -> dict[str, Any]:
        session = self._repository.get_import_session(import_id)
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
            exists = self._repository.find_product(row["skc"], _site(session.site)) is not None
            if exists and on_conflict == "skip":
                skipped += 1
                continue
            product = self.upsert_product({**row, "site": session.site, "visibility": "shared"})
            products.append(product)
            if exists:
                replaced += 1
            else:
                imported += 1
        result = {"imported": imported, "skipped": skipped, "replaced": replaced, "products": products, "summary": _import_summary(rows)}
        task = self._repository.create_import_task(import_id, result)
        return {"task_id": task.id, "status": "completed", "task_status": "completed", "task": {"id": task.id, "status": "completed", "updated_at": task.updated_at}, **result}

    def get_import_task(self, task_id: int) -> dict[str, Any]:
        task = self._repository.get_import_task(task_id)
        if task is None:
            raise ProfitActivityNotFound("import_task_not_found")
        return {"task": {"id": task.id, "status": task.status, "updated_at": task.updated_at}, "result": json.loads(task.result_json), "blockers": []}

    def create_catalog(self, site: SiteCode) -> Path:
        workbook = new_workbook()
        sheet = workbook.active
        sheet.title = "products"
        sheet.append(["site", "SKC", "售价", "成本", "重量KG", "备注", "货源", "利润", "利润率"])
        for product in self.list_products(site=site):
            sheet.append([product["site"], product["skc"], product["selling_price"], product["cost_price"], product["weight_kg"], product["note"], product["source_url"], product["net_profit"], product["profit_rate"]])
        path = self._output_root() / f"{site}_product_catalog.xlsx"
        path.write_bytes(workbook_bytes(workbook))
        return path

    def filter_activity_template(self, workbook: bytes, original_filename: str, site: SiteCode) -> dict[str, Any]:
        _, template_rows = parse_activity_workbook(workbook)
        products = {product["skc"]: product for product in self.list_products(site=site)}
        kept: list[list[Any]] = []
        removed: list[list[Any]] = []
        qualification_counts: dict[str, int] = {}
        decisions: list[dict[str, Any]] = []
        settings = self.get_settings().settings
        for _, row_number, skc in template_rows:
            product = products.get(skc)
            if product is None:
                decision, reason = "excluded", "missing_product"
            else:
                preview = _preview_from_product(product)
                decision, reason = activity_decision(preview, settings)
            qualification_counts[reason] = qualification_counts.get(reason, 0) + 1
            entry = [skc, decision, reason, product.get("net_profit") if product else None, product.get("profit_rate") if product else None]
            (kept if decision == "eligible" else removed).append(entry)
            decisions.append({"row_id": f"row_{row_number}", "skc": skc, "decision": decision, "reason_code": reason})
        filtered_path, removed_path = self._write_filter_outputs(kept, removed)
        result = {
            "site": site, "requested_site": site, "site_auto_switched": False,
            "template_site_summary": {"total_price_rows": len(template_rows), "site_counts": {site: len(template_rows)}, "unique_skc_count_by_site": {site: len({item[2] for item in template_rows})}},
            "original_filename": original_filename, "filtered_path": str(filtered_path), "removed_path": str(removed_path),
            "kept_skc_count": len({row[0] for row in kept}), "removed_skc_count": len({row[0] for row in removed}),
            "kept_row_count": len(kept), "removed_row_count": len(removed), "kept_activity_count": len(kept), "removed_activity_count": len(removed),
            "threshold": float(settings.activity_min_net_profit), "min_net_profit_threshold": float(settings.activity_min_net_profit),
            "profit_rate_threshold": float(settings.activity_profit_rate_threshold), "activity_profit_rate_threshold": float(settings.activity_profit_rate_threshold),
            "activity_filter_rule_version": settings.rule_version, "qualification_counts": qualification_counts,
            "removed_rows": [{"skc": row[0], "reason_code": row[2]} for row in removed], "activity_decisions": decisions,
        }
        task = self._repository.create_filter_task(result)
        return {"task_id": task.id, "filter_task_id": task.id, "operation_task_id": task.id, "status": "completed", **result}

    def get_filter_task_legacy(self, task_id: int) -> dict[str, Any]:
        task = self._repository.get_filter_task(task_id)
        if task is None:
            raise ProfitActivityNotFound("filter_task_not_found")
        return {"task_id": task.id, "filter_task_id": task.id, "operation_task_id": task.id, "status": task.status, **json.loads(task.result_json)}

    def output_path(self, task_id: int, kind: str) -> Path:
        result = self.get_filter_task_legacy(task_id)
        if kind not in {"filtered", "removed"}:
            raise ValueError("kind must be filtered or removed")
        return Path(result[f"{kind}_path"])

    def image_path(self, skc: str, site: SiteCode, kind: str, group: int = 0, index: int = 0) -> Path:
        product = self._repository.find_product(skc, site)
        if product is None:
            raise ProfitActivityNotFound("product_not_found")
        if kind == "product":
            return resolve_asset(product.image_path)
        groups = _source_groups(product.source_groups_json, "[]")
        paths = groups[group].get("image_paths", []) if group < len(groups) else []
        return resolve_asset(paths[index] if index < len(paths) else product.source_image_path)

    def _asset_root(self, settings: ProfitSettings) -> Path:
        root = Path(settings.save_root) if settings.save_root else self._output_root()
        root.mkdir(parents=True, exist_ok=True)
        return root

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

    def run_filter(self, site_code: SiteCode | None, record_ids: list[int] | None):
        settings = self._repository.get_settings().settings
        decisions = []
        for record in self._repository.get_records_for_filter(site_code, record_ids):
            preview = ProfitPreview(
                site_code=record.site_code, selling_price=record.selling_price, cost_price=record.cost_price, weight_kg=record.weight_kg,
                domestic_fee=record.domestic_fee, shipping_subsidy=record.shipping_subsidy, shipping_cost=record.shipping_cost,
                end_fee=record.end_fee, total_cost=record.total_cost, gross_profit=record.gross_profit,
                net_profit=record.net_profit, profit_rate=record.profit_rate,
            )
            decision, reason = activity_decision(preview, settings)
            decisions.append((record.id, decision, reason))
        return self._repository.create_activity_run(site_code, settings, decisions)

    def get_filter_run(self, run_id: int):
        result = self._repository.get_activity_run(run_id)
        if result is None:
            raise ProfitActivityNotFound("activity_run_not_found")
        return result


def create_profit_activity_service(database_url: str | None = None) -> ProfitActivityService:
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


def _product_payload(record) -> dict[str, Any]:
    groups = _source_groups(record.source_groups_json, "[]")
    return {
        "id": record.id, "site": record.site_code, "site_code": record.site_code, "skc": record.skc,
        "visibility": record.visibility, "created_by": record.created_by, "created_by_username": record.created_by_username,
        "workspace_id": None, "workspace_code": "local", "workspace_name": "本地工作台", "is_owner": True, "can_edit": True,
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
