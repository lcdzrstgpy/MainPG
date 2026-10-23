from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from wh_local.pod_migrations import ensure_pod_migration, pod_migration_effect_is_present


MIGRATIONS = (
    Path(__file__).parents[1]
    / "wh_local"
    / "modules"
    / "pod_customization"
    / "migrations"
)
FULL_MIGRATIONS = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
PRE_027 = [name for name in FULL_MIGRATIONS if not name.startswith("027_")]


def _apply(database: Path, names: list[str]) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for name in names:
            connection.executescript((MIGRATIONS / name).read_text(encoding="utf-8"))


def _table_columns(database: Path, table: str) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }


def _legacy_run_row(
    *,
    run_id: str,
    action_key: str,
    plan_json: str,
    status: str = "settled",
    freeze_id: str = "freeze-1",
) -> tuple[object, ...]:
    return (
        run_id,
        action_key,
        "batch_initial",
        "batch-1",
        "batch-1",
        "workspace-a",
        "owner",
        freeze_id,
        7,
        "2099-01-01T00:00:00Z",
        plan_json,
        "{}",
        status,
        "",
        "",
        "2026-10-01T00:00:00Z",
        "2026-10-01T00:00:00Z",
        "2026-10-01T00:00:00Z",
    )


def test_fresh_database_builds_ledger_tables(tmp_path: Path) -> None:
    database = tmp_path / "fresh.sqlite3"
    _apply(database, FULL_MIGRATIONS)

    columns = _table_columns(database, "pod_customization_billing_runs")
    assert {
        "module",
        "unit_kind",
        "billing_profile",
        "link_count",
        "item_count",
        "frozen_points",
        "link_prices_json",
        "unit_ids_json",
        "multiplier_percent",
        "settle_deadline",
        "device_id",
        "freeze_raw_json",
        "release_reason",
        "zero_billed",
        "batch_deleted_at",
    } <= columns
    assert _table_columns(database, "pod_customization_billing_settlements") >= {
        "settlement_id",
        "freeze_id",
        "source",
        "expected_points",
        "charged_points",
        "refunded_points",
        "released_points",
        "retained_points",
        "release_reason",
        "rule_version",
        "raw_response_json",
    }
    assert _table_columns(
        database, "pod_customization_billing_settlement_items"
    ) >= {"settlement_id", "link_idx", "unit_id", "feature_status_json"}
    assert _table_columns(database, "pod_customization_billing_recon_runs") >= {
        "run_id",
        "scope",
        "checked_count",
        "repaired_count",
        "loss_count",
        "loss_points",
        "daily_difference",
    }


def test_freeze_id_null_allows_multiple_intent_rows(tmp_path: Path) -> None:
    database = tmp_path / "intent.sqlite3"
    _apply(database, FULL_MIGRATIONS)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for index in (1, 2):
            connection.execute(
                """INSERT INTO pod_customization_billing_runs (
                       run_id, action_key, action_type, target_id, batch_id, workspace_id,
                       owner_user_id, freeze_id, plan_json, status, created_at, updated_at
                   ) VALUES (?, ?, 'batch_initial', 'batch', 'batch', 'workspace-a',
                             'owner', NULL, '{}', 'intent', 'now', 'now')""",
                (f"run-{index}", f"action-{index}"),
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM pod_customization_billing_runs WHERE freeze_id IS NULL"
        ).fetchone()[0] == 2


def test_status_check_rejects_unknown_state(tmp_path: Path) -> None:
    database = tmp_path / "status.sqlite3"
    _apply(database, FULL_MIGRATIONS)
    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """INSERT INTO pod_customization_billing_runs (
                   run_id, action_key, action_type, target_id, batch_id, workspace_id,
                   owner_user_id, freeze_id, plan_json, status, created_at, updated_at
               ) VALUES ('run', 'action', 'batch_initial', 'batch', 'batch', 'workspace-a',
                         'owner', 'freeze', '{}', 'not-a-state', 'now', 'now')"""
        )


def test_upgrade_from_008_preserves_rows_and_derives_module(tmp_path: Path) -> None:
    database = tmp_path / "upgrade.sqlite3"
    _apply(database, PRE_027)
    semi_plan = '{"idempotency_key":"k1","calls":[],"billing_profile":"pod_semi_v1"}'
    custom_plan = '{"idempotency_key":"k2","calls":[],"billing_profile":"pod_random_v1"}'
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executemany(
            """INSERT INTO pod_customization_billing_runs (
                   run_id, action_key, action_type, target_id, batch_id, workspace_id,
                   owner_user_id, freeze_id, rule_version, grant_expires_at, plan_json,
                   action_payload_json, status, result_status, error_message, created_at,
                   updated_at, settled_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                _legacy_run_row(
                    run_id="run-semi",
                    action_key="action-semi",
                    plan_json=semi_plan,
                    status="auth_required",
                ),
                _legacy_run_row(
                    run_id="run-custom",
                    action_key="action-custom",
                    plan_json=custom_plan,
                    freeze_id="freeze-2",
                ),
            ],
        )
        connection.execute(
            """INSERT INTO pod_customization_billing_outcomes
               (run_id, call_id, feature, status, updated_at)
               VALUES ('run-custom', 'call-1', 'pod.image', 'success', 'now')"""
        )

    _apply(database, ["027_billing_ledger_v2.sql"])

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = {
            row["run_id"]: row
            for row in connection.execute(
                "SELECT * FROM pod_customization_billing_runs"
            )
        }
        outcomes = connection.execute(
            "SELECT COUNT(*) FROM pod_customization_billing_outcomes"
        ).fetchone()[0]
    assert set(rows) == {"run-semi", "run-custom"}
    assert rows["run-semi"]["status"] == "auth_required"
    assert rows["run-semi"]["module"] == "pod_semi"
    assert rows["run-semi"]["unit_kind"] == "group"
    assert rows["run-semi"]["billing_profile"] == "pod_semi_v1"
    assert rows["run-semi"]["frozen_points"] == 0
    assert rows["run-semi"]["settle_deadline"] == ""
    assert rows["run-custom"]["module"] == "pod_custom"
    assert rows["run-custom"]["unit_kind"] == "style"
    assert rows["run-custom"]["freeze_id"] == "freeze-2"
    assert outcomes == 1


def test_ensure_pod_migration_applies_027_contract(tmp_path: Path) -> None:
    database = tmp_path / "ensure.sqlite3"
    _apply(database, PRE_027)
    sql = (MIGRATIONS / "027_billing_ledger_v2.sql").read_text(encoding="utf-8")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   migration_id TEXT PRIMARY KEY,
                   module TEXT NOT NULL,
                   applied_at TEXT NOT NULL DEFAULT (datetime('now'))
               )"""
        )
        assert not pod_migration_effect_is_present(connection, "027_billing_ledger_v2")
        ensure_pod_migration(connection, "027_billing_ledger_v2", sql)
        assert pod_migration_effect_is_present(connection, "027_billing_ledger_v2")
        marker = connection.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
            ("pod_customization:027_billing_ledger_v2",),
        ).fetchone()
    assert marker is not None
