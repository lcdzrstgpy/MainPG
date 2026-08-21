from __future__ import annotations

import sqlite3
from pathlib import Path


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
