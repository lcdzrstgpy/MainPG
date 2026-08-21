from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from wh_local.db import init_db
from wh_local.modules.pod_customization.repository import (
    PodCustomizationRepository,
    _migration_effect_is_present,
)


MIGRATION_ROOT = (
    Path(__file__).parents[1]
    / "wh_local"
    / "modules"
    / "pod_customization"
    / "migrations"
)
MIGRATION_NAMES = (
    "001_pod_customization",
    "002_direct_listing_trials",
    "003_style_grid_v2",
    "004_style_grid_publications",
    "005_dianxiaomi_exports",
    "006_pod_titles",
    "007_requested_count_upgrade",
    "008_persistent_billing_runs",
    "009_export_records",
)


def _migration_sql(name: str) -> str:
    return (MIGRATION_ROOT / f"{name}.sql").read_text(encoding="utf-8")


def _create_marker_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               migration_id TEXT PRIMARY KEY,
               module TEXT NOT NULL,
               applied_at TEXT NOT NULL DEFAULT (datetime('now'))
           )"""
    )


def _apply_prefix(database: Path, count: int, *, mark: bool = True) -> None:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        _create_marker_table(connection)
        for name in MIGRATION_NAMES[:count]:
            connection.executescript(_migration_sql(name))
            if mark:
                connection.execute(
                    """INSERT OR IGNORE INTO schema_migrations (migration_id, module)
                       VALUES (?, 'pod_customization')""",
                    (f"pod_customization:{name}",),
                )


def _markers(database: Path) -> list[str]:
    with sqlite3.connect(database) as connection:
        return [
            str(row[0])
            for row in connection.execute(
                """SELECT migration_id FROM schema_migrations
                   WHERE module = 'pod_customization' ORDER BY migration_id"""
            )
        ]


def _assert_all_effects(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        assert all(
            _migration_effect_is_present(connection, name)
            for name in MIGRATION_NAMES
        )


def _insert_batch_fixture(connection: sqlite3.Connection) -> None:
    connection.execute(
        """INSERT INTO pod_customization_assets
           (asset_id, workspace_id, owner_user_id, kind, filename, relative_path,
            content_type, byte_size, sha256, width, height, created_at)
           VALUES ('asset', 'workspace-a', 'owner', 'template', 'a.png', 'a.png',
                   'image/png', 1, 'sha', 1, 1, 'now')"""
    )
    connection.execute(
        """INSERT INTO pod_customization_templates
           (template_id, workspace_id, owner_user_id, name, source, asset_id,
            width, height, calibration_status, calibration_json, version,
            created_at, updated_at)
           VALUES ('template', 'workspace-a', 'owner', 'Template', 'personal',
                   'asset', 1, 1, 'ready', '{}', 1, 'now', 'now')"""
    )
    connection.execute(
        """INSERT INTO pod_customization_template_snapshots
           (snapshot_id, template_id, workspace_id, owner_user_id, version, name,
            source, asset_id, width, height, calibration_json, created_at)
           VALUES ('snapshot', 'template', 'workspace-a', 'owner', 1, 'Template',
                   'personal', 'asset', 1, 1, '{}', 'now')"""
    )
    connection.execute(
        """INSERT INTO pod_customization_batches
           (batch_id, workspace_id, owner_user_id, title, status, template_id,
            template_snapshot_id, template_name, requested_count,
            initial_call_count, max_refill_calls, prompt_version, prompt_snapshot,
            business_fields_json, creative_prompt, listing_fields_json,
            created_at, updated_at)
           VALUES ('batch', 'workspace-a', 'owner', 'Batch', 'queued', 'template',
                   'snapshot', 'Template', 20, 20, 0, 'v1', 'prompt', '{}',
                   'creative', '{"title":"kept"}', 'now', 'now')"""
    )


@pytest.mark.parametrize("initializer", ["repository", "shared_db"])
def test_fresh_and_repeated_startup_produces_complete_pod_schema(
    tmp_path: Path,
    initializer: str,
) -> None:
    database = tmp_path / f"fresh-{initializer}.sqlite3"

    if initializer == "repository":
        PodCustomizationRepository(database)
        PodCustomizationRepository(database)
    else:
        init_db(database)
        init_db(database)

    _assert_all_effects(database)
    assert _markers(database)[:9] == [
        f"pod_customization:{name}" for name in MIGRATION_NAMES
    ]


@pytest.mark.parametrize("checkpoint", [1, 6])
def test_repository_upgrades_supported_pod_checkpoints_and_is_repeatable(
    tmp_path: Path,
    checkpoint: int,
) -> None:
    database = tmp_path / f"checkpoint-{checkpoint}.sqlite3"
    _apply_prefix(database, checkpoint)

    PodCustomizationRepository(database)
    PodCustomizationRepository(database)

    _assert_all_effects(database)
    assert _markers(database)[:9] == [
        f"pod_customization:{name}" for name in MIGRATION_NAMES
    ]


@pytest.mark.parametrize("initializer", ["repository", "shared_db"])
def test_migration_runner_recovers_005_when_alter_committed_before_marker_and_tables(
    tmp_path: Path,
    initializer: str,
) -> None:
    database = tmp_path / "partial-005.sqlite3"
    _apply_prefix(database, 4)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """ALTER TABLE pod_customization_batches
               ADD COLUMN listing_fields_json TEXT NOT NULL DEFAULT 'null'"""
        )

    if initializer == "repository":
        PodCustomizationRepository(database)
    else:
        init_db(database)

    _assert_all_effects(database)
    assert "pod_customization:005_dianxiaomi_exports" in _markers(database)


@pytest.mark.parametrize("initializer", ["repository", "shared_db"])
def test_migration_runner_finishes_007_when_old_table_was_dropped_before_rename(
    tmp_path: Path,
    initializer: str,
) -> None:
    database = tmp_path / "partial-007.sqlite3"
    _apply_prefix(database, 6)
    migration = _migration_sql("007_requested_count_upgrade")
    partial = migration.split("DROP TABLE IF EXISTS pod_customization_batches_requested_count_v2;", 1)[1]
    partial = partial.split(
        "ALTER TABLE pod_customization_batches_requested_count_v2", 1
    )[0]
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        _insert_batch_fixture(connection)
        connection.executescript(partial)
        assert connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'pod_customization_batches'"""
        ).fetchone() is None

    if initializer == "repository":
        PodCustomizationRepository(database)
    else:
        init_db(database)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """SELECT requested_count, creative_prompt, listing_fields_json
               FROM pod_customization_batches WHERE batch_id = 'batch'"""
        ).fetchone()
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    assert row == (20, "creative", '{"title":"kept"}')
    assert foreign_key_violations == []
    _assert_all_effects(database)
    assert "pod_customization:007_requested_count_upgrade" in _markers(database)


