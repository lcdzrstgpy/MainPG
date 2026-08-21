from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Mapping


class PodMigrationContractError(RuntimeError):
    """Raised when a POD migration cannot be completed without risking data loss."""


@dataclass(frozen=True)
class TableEffect:
    columns: tuple[str, ...]
    sql_fragments: tuple[str, ...] = ()
    sql_any: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class IndexEffect:
    table: str
    columns: tuple[str, ...]
    unique: bool = False
    descending: tuple[bool, ...] = ()
    sql_fragments: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationEffect:
    tables: Mapping[str, TableEffect] = field(default_factory=dict)
    indexes: Mapping[str, IndexEffect] = field(default_factory=dict)
    column_additions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    absent_tables: tuple[str, ...] = ()


def _table(
    columns: str,
    *,
    checks: tuple[str, ...] = (),
    any_checks: tuple[tuple[str, ...], ...] = (),
) -> TableEffect:
    return TableEffect(tuple(columns.split()), checks, any_checks)


def _index(
    table: str,
    columns: str,
    *,
    unique: bool = False,
    descending: tuple[bool, ...] = (),
    sql_fragments: tuple[str, ...] = (),
) -> IndexEffect:
    return IndexEffect(
        table=table,
        columns=tuple(columns.split()),
        unique=unique,
        descending=descending,
        sql_fragments=sql_fragments,
    )


