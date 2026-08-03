from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from .domain.engine import activity_decision, calculate_profit
from .domain.models import ProfitPreview, ProfitSettings, SiteCode
from .infrastructure.database import ProfitActivityDatabase, create_database
from .infrastructure.repository import ProfitActivityRepository, SettingsRevisionConflict, SettingsSnapshot


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

    def update_settings(self, expected_revision: int, settings: ProfitSettings) -> SettingsSnapshot:
        try:
            return self._repository.update_settings(expected_revision, settings)
        except SettingsRevisionConflict as exc:
            raise ProfitActivityConflict("settings_revision_conflict") from exc

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
