import sqlite3
from pathlib import Path

from sqlalchemy import inspect

from wh_local.modules.product_processing.infrastructure.database import create_database


def test_dimension_canvas_schema_and_preview_revision_exist() -> None:
    database = create_database("sqlite:///:memory:")
    inspector = inspect(database.engine)

    assert {
        "product_processing_dimension_batches",
        "product_processing_dimension_items",
        "product_processing_dimension_assets",
        "product_processing_dimension_change_sets",
        "product_processing_dimension_change_items",
        "product_processing_dimension_notifications",
    }.issubset(set(inspector.get_table_names()))
    draft_columns = {
        column["name"]
        for column in inspector.get_columns("product_processing_drafts")
    }
    item_columns = {
        column["name"]
        for column in inspector.get_columns("product_processing_dimension_items")
    }
    assert "preview_revision" in draft_columns
    assert {
        "render_input_hash",
        "rendered_input_hash",
        "publish_claim_token",
        "publish_claimed_at",
    }.issubset(item_columns)

    database.dispose()


def test_dimension_item_identity_and_change_set_idempotency_are_unique() -> None:
    database = create_database("sqlite:///:memory:")
    inspector = inspect(database.engine)

    item_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "product_processing_dimension_items"
        )
    }
    change_set_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "product_processing_dimension_change_sets"
        )
    }
    assert "uq_dimension_item_identity" in item_constraints
    assert "uq_dimension_change_set_idempotency" in change_set_constraints

    database.dispose()


def test_legacy_dimension_template_tables_receive_learning_columns(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-dimension-templates.sqlite3"
    migration_path = (
        Path(__file__).parents[1]
        / "wh_local"
        / "modules"
        / "product_processing"
        / "migrations"
        / "005_dimension_templates.sql"
    )
    legacy_sql = migration_path.read_text(encoding="utf-8")
    for declaration in (
        "quarantined_axis_count INTEGER NOT NULL DEFAULT 0,",
        "accuracy_json TEXT NOT NULL DEFAULT '{}',",
        "quality_json TEXT NOT NULL DEFAULT '{}',",
        "raw_estimate_json TEXT NOT NULL DEFAULT '{}',",
        "resolved_estimate_json TEXT NOT NULL DEFAULT '{}',",
        "error_metrics_json TEXT NOT NULL DEFAULT '{}',",
    ):
        legacy_sql = legacy_sql.replace(f"    {declaration}\n", "")

    with sqlite3.connect(database_path) as connection:
        connection.executescript(legacy_sql)
        template_columns_before = {
            row[1]
            for row in connection.execute("PRAGMA table_info(product_dimension_templates)")
        }
        observation_columns_before = {
            row[1]
            for row in connection.execute("PRAGMA table_info(product_dimension_observations)")
        }

    assert "accuracy_json" not in template_columns_before
    assert "quality_json" not in observation_columns_before

    database = create_database(f"sqlite:///{database_path.as_posix()}")
    inspector = inspect(database.engine)
    template_columns = {
        column["name"]
        for column in inspector.get_columns("product_dimension_templates")
    }
    observation_columns = {
        column["name"]
        for column in inspector.get_columns("product_dimension_observations")
    }

    assert {"accuracy_json", "quarantined_axis_count"}.issubset(template_columns)
    assert {
        "quality_json",
        "raw_estimate_json",
        "resolved_estimate_json",
        "error_metrics_json",
    }.issubset(observation_columns)

    database.dispose()