POD_MIGRATION_CONTRACTS: dict[str, MigrationEffect] = {
    "001_pod_customization": MigrationEffect(
        tables={
            "pod_customization_assets": _table(
                "asset_id workspace_id owner_user_id kind filename relative_path content_type "
                "byte_size sha256 width height created_at"
            ),
            "pod_customization_templates": _table(
                "template_id workspace_id owner_user_id name source asset_id width height "
                "calibration_status calibration_json error_message version deleted_at created_at updated_at",
                checks=("CHECK (source IN ('system', 'personal'))",),
            ),
            "pod_customization_template_snapshots": _table(
                "snapshot_id template_id workspace_id owner_user_id version name source asset_id "
                "width height calibration_json created_at"
            ),
            "pod_customization_batches": _table(
                "batch_id workspace_id owner_user_id title status template_id template_snapshot_id "
                "template_name requested_count processed_count completed_count failed_count "
                "initial_call_count refill_call_count max_refill_calls prompt_version prompt_snapshot "
                "business_fields_json creative_prompt error_message created_at updated_at started_at finished_at",
                any_checks=((
                    "CHECK (requested_count IN (20, 40, 100))",
                    "CHECK (requested_count BETWEEN 1 AND 200)",
                ),),
            ),
            "pod_customization_batch_items": _table(
                "item_id batch_id workspace_id owner_user_id item_index status pattern_asset_id "
                "composite_asset_id pattern_fingerprint scene_optimized error_message created_at updated_at"
            ),
            "pod_customization_generation_calls": _table(
                "call_id batch_id workspace_id owner_user_id call_kind call_index status prompt_snapshot "
                "grid_asset_id error_message created_at started_at finished_at"
            ),
            "pod_customization_pattern_candidates": _table(
                "candidate_id batch_id call_id workspace_id owner_user_id grid_cell status "
                "rejection_reason fingerprint pattern_asset_id created_at"
            ),
        },
        indexes={
            "idx_pod_customization_assets_owner": _index(
                "pod_customization_assets",
                "workspace_id owner_user_id created_at",
                descending=(False, False, True),
            ),
            "idx_pod_customization_templates_owner": _index(
                "pod_customization_templates",
                "workspace_id owner_user_id deleted_at updated_at",
                descending=(False, False, False, True),
            ),
            "idx_pod_customization_template_snapshots_owner": _index(
                "pod_customization_template_snapshots",
                "workspace_id owner_user_id template_id version",
                descending=(False, False, False, True),
            ),
            "idx_pod_customization_batches_owner": _index(
                "pod_customization_batches",
                "workspace_id owner_user_id created_at",
                descending=(False, False, True),
            ),
            "idx_pod_customization_batches_status": _index(
                "pod_customization_batches", "status updated_at"
            ),
            "idx_pod_customization_batch_items_owner": _index(
                "pod_customization_batch_items",
                "batch_id workspace_id owner_user_id item_index",
            ),
            "idx_pod_customization_generation_calls_batch": _index(
                "pod_customization_generation_calls", "batch_id call_kind call_index"
            ),
            "idx_pod_customization_pattern_candidates_batch": _index(
                "pod_customization_pattern_candidates", "batch_id status created_at"
            ),
        },
    ),
    "002_direct_listing_trials": MigrationEffect(
        tables={
            "pod_customization_direct_listing_trials": _table(
                "trial_id workspace_id owner_user_id template_id status prompt_snapshot "
                "grid_attempt_asset_ids_json panel_asset_ids_json public_urls_json error_message "
                "created_at updated_at",
                checks=("CHECK (status IN ('completed', 'failed'))",),
            )
        },
        indexes={
            "idx_pod_direct_listing_trials_owner": _index(
                "pod_customization_direct_listing_trials",
                "workspace_id owner_user_id created_at",
                descending=(False, False, True),
            )
        },
    ),
    "003_style_grid_v2": MigrationEffect(
        tables={
            "pod_customization_style_grid_batches": _table("batch_id created_at"),
            "pod_customization_style_grid_results": _table(
                "result_id batch_id workspace_id owner_user_id style_index variant_index status "
                "pattern_asset_id composite_asset_id pattern_fingerprint scene_optimized "
                "error_message created_at updated_at"
            ),
        },
        indexes={
            "idx_pod_style_grid_results_batch": _index(
                "pod_customization_style_grid_results",
                "batch_id style_index variant_index",
            )
        },
    ),
    "004_style_grid_publications": MigrationEffect(
        tables={
            "pod_customization_style_grid_publications": _table(
                "result_id role public_url updated_at"
            )
        }
    ),
    "005_dianxiaomi_exports": MigrationEffect(
        tables={
            "pod_customization_style_copy": _table(
                "batch_id style_index title english_title description created_at updated_at"
            )
        },
        indexes={
            "idx_pod_customization_style_copy_batch": _index(
                "pod_customization_style_copy", "batch_id style_index"
            )
        },
        column_additions={"pod_customization_batches": ("listing_fields_json",)},
    ),
    "006_pod_titles": MigrationEffect(
        tables={
            "pod_customization_style_titles": _table(
                "batch_id style_index style_task_id status title normalized_title visual_tags_json "
                "model prompt_version attempt_count error_message created_at updated_at started_at finished_at",
                checks=(
                    "CHECK (status IN ('queued', 'generating', 'completed', 'failed'))",
                ),
            ),
            "pod_customization_direct_listing_titles": _table(
                "trial_id style_task_id status title normalized_title visual_tags_json model "
                "prompt_version attempt_count error_message created_at updated_at",
                checks=("CHECK (status IN ('completed', 'failed'))",),
            ),
        },
        indexes={
            "idx_pod_customization_style_titles_status": _index(
                "pod_customization_style_titles", "batch_id status style_index"
            ),
            "uq_pod_customization_style_titles_normalized": _index(
                "pod_customization_style_titles",
                "batch_id normalized_title",
                unique=True,
                sql_fragments=(
                    "WHERE normalized_title IS NOT NULL AND normalized_title <> ''",
                ),
            ),
        },
    ),
    "007_requested_count_upgrade": MigrationEffect(
        tables={
            "pod_customization_batches": _table(
                "batch_id workspace_id owner_user_id title status template_id template_snapshot_id "
                "template_name requested_count processed_count completed_count failed_count "
                "initial_call_count refill_call_count max_refill_calls prompt_version prompt_snapshot "
                "business_fields_json creative_prompt error_message created_at updated_at started_at "
                "finished_at listing_fields_json",
                checks=("CHECK (requested_count BETWEEN 1 AND 200)",),
            )
        },
        indexes={
            "idx_pod_customization_batches_owner": _index(
                "pod_customization_batches",
                "workspace_id owner_user_id created_at",
                descending=(False, False, True),
            ),
            "idx_pod_customization_batches_status": _index(
                "pod_customization_batches", "status updated_at"
            ),
        },
        absent_tables=("pod_customization_batches_requested_count_v2",),
    ),
    "008_persistent_billing_runs": MigrationEffect(
        tables={
            "pod_customization_billing_runs": _table(
                "run_id action_key action_type target_id batch_id workspace_id owner_user_id freeze_id "
                "rule_version grant_expires_at plan_json action_payload_json status result_status "
                "error_message created_at updated_at settled_at",
                checks=(
                    "CHECK (action_type IN ('batch_initial', 'direct_trial', 'scene_optimization', "
                    "'item_retry', 'style_retry', 'title_retry'))",
                    "CHECK (status IN ('authorized', 'resume_claimed', 'settling', "
                    "'settlement_pending', 'auth_required', 'settled'))",
                ),
            ),
            "pod_customization_billing_outcomes": _table(
                "run_id call_id feature status updated_at",
                checks=(
                    "CHECK (feature IN ('pod.title', 'pod.image'))",
                    "CHECK (status IN ('planned', 'started', 'success', 'no_return'))",
                ),
            ),
        },
        indexes={
            "idx_pod_billing_runs_owner_status": _index(
                "pod_customization_billing_runs",
                "workspace_id owner_user_id status updated_at",
                descending=(False, False, False, True),
            ),
            "idx_pod_billing_runs_batch": _index(
                "pod_customization_billing_runs",
                "batch_id workspace_id owner_user_id status",
            ),
            "idx_pod_billing_outcomes_run_status": _index(
                "pod_customization_billing_outcomes", "run_id status call_id"
            ),
        },
    ),
    "009_export_records": MigrationEffect(
        tables={
            "pod_customization_export_records": _table(
                "export_id batch_id workspace_id owner_user_id file_name format exported_count "
                "skipped_count created_at",
                checks=(
                    "CHECK (exported_count >= 0)",
                    "CHECK (skipped_count >= 0)",
                ),
            )
        },
        indexes={
            "idx_pod_export_records_owner_batch_created": _index(
                "pod_customization_export_records",
                "workspace_id owner_user_id batch_id created_at export_id",
                descending=(False, False, False, True, True),
            )
        },
    ),
}


