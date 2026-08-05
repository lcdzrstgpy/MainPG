from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal

from sqlalchemy import func, select
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

    def get_settings(self, workspace_id: str = "default") -> SettingsSnapshot:
        with self._sessions.begin() as session:
            row = self._settings_row(session, workspace_id)
            return SettingsSnapshot(row.revision, _settings(row))

    def update_settings(self, expected_revision: int, settings: ProfitSettings, workspace_id: str = "default") -> SettingsSnapshot:
        with self._sessions.begin() as session:
            row = self._settings_row(session, workspace_id)
            if row.revision != expected_revision:
                raise SettingsRevisionConflict("settings_revision_conflict")
            for field, value in asdict(settings).items():
                setattr(row, field, value)
            row.revision += 1
            session.flush()
            return SettingsSnapshot(row.revision, _settings(row))

    def upsert_record(self, *, workspace_id: str, created_by: str, created_by_username: str, skc: str, note: str, preview: ProfitPreview, calculation_hash: str, settings_revision: int, refund_rate: Decimal = Decimal("0"), visibility: str = "shared", source_url: str = "", image_path: str = "", source_image_path: str = "", source_groups: list[dict] | None = None) -> ProfitRecordRow:
        with self._sessions.begin() as session:
            row = session.scalar(select(ProfitRecordRow).where(ProfitRecordRow.workspace_id == workspace_id, ProfitRecordRow.site_code == preview.site_code, ProfitRecordRow.skc == skc))
            values = {
                **asdict(preview), "workspace_id": workspace_id, "note": note, "calculation_hash": calculation_hash,
                "settings_revision": settings_revision, "refund_rate": refund_rate, "visibility": visibility,
                "source_url": source_url, "image_path": image_path,
                "source_image_path": source_image_path,
                "source_groups_json": json.dumps(source_groups or [], ensure_ascii=False, separators=(",", ":")),
            }
            if row is None:
                row = ProfitRecordRow(skc=skc, created_by=created_by, created_by_username=created_by_username, **values)
                session.add(row)
            else:
                for field, value in values.items():
                    setattr(row, field, value)
                row.revision += 1
            session.flush()
            return row

    def list_records(self, workspace_id: str, site_code: SiteCode | None, offset: int, limit: int, *, actor_id: str = "", include_workspace_shared: bool = False) -> list[ProfitRecordRow]:
        with self._sessions() as session:
            query = select(ProfitRecordRow).where(ProfitRecordRow.workspace_id == workspace_id).order_by(ProfitRecordRow.id.desc()).offset(offset).limit(limit)
            if site_code is not None:
                query = query.where(ProfitRecordRow.site_code == site_code)
            if not include_workspace_shared:
                query = query.where(ProfitRecordRow.created_by == actor_id)
            return list(session.scalars(query))

    def create_activity_run(self, workspace_id: str, site_code: SiteCode | None, settings: ProfitSettings, decisions: list[tuple[int, str, str]]) -> ActivityRunRow:
        with self._sessions.begin() as session:
            run = ActivityRunRow(workspace_id=workspace_id, site_code=site_code, rule_version=settings.rule_version, minimum_net_profit=settings.activity_min_net_profit, minimum_profit_rate=settings.activity_profit_rate_threshold, retained_count=sum(item[1] == "eligible" for item in decisions), excluded_count=sum(item[1] == "excluded" for item in decisions))
            session.add(run)
            session.flush()
            session.add_all(ActivityDecisionRow(workspace_id=workspace_id, run_id=run.id, record_id=record_id, decision=decision, reason_code=reason) for record_id, decision, reason in decisions)
            session.flush()
            return run

    def get_records_for_filter(self, workspace_id: str, site_code: SiteCode | None, record_ids: list[int] | None, *, actor_id: str = "", include_workspace_shared: bool = False) -> list[ProfitRecordRow]:
        with self._sessions() as session:
            query = select(ProfitRecordRow).where(ProfitRecordRow.workspace_id == workspace_id).order_by(ProfitRecordRow.id)
            if site_code is not None:
                query = query.where(ProfitRecordRow.site_code == site_code)
            if record_ids is not None:
                query = query.where(ProfitRecordRow.id.in_(record_ids))
            if not include_workspace_shared:
                query = query.where(ProfitRecordRow.created_by == actor_id)
            return list(session.scalars(query))

    def product_keys(self, workspace_id: str) -> set[tuple[str, str]]:
        with self._sessions() as session:
            return {(str(site), str(skc)) for site, skc in session.execute(select(ProfitRecordRow.site_code, ProfitRecordRow.skc).where(ProfitRecordRow.workspace_id == workspace_id))}

    def find_product(self, skc: str, site: SiteCode, workspace_id: str = "default") -> ProfitRecordRow | None:
        with self._sessions() as session:
            return session.scalar(select(ProfitRecordRow).where(ProfitRecordRow.workspace_id == workspace_id, ProfitRecordRow.site_code == site, ProfitRecordRow.skc == skc))

    def delete_product(self, skc: str, site: SiteCode, workspace_id: str = "default") -> bool:
        with self._sessions.begin() as session:
            row = session.scalar(select(ProfitRecordRow).where(ProfitRecordRow.workspace_id == workspace_id, ProfitRecordRow.site_code == site, ProfitRecordRow.skc == skc))
            if row is None:
                return False
            session.delete(row)
            return True

    def save_import_session(self, workspace_id: str, import_id: str, original_filename: str, site: SiteCode, rows: list[dict]) -> None:
        with self._sessions.begin() as session:
            session.merge(ImportSessionRow(import_id=import_id, workspace_id=workspace_id, original_filename=original_filename, site=site, rows_json=json.dumps(rows, ensure_ascii=False, separators=(",", ":"))))

    def get_import_session(self, import_id: str, workspace_id: str = "default") -> ImportSessionRow | None:
        with self._sessions() as session:
            row = session.get(ImportSessionRow, import_id)
            return row if row is not None and row.workspace_id == workspace_id else None

    def create_import_task(self, workspace_id: str, import_id: str, result: dict) -> ImportTaskRow:
        with self._sessions.begin() as session:
            task = ImportTaskRow(workspace_id=workspace_id, import_id=import_id, status="completed", result_json=json.dumps(result, ensure_ascii=False, default=str))
            session.add(task)
            session.flush()
            return task

    def get_import_task(self, task_id: int, workspace_id: str = "default") -> ImportTaskRow | None:
        with self._sessions() as session:
            row = session.get(ImportTaskRow, task_id)
            return row if row is not None and row.workspace_id == workspace_id else None

    def create_filter_task(self, workspace_id: str, result: dict) -> FilterTaskRow:
        with self._sessions.begin() as session:
            task = FilterTaskRow(workspace_id=workspace_id, status="completed", result_json=json.dumps(result, ensure_ascii=False, default=str))
            session.add(task)
            session.flush()
            return task

    def get_filter_task(self, task_id: int, workspace_id: str = "default") -> FilterTaskRow | None:
        with self._sessions() as session:
            row = session.get(FilterTaskRow, task_id)
            return row if row is not None and row.workspace_id == workspace_id else None

    def get_activity_run(self, run_id: int, workspace_id: str = "default") -> tuple[ActivityRunRow, list[ActivityDecisionRow]] | None:
        with self._sessions() as session:
            run = session.get(ActivityRunRow, run_id)
            if run is None or run.workspace_id != workspace_id:
                return None
            decisions = list(session.scalars(select(ActivityDecisionRow).where(ActivityDecisionRow.workspace_id == workspace_id, ActivityDecisionRow.run_id == run_id).order_by(ActivityDecisionRow.id)))
            return run, decisions

    @staticmethod
    def _settings_row(session: Session, workspace_id: str) -> ProfitSettingsRow:
        row = session.scalar(select(ProfitSettingsRow).where(ProfitSettingsRow.workspace_id == workspace_id))
        if row is None:
            next_id = (session.scalar(select(func.max(ProfitSettingsRow.id))) or 0) + 1
            row = ProfitSettingsRow(id=next_id, workspace_id=workspace_id)
            session.add(row)
            session.flush()
        return row


def _settings(row: ProfitSettingsRow) -> ProfitSettings:
    return ProfitSettings(**{name: getattr(row, name) for name in ProfitSettings.__dataclass_fields__})
