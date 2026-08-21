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

    assert shop_schema < shop_leases
    assert shop_leases < sku_repull_outbox
    assert direct_intake > migration_ids.index("product_processing:003_source_image_sync_lease")


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
    ]


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