def test_repository_refuses_to_mark_unrecoverable_007_partial_table(
    tmp_path: Path,
) -> None:
    database = tmp_path / "broken-partial-007.sqlite3"
    _apply_prefix(database, 6)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE pod_customization_batches")
        connection.execute(
            """CREATE TABLE pod_customization_batches_requested_count_v2 (
                   batch_id TEXT PRIMARY KEY
               )"""
        )

    with pytest.raises(RuntimeError, match="007_requested_count_upgrade"):
        PodCustomizationRepository(database)

    assert "pod_customization:007_requested_count_upgrade" not in _markers(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table'
                 AND name = 'pod_customization_batches_requested_count_v2'"""
        ).fetchone() is not None


@pytest.mark.parametrize("premarked", [False, True])
def test_shared_db_completes_partial_008_before_trusting_marker(
    tmp_path: Path,
    premarked: bool,
) -> None:
    database = tmp_path / "partial-008.sqlite3"
    _apply_prefix(database, 7)
    migration = _migration_sql("008_persistent_billing_runs")
    first_table = migration.split(
        "CREATE INDEX IF NOT EXISTS idx_pod_billing_runs_owner_status", 1
    )[0]
    with sqlite3.connect(database) as connection:
        connection.executescript(first_table)
        if premarked:
            connection.execute(
                """INSERT INTO schema_migrations (migration_id, module)
                   VALUES ('pod_customization:008_persistent_billing_runs',
                           'pod_customization')"""
            )

    init_db(database)

    _assert_all_effects(database)
    assert "pod_customization:008_persistent_billing_runs" in _markers(database)


def test_repository_completes_partial_009_before_writing_marker(tmp_path: Path) -> None:
    database = tmp_path / "partial-009.sqlite3"
    _apply_prefix(database, 8)
    migration = _migration_sql("009_export_records")
    table_only = migration.split(
        "CREATE INDEX IF NOT EXISTS idx_pod_export_records_owner_batch_created", 1
    )[0]
    with sqlite3.connect(database) as connection:
        connection.executescript(table_only)

    PodCustomizationRepository(database)

    _assert_all_effects(database)
    assert "pod_customization:009_export_records" in _markers(database)


def test_every_published_migration_has_an_explicit_complete_effect_contract() -> None:
    from wh_local.pod_migrations import POD_MIGRATION_CONTRACTS

    assert tuple(POD_MIGRATION_CONTRACTS) == MIGRATION_NAMES
    for name, contract in POD_MIGRATION_CONTRACTS.items():
        assert contract.tables or contract.column_additions, name
        assert all(table.columns for table in contract.tables.values()), name


def test_contract_does_not_accept_006_without_its_unique_index(tmp_path: Path) -> None:
    database = tmp_path / "incomplete-006.sqlite3"
    _apply_prefix(database, 6, mark=False)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("DROP INDEX uq_pod_customization_style_titles_normalized")
        assert not _migration_effect_is_present(connection, "006_pod_titles")


def test_contract_does_not_accept_007_without_recreated_owner_index(tmp_path: Path) -> None:
    database = tmp_path / "incomplete-007.sqlite3"
    _apply_prefix(database, 7, mark=False)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("DROP INDEX idx_pod_customization_batches_owner")
        assert not _migration_effect_is_present(
            connection, "007_requested_count_upgrade"
        )


def test_contract_checks_008_status_semantics_not_only_object_names(tmp_path: Path) -> None:
    database = tmp_path / "wrong-check-008.sqlite3"
    _apply_prefix(database, 7, mark=False)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """CREATE TABLE pod_customization_billing_runs (
                   run_id TEXT PRIMARY KEY,
                   action_key TEXT NOT NULL UNIQUE,
                   action_type TEXT NOT NULL,
                   target_id TEXT NOT NULL,
                   batch_id TEXT NOT NULL DEFAULT '',
                   workspace_id TEXT NOT NULL,
                   owner_user_id TEXT NOT NULL,
                   freeze_id TEXT NOT NULL,
                   rule_version INTEGER NOT NULL,
                   grant_expires_at TEXT NOT NULL,
                   plan_json TEXT NOT NULL,
                   action_payload_json TEXT NOT NULL DEFAULT '{}',
                   status TEXT NOT NULL,
                   result_status TEXT NOT NULL DEFAULT '',
                   error_message TEXT NOT NULL DEFAULT '',
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   settled_at TEXT NOT NULL DEFAULT ''
               );
               CREATE INDEX idx_pod_billing_runs_owner_status
                   ON pod_customization_billing_runs
                      (workspace_id, owner_user_id, status, updated_at DESC);
               CREATE INDEX idx_pod_billing_runs_batch
                   ON pod_customization_billing_runs
                      (batch_id, workspace_id, owner_user_id, status);
               CREATE TABLE pod_customization_billing_outcomes (
                   run_id TEXT NOT NULL,
                   call_id TEXT NOT NULL,
                   feature TEXT NOT NULL,
                   status TEXT NOT NULL,
                   updated_at TEXT NOT NULL,
                   PRIMARY KEY (run_id, call_id)
               );
               CREATE INDEX idx_pod_billing_outcomes_run_status
                   ON pod_customization_billing_outcomes (run_id, status, call_id);"""
        )
        assert not _migration_effect_is_present(
            connection, "008_persistent_billing_runs"
        )