def pod_migration_effect_is_present(
    connection: sqlite3.Connection,
    migration_name: str,
) -> bool:
    contract = POD_MIGRATION_CONTRACTS.get(migration_name)
    if contract is None:
        return False
    for table_name, effect in contract.tables.items():
        if not _table_effect_is_present(connection, table_name, effect):
            return False
    for table_name, columns in contract.column_additions.items():
        if not set(columns).issubset(_table_columns(connection, table_name)):
            return False
    for index_name, effect in contract.indexes.items():
        if not _index_effect_is_present(connection, index_name, effect):
            return False
    return not any(_table_exists(connection, table) for table in contract.absent_tables)


def ensure_pod_migration(
    connection: sqlite3.Connection,
    migration_name: str,
    sql: str,
) -> None:
    migration_id = f"pod_customization:{migration_name}"
    marker_exists = connection.execute(
        "SELECT 1 FROM schema_migrations WHERE migration_id = ?", (migration_id,)
    ).fetchone() is not None
    contract = POD_MIGRATION_CONTRACTS.get(migration_name)
    if contract is None:
        if marker_exists:
            return
        connection.executescript(sql)
        _insert_marker(connection, migration_id)
        return
    if pod_migration_effect_is_present(connection, migration_name):
        if not marker_exists:
            _insert_marker(connection, migration_id)
        return

    if migration_name == "005_dianxiaomi_exports":
        _apply_005(connection, sql)
    elif migration_name == "007_requested_count_upgrade":
        _apply_or_recover_007(connection, sql)
    else:
        connection.executescript(sql)

    if not pod_migration_effect_is_present(connection, migration_name):
        raise PodMigrationContractError(
            f"POD migration {migration_name} did not satisfy its complete effect contract"
        )
    if not marker_exists:
        _insert_marker(connection, migration_id)


def recover_interrupted_pod_migrations(
    connection: sqlite3.Connection,
    migrations: Mapping[str, str],
) -> None:
    """Recover states that would be destroyed by replaying an earlier migration."""
    target = "pod_customization_batches"
    temporary = "pod_customization_batches_requested_count_v2"
    if _table_exists(connection, target) or not _table_exists(connection, temporary):
        return
    sql = migrations.get("007_requested_count_upgrade")
    if sql is None:
        raise PodMigrationContractError(
            "POD migration 007_requested_count_upgrade SQL is required to recover "
            "an interrupted batch-table rename"
        )
    _apply_or_recover_007(connection, sql)


