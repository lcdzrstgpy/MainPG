from pathlib import Path

from wh_local.db import _module_migrations, init_db, transaction


def test_price_verification_forward_migrations_are_registered_in_order() -> None:
    migration_ids = [migration_id for migration_id, _module, _sql in _module_migrations()]

    prescreen = migration_ids.index("price_verification:007_prescreen_settings")
    batch_sessions = migration_ids.index("price_verification:008_batch_sourcing_sessions")

    assert prescreen < batch_sessions


def test_shop_and_direct_intake_migrations_are_registered_in_dependency_order() -> None:
    migration_ids = [migration_id for migration_id, _module, _sql in _module_migrations()]

    shop_schema = migration_ids.index("data_collection:005_shop_collection")
    shop_leases = migration_ids.index("data_collection:006_shop_collection_lease_tokens")
    sku_repull_outbox = migration_ids.index("data_collection:007_sku_repull_outbox")
    direct_intake = migration_ids.index("product_processing:004_shop_candidate_uniqueness")
    dimension_templates = migration_ids.index("product_processing:005_dimension_templates")

    assert shop_schema < shop_leases
    assert shop_leases < sku_repull_outbox
    assert direct_intake > migration_ids.index("product_processing:003_source_image_sync_lease")
    assert dimension_templates > direct_intake


def test_pod_customization_migrations_are_registered_in_forward_order() -> None:
    migration_ids = [migration_id for migration_id, _module, _sql in _module_migrations()]
    pod_ids = [
        migration_id
        for migration_id in migration_ids
        if migration_id.startswith("pod_customization:")
    ]

    assert pod_ids == [
        "pod_customization:001_pod_customization",
        "pod_customization:002_direct_listing_trials",
        "pod_customization:003_style_grid_v2",
        "pod_customization:004_style_grid_publications",
        "pod_customization:005_dianxiaomi_exports",
        "pod_customization:006_pod_titles",
        "pod_customization:007_requested_count_upgrade",
        "pod_customization:008_persistent_billing_runs",
        "pod_customization:009_export_records",
        "pod_customization:010_pod_title_source",
        "pod_customization:011_pod_style_export_selection",
        "pod_customization:012_batch_execution_fencing",
        "pod_customization:013_style_elements",
        "pod_customization:014_semi_customization",
        "pod_customization:015_replica_customization",
        "pod_customization:016_pod_style_events",
    ]


def test_pod_migration_sql_files_on_disk_match_the_registry() -> None:
    """磁盘上的 POD 迁移 .sql 必须与唯一权威注册表一一对应（双向卡死）。

    `init_db` 已改为从 `POD_MIGRATION_CONTRACTS` 派生迁移清单，所以：
      * 磁盘多一个 .sql 而注册表没登记 → 该迁移被静默跳过（表建不出来）；
      * 注册表登记了却没有 .sql 文件 → 同样被静默跳过。
    2026-10-08 一天内出现三次「新增迁移只改了一半」的漂移，故加此守卫。
    """
    from wh_local.pod_migrations import POD_MIGRATION_CONTRACTS

    migration_root = (
        Path(__file__).parents[1]
        / "wh_local"
        / "modules"
        / "pod_customization"
        / "migrations"
    )
    on_disk = {path.stem for path in migration_root.glob("*.sql")}
    registered = set(POD_MIGRATION_CONTRACTS)

    assert on_disk == registered, (
        f"仅在磁盘: {sorted(on_disk - registered)}；"
        f"仅注册表: {sorted(registered - on_disk)}"
    )


def test_init_db_applies_pod_title_source_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "pod.sqlite3"
    init_db(database_path)

    with transaction(database_path) as conn:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(pod_customization_style_titles)")
        }

    assert "source" in columns


def test_dimension_template_migration_creates_learning_and_quality_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "dimension-templates.sqlite3"
    init_db(database_path)

    with transaction(database_path) as conn:
        template_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(product_dimension_templates)")
        }
        observation_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(product_dimension_observations)")
        }
        refresh_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(product_dimension_template_refresh_queue)")
        }

    assert {"accuracy_json", "quarantined_axis_count"}.issubset(template_columns)
    assert {
        "quality_json",
        "raw_estimate_json",
        "resolved_estimate_json",
        "error_metrics_json",
    }.issubset(observation_columns)
    assert {"pending_changes", "not_before_epoch", "last_error"}.issubset(refresh_columns)


def test_operator_role_receives_new_pod_permissions(tmp_path: Path) -> None:
    database_path = tmp_path / "permissions.sqlite3"
    init_db(database_path)

    with transaction(database_path) as conn:
        permissions = {
            row["permission_key"]
            for row in conn.execute(
                "SELECT permission_key FROM role_permissions WHERE role = 'operator'"
            )
        }

    assert {
        "pod_customization.read",
        "pod_customization.create",
        "pod_customization.template_manage",
        "pod_customization.export",
    }.issubset(permissions)
