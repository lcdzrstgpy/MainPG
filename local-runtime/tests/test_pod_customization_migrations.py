from __future__ import annotations

import sqlite3
from pathlib import Path

from wh_local.db import _module_migrations
from wh_local.modules.pod_customization.repository import PodCustomizationRepository


MIGRATIONS = Path(__file__).parents[1] / "wh_local" / "modules" / "pod_customization" / "migrations"


def apply_migrations(database: Path, names: list[str]) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for name in names:
            connection.executescript((MIGRATIONS / name).read_text(encoding="utf-8"))


def table_sql(database: Path, table: str) -> str:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_migrations_are_forward_only_and_007_expands_requested_count(tmp_path: Path) -> None:
    database = tmp_path / "pod.sqlite3"
    names = [
        "001_pod_customization.sql",
        "002_direct_listing_trials.sql",
        "003_style_grid_v2.sql",
        "004_style_grid_publications.sql",
        "005_dianxiaomi_exports.sql",
        "006_pod_titles.sql",
    ]
    apply_migrations(database, names)
    assert "requested_count IN (20, 40, 100)" in table_sql(
        database, "pod_customization_batches"
    )

    apply_migrations(database, ["007_requested_count_upgrade.sql"])
    assert "requested_count BETWEEN 1 AND 200" in table_sql(
        database, "pod_customization_batches"
    )


def test_007_preserves_existing_batch_and_foreign_keys(tmp_path: Path) -> None:
    database = tmp_path / "upgrade.sqlite3"
    names = sorted(path.name for path in MIGRATIONS.glob("*.sql") if not path.name.startswith("007_"))
    apply_migrations(database, names)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO pod_customization_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("asset", "workspace-a", "owner", "template", "a.png", "a.png", "image/png", 1, "sha", 1, 1, "now"),
        )
        connection.execute(
            "INSERT INTO pod_customization_templates VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("template", "workspace-a", "owner", "Template", "personal", "asset", 1, 1, "ready", "{}", "", 1, "", "now", "now"),
        )
        connection.execute(
            "INSERT INTO pod_customization_template_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("snapshot", "template", "workspace-a", "owner", 1, "Template", "personal", "asset", 1, 1, "{}", "now"),
        )
        connection.execute(
            """INSERT INTO pod_customization_batches (
                   batch_id, workspace_id, owner_user_id, title, status, template_id,
                   template_snapshot_id, template_name, requested_count, initial_call_count,
                   max_refill_calls, prompt_version, prompt_snapshot, business_fields_json,
                   listing_fields_json, creative_prompt, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("batch", "workspace-a", "owner", "Batch", "queued", "template", "snapshot", "Template", 20, 20, 0, "v1", "prompt", "{}", "{}", "creative", "now", "now"),
        )

    apply_migrations(database, ["007_requested_count_upgrade.sql"])
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT requested_count, listing_fields_json, creative_prompt FROM pod_customization_batches"
        ).fetchone()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    assert row == (20, "{}", "creative")
    assert violations == []


def test_008_adds_secret_free_persistent_billing_runs(tmp_path: Path) -> None:
    database = tmp_path / "billing-runs.sqlite3"
    apply_migrations(
        database,
        [
            "001_pod_customization.sql",
            "002_direct_listing_trials.sql",
            "003_style_grid_v2.sql",
            "004_style_grid_publications.sql",
            "005_dianxiaomi_exports.sql",
            "006_pod_titles.sql",
            "007_requested_count_upgrade.sql",
            "008_persistent_billing_runs.sql",
        ],
    )

    with sqlite3.connect(database) as connection:
        run_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(pod_customization_billing_runs)")
        }
        outcome_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(pod_customization_billing_outcomes)")
        }

    assert {"run_id", "action_key", "workspace_id", "owner_user_id", "freeze_id", "plan_json"} <= run_columns
    assert {"run_id", "call_id", "feature", "status"} <= outcome_columns
    assert not any("token" in column or "key" in column and column != "action_key" for column in run_columns)
    assert not any("token" in column or "key" in column for column in outcome_columns)


def test_008_is_registered_by_the_application_migration_registry() -> None:
    migration_ids = [migration_id for migration_id, _module, _sql in _module_migrations()]
    assert "pod_customization:008_persistent_billing_runs" in migration_ids


def test_repository_recovers_when_ddl_exists_but_pod_markers_are_missing(tmp_path: Path) -> None:
    database = tmp_path / "markerless.sqlite3"
    names = sorted(path.name for path in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    apply_migrations(database, names)

    PodCustomizationRepository(database)
    PodCustomizationRepository(database)

    with sqlite3.connect(database) as connection:
        markers = connection.execute(
            """SELECT migration_id FROM schema_migrations
               WHERE module = 'pod_customization' ORDER BY migration_id"""
        ).fetchall()
    assert [row[0] for row in markers] == [
        f"pod_customization:{Path(name).stem}" for name in names
    ]


def test_repository_completes_a_partially_applied_008_before_marking_it(tmp_path: Path) -> None:
    database = tmp_path / "partial-008.sqlite3"
    names = sorted(
        path.name
        for path in MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")
        if not path.name.startswith("008_")
    )
    apply_migrations(database, names)
    migration_008 = (MIGRATIONS / "008_persistent_billing_runs.sql").read_text(encoding="utf-8")
    first_table_only = migration_008.split(
        "CREATE INDEX IF NOT EXISTS idx_pod_billing_runs_owner_status", 1
    )[0]
    with sqlite3.connect(database) as connection:
        connection.executescript(first_table_only)

    PodCustomizationRepository(database)

    with sqlite3.connect(database) as connection:
        outcomes = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pod_customization_billing_outcomes'"
        ).fetchone()
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_pod_billing_%'"
            )
        }
        marker = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = 'pod_customization:008_persistent_billing_runs'"
        ).fetchone()
    assert outcomes is not None
    assert indexes == {
        "idx_pod_billing_runs_owner_status",
        "idx_pod_billing_runs_batch",
        "idx_pod_billing_outcomes_run_status",
    }
    assert marker is not None
