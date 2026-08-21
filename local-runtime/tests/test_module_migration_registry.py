from pathlib import Path

from wh_local.db import _module_migrations, init_db, transaction


def test_price_verification_forward_migrations_are_registered_in_order() -> None:
    migration_ids = [migration_id for migration_id, _module, _sql in _module_migrations()]

    prescreen = migration_ids.index("price_verification:007_prescreen_settings")
    batch_sessions = migration_ids.index("price_verification:008_batch_sourcing_sessions")

    assert prescreen < batch_sessions


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
