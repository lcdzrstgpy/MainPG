from wh_local.db import _module_migrations


def test_price_verification_forward_migrations_are_registered_in_order() -> None:
    migration_ids = [migration_id for migration_id, _module, _sql in _module_migrations()]

    prescreen = migration_ids.index("price_verification:007_prescreen_settings")
    batch_sessions = migration_ids.index("price_verification:008_batch_sourcing_sessions")

    assert prescreen < batch_sessions
