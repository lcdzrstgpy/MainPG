from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from wh_local.modules.profit_activity.domain.models import ProfitSettings
from wh_local.modules.profit_activity.infrastructure.database import create_database
from wh_local.modules.profit_activity.infrastructure.repository import ProfitActivityRepository
from wh_local.modules.profit_activity.service import ProfitActivityService


MODULE_ROOT = Path(__file__).resolve().parents[1] / "wh_local/modules/profit_activity"
BASE_SCHEMA = MODULE_ROOT / "migrations/001_profit_activity.sql"
THRESHOLD_MIGRATION = MODULE_ROOT / "migrations/005_activity_threshold_configuration.sql"


def _legacy_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(BASE_SCHEMA.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO profit_activity_settings "
            "(id, workspace_id, activity_min_net_profit, activity_profit_rate_threshold) "
            "VALUES (1, 'legacy-default', 8, 0.20)"
        )
        connection.execute(
            "INSERT INTO profit_activity_settings "
            "(id, workspace_id, activity_min_net_profit, activity_profit_rate_threshold) "
            "VALUES (2, 'legacy-custom', 12, 0.25)"
        )
        connection.commit()
    finally:
        connection.close()


def test_new_profit_settings_start_with_empty_activity_thresholds(tmp_path: Path) -> None:
    settings = ProfitSettings()
    assert settings.activity_min_net_profit == Decimal("0")
    assert settings.activity_profit_rate_threshold == Decimal("0")
    assert settings.activity_threshold_configured is False

    database = create_database(tmp_path / "fresh.sqlite3")
    try:
        stored = ProfitActivityRepository(database.sessions).get_settings().settings
        assert stored.activity_min_net_profit == Decimal("0")
        assert stored.activity_profit_rate_threshold == Decimal("0")
        assert stored.activity_threshold_configured is False
    finally:
        database.dispose()


def test_module_database_migrates_old_default_pair_to_unconfigured(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    _legacy_database(path)

    database = create_database(path)
    repository = ProfitActivityRepository(database.sessions)
    try:
        old_default = repository.get_settings("legacy-default").settings
        old_custom = repository.get_settings("legacy-custom").settings
        assert old_default.activity_min_net_profit == Decimal("0")
        assert old_default.activity_profit_rate_threshold == Decimal("0")
        assert old_default.activity_threshold_configured is False
        assert old_custom.activity_min_net_profit == Decimal("12")
        assert old_custom.activity_profit_rate_threshold == Decimal("0.25")
        assert old_custom.activity_threshold_configured is True
    finally:
        database.dispose()


def test_shared_sql_migration_classifies_legacy_thresholds() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE profit_activity_settings ("
            "id INTEGER PRIMARY KEY, workspace_id TEXT NOT NULL UNIQUE, "
            "activity_min_net_profit NUMERIC NOT NULL DEFAULT 8, "
            "activity_profit_rate_threshold NUMERIC NOT NULL DEFAULT 0.20)"
        )
        connection.executemany(
            "INSERT INTO profit_activity_settings VALUES (?, ?, ?, ?)",
            [(1, "default", 8, 0.20), (2, "custom", 12, 0.25)],
        )
        connection.executescript(THRESHOLD_MIGRATION.read_text(encoding="utf-8"))
        rows = connection.execute(
            "SELECT workspace_id, activity_min_net_profit, "
            "activity_profit_rate_threshold, activity_threshold_configured "
            "FROM profit_activity_settings ORDER BY id"
        ).fetchall()
        assert rows == [("default", 0, 0, 0), ("custom", 12, 0.25, 1)]
    finally:
        connection.close()


def test_legacy_settings_update_preserves_threshold_configuration_state(tmp_path: Path) -> None:
    database = create_database(tmp_path / "legacy-update.sqlite3")
    repository = ProfitActivityRepository(database.sessions)
    service = ProfitActivityService(repository, database)
    try:
        snapshot = repository.get_settings()
        repository.update_settings(
            snapshot.revision,
            ProfitSettings(activity_threshold_configured=True),
        )

        updated = service.update_legacy_settings({"domestic_fee": "6"})
        stored = repository.get_settings().settings

        assert updated["activity_threshold_configured"] is True
        assert stored.activity_threshold_configured is True
        assert isinstance(stored.activity_threshold_configured, bool)
        assert stored.domestic_fee == Decimal("6")
    finally:
        service.close()