def _insert_marker(connection: sqlite3.Connection, migration_id: str) -> None:
    connection.execute(
        """INSERT OR IGNORE INTO schema_migrations (migration_id, module)
           VALUES (?, 'pod_customization')""",
        (migration_id,),
    )


def _apply_005(connection: sqlite3.Connection, sql: str) -> None:
    alter_token = "ALTER TABLE pod_customization_batches"
    if alter_token not in sql:
        raise PodMigrationContractError(
            "POD migration 005_dianxiaomi_exports has an unsupported SQL shape"
        )
    additive_objects, alter_statement = sql.split(alter_token, 1)
    connection.executescript(additive_objects)
    if "listing_fields_json" not in _table_columns(
        connection, "pod_customization_batches"
    ):
        connection.execute(f"{alter_token}{alter_statement}")


def _apply_or_recover_007(connection: sqlite3.Connection, sql: str) -> None:
    target = "pod_customization_batches"
    temporary = "pod_customization_batches_requested_count_v2"
    if not _table_exists(connection, target):
        target_contract = POD_MIGRATION_CONTRACTS[
            "007_requested_count_upgrade"
        ].tables[target]
        if not _table_effect_is_present(connection, temporary, target_contract):
            raise PodMigrationContractError(
                "POD migration 007_requested_count_upgrade found an incomplete "
                "recovery table while the original batch table is missing"
            )
        rename_token = f"ALTER TABLE {temporary}"
        if rename_token not in sql or "COMMIT;" not in sql:
            raise PodMigrationContractError(
                "POD migration 007_requested_count_upgrade has an unsupported SQL shape"
            )
        finalize = sql.split(rename_token, 1)[1].split("COMMIT;", 1)[0]
        recovery_sql = (
            "PRAGMA foreign_keys = OFF;\nBEGIN IMMEDIATE;\n"
            f"{rename_token}{finalize}"
            "COMMIT;\nPRAGMA foreign_keys = ON;\n"
        )
        _executescript_restoring_foreign_keys(connection, recovery_sql)
        return
    _executescript_restoring_foreign_keys(connection, sql)


def _executescript_restoring_foreign_keys(
    connection: sqlite3.Connection,
    sql: str,
) -> None:
    try:
        connection.executescript(sql)
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        connection.execute("PRAGMA foreign_keys=ON")
        raise
    connection.execute("PRAGMA foreign_keys=ON")


def _table_effect_is_present(
    connection: sqlite3.Connection,
    table_name: str,
    effect: TableEffect,
) -> bool:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    if row is None:
        return False
    if not set(effect.columns).issubset(_table_columns(connection, table_name)):
        return False
    normalized_sql = _normalize_sql(str(row[0]))
    if any(_normalize_sql(fragment) not in normalized_sql for fragment in effect.sql_fragments):
        return False
    return all(
        any(_normalize_sql(fragment) in normalized_sql for fragment in alternatives)
        for alternatives in effect.sql_any
    )


def _index_effect_is_present(
    connection: sqlite3.Connection,
    index_name: str,
    effect: IndexEffect,
) -> bool:
    row = connection.execute(
        "SELECT tbl_name, sql FROM sqlite_master WHERE type = 'index' AND name = ?",
        (index_name,),
    ).fetchone()
    if row is None or str(row[0]) != effect.table:
        return False
    index_list = {
        str(item[1]): bool(item[2])
        for item in connection.execute(f"PRAGMA index_list({_quoted(effect.table)})")
    }
    if index_list.get(index_name) is not effect.unique:
        return False
    key_rows = [
        item
        for item in connection.execute(f"PRAGMA index_xinfo({_quoted(index_name)})")
        if bool(item[5])
    ]
    if tuple(str(item[2]) for item in key_rows) != effect.columns:
        return False
    descending = tuple(bool(item[3]) for item in key_rows)
    expected_descending = effect.descending or (False,) * len(effect.columns)
    if descending != expected_descending:
        return False
    normalized_sql = _normalize_sql(str(row[1] or ""))
    return all(
        _normalize_sql(fragment) in normalized_sql for fragment in effect.sql_fragments
    )


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    return {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({_quoted(table_name)})")
    }


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _normalize_sql(value: str) -> str:
    return re.sub(r"[\s\"`\[\]]+", "", value).lower()
