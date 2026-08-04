from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..domain.models import ProfitPreview, ProfitSettings, SiteCode
from .orm import (
    ActivityDecisionRow, ActivityRunRow, FilterTaskRow, ImportSessionRow,
    ImportTaskRow, ProfitRecordRow, ProfitSettingsRow,
)


class SettingsRevisionConflict(ValueError):
    pass


@dataclass(frozen=True)
class SettingsSnapshot:
    revision: int
    settings: ProfitSettings


class ProfitActivityRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def get_settings(self) -> SettingsSnapshot:
        with self._sessions.begin() as session:
            row = self._settings_row(session)
            return SettingsSnapshot(row.revision, _settings(row))

    def update_settings(self, expected_revision: int, settings: ProfitSettings) -> SettingsSnapshot:
        with self._sessions.begin() as session:
            row = self._settings_row(session)
            if row.revision != expected_revision:
                raise SettingsRevisionConflict("settings_revision_conflict")
            for field, value in asdict(settings).items():
                setattr(row, field, value)
            row.revision += 1
            session.flush()
            return SettingsSnapshot(row.revision, _settings(row))

    def upsert_record(self, *, skc: str, note: str, preview: ProfitPreview, calculation_hash: str, settings_revision: int, refund_rate: Decimal = Decimal("0"), visibility: str = "shared", source_url: str = "", image_path: str = "", source_image_path: str = "", source_groups: list[dict] | None = None) -> ProfitRecordRow:
        with self._sessions.begin() as session:
            row = session.scalar(select(ProfitRecordRow).where(ProfitRecordRow.site_code == preview.site_code, ProfitRecordRow.skc == skc))
            values = {
                **asdict(preview), "note": note, "calculation_hash": calculation_hash,
                "settings_revision": settings_revision, "refund_rate": refund_rate, "visibility": visibility,
                "source_url": source_url, "image_path": image_path,
                "source_image_path": source_image_path,
                "source_groups_json": json.dumps(source_groups or [], ensure_ascii=False, separators=(",", ":")),
            }
            if row is None:
                row = ProfitRecordRow(skc=skc, **values)
                session.add(row)
            else:
                for field, value in values.items():
                    setattr(row, field, value)
                row.revision += 1
            session.flush()
            return row

    def list_records(self, site_code: SiteCode | None, offset: int, limit: int) -> list[ProfitRecordRow]:
        with self._sessions() as session:
            query = select(ProfitRecordRow).order_by(ProfitRecordRow.id.desc()).offset(offset).limit(limit)
            if site_code is not None:
                query = query.where(ProfitRecordRow.site_code == site_code)
            return list(session.scalars(query))

    def create_activity_run(self, site_code: SiteCode | None, settings: ProfitSettings, decisions: list[tuple[int, str, str]]) -> ActivityRunRow:
        with self._sessions.begin() as session:
            run = ActivityRunRow(site_code=site_code, rule_version=settings.rule_version, minimum_net_profit=settings.activity_min_net_profit, minimum_profit_rate=settings.activity_profit_rate_threshold, retained_count=sum(item[1] == "eligible" for item in decisions), excluded_count=sum(item[1] == "excluded" for item in decisions))
            session.add(run)
            session.flush()
            session.add_all(ActivityDecisionRow(run_id=run.id, record_id=record_id, decision=decision, reason_code=reason) for record_id, decision, reason in decisions)
            session.flush()
            return run

    def get_records_for_filter(self, site_code: SiteCode | None, record_ids: list[int] | None) -> list[ProfitRecordRow]:
        with self._sessions() as session:
            query = select(ProfitRecordRow).order_by(ProfitRecordRow.id)
            if site_code is not None:
                query = query.where(ProfitRecordRow.site_code == site_code)
            if record_ids is not None:
                query = query.where(ProfitRecordRow.id.in_(record_ids))
            return list(session.scalars(query))

    def product_keys(self) -> set[tuple[str, str]]:
        with self._sessions() as session:
            return {(str(site), str(skc)) for site, skc in session.execute(select(ProfitRecordRow.site_code, ProfitRecordRow.skc))}

    def find_product(self, skc: str, site: SiteCode) -> ProfitRecordRow | None:
        with self._sessions() as session:
            return session.scalar(select(ProfitRecordRow).where(ProfitRecordRow.site_code == site, ProfitRecordRow.skc == skc))

    def delete_product(self, skc: str, site: SiteCode) -> bool:
        with self._sessions.begin() as session:
            row = session.scalar(select(ProfitRecordRow).where(ProfitRecordRow.site_code == site, ProfitRecordRow.skc == skc))
            if row is None:
                return False
            session.delete(row)
            return True

    def save_import_session(self, import_id: str, original_filename: str, site: SiteCode, rows: list[dict]) -> None:
        with self._sessions.begin() as session:
            session.merge(ImportSessionRow(import_id=import_id, original_filename=original_filename, site=site, rows_json=json.dumps(rows, ensure_ascii=False, separators=(",", ":"))))

    def get_import_session(self, import_id: str) -> ImportSessionRow | None:
        with self._sessions() as session:
            return session.get(ImportSessionRow, import_id)

    def create_import_task(self, import_id: str, result: dict) -> ImportTaskRow:
        with self._sessions.begin() as session:
            task = ImportTaskRow(import_id=import_id, status="completed", result_json=json.dumps(result, ensure_ascii=False, default=str))
            session.add(task)
            session.flush()
            return task

    def get_import_task(self, task_id: int) -> ImportTaskRow | None:
        with self._sessions() as session:
            return session.get(ImportTaskRow, task_id)

    def create_filter_task(self, result: dict) -> FilterTaskRow:
        with self._sessions.begin() as session:
            task = FilterTaskRow(status="completed", result_json=json.dumps(result, ensure_ascii=False, default=str))
            session.add(task)
            session.flush()
            return task

    def get_filter_task(self, task_id: int) -> FilterTaskRow | None:
        with self._sessions() as session:
            return session.get(FilterTaskRow, task_id)

    def get_activity_run(self, run_id: int) -> tuple[ActivityRunRow, list[ActivityDecisionRow]] | None:
        with self._sessions() as session:
            run = session.get(ActivityRunRow, run_id)
            if run is None:
                return None
            decisions = list(session.scalars(select(ActivityDecisionRow).where(ActivityDecisionRow.run_id == run_id).order_by(ActivityDecisionRow.id)))
            return run, decisions

    @staticmethod
    def _settings_row(session: Session) -> ProfitSettingsRow:
        row = session.get(ProfitSettingsRow, 1)
        if row is None:
            row = ProfitSettingsRow(id=1)
            session.add(row)
            session.flush()
        return row


def _settings(row: ProfitSettingsRow) -> ProfitSettings:
    return ProfitSettings(**{name: getattr(row, name) for name in ProfitSettings.__dataclass_fields__})
